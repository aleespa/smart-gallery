"""The viewer's filmstrip: one row of thumbnails of the photos the viewer was
opened with, the current one outlined in the accent. Thumbnails come from the
shared ``ctx.thumbs`` loader, for the visible ones only."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPoint,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate

from smart_gallery import colors
from smart_gallery.gui import theme
from smart_gallery.gui.library.model import LibraryRow

STRIP_HEIGHT = 72
TILE = QSize(78, 56)  # one cell: a thumbnail fitted inside, with a gap
DIMMED = 0.55  # opacity of the tiles that are not the current one
VISIBLE_DEBOUNCE_MS = 30


class _StripModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[LibraryRow] = []
        self.pixmaps: dict[int, QPixmap] = {}  # media id -> thumbnail
        self.current = -1

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.ToolTipRole:
            return None
        return self.rows[index.row()].relpath

    def set_rows(self, rows: list[LibraryRow]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.pixmaps.clear()
        self.endResetModel()

    def changed(self, row: int) -> None:
        if 0 <= row < len(self.rows):
            index = self.index(row)
            self.dataChanged.emit(index, index)


class _TileDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt API)
        return TILE

    def paint(self, painter: QPainter, option, index) -> None:
        model: _StripModel = index.model()
        row = model.rows[index.row()]
        cell = QRectF(option.rect).adjusted(3, 3, -3, -3)
        current = index.row() == model.current
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(1.0 if current else DIMMED)
        pixmap = model.pixmaps.get(row.id)
        if pixmap is None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(colors.TILE_BACK))
            painter.drawRoundedRect(cell, 3, 3)
            shown = cell
        else:
            size = pixmap.size().scaled(
                cell.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio
            )
            shown = QRectF(0, 0, size.width(), size.height())
            shown.moveCenter(cell.center())
            painter.drawPixmap(shown.toRect(), pixmap)
        if current:
            painter.setOpacity(1.0)
            painter.setPen(QPen(theme.qcolor(theme.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(shown.adjusted(-2, -2, 2, 2), 4, 4)
        painter.restore()


class Filmstrip(QListView):
    picked = Signal(int)  # row index clicked

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.root: Path | None = None
        self._asked: set[int] = set()
        self._model = _StripModel(self)
        self.setModel(self._model)
        self.setItemDelegate(_TileDelegate(self))
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setUniformItemSizes(True)
        self.setFixedHeight(STRIP_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # arrows page photos
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setStyleSheet(
            f"QListView {{ background: {colors.VIEWER_STAGE}; border: none;"
            " border-top: 1px solid palette(mid); }"
        )
        self._timer = QTimer(self, singleShot=True, interval=VISIBLE_DEBOUNCE_MS)
        self._timer.timeout.connect(self._request_visible)
        self.horizontalScrollBar().valueChanged.connect(self._schedule)
        self.clicked.connect(lambda index: self.picked.emit(index.row()))
        thumbs = getattr(ctx, "thumbs", None)
        if thumbs is not None:
            thumbs.ready.connect(self._on_thumb)

    def set_rows(self, root: Path, rows: list[LibraryRow]) -> None:
        self.root = Path(root)
        self._asked.clear()
        self._model.set_rows(rows)
        self._schedule()

    def set_current(self, index: int) -> None:
        old, self._model.current = self._model.current, index
        self._model.changed(old)
        self._model.changed(index)
        if 0 <= index < self._model.rowCount():
            self.scrollTo(
                self._model.index(index), QAbstractItemView.ScrollHint.PositionAtCenter
            )
        self._schedule()

    def current_row(self) -> int:
        return self._model.current

    def has_thumbnail(self, media_id: int) -> bool:
        return media_id in self._model.pixmaps

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt API)
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - event.angleDelta().y())
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._schedule()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        self._schedule()

    def _schedule(self, *_args) -> None:
        self._timer.start()

    def _request_visible(self) -> None:
        thumbs = getattr(self.ctx, "thumbs", None)
        if thumbs is None or self.root is None or not self.isVisible():
            return
        area = self.viewport().rect()
        first = self.indexAt(QPoint(1, area.height() // 2))
        start = first.row() if first.isValid() else 0
        for i in range(start, self._model.rowCount()):
            rect = self.visualRect(self._model.index(i))
            if rect.left() > area.right():
                break
            row = self._model.rows[i]
            if row.id in self._model.pixmaps or row.id in self._asked or row.offloaded:
                continue
            self._asked.add(row.id)
            thumbs.request(
                self.root, row.id, row.mtime_ns, row.relpath, row.ext, row.media_type
            )

    def _on_thumb(self, root: str, media_id: int, path: str) -> None:
        if self.root is None or root != str(self.root) or not path:
            return
        for i, row in enumerate(self._model.rows):
            if row.id == media_id:
                pixmap = QPixmap(path)
                if pixmap.isNull():
                    return
                self._model.pixmaps[media_id] = pixmap.scaled(
                    TILE * 2,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._model.changed(i)
                return
