"""The Storage page's RAW -> JPEG grid: CR3 photos with no JPEG beside them.

The user picks photos here; the footer adds the picks up, and one confirmed
button reports the CR3s to convert. Nothing here touches the disk or the
catalog.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path, PurePosixPath

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from smart_gallery import colors
from smart_gallery.gui.library.model import LibraryRow
from smart_gallery.gui.library.viewer import MediaViewer
from smart_gallery.recycle_bin import recycle_bin_capacity
from smart_gallery.storage.pairs import FileRef
from smart_gallery.storage.raw_jpeg import (
    RawCandidate,
    estimate_jpeg_bytes,
    in_scope,
    reconcile_picks,
    replacement_entries,
)
from smart_gallery.storage.tree import format_bytes

TITLE = "RAW → JPEG"
SCOPE_ONE = "In {folder} · {n:,} CR3s without a JPEG · {size}"
SCOPE_ALL = "All folders · {n:,} CR3s without a JPEG · {size}"
SHOW_ALL = "Show all {n:,} CR3s"
DROPPED = "{n:,} picks cleared because their files changed"
EMPTY_ONE = "No CR3s without a JPEG in this folder"
EMPTY_ALL = "No CR3s without a JPEG in this gallery"
FOOTER = "{n:,} picked · {raw} of CR3 → about {jpeg} of JPEG · frees ~{freed}"
FOOTER_FOLDERS = " (in {k} folders)"
CONVERT = "Convert {n:,} and recycle the CR3s"
CONVERT_NONE = "Convert and recycle"
CLEAR = "Clear"
CONFIRM_TITLE = "Convert to JPEG"
CONFIRM = (
    "Convert {n:,} photos to JPEG and move their CR3s ({size}) to the Recycle Bin?"
)
BIN_LIMIT = "\n\n{drive}'s Recycle Bin holds {limit}."
BIN_WARNING = (
    "\n\nThat is more than {drive}'s Recycle Bin holds ({limit}); "
    "Windows will empty the oldest items to make room."
)
EDITED = "edited"
PICKED = "Picked"
NOT_PICKED = "Not picked"
KEY_ROLE, PICKED_ROLE, EDITED_ROLE, CAND_ROLE = (
    Qt.ItemDataRole.UserRole + i for i in (1, 2, 3, 4)
)

THUMB = 118
PAD = 6
LINE = 16
CELL = QSize(THUMB + 2 * PAD, THUMB + LINE + 3 * PAD)
BOX = 18  # the pick box
PIXMAP_CACHE_SIZE = 600
_YES = QMessageBox.StandardButton.Yes
_NO = QMessageBox.StandardButton.No


class _RawModel(QAbstractListModel):
    def __init__(self, view: RawView) -> None:
        super().__init__(view)
        self._view = view
        self.rows: list[RawCandidate] = []
        self._row_of: dict[str, int] = {}

    def set_rows(self, rows: list[RawCandidate]) -> None:
        self.beginResetModel()
        self.rows = rows
        self._row_of = {c.key: i for i, c in enumerate(rows)}
        self.endResetModel()

    def row_of(self, key: str) -> int | None:
        return self._row_of.get(key)

    def changed(self) -> None:
        if self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1))

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        c = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{c.stem} · {format_bytes(c.raw.size_bytes)}"
        if role == KEY_ROLE:
            return c.key
        if role == PICKED_ROLE:
            return c.key in self._view.picks_ref()
        if role == EDITED_ROLE:
            return c.edited
        if role == CAND_ROLE:
            return c
        return None


class _Grid(QListView):
    def __init__(self, view: RawView) -> None:
        super().__init__()
        self._view = view
        self._undo: str | None = None  # the key a press just toggled

    def _key_at(self, pos) -> str | None:
        index = self.indexAt(pos)
        return index.data(KEY_ROLE) if index.isValid() else None

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        key = self._key_at(event.position().toPoint())
        self._undo = None
        if key is None or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setCurrentIndex(self.indexAt(event.position().toPoint()))
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._view.pick_range(key)
        else:
            self._view.toggle(key)
            self._undo = key
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt API)
        key = self._key_at(event.position().toPoint())
        if key is None or event.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(event)
        if self._undo == key:
            self._view.toggle(key)  # the first half of this double-click
        self._undo = None
        self._view.open_viewer(key)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if event.key() == Qt.Key.Key_A and ctrl:
            self._view.pick_all_in_scope()
        elif event.key() == Qt.Key.Key_Escape:
            self._view.clear_picks_in_scope()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            key = self.currentIndex().data(KEY_ROLE)
            if key is not None:
                self._view.open_viewer(key)
        elif event.key() == Qt.Key.Key_Space:
            key = self.currentIndex().data(KEY_ROLE)
            if key is not None:
                self._view.toggle(key)
        else:
            super().keyPressEvent(event)


class RawDelegate(QStyledItemDelegate):
    def __init__(self, view: RawView) -> None:
        super().__init__(view.list)
        self._view = view

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt API)
        return CELL

    def paint(self, painter: QPainter, option, index) -> None:
        c: RawCandidate = index.data(CAND_ROLE)
        picked = bool(index.data(PICKED_ROLE))
        palette = option.palette
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = option.rect
            if option.state & QStyle.StateFlag.State_MouseOver:
                painter.fillRect(r, palette.alternateBase())
            box = QRect(r.left() + PAD, r.top() + PAD, THUMB, THUMB)
            painter.save()
            painter.setOpacity(0.55 if picked else 1.0)
            self._draw_thumb(painter, c.raw, box)
            painter.restore()
            if picked:
                painter.setPen(palette.highlight().color())
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
            self._paint_pick_box(painter, box, picked, palette)
            if c.edited:
                self._paint_badge(painter, box, option.font)
            painter.setFont(option.font)
            painter.setPen(palette.text().color())
            line = QRect(box.left(), box.bottom() + PAD, THUMB, LINE)
            text = painter.fontMetrics().elidedText(
                index.data(), Qt.TextElideMode.ElideRight, THUMB
            )
            painter.drawText(
                line, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text
            )
        finally:
            painter.restore()

    def _draw_thumb(self, painter, ref: FileRef, box: QRect) -> None:
        pixmap = self._view.thumbnail(ref)
        if pixmap is None:
            painter.fillRect(box, QColor(*colors.TILE_BACK))
            return
        size = pixmap.size()
        if size.width() > box.width() or size.height() > box.height():
            size = size.scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRect(0, 0, size.width(), size.height())
        target.moveCenter(box.center())
        painter.drawPixmap(target, pixmap)

    @staticmethod
    def _paint_pick_box(painter, thumb: QRect, picked: bool, palette) -> None:
        box = QRect(thumb.left() + 4, thumb.top() + 4, BOX, BOX)
        painter.setPen(palette.highlight().color() if picked else palette.mid().color())
        painter.setBrush(palette.highlight() if picked else palette.base())
        painter.drawRoundedRect(box, 3, 3)
        if picked:
            painter.setPen(palette.highlightedText().color())
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "✓")

    @staticmethod
    def _paint_badge(painter, thumb: QRect, font) -> None:
        small = QFont(font)
        small.setPointSizeF(max(6.0, font.pointSizeF() - 1))
        painter.setFont(small)
        width = painter.fontMetrics().horizontalAdvance(EDITED) + 10
        badge = QRect(thumb.right() - width - 4, thumb.top() + 4, width, LINE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*colors.BADGE_BACK))
        painter.drawRoundedRect(badge, 3, 3)
        painter.setPen(QColor(*colors.BADGE_TEXT))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, EDITED)


class _PickViewer(MediaViewer):
    """The photo viewer with Space to pick or unpick the shown CR3."""

    def __init__(self, view: RawView, ctx, root, rows, keys, index) -> None:
        self._raw_view = view
        self._keys = keys
        super().__init__(ctx, root, rows, index, view)
        view.picks_changed.connect(self._retitle)
        self.finished.connect(lambda *_: view.picks_changed.disconnect(self._retitle))
        self.setFocus()  # not a button: Space would press it

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._raw_view.toggle(self._keys[self._index])
            return
        if event.key() == Qt.Key.Key_Left:
            self.show_previous()  # the base shortcut does not fire unfocused
            return
        if event.key() == Qt.Key.Key_Right:
            self.show_next()
            return
        super().keyPressEvent(event)

    def show_index(self, index: int) -> None:
        super().show_index(index)
        self._retitle()

    def _retitle(self) -> None:
        picked = self._keys[self._index] in self._raw_view.picks_ref()
        name = PurePosixPath(self.row().relpath).name
        self.setWindowTitle(f"{name} · {PICKED if picked else NOT_PICKED}")


class RawView(QWidget):
    convert_requested = Signal(list)  # list[FileRef] confirmed by the user
    scope_changed = Signal(object)  # str | None
    thumbs_changed = Signal()  # a thumbnail arrived
    picks_changed = Signal()

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._cands: dict[str, RawCandidate] = {}
        self._picks: set[str] = set()
        self._anchor: str | None = None
        self._scope: str | None = None
        self._dropped = 0
        self._root = Path()
        self.viewer: MediaViewer | None = None
        self._pixmaps: OrderedDict[tuple[int, int | None], QPixmap] = OrderedDict()
        self._missing: set[tuple[int, int | None]] = set()
        self._requested: dict[int, tuple[int, int | None]] = {}

        title = QLabel(TITLE)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)

        self.scope_label = QLabel("")
        self.show_all = QPushButton("")
        self.show_all.setFlat(True)
        self.show_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_all.clicked.connect(lambda: self.set_scope(None))
        scope = QHBoxLayout()
        scope.addWidget(self.scope_label)
        scope.addStretch(1)
        scope.addWidget(self.show_all)

        self.model = _RawModel(self)
        self.list = _Grid(self)
        self.list.setModel(self.model)
        self.list.setItemDelegate(RawDelegate(self))
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setGridSize(CELL)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.empty_label = QLabel("")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.list)
        self._stack.addWidget(self.empty_label)

        self.footer_label = QLabel("")
        self.clear_button = QPushButton(CLEAR)
        self.clear_button.clicked.connect(self.clear_picks)
        self.convert_button = QPushButton(CONVERT_NONE)
        self.convert_button.clicked.connect(self._on_convert)
        foot = QHBoxLayout()
        foot.addWidget(self.footer_label, 1)
        foot.addWidget(self.clear_button)
        foot.addWidget(self.convert_button)

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
    def set_candidates(self, cands: list[RawCandidate], root: Path) -> None:
        """Show these CR3s; picks survive only on unchanged candidates."""
        root = Path(root)
        if root != self._root:
            self._pixmaps.clear()
            self._missing.clear()
            self._requested.clear()
        self._root = root
        if self.viewer is not None:
            self.viewer.close()
            self.viewer = None
        before = self._cands
        self._cands = {c.key: c for c in cands}
        old = self._picks
        self._picks = reconcile_picks(cands, old, before)
        # A CR3 gone from the list was converted, not cleared: not counted.
        self._dropped = sum(1 for k in old - self._picks if k in self._cands)
        if self._anchor not in self._cands:
            self._anchor = None
        self._refresh()

    def root_name(self) -> str:
        return self._root.name or self._root.drive or self._root.as_posix()

    def scope(self) -> str | None:
        return self._scope

    def set_scope(self, folder: str | None) -> None:
        folder = folder or None
        changed = folder != self._scope
        self._scope = folder
        self._dropped = 0
        self._anchor = None
        self._refresh()
        if changed:
            self.scope_changed.emit(folder)

    def picks(self) -> set[str]:
        return set(self._picks)

    def picks_ref(self) -> set[str]:
        return self._picks

    def toggle(self, key: str) -> None:
        if key not in self._cands:
            return
        self._picks ^= {key}
        self._anchor = key
        self._after_change()

    def pick_range(self, key: str) -> None:
        """Pick ``key`` and everything between it and the last click."""
        if key not in self._cands:
            return
        keys = [c.key for c in self._scoped()]
        if self._anchor in keys and key in keys:
            a, b = sorted((keys.index(self._anchor), keys.index(key)))
            self._picks.update(keys[a : b + 1])
        else:
            self._picks.add(key)
        self._anchor = key
        self._after_change()

    def pick_all_in_scope(self) -> None:
        self._picks.update(c.key for c in self._scoped())
        self._after_change()

    def clear_picks(self) -> None:
        self._picks.clear()
        self._after_change()

    def clear_picks_in_scope(self) -> None:
        self._picks.difference_update(c.key for c in self._scoped())
        self._after_change()

    def open_viewer(self, key: str) -> MediaViewer:
        shown = self._scoped()
        keys = [c.key for c in shown]
        rows = [
            LibraryRow(
                c.raw.id,
                c.raw.relpath,
                c.raw.media_type,
                c.raw.ext,
                c.raw.mtime_ns,
                size_bytes=c.raw.size_bytes,
            )
            for c in shown
        ]
        self.viewer = _PickViewer(
            self, self.ctx, self._root, rows, keys, keys.index(key)
        )
        self.viewer.show()
        return self.viewer

    def forget_thumb_requests(self) -> None:
        """Ask again for thumbnails still outstanding."""
        self._requested.clear()
        self.list.viewport().update()

    def thumbnail(self, ref: FileRef) -> QPixmap | None:
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

    # ── internals ───────────────────────────────────────────────────────────
    def _scoped(self) -> list[RawCandidate]:
        return [c for c in self._cands.values() if in_scope(c, self._scope)]

    def _picked(self) -> list[RawCandidate]:
        return [self._cands[k] for k in self._picks if k in self._cands]

    def _after_change(self) -> None:
        if self._dropped:
            self._dropped = 0
            self._update_scope_label()
        self.model.changed()
        self.list.viewport().update()
        self._update_footer()
        self.picks_changed.emit()

    def _refresh(self) -> None:
        shown = self._scoped()
        self.model.set_rows(shown)
        self._update_scope_label()
        self.show_all.setText(SHOW_ALL.format(n=len(self._cands)))
        self.show_all.setVisible(bool(self._scope))
        if not self._cands:
            self.empty_label.setText(EMPTY_ALL)
        elif not shown:
            self.empty_label.setText(EMPTY_ONE)
        self._stack.setCurrentWidget(self.list if shown else self.empty_label)
        self._update_footer()

    def _update_scope_label(self) -> None:
        shown = self._scoped()
        args = {"n": len(shown), "size": format_bytes(sum(c.size_bytes for c in shown))}
        if self._scope:
            label = SCOPE_ONE.format(folder=self._scope, **args)
        else:
            label = SCOPE_ALL.format(**args)
        if self._dropped:
            label += " · " + DROPPED.format(n=self._dropped)
        self.scope_label.setText(label)

    def _update_footer(self) -> None:
        picked = self._picked()
        raw = sum(c.raw.size_bytes for c in picked)
        jpeg = estimate_jpeg_bytes(picked)
        text = FOOTER.format(
            n=len(picked),
            raw=format_bytes(raw),
            jpeg=format_bytes(jpeg),
            freed=format_bytes(max(0, raw - jpeg)),
        )
        if any(not in_scope(c, self._scope) for c in picked):
            text += FOOTER_FOLDERS.format(k=len({c.folder.casefold() for c in picked}))
        self.footer_label.setText(text)
        n = len(picked)
        self.convert_button.setText(CONVERT.format(n=n) if n else CONVERT_NONE)
        self.convert_button.setEnabled(bool(n))
        self.clear_button.setEnabled(bool(n))

    def _on_convert(self) -> None:
        picked = self._picked()
        if not picked:
            return
        size = sum(c.raw.size_bytes for c in picked)
        text = CONFIRM.format(n=len(picked), size=format_bytes(size))
        limit = recycle_bin_capacity(self._root)
        if limit is not None:  # None: no limit known
            note = BIN_WARNING if size > limit else BIN_LIMIT
            text += note.format(drive=self.root_name(), limit=format_bytes(limit))
        if QMessageBox.question(self, CONFIRM_TITLE, text, _YES | _NO, _NO) == _YES:
            self.convert_requested.emit(replacement_entries(picked))

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
        self.thumbs_changed.emit()
