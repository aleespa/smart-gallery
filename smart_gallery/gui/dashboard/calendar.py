"""The Stats page's photo calendar: photos per day of one year, GitHub style.

A heading ("1,284 photos in 2026") and a year picker above a grid of one
square per day. The grid is painted, not a Qt chart (Qt Charts has no
heatmap). Its colours come from the palette and ``colors.CALENDAR_SHADES`` at
paint time, so a light/dark switch needs no re-theming. When the panel is too
narrow for the smallest squares, the grid scrolls sideways.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from smart_gallery import colors
from smart_gallery.dashboard import data
from smart_gallery.gui import theme

# Square side (px), between these bounds; the gap between squares is a share
# of it, as on GitHub.
MIN_CELL = 9
MAX_CELL = 18
GAP_SHARE = 0.2
RADIUS_SHARE = 0.2
EMPTY_ALPHA = 28  # empty days: the text colour, this faint (0-255)
DAY_LABELS = ((1, "Mon"), (3, "Wed"), (5, "Fri"))  # weekday rows, Sunday 0
LESS, MORE = "Less", "More"


class CalendarGrid(QWidget):
    """One year's days as squares; ``set_year`` picks what is drawn."""

    height_changed = Signal()  # the squares grew or shrank with the width

    def __init__(self, days: dict[date, int], parent: QWidget | None = None):
        super().__init__(parent)
        self._days = days
        self.year = 0
        self._weeks = 53
        self._thresholds: tuple[float, ...] = ()
        fm = self.fontMetrics()
        self._left = max(fm.horizontalAdvance(t) for _r, t in DAY_LABELS) + 8
        self._top = fm.height() + 4
        self._legend = fm.height() + 10
        self.setMinimumWidth(self._left + self._weeks * MIN_CELL)

    def set_year(self, year: int) -> None:
        self.year = year
        self._weeks = data.calendar_weeks(year)
        self._thresholds = data.calendar_thresholds(
            n for day, n in self._days.items() if day.year == year
        )
        self.setMinimumWidth(self._left + self._weeks * MIN_CELL)
        self._fit_height()
        self.update()

    # --- geometry ----------------------------------------------------------

    def cell(self) -> float:
        """Square pitch (side plus gap) for the current width."""
        pitch = (self.width() - self._left) / self._weeks
        return min(max(pitch, MIN_CELL), MAX_CELL)

    def _height_for(self, pitch: float) -> int:
        return int(self._top + 7 * pitch + self._legend) + 1

    def _fit_height(self) -> None:
        height = self._height_for(self.cell())
        if height != self.maximumHeight():
            self.setFixedHeight(height)
            self.height_changed.emit()

    def sizeHint(self) -> QSize:
        width = self._left + self._weeks * MAX_CELL
        return QSize(width, self._height_for(MAX_CELL))

    def resizeEvent(self, event) -> None:
        self._fit_height()
        super().resizeEvent(event)

    def day_at(self, pos: QPoint) -> date | None:
        """The day of the chosen year under ``pos``, if any."""
        pitch = self.cell()
        x, y = pos.x() - self._left, pos.y() - self._top
        if x < 0 or y < 0:
            return None
        week, row = int(x // pitch), int(y // pitch)
        if week >= self._weeks or row >= 7:
            return None
        day = data.calendar_start(self.year) + timedelta(days=week * 7 + row)
        return day if day.year == self.year else None

    # --- painting ----------------------------------------------------------

    def _shade(self, count: int, empty: QColor) -> QColor:
        level = data.calendar_level(count, self._thresholds)
        return QColor(colors.CALENDAR_SHADES[level - 1]) if level else empty

    def paintEvent(self, _event) -> None:
        if not self.year:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = self.palette().color(QPalette.ColorRole.WindowText)
        empty = QColor(text)
        empty.setAlpha(EMPTY_ALPHA)
        pitch = self.cell()
        side = pitch * (1 - GAP_SHARE)
        radius = side * RADIUS_SHARE
        fm = self.fontMetrics()

        painter.setPen(text)
        for week, name in data.calendar_month_labels(self.year):
            painter.drawText(QPoint(int(self._left + week * pitch), fm.ascent()), name)
        for row, name in DAY_LABELS:
            y = self._top + row * pitch + (side + fm.ascent() - fm.descent()) / 2
            painter.drawText(QPoint(0, int(y)), name)

        painter.setPen(Qt.PenStyle.NoPen)
        day = date(self.year, 1, 1)
        while day.year == self.year:
            week, row = data.calendar_cell(day)
            rect = QRectF(
                self._left + week * pitch, self._top + row * pitch, side, side
            )
            painter.setBrush(self._shade(self._days.get(day, 0), empty))
            painter.drawRoundedRect(rect, radius, radius)
            day += timedelta(days=1)

        # Legend, right-aligned under the grid: Less ▢▢▢▢▢ More.
        y = self._top + 7 * pitch + (self._legend - side) / 2
        swatches = [empty] + [QColor(c) for c in colors.CALENDAR_SHADES]
        right = self._left + self._weeks * pitch - (pitch - side)
        x = right - fm.horizontalAdvance(MORE)
        baseline = int(y + (side + fm.ascent() - fm.descent()) / 2)
        painter.setPen(text)
        painter.drawText(QPoint(int(x), baseline), MORE)
        painter.setPen(Qt.PenStyle.NoPen)
        x -= 6 + len(swatches) * pitch - (pitch - side)
        for i, color in enumerate(swatches):
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(x + i * pitch, y, side, side), radius, radius
            )
        painter.setPen(text)
        painter.drawText(
            QPoint(int(x - 6 - fm.horizontalAdvance(LESS)), baseline), LESS
        )
        painter.end()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            day = self.day_at(event.pos())
            if day is None:
                QToolTip.hideText()
                event.ignore()
            else:
                tip = data.day_tooltip(day, self._days.get(day, 0))
                QToolTip.showText(event.globalPos(), tip, self)
            return True
        return super().event(event)


