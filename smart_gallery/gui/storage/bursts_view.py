"""The Storage page's Bursts list: photos taken within seconds of each other.

Bursts are listed largest first. Marks (shots to remove) live here, add up in
the footer, and one confirmed button reports the files to send to the Recycle
Bin. Nothing here touches the disk or the catalog.
"""

from __future__ import annotations

from collections import OrderedDict
from functools import partial
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
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
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from smart_gallery import colors
from smart_gallery.storage.bursts import (
    DEFAULT_GAP,
    MAX_GAP,
    MIN_GAP,
    Burst,
    Shot,
    ShotKey,
    burst_removals,
    can_mark,
    group_bursts,
    reconcile_marks,
)
from smart_gallery.storage.pairs import FileRef, in_scope
from smart_gallery.storage.tree import format_bytes

TITLE = "Bursts"
SCOPE_ONE = "In {folder} · {n:,} bursts · {photos:,} photos · {size}"
SCOPE_ALL = "All folders · {n:,} bursts · {photos:,} photos · {size}"
SHOW_ALL = "Show all {n:,} bursts"
EMPTY_FOLDER = "No bursts in this folder"
EMPTY_ALL = "No bursts in this gallery"
NOT_DECIDED = "not decided"
KEEPING = "keeping {k:,} of {n:,}"
REMOVING = "removing {n:,} · {size}"
KEEP_ONE = "keep at least one"
DROPPED = "{n:,} marks cleared because their bursts changed"
GAP_LABEL = "Gap"
GAP_TITLE = "Change gap"
GAP_CONFIRM = "Discard {n:,} marks?"
FOOTER = "{n:,} marked · frees {size}"
FOOTER_FOLDERS = " (in {k:,} folders)"
FOOTER_NOTE = "Untouched bursts are left alone"
CLEAR = "Clear marks"
MOVE = "Move {n:,} files to Recycle Bin"
MOVE_NONE = "Move files to Recycle Bin"
MOVE_TITLE = "Move to Recycle Bin"
CONFIRM = "Move {n:,} files ({size}) to the Recycle Bin?"
LEAVE_TITLE = "Unapplied choices"
LEAVE = "You have {n:,} unapplied choices. Discard them?"
REVIEW = "Review"
KEEP_TAG = "keep"
REMOVE_TAG = "remove"
KEY_ROLE, BURST_ROLE, STATE_ROLE = (Qt.ItemDataRole.UserRole + i for i in (1, 2, 3))

THUMB = 52
PAD = 6
ROW_HEIGHT = THUMB + 2 * PAD
GRID_THUMB = 150
LINE = 16
CELL_H = GRID_THUMB + 3 * LINE + PAD
TEXT_MIN = 300  # width kept for the texts beside the collapsed strip
REVIEW_SIZE = QSize(76, 24)
REFUSED_MS = 1500
PIXMAP_CACHE_SIZE = 600
_YES = QMessageBox.StandardButton.Yes
_NO = QMessageBox.StandardButton.No


class _BurstsModel(QAbstractListModel):
    def __init__(self, view: BurstsView) -> None:
        super().__init__(view)
        self._view = view
        self.rows: list[Burst] = []
        self._row_of: dict[ShotKey, int] = {}

    def set_rows(self, rows: list[Burst]) -> None:
        self.beginResetModel()
        self.rows = rows
        self._row_of = {b.key: i for i, b in enumerate(rows)}
        self.endResetModel()

    def row_of(self, key: ShotKey) -> int | None:
        return self._row_of.get(key)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        burst = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{len(burst.shots)} shots · {burst.span} s"
        if role == KEY_ROLE:
            return burst.key
        if role == BURST_ROLE:
            return burst
        if role == STATE_ROLE:
            return self._view.state_text(burst)
        return None


class _List(QListView):
    def __init__(self, view: BurstsView) -> None:
        super().__init__()
        self._view = view

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self.doItemsLayout()  # the expanded row's height depends on the width

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            burst = self.currentIndex().data(BURST_ROLE)
            if burst is not None:
                self._view.review_requested.emit(burst.key, burst.shots[0].key)
                return
        super().keyPressEvent(event)


