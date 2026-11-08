"""A filter panel that hides behind a slim strip, remembering its state.

Shared by the Library and Stats. The panel's top row gets a « button; while the
panel is hidden the ``strip`` stands in for it and brings it back. Whether it is
shown persists under ``"filters_shown"`` in the page's JSON state file.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QToolButton, QWidget

from smart_gallery.gui.filters_panel import FilterPanel
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.state_store import load_json, save_json

logger = logging.getLogger(__name__)

HIDE_FILTERS_TEXT = "«"
HIDE_FILTERS_TIP = "Hide the filters"
SHOW_FILTERS_TIP = "Show the filters"
FILTERS_STRIP_TEXT = "Filters"
STRIP_WIDTH = 28
SHOWN_KEY = "filters_shown"


class CollapsedStrip(QAbstractButton):
    """A slim bar with its label running bottom to top; it stands in for a
    hidden panel and brings it back when clicked."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(STRIP_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(STRIP_WIDTH, self.fontMetrics().horizontalAdvance(self.text()))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        back = palette.color(QPalette.ColorRole.Button)
        if self.isDown():
            back = back.darker(115)
        elif self.underMouse():
            back = back.lighter(125)
        painter.setPen(palette.color(QPalette.ColorRole.Mid))
        painter.setBrush(back)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4
        )
        painter.setPen(palette.color(QPalette.ColorRole.ButtonText))
        painter.translate(0, self.height())
        painter.rotate(-90)
        # Rotated, x runs up the bar and y across it; the label starts at the top.
        width = self.fontMetrics().horizontalAdvance(self.text())
        top = QRectF(self.height() - width - 12, 0, width, self.width())
        painter.drawText(top, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class CollapsibleFilters:
    """Wires ``panel`` to a hide button and a ``strip``; the caller adds both
    to its splitter."""

    def __init__(self, panel: FilterPanel, state_key: str):
        self.panel = panel
        self.state_path = app_data_dir() / state_key
        self.hide_btn = QToolButton()
        self.hide_btn.setText(HIDE_FILTERS_TEXT)
        self.hide_btn.setToolTip(HIDE_FILTERS_TIP)
        self.hide_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        panel.top_row.addWidget(self.hide_btn)
        self.strip = CollapsedStrip(FILTERS_STRIP_TEXT)
        self.strip.setToolTip(SHOW_FILTERS_TIP)
        self.hide_btn.clicked.connect(lambda: self.set_shown(False))
        self.strip.clicked.connect(lambda: self.set_shown(True))
        self.shown = load_json(self.state_path).get(SHOWN_KEY) is not False
        self._apply()

    def set_shown(self, shown: bool) -> None:
        if shown == self.shown:
            return
        self.shown = shown
        self._apply()
        state = load_json(self.state_path)  # merge: the page keeps its own keys
        state[SHOWN_KEY] = shown
        try:
            save_json(self.state_path, state)
        except OSError as exc:
            logger.warning("Could not save %s: %s", self.state_path, exc)

    def _apply(self) -> None:
        self.panel.setVisible(self.shown)
        self.strip.setVisible(not self.shown)
