"""Side panels that hide behind a slim strip, remembering their state.

The filter panel (Library and Stats) and the Library's folder tree use it. The
panel's top row gets a « button; while the panel is hidden the ``strip``
stands in for it and brings it back. Whether it is shown persists under the
panel's own key (``"filters_shown"``, ``"tree_shown"``) in the page's JSON
state file.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from smart_gallery.gui.filters_panel import FilterPanel
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.state_store import load_json, save_json

logger = logging.getLogger(__name__)

HIDE_TEXT = "«"
HIDE_FILTERS_TEXT = HIDE_TEXT
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


class CollapsiblePanel:
    """Wires ``panel`` to a « button at the end of ``top_row`` and a
    ``strip`` labelled ``strip_text``; the caller adds the panel and the strip
    to its splitter. ``state_file`` is the JSON file name under
    app_data_dir(); ``shown_key`` is this panel's key in it."""

    def __init__(
        self,
        panel: QWidget,
        top_row: QBoxLayout,
        state_file: str,
        *,
        shown_key: str,
        strip_text: str,
        hide_tip: str,
        show_tip: str,
    ):
        self.panel = panel
        self.state_path = app_data_dir() / state_file
        self.shown_key = shown_key
        self.hide_btn = QToolButton()
        self.hide_btn.setText(HIDE_TEXT)
        self.hide_btn.setToolTip(hide_tip)
        self.hide_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        top_row.addWidget(self.hide_btn)
        self.strip = CollapsedStrip(strip_text)
        self.strip.setToolTip(show_tip)
        self.hide_btn.clicked.connect(lambda: self.set_shown(False))
        self.strip.clicked.connect(lambda: self.set_shown(True))
        self.shown = load_json(self.state_path).get(shown_key) is not False
        self._apply()

    def set_shown(self, shown: bool) -> None:
        if shown == self.shown:
            return
        self.shown = shown
        self._apply()
        state = load_json(self.state_path)  # merge: the page keeps its own keys
        state[self.shown_key] = shown
        try:
            save_json(self.state_path, state)
        except OSError as exc:
            logger.warning("Could not save %s: %s", self.state_path, exc)

    def _apply(self) -> None:
        if self.shown:
            if not self.panel.isWindow():
                self.panel.setVisible(True)
        else:
            self.panel.setVisible(False)
        self.strip.setVisible(not self.shown)


class CollapsibleFilters(CollapsiblePanel):
    """The filter panel's « button (in its top row) and "Filters" strip."""

    def __init__(self, panel: FilterPanel, state_file: str):
        super().__init__(
            panel,
            panel.top_row,
            state_file,
            shown_key=SHOWN_KEY,
            strip_text=FILTERS_STRIP_TEXT,
            hide_tip=HIDE_FILTERS_TIP,
            show_tip=SHOW_FILTERS_TIP,
        )