class BurstsDelegate(QStyledItemDelegate):
    def __init__(self, view: BurstsView) -> None:
        super().__init__(view.list)
        self._view = view
        self._swallow = False  # the release that ends a double-click
        self._last: tuple[ShotKey, bool] | None = None  # last shot click, toggled

    # -- geometry ------------------------------------------------------------
    @staticmethod
    def columns(width: int) -> int:
        return max(1, (width - PAD) // (GRID_THUMB + PAD))

    @staticmethod
    def strip_count(width: int, n: int) -> int:
        """How many thumbnails fit beside the texts of a collapsed row."""
        return max(1, min(n, (width - 2 * PAD - TEXT_MIN) // (THUMB + 4)))

    def grid_height(self, burst: Burst, width: int) -> int:
        rows = -(-len(burst.shots) // self.columns(width))
        return rows * CELL_H + PAD

    def cell_rect(self, option_rect: QRect, i: int) -> QRect:
        cols = self.columns(option_rect.width())
        row, col = divmod(i, cols)
        return QRect(
            option_rect.left() + PAD + col * (GRID_THUMB + PAD),
            option_rect.top() + ROW_HEIGHT + row * CELL_H,
            GRID_THUMB,
            CELL_H - PAD,
        )

    def shot_at(self, option_rect: QRect, burst: Burst, pos: QPoint) -> ShotKey | None:
        for i, shot in enumerate(burst.shots):
            if self.cell_rect(option_rect, i).contains(pos):
                return shot.key
        return None

    def review_rect(self, option_rect: QRect) -> QRect:
        return QRect(
            QPoint(
                option_rect.right() - PAD - REVIEW_SIZE.width(),
                option_rect.top() + ROW_HEIGHT // 2,
            ),
            REVIEW_SIZE,
        )

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt API)
        width = self._view.list.viewport().width() or option.rect.width()
        burst: Burst = index.data(BURST_ROLE)
        if burst is not None and burst.key == self._view.expanded():
            return QSize(width, ROW_HEIGHT + self.grid_height(burst, width))
        return QSize(width, ROW_HEIGHT)

    # -- painting ------------------------------------------------------------
    def _draw_thumb(self, painter, ref, box: QRect) -> None:
        pixmap = self._view._thumb(ref)
        if pixmap is None:
            painter.fillRect(box, QColor(*colors.TILE_BACK))
            return
        size = pixmap.size()
        if size.width() > box.width() or size.height() > box.height():
            size = size.scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRect(0, 0, size.width(), size.height())
        target.moveCenter(box.center())
        painter.drawPixmap(target, pixmap)

    def paint(self, painter: QPainter, option, index) -> None:
        burst: Burst = index.data(BURST_ROLE)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawPrimitive(
                QStyle.PrimitiveElement.PE_PanelItemViewItem,
                option,
                painter,
                option.widget,
            )
            r = option.rect
            palette = option.palette
            x, top = r.left() + PAD, r.top() + PAD
            for shot in burst.shots[: self.strip_count(r.width(), len(burst.shots))]:
                self._draw_thumb(painter, shot.files[0], QRect(x, top, THUMB, THUMB))
                x += THUMB + 4
            text_left = x + PAD
            width = r.right() - PAD - text_left
            line_h = ROW_HEIGHT // 2 - PAD
            first = QRect(text_left, top, width, line_h)
            second = QRect(text_left, top + line_h, width - REVIEW_SIZE.width(), line_h)
            flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            bold = QFont(option.font)
            bold.setBold(True)
            painter.setFont(bold)
            painter.setPen(palette.text().color())
            painter.drawText(first, flags, index.data())
            painter.setFont(option.font)
            painter.setPen(palette.placeholderText().color())
            where = burst.folder or self._view.root_name()
            painter.drawText(
                second, flags, f"{where} · {format_bytes(burst.size_bytes)}"
            )
            keep, rest = self._view.state_parts(burst)
            if rest:
                painter.setPen(palette.text().color())
                painter.drawText(first, right, rest)
                used = painter.fontMetrics().horizontalAdvance(rest)
                first.setRight(first.right() - used)
                painter.setPen(QColor(colors.KEEP))
            else:
                painter.setPen(palette.placeholderText().color())
            painter.drawText(first, right, keep)
            if burst.key == self._view.expanded():
                self._paint_review(painter, r, palette)
            if burst.key == self._view.expanded():
                for i, shot in enumerate(burst.shots):
                    self._paint_cell(painter, option, burst, i, shot)
        finally:
            painter.restore()

    def _paint_review(self, painter, r: QRect, palette) -> None:
        box = self.review_rect(r)
        painter.setPen(palette.mid().color())
        painter.setBrush(palette.button())
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(palette.buttonText().color())
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, REVIEW)

    def _paint_cell(self, painter, option, burst: Burst, i: int, shot: Shot) -> None:
        palette = option.palette
        cell = self.cell_rect(option.rect, i)
        marked = shot.key in self._view.marks()
        painter.save()
        painter.setOpacity(0.35 if marked else 1.0)
        box = QRect(cell.left(), cell.top(), GRID_THUMB, GRID_THUMB)
        self._draw_thumb(painter, shot.files[0], box)
        painter.restore()
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        line = QRect(cell.left(), cell.top() + GRID_THUMB, GRID_THUMB, LINE)
        refused = self._view.refused() == shot.key
        tag = KEEP_ONE if refused else (REMOVE_TAG if marked else KEEP_TAG)
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QColor(colors.ERROR if marked or refused else colors.KEEP))
        painter.drawText(line, flags, tag)
        painter.setFont(option.font)
        painter.setPen(palette.text().color())
        offset = shot.taken_ts - burst.shots[0].taken_ts
        name = painter.fontMetrics().elidedText(
            f"{shot.stem} · +{offset} s", Qt.TextElideMode.ElideRight, GRID_THUMB
        )
        line.translate(0, LINE)
        painter.drawText(line, flags, name)
        if len(shot.files) > 1:
            painter.setPen(palette.placeholderText().color())
            line.translate(0, LINE)
            painter.drawText(line, flags, shot.formats)

    # -- input ---------------------------------------------------------------
    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        kind = event.type()
        if kind not in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ):
            return False
        burst: Burst | None = index.data(BURST_ROLE)
        if burst is None or event.button() != Qt.MouseButton.LeftButton:
            return False
        pos = event.position().toPoint()
        expanded = burst.key == self._view.expanded()
        shot = self.shot_at(option.rect, burst, pos) if expanded else None
        on_review = expanded and self.review_rect(option.rect).contains(pos)
        if kind == QEvent.Type.MouseButtonPress:
            self._swallow = False
            return on_review or shot is not None  # these do not select the row
        if kind == QEvent.Type.MouseButtonDblClick:
            self._swallow = True
            if shot is not None:
                if self._last is not None and self._last[0] == shot:
                    self._view.undo_click(shot, self._last[1])
                self._last = None
                self._view.review_requested.emit(burst.key, shot)
            return True
        if self._swallow:
            self._swallow = False
            return True
        if on_review:
            self._view.review_requested.emit(burst.key, burst.shots[0].key)
        elif shot is not None:
            self._last = (shot, self._view.click_shot(shot))
        elif pos.y() < option.rect.top() + ROW_HEIGHT:
            self._view.set_expanded(None if expanded else burst.key)
        return True