class PhotoCalendar(QWidget):
    """Heading and year picker over a ``CalendarGrid``."""

    def __init__(
        self,
        days: dict[date, int],
        year: int | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.days = days
        self.years = data.calendar_years(days)
        self.heading = QLabel()
        self.heading.setFont(theme.serif_font(theme.SECTION_PT))
        self.year_box = QComboBox()
        self.year_box.addItems([str(y) for y in self.years])
        self.grid = CalendarGrid(days)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.grid)
        self.grid.height_changed.connect(self._fit_scroll)
        self.scroll.installEventFilter(self)

        header = QHBoxLayout()
        header.addWidget(self.heading)
        header.addStretch(1)
        header.addWidget(self.year_box)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.scroll)

        self.year_box.currentTextChanged.connect(self._on_year)
        start = year if year in self.years else self.years[0]
        self.year_box.setCurrentText(str(start))
        self._on_year(str(start))

    @property
    def year(self) -> int:
        return self.grid.year

    def _on_year(self, text: str) -> None:
        if not text:
            return
        year = int(text)
        total = sum(n for day, n in self.days.items() if day.year == year)
        self.heading.setText(data.calendar_heading(total, year))
        self.grid.set_year(year)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.scroll and event.type() == QEvent.Type.Resize:
            self._fit_scroll()
        return super().eventFilter(obj, event)

    def _fit_scroll(self) -> None:
        # As tall as the grid, plus the scroll bar when the grid's smallest
        # squares are still wider than the panel.
        bar = self.scroll.horizontalScrollBar().sizeHint().height()
        wide = self.grid.minimumWidth() > self.scroll.width()
        height = self.grid.maximumHeight()  # fixed: see CalendarGrid._fit_height
        self.scroll.setFixedHeight(height + (bar if wide else 0))
