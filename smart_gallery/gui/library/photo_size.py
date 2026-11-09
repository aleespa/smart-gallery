"""The photo size control shared by the Library and the person page.

The size is a row height in pixels. It is remembered in ``library.json`` (next
to the filter panel's own key) and shared between pages through
``AppContext.photo_size_changed``.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSlider, QWidget

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.library.justify import (
    ROW_HEIGHT_DEFAULT,
    ROW_HEIGHT_MAX,
    ROW_HEIGHT_MIN,
    ROW_HEIGHT_STEP,
)
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.state_store import load_json, save_json

KEY = "row_height"
TOOLTIP = "Photo size (Ctrl + scroll wheel)"
STATE_FILE = "library.json"
SAVE_DELAY_MS = 300  # one write after a drag, not one per step


def _clamp(px: int) -> int:
    return min(ROW_HEIGHT_MAX, max(ROW_HEIGHT_MIN, int(px)))


def load_row_height() -> int:
    value = load_json(app_data_dir() / STATE_FILE).get(KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ROW_HEIGHT_DEFAULT
    return _clamp(value)


def save_row_height(px: int) -> None:
    path = app_data_dir() / STATE_FILE
    state = load_json(path)  # merge: other keys belong to other owners
    state[KEY] = _clamp(px)
    save_json(path, state)


class _Glyph(QWidget):
    """A small or large square at an end of the slider."""

    def __init__(self, edge: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._edge = edge
        self.setFixedSize(QSize(16, 16))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.palette().color(self.foregroundRole()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        left = (self.width() - self._edge) / 2
        top = (self.height() - self._edge) / 2
        painter.drawRoundedRect(QRectF(left, top, self._edge, self._edge), 1.5, 1.5)


class PhotoSizeSlider(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._grid = None
        self._current = load_row_height()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(ROW_HEIGHT_MIN, ROW_HEIGHT_MAX)
        self.slider.setPageStep(ROW_HEIGHT_STEP)
        self.slider.setSingleStep(ROW_HEIGHT_STEP // 2)
        self.slider.setFixedWidth(120)
        self.slider.setValue(self._current)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_Glyph(5))
        layout.addWidget(self.slider)
        layout.addWidget(_Glyph(12))
        self.setToolTip(TOOLTIP)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.flush)
        # A change in the last SAVE_DELAY_MS before quitting is still saved.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._flush_pending)
        self.slider.valueChanged.connect(self._on_slider)
        ctx.photo_size_changed.connect(self._on_context)

    def bind(self, grid) -> None:
        """Drive ``grid`` and follow it (its Ctrl + wheel)."""
        self._grid = grid
        grid.set_row_height(self._current)
        grid.row_height_changed.connect(self._commit)

    def _show(self, px: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(px)
        self.slider.blockSignals(False)

    def _on_slider(self, px: int) -> None:
        if self._grid is not None:
            self._grid.set_row_height(px)  # reports back through _commit
        self._commit(px)

    def _commit(self, px: int) -> None:
        """A change made here or on the grid: show, save and tell the others."""
        px = _clamp(px)
        if px == self._current:
            return
        self._current = px
        self._show(px)
        self._save_timer.start()
        self.ctx.photo_size_changed.emit(px)

    def flush(self) -> None:
        """Write the current size now."""
        self._save_timer.stop()
        save_row_height(self._current)

    def _flush_pending(self) -> None:
        """Write a size still waiting out its delay (nothing otherwise)."""
        if self._save_timer.isActive():
            self.flush()

    def _on_context(self, px: int) -> None:
        """A change made elsewhere: follow it without saving or re-emitting."""
        px = _clamp(px)
        if px == self._current:
            return  # our own change coming back
        self._current = px
        self._show(px)
        self._save_timer.stop()  # the page that changed it saves
        if self._grid is not None:
            self._grid.set_row_height(px)