class BurstsView(QWidget):
    move_requested = Signal(list)  # list[FileRef] confirmed by the user
    review_requested = Signal(object, object)  # burst key, shot key
    scope_changed = Signal(object)  # str | None
    gap_changed = Signal(int)  # after it is applied

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._shots: dict[ShotKey, Shot] = {}
        self._bursts: list[Burst] = []
        self._by_shot: dict[ShotKey, Burst] = {}
        self._marks: set[ShotKey] = set()
        self._gap = DEFAULT_GAP
        self._scope: str | None = None
        self._dropped = 0
        self._refused: ShotKey | None = None
        self._refusals = 0
        self._expanded: ShotKey | None = None
        self._root = Path()
        self._pixmaps: OrderedDict[tuple[int, int | None], QPixmap] = OrderedDict()
        self._missing: set[tuple[int, int | None]] = set()
        self._requested: dict[int, tuple[int, int | None]] = {}

        title = QLabel(TITLE)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        self.gap_box = QSpinBox()
        self.gap_box.setRange(MIN_GAP, MAX_GAP)
        self.gap_box.setSuffix(" s")
        self.gap_box.setValue(self._gap)
        self.gap_box.valueChanged.connect(self.set_gap)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(QLabel(GAP_LABEL))
        head.addWidget(self.gap_box)

        self.scope_label = QLabel("")
        self.show_all = QPushButton("")
        self.show_all.setFlat(True)
        self.show_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_all.clicked.connect(lambda: self.set_scope(None))
        scope = QHBoxLayout()
        scope.addWidget(self.scope_label)
        scope.addStretch(1)
        scope.addWidget(self.show_all)

        self.model = _BurstsModel(self)
        self.list = _List(self)
        self.list.setModel(self.model)
        self.list.setItemDelegate(BurstsDelegate(self))
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
        self.clear_button.clicked.connect(self.clear_marks)
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

        thumbs = getattr(ctx, "thumbs", None)
        if thumbs is not None:
            thumbs.ready.connect(self._on_thumb_ready)
        self._refresh()

    # ── API ─────────────────────────────────────────────────────────────────
    def set_shots(self, shots: list[Shot], root: Path) -> None:
        """Show these shots; marks survive only on unchanged shots in bursts."""
        root = Path(root)
        if root != self._root:
            self._pixmaps.clear()
            self._missing.clear()
            self._requested.clear()
        self._root = root
        old = self._shots
        self._shots = {s.key: s for s in shots}
        self._regroup()
        before = self._marks
        self._marks, _ = reconcile_marks(old, before, self._bursts)
        # A shot gone from the catalog was moved away, not cleared: not counted.
        self._dropped = sum(1 for k in before - self._marks if k in self._shots)
        if self._expanded not in {b.key for b in self._bursts}:
            self._expanded = None
        self._refresh()

    def root_name(self) -> str:
        return self._root.name or self._root.as_posix()

    def scope(self) -> str | None:
        return self._scope

    def set_scope(self, folder: str | None) -> None:
        folder = folder or None
        changed = folder != self._scope
        self._scope = folder
        self._dropped = 0
        self._refresh()
        if changed:
            self.scope_changed.emit(folder)

    def gap(self) -> int:
        return self._gap

    def set_gap(self, seconds: int) -> bool:
        """Regroup at ``seconds``; False when the user declined to drop marks."""
        seconds = max(MIN_GAP, min(MAX_GAP, int(seconds)))
        applied = seconds != self._gap
        if applied:
            if self._marks:
                text = GAP_CONFIRM.format(n=len(self._marks))
                if QMessageBox.question(self, GAP_TITLE, text, _YES | _NO, _NO) != _YES:
                    self._show_gap()
                    return False
            self._gap = seconds
            self._marks.clear()
            self._dropped = 0
            self._regroup()
            self._expanded = None
            self._refresh()
        self._show_gap()
        if applied:
            self.gap_changed.emit(seconds)
        return True

    def bursts_in_scope(self) -> list[Burst]:
        return [b for b in self._bursts if in_scope(b, self._scope)]

    def toggle(self, shot: ShotKey) -> bool:
        """Mark or unmark a shot; False when it is the last one kept."""
        burst = self._by_shot.get(shot)
        if burst is None or not can_mark(burst, self._marks, shot):
            return False
        self._marks ^= {shot}
        if self._dropped:
            self._dropped = 0
            self._update_scope_label()
        row = self.model.row_of(burst.key)
        if row is not None:
            index = self.model.index(row)
            self.model.dataChanged.emit(index, index)
        self._update_footer()
        return True

    def marks(self) -> set[ShotKey]:
        return set(self._marks)

    def marked_in(self, burst: Burst) -> int:
        return sum(1 for s in burst.shots if s.key in self._marks)

    def clear_marks(self) -> None:
        self._marks.clear()
        if self._dropped:
            self._dropped = 0
            self._update_scope_label()
        self.list.viewport().update()
        self._update_footer()

    def removals(self) -> list[FileRef]:
        return burst_removals(self._shots[k] for k in self._marks)

    def confirm_leave(self) -> bool:
        if not self._marks:
            return True
        text = LEAVE.format(n=len(self._marks))
        if QMessageBox.question(self, LEAVE_TITLE, text, _YES | _NO, _NO) != _YES:
            return False
        self.clear_marks()
        return True

    def expanded(self) -> ShotKey | None:
        return self._expanded

    def set_expanded(self, burst: ShotKey | None) -> None:
        self._expanded = burst
        self.list.doItemsLayout()
        row = None if burst is None else self.model.row_of(burst)
        if row is not None:
            self.list.scrollTo(self.model.index(row))
        self.list.viewport().update()

    def refused(self) -> ShotKey | None:
        """The shot whose tag reads "keep at least one" for a moment."""
        return self._refused

    def click_shot(self, shot: ShotKey) -> bool:
        """Toggle a shot; False (with a brief notice) when it was refused."""
        if self.toggle(shot):
            return True
        self._refused = shot
        self._refusals += 1
        QTimer.singleShot(REFUSED_MS, partial(self._clear_refused, self._refusals))
        self.list.viewport().update()
        return False

    def undo_click(self, shot: ShotKey, toggled: bool) -> None:
        """Take back a click that turned out to be half of a double-click."""
        if toggled:
            self.toggle(shot)
        elif self._refused == shot:
            self._refusals += 1  # retire the pending timer
            self._refused = None
            self.list.viewport().update()

    def _clear_refused(self, token: int) -> None:
        if token == self._refusals:
            self._refused = None
            self.list.viewport().update()

    def state_parts(self, burst: Burst) -> tuple[str, str]:
        """The state text split into its keeping part and the rest."""
        n, m = len(burst.shots), self.marked_in(burst)
        if not m:
            return NOT_DECIDED, ""
        size = format_bytes(
            sum(s.size_bytes for s in burst.shots if s.key in self._marks)
        )
        return KEEPING.format(k=n - m, n=n), f" · {REMOVING.format(n=m, size=size)}"

    def state_text(self, burst: Burst) -> str:
        return "".join(self.state_parts(burst))

    # ── internals ───────────────────────────────────────────────────────────
    def _show_gap(self) -> None:
        self.gap_box.blockSignals(True)
        self.gap_box.setValue(self._gap)
        self.gap_box.blockSignals(False)

    def _regroup(self) -> None:
        self._bursts = group_bursts(list(self._shots.values()), self._gap)
        self._by_shot = {s.key: b for b in self._bursts for s in b.shots}

    def _refresh(self) -> None:
        shown = self.bursts_in_scope()
        self.model.set_rows(shown)
        self._update_scope_label()
        self.show_all.setText(SHOW_ALL.format(n=len(self._bursts)))
        self.show_all.setVisible(bool(self._scope))
        if not self._bursts:
            self.empty_label.setText(EMPTY_ALL)
        elif not shown:
            self.empty_label.setText(EMPTY_FOLDER)
        self._stack.setCurrentWidget(self.list if shown else self.empty_label)
        self._update_footer()

    def _update_scope_label(self) -> None:
        shown = self.bursts_in_scope()
        args = {
            "n": len(shown),
            "photos": sum(len(b.shots) for b in shown),
            "size": format_bytes(sum(b.size_bytes for b in shown)),
        }
        if self._scope:
            label = SCOPE_ONE.format(folder=self._scope, **args)
        else:
            label = SCOPE_ALL.format(**args)
        if self._dropped:
            label += " · " + DROPPED.format(n=self._dropped)
        self.scope_label.setText(label)

    def _update_footer(self) -> None:
        removals = self.removals()
        size = format_bytes(sum(f.size_bytes for f in removals))
        text = FOOTER.format(n=len(self._marks), size=size)
        marked = [self._shots[k] for k in self._marks]
        if any(not in_scope(s, self._scope) for s in marked):
            text += FOOTER_FOLDERS.format(k=len({s.folder.casefold() for s in marked}))
        self.footer_label.setText(text)
        n = len(removals)
        self.move_button.setText(MOVE.format(n=n) if n else MOVE_NONE)
        self.move_button.setEnabled(bool(n))
        self.clear_button.setEnabled(bool(self._marks))

    def _on_move(self) -> None:
        removals = self.removals()
        if not removals:
            return
        size = format_bytes(sum(f.size_bytes for f in removals))
        text = CONFIRM.format(n=len(removals), size=size)
        if QMessageBox.question(self, MOVE_TITLE, text, _YES | _NO, _NO) == _YES:
            self.move_requested.emit(removals)

    def forget_thumb_requests(self) -> None:
        """Ask again for thumbnails still outstanding."""
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
                GRID_THUMB,
                GRID_THUMB,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            while len(self._pixmaps) > PIXMAP_CACHE_SIZE:
                self._pixmaps.popitem(last=False)
        self.list.viewport().update()
