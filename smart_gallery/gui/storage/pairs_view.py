"""The Storage page's right column: RAW + JPEG pairs and the keep choices.

Pairs are grouped under folder headings, biggest folder first (as the tree
in Folder mode orders them), and by name within a folder. Each row shows both thumbnails, the
picture check's verdict and three pills (RAW / JPEG / Both). Choices add up in
the footer, and one confirmed button reports the files to send to the Recycle
Bin. Nothing here touches the disk or the catalog.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QListView,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from smart_gallery import colors
from smart_gallery.gui.storage.pairs_dialog import PILLS, PairDialog
from smart_gallery.storage.compare import DIFFERENT, MATCH, UNREADABLE
from smart_gallery.storage.pairs import (
    FileRef,
    Pair,
    PairKey,
    in_scope,
    resolve_removals,
)
from smart_gallery.storage.tree import format_bytes, type_label

TITLE = "RAW + JPEG pairs"
SCOPE_ONE = "In {folder} · {n:,} pairs"
SCOPE_ALL = "All folders · {n:,} pairs"
SHOW_ALL = "Show all {n:,} pairs"
EMPTY_FOLDER = "No RAW + JPEG pairs in this folder"
EMPTY_ALL = "No RAW + JPEG pairs in this gallery"
CHECKING = "Checking pictures… {done:,} / {total:,}"
CHECK_FAILED = "Couldn't check pictures: {reason}"
STATUS = {
    None: "checking…",
    DIFFERENT: "⚠ look different",
    UNREADABLE: "⚠ couldn't compare",
    MATCH: "",
}
FOOTER = "{n:,} chosen · frees {size}"
FOOTER_FOLDERS = " (in {k:,} folders)"
FOOTER_NOTE = "Undecided pairs are left alone"
CLEAR = "Clear choices"
MOVE = "Move {n:,} files to Recycle Bin"
MOVE_NONE = "Move files to Recycle Bin"
MOVE_TITLE = "Move to Recycle Bin"
CONFIRM = "Move {n:,} files ({size}) to the Recycle Bin?"
LEAVE_TITLE = "Unapplied choices"
LEAVE = "You have {n:,} unapplied choices. Discard them?"
KEY_ROLE, CHOICE_ROLE, VERDICT_ROLE = (Qt.ItemDataRole.UserRole + i for i in (1, 2, 3))
PAIR_ROLE = Qt.ItemDataRole.UserRole + 4

THUMB = 52
PAD = 6
HEADING_HEIGHT = 26
ROW_HEIGHT = THUMB + 2 * PAD
PILL_W, PILL_H, PILL_GAP = 50, 22, 4
PIXMAP_CACHE_SIZE = 600
_YES = QMessageBox.StandardButton.Yes
_NO = QMessageBox.StandardButton.No


def details(pair: Pair) -> str:
    """``"CR3 24.0 MB + XMP · JPG 9.80 MB"``."""
    raw, jpeg = pair.raw, pair.jpeg
    side = " + XMP" if pair.sidecar is not None else ""
    return (
        f"{type_label(raw.ext)} {format_bytes(raw.size_bytes)}{side} · "
        f"{type_label(jpeg.ext)} {format_bytes(jpeg.size_bytes)}"
    )


def _same_files(a: Pair | None, b: Pair | None) -> bool:
    if a is None or b is None:
        return False
    return (a.raw, a.jpeg, a.sidecar) == (b.raw, b.jpeg, b.sidecar)


class _PairsModel(QAbstractListModel):
    """Folder heading rows (``str``) and pair rows (``Pair``) in scope."""

    def __init__(self, view: PairsView) -> None:
        super().__init__(view)
        self._view = view
        self.rows: list[Pair | str] = []
        self._row_of: dict[PairKey, int] = {}

    def set_rows(self, rows: list[Pair | str]) -> None:
        self.beginResetModel()
        self.rows = rows
        self._row_of = {r.key: i for i, r in enumerate(rows) if isinstance(r, Pair)}
        self.endResetModel()

    def refresh(self, key: PairKey) -> None:
        i = self._row_of.get(key)
        if i is not None:
            index = self.index(i)
            self.dataChanged.emit(index, index)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self.rows)

    def flags(self, index):
        row = self.rows[index.row()]
        if isinstance(row, str):
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if isinstance(row, str):
            if role == Qt.ItemDataRole.DisplayRole:
                # Small upper-case heading; a root pair shows the root name.
                return (row or self._view.root_name()).upper()
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return row.stem
        if role == KEY_ROLE:
            return row.key
        if role == CHOICE_ROLE:
            return self._view._choices.get(row.key)
        if role == VERDICT_ROLE:
            return self._view._verdicts.get(row.key)
        if role == PAIR_ROLE:
            return row
        return None


class PairsDelegate(QStyledItemDelegate):
    """Paints headings and pair rows; a click on a pill chooses."""

    def __init__(self, view: PairsView) -> None:
        super().__init__(view.list)
        self._view = view

    def button_rects(self, option_rect: QRect) -> dict[str, QRect]:
        top = option_rect.top() + (option_rect.height() - PILL_H) // 2
        right = option_rect.right() - PAD
        rects: dict[str, QRect] = {}
        for choice in reversed(PILLS):
            rects[choice] = QRect(right - PILL_W + 1, top, PILL_W, PILL_H)
            right -= PILL_W + PILL_GAP
        return {c: rects[c] for c in PILLS}

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt API)
        height = ROW_HEIGHT if index.data(KEY_ROLE) is not None else HEADING_HEIGHT
        return QSize(option.rect.width(), height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        pair: Pair | None = index.data(PAIR_ROLE)
        painter.save()
        try:
            if pair is None:
                self._paint_heading(painter, option, index.data())
            else:
                self._paint_pair(painter, option, index, pair)
        finally:
            painter.restore()

    def _paint_heading(self, painter, option, text: str) -> None:
        font = QFont(option.font)
        font.setPointSizeF(font.pointSizeF() * 0.9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(option.palette.placeholderText().color())
        r = option.rect.adjusted(PAD, 0, -PAD, -2)
        painter.drawText(
            r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, text
        )

    def _paint_pair(self, painter, option, index, pair: Pair) -> None:
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget
        )
        r = option.rect
        palette = option.palette
        x = r.left() + PAD
        top = r.top() + PAD
        for ref in (pair.raw, pair.jpeg):
            box = QRect(x, top, THUMB, THUMB)
            pixmap = self._view._thumb(ref)
            if pixmap is None:
                painter.fillRect(box, QColor(*colors.TILE_BACK))
            else:
                size = pixmap.size().scaled(
                    box.size(), Qt.AspectRatioMode.KeepAspectRatio
                )
                target = QRect(0, 0, size.width(), size.height())
                target.moveCenter(box.center())
                painter.drawPixmap(target, pixmap)
            x += THUMB + 4

        rects = self.button_rects(r)
        text_left = x + PAD
        text_right = min(rr.left() for rr in rects.values()) - PAD
        line_h = r.height() // 2 - PAD
        first = QRect(text_left, top, text_right - text_left, line_h)
        second = QRect(text_left, top + line_h, text_right - text_left, line_h)

        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(palette.text().color())
        stem_w = min(painter.fontMetrics().horizontalAdvance(pair.stem), first.width())
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(first, flags, pair.stem)

        status, warn = self._view.status_for(pair.key)
        painter.setFont(option.font)
        if status:
            colour = (
                QColor(colors.WARNING) if warn else palette.placeholderText().color()
            )
            painter.setPen(colour)
            painter.drawText(first.adjusted(stem_w + 8, 0, 0, 0), flags, status)
        painter.setPen(palette.placeholderText().color())
        painter.drawText(second, flags, details(pair))

        choice = index.data(CHOICE_ROLE)
        for key, rect in rects.items():
            on = key == choice
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(palette.highlight().color() if on else palette.mid().color())
            painter.setBrush(palette.highlight() if on else Qt.BrushStyle.NoBrush)
            radius = rect.height() / 2
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)
            painter.setPen(
                palette.highlightedText().color() if on else palette.text().color()
            )
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, PILLS[key])

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        key = index.data(KEY_ROLE)
        if key is None or event.type() not in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        ):
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        pos = event.position().toPoint()
        hit = next(
            (c for c, r in self.button_rects(option.rect).items() if r.contains(pos)),
            None,
        )
        if event.type() == QEvent.Type.MouseButtonPress:
            return hit is not None  # a pill press does not select the row
        if hit is not None:
            self._view.choose(key, hit)
        else:
            self._view.pair_activated.emit(key)
        return True


class PairsView(QWidget):
    move_requested = Signal(list)  # list[FileRef] confirmed by the user
    pair_activated = Signal(object)  # PairKey; opens PairDialog

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._pairs: list[Pair] = []
        self._folder_bytes: dict[str, int] = {}
        self._by_key: dict[PairKey, Pair] = {}
        self._choices: dict[PairKey, str] = {}
        self._verdicts: dict[PairKey, str] = {}
        # The check ended (done or failed): an unknown verdict stays unknown.
        self._check_over = False
        self._scope: str | None = None
        self._root = Path()
        self._pixmaps: OrderedDict[tuple[int, int | None], QPixmap] = OrderedDict()
        self._missing: set[tuple[int, int | None]] = set()
        self._requested: dict[int, tuple[int, int | None]] = {}

        title = QLabel(TITLE)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        self.status = QLabel("")
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.status)

        self.scope_label = QLabel("")
        self.show_all = QPushButton("")
        self.show_all.setFlat(True)
        self.show_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_all.clicked.connect(lambda: self.set_scope(None))
        scope = QHBoxLayout()
        scope.addWidget(self.scope_label)
        scope.addStretch(1)
        scope.addWidget(self.show_all)

        self.model = _PairsModel(self)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(PairsDelegate(self))
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.empty_label = QLabel("")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.list)
        self._stack.addWidget(self.empty_label)

        self.footer_label = QLabel("")
        note = QLabel(FOOTER_NOTE)
        note.setEnabled(False)  # drawn muted
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(self.footer_label)
        texts.addWidget(note)
        self.clear_button = QPushButton(CLEAR)
        self.clear_button.clicked.connect(self.clear_choices)
        self.move_button = QPushButton(MOVE_NONE)
        self.move_button.clicked.connect(self._on_move)
        foot = QHBoxLayout()
        foot.addLayout(texts, 1)
        foot.addWidget(self.clear_button)
        foot.addWidget(self.move_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(head)
        layout.addLayout(scope)
        layout.addWidget(self._stack, 1)
        layout.addLayout(foot)

        # Through a lambda so the slot can be replaced (tests, the page).
        self.pair_activated.connect(lambda key: self._open_dialog(key))
        thumbs = getattr(ctx, "thumbs", None)
        if thumbs is not None:
            thumbs.ready.connect(self._on_thumb_ready)
        self._refresh()

    # ── API ─────────────────────────────────────────────────────────────────
    def set_pairs(
        self,
        pairs: list[Pair],
        root: Path,
        folder_bytes: dict[str, int] | None = None,
    ) -> None:
        """Show ``pairs`` (in ``find_pairs`` order). ``folder_bytes`` maps a
        case-folded folder relpath to its size in the tree, for the order."""
        root = Path(root)
        self._folder_bytes = dict(folder_bytes or {})
        if root != self._root:
            self._pixmaps.clear()
            self._missing.clear()
            self._requested.clear()
        self._root = root
        self._pairs = list(pairs)
        old = self._by_key
        self._by_key = {p.key: p for p in self._pairs}
        # A choice survives only when its files are exactly the ones seen.
        self._choices = {
            k: c
            for k, c in self._choices.items()
            if _same_files(old.get(k), self._by_key.get(k))
        }
        self._verdicts = {}
        self._check_over = False  # the page starts a new check
        self._refresh()

    def root_name(self) -> str:
        return self._root.name or self._root.as_posix()

    def set_scope(self, folder: str | None) -> None:
        self._scope = folder or None
        self._refresh()

    def set_verdict(self, key: PairKey, verdict: str) -> None:
        if key not in self._by_key:
            return
        self._verdicts[key] = verdict
        self.model.refresh(key)

    def set_progress(self, done: int, total: int) -> None:
        text = CHECKING.format(done=done, total=total) if done < total else ""
        self.status.setText(text)

    def check_finished(self) -> None:
        self._check_over = True
        self.status.setText("")
        self.list.viewport().update()

    def check_failed(self, reason: str) -> None:
        """The check stopped: say why, and mark rows it never reached."""
        self._check_over = True
        self.status.setText(CHECK_FAILED.format(reason=reason))
        self.list.viewport().update()

    def status_for(self, key: PairKey) -> tuple[str, bool]:
        """The row's check text and whether it is a warning."""
        verdict = self._verdicts.get(key)
        if verdict is None and self._check_over:
            verdict = UNREADABLE  # shown only; never written to the cache
        return STATUS.get(verdict, ""), verdict in (DIFFERENT, UNREADABLE)

    def choose(self, key: PairKey, choice: str | None) -> None:
        if key not in self._by_key:
            return
        if choice is None or self._choices.get(key) == choice:
            self._choices.pop(key, None)
        else:
            self._choices[key] = choice
        self.model.refresh(key)
        self._update_footer()

    def choices(self) -> dict[PairKey, str]:
        return dict(self._choices)

    def clear_choices(self) -> None:
        self._choices.clear()
        self.list.viewport().update()
        self._update_footer()

    def removals(self) -> list[FileRef]:
        return resolve_removals(self._pairs, self._choices)

    def confirm_leave(self) -> bool:
        if not self._choices:
            return True
        text = LEAVE.format(n=len(self._choices))
        if QMessageBox.question(self, LEAVE_TITLE, text, _YES | _NO, _NO) != _YES:
            return False
        self.clear_choices()
        return True

    # ── internals ───────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        shown = [p for p in self._pairs if in_scope(p, self._scope)]
        groups: dict[str, list[Pair | str]] = {}
        for p in shown:
            groups.setdefault(p.folder.casefold(), [p.folder]).append(p)
        order = sorted(groups, key=lambda f: (-self._group_bytes(f), f))
        self.model.set_rows([row for f in order for row in groups[f]])

        if self._scope:
            label = SCOPE_ONE.format(folder=self._scope, n=len(shown))
            self.show_all.setText(SHOW_ALL.format(n=len(self._pairs)))
        else:
            label = SCOPE_ALL.format(n=len(shown))
        self.scope_label.setText(label)
        self.show_all.setVisible(bool(self._scope))

        if not self._pairs:
            self.empty_label.setText(EMPTY_ALL)
        elif not shown:
            self.empty_label.setText(EMPTY_FOLDER)
        self._stack.setCurrentWidget(self.list if shown else self.empty_label)
        self._update_footer()

    def _group_bytes(self, folder: str) -> int:
        """The folder's size in the tree, else the combined bytes of the pairs
        in it and under it (a folder's tree size includes its subfolders)."""
        size = self._folder_bytes.get(folder)
        if size is not None:
            return size
        return sum(
            f.size_bytes
            for p in self._pairs
            if in_scope(p, folder)
            for f in (p.raw, p.jpeg, p.sidecar)
            if f is not None
        )

    def _update_footer(self) -> None:
        removals = self.removals()
        size = format_bytes(sum(f.size_bytes for f in removals))
        text = FOOTER.format(n=len(self._choices), size=size)
        chosen = [self._by_key[k] for k in self._choices]
        if any(not in_scope(p, self._scope) for p in chosen):
            text += FOOTER_FOLDERS.format(k=len({p.folder.casefold() for p in chosen}))
        self.footer_label.setText(text)
        n = len(removals)
        self.move_button.setText(MOVE.format(n=n) if n else MOVE_NONE)
        self.move_button.setEnabled(bool(n))
        self.clear_button.setEnabled(bool(self._choices))

    def _on_move(self) -> None:
        removals = self.removals()
        if not removals:
            return
        size = format_bytes(sum(f.size_bytes for f in removals))
        text = CONFIRM.format(n=len(removals), size=size)
        if QMessageBox.question(self, MOVE_TITLE, text, _YES | _NO, _NO) == _YES:
            self.move_requested.emit(removals)

    def _open_dialog(self, key: PairKey) -> None:
        pair = self._by_key.get(key)
        if pair is None:
            return
        dialog = PairDialog(self, pair, self._root, self._choices.get(key))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()

    def forget_thumb_requests(self) -> None:
        """Ask again for thumbnails still outstanding: the shared loader may
        have dropped them (``cancel_pending``) while the page was away."""
        self._requested.clear()
        self.list.viewport().update()

    def _thumb(self, ref: FileRef) -> QPixmap | None:
        """The cached thumbnail, or None after asking for it."""
        key = (ref.id, ref.mtime_ns)
        pixmap = self._pixmaps.get(key)
        if pixmap is not None:
            self._pixmaps.move_to_end(key)
            return pixmap
        thumbs = getattr(self.ctx, "thumbs", None)
        if key in self._missing or thumbs is None or ref.id in self._requested:
            return None
        self._requested[ref.id] = key
        thumbs.request(
            self._root, ref.id, ref.mtime_ns, ref.relpath, ref.ext, ref.media_type
        )
        return None

    def _on_thumb_ready(self, root: str, media_id: int, path: str) -> None:
        if root != str(self._root) or media_id not in self._requested:
            return
        key = self._requested.pop(media_id)
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self._missing.add(key)
        else:
            self._pixmaps[key] = pixmap.scaled(
                THUMB,
                THUMB,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            while len(self._pixmaps) > PIXMAP_CACHE_SIZE:
                self._pixmaps.popitem(last=False)
        self.list.viewport().update()
