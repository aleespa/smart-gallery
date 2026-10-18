"""Native Qt Charts views for the Dashboard, built from ``dashboard.data``.

Every builder returns a static ``QChartView``: antialiased, no animation, no
zoom or selection. Hovering a bar or slice shows a tooltip, and that is the
only interaction. Colours chosen here are kept as the ``sg_color`` property of
each bar set or slice, because ``QChart.setTheme`` resets them;
``apply_theme`` puts them back after switching the theme.
"""

from __future__ import annotations

import html
import math
from typing import Optional, Sequence

import shiboken6
from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QCategoryAxis,
    QChart,
    QChartView,
    QHorizontalStackedBarSeries,
    QPieSeries,
    QPieSlice,
    QStackedBarSeries,
    QValueAxis,
)
from PySide6.QtCore import QLocale, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem, QToolTip

from smart_gallery.dashboard.data import (
    FILE_TYPE_COLORS,
    Counts,
    MonthSeries,
    color_for,
    month_axis_labels,
    month_label,
)

COLOR_PROPERTY = "sg_color"
END_LABEL = "sg_end_label"  # QGraphicsItem.data(0) of a ranked bar's count label
_END_LABEL_GAP = 4  # px between a bar's end and its count
_FILE_TYPES = ("Images", "Videos", "Others")
_NUMBER_LOCALE = QLocale(QLocale.English, QLocale.UnitedStates)
_PIE_SIZE = 0.65  # leaves room above for the outside labels
_HOLE_RATIO = 0.4
_SMALL_SLICE = 0.04  # slices under 4 % put their label outside
_ROTATE_MONTH_LABELS = 12  # more labels than this are drawn at an angle


# --- tooltip and label text ------------------------------------------------


def slice_label(name: str, count: int, total: int, inside: bool = False) -> str:
    """``"Images 72 %"``; inside the ring it breaks after the name (rich text)."""
    pct = 100 * count / total if total else 0
    if inside:
        return f"{html.escape(name)}<br>{pct:.0f} %"
    return f"{name} {pct:.0f} %"


def slice_tooltip(name: str, count: int) -> str:
    return f"{name}: {count:,}"


def ranked_tooltip(name: str, count: int, total: int) -> str:
    share = count / total if total else 0
    return f"{name}: {count:,} ({share:.1%} of {total:,} images)"


def stop_tooltip(label: str, count: int) -> str:
    return f"{label}: {count:,}"


def month_tooltip(
    month: str,
    value: float,
    unit: str,
    series: Optional[str] = None,
    value_format: str = ",.1f",
) -> str:
    name = f"{series} " if series is not None else ""
    return f"{month_label(month)}: {name}{value:{value_format}} {unit}"


def file_type_colors(items) -> list[str]:
    """File-type colours by name, so Videos stays green when there are no images."""
    by_name = dict(zip(_FILE_TYPES, FILE_TYPE_COLORS))
    return [by_name.get(name, color_for(name, i)) for i, (name, _) in enumerate(items)]


def _show_tip(state: bool, text: str) -> None:
    if state:
        QToolTip.showText(QCursor.pos(), text)
    else:
        QToolTip.hideText()


# --- builders --------------------------------------------------------------


def donut_chart(items: Sequence[tuple[str, int]], colors: Sequence[str]) -> QChartView:
    """Pie with a 40 % hole; ``colors`` is aligned with ``items``."""
    total = sum(count for _, count in items)
    series = QPieSeries()
    # Qt sizes both as fractions of the chart; the hole is 40 % of the pie.
    series.setPieSize(_PIE_SIZE)
    series.setHoleSize(_HOLE_RATIO * _PIE_SIZE)
    small = [bool(total) and count / total < _SMALL_SLICE for _, count in items]
    # Centre a run of thin slices at the end on 12 o'clock, so their outside
    # labels split to the left and right instead of piling up on one side.
    tail = 0
    for (_, count), is_small in zip(reversed(items), reversed(small)):
        if not is_small:
            break
        tail += count
    start = 180 * tail / total if total else 0
    series.setPieStartAngle(start)
    series.setPieEndAngle(start + 360)
    for (name, count), color, is_small in zip(items, colors, small):
        slice_ = series.append(slice_label(name, count, total, not is_small), count)
        slice_.setProperty(COLOR_PROPERTY, color)
        slice_.setProperty("sg_name", name)
        slice_.setLabelVisible(True)
        slice_.setLabelPosition(
            QPieSlice.LabelOutside if is_small else QPieSlice.LabelInsideHorizontal
        )

    def on_hover(slice_: QPieSlice, state: bool) -> None:
        text = slice_tooltip(slice_.property("sg_name"), int(slice_.value()))
        _show_tip(state, text)

    series.hovered.connect(on_hover)
    chart = _chart(series)
    return _view(chart)


def ranked_bar_chart(counts: Counts) -> QChartView:
    """Horizontal bars, largest at the top, each with its count at its end.

    One stacked bar set per item, holding its count at its own category and
    zero elsewhere: every bar gets its own colour and stays centred. The
    counts are text items placed just past each bar's end, because Qt's own
    stacked-bar labels only fit inside the bar and spill out of short ones."""
    n = len(counts.items)
    names = [name for name, _ in counts.items]
    series = QHorizontalStackedBarSeries()
    series.setBarWidth(0.7)
    for rank, (name, count) in enumerate(counts.items):
        values = [0] * n
        values[n - 1 - rank] = count
        series.append(_bar_set(name, values, color_for(name, rank)))

    def on_hover(state: bool, index: int, bar_set: QBarSet) -> None:
        text = ranked_tooltip(bar_set.label(), int(bar_set.at(index)), counts.total)
        _show_tip(state, text)

    series.hovered.connect(on_hover)
    chart = _chart(series)
    names_axis = QBarCategoryAxis()
    names_axis.append(list(reversed(names)))
    names_axis.setTruncateLabels(False)
    _attach(chart, series, names_axis, Qt.AlignLeft)
    top = max((count for _, count in counts.items), default=0)
    # At least 20 % of the rounded axis is left past the longest bar, for its count.
    _attach(chart, series, _value_axis(top * 1.25, integer=True), Qt.AlignBottom)
    _end_labels(
        chart, series, [(n - 1 - r, c) for r, (_, c) in enumerate(counts.items)]
    )
    return _view(chart)


def _end_labels(chart: QChart, series, bars: list[tuple[int, int]]) -> None:
    """A count label just past the end of each horizontal bar."""
    labels = []
    for index, count in bars:
        item = QGraphicsSimpleTextItem(f"{count:,}", chart)
        item.setData(0, END_LABEL)
        labels.append((item, index, count))

    def place() -> None:
        for item, index, count in labels:
            end = chart.mapToPosition(QPointF(count, index), series)
            height = item.boundingRect().height()
            item.setPos(end.x() + _END_LABEL_GAP, end.y() - height / 2)

    chart.plotAreaChanged.connect(place)
    place()


def stop_bar_chart(buckets: Sequence[tuple[str, int]], color: str) -> QChartView:
    """One bar per camera-setting stop."""
    labels = [label for label, _ in buckets]
    series = QBarSeries()
    series.setBarWidth(0.8)
    series.append(_bar_set("", [count for _, count in buckets], color))

    def on_hover(state: bool, index: int, bar_set: QBarSet) -> None:
        _show_tip(state, stop_tooltip(labels[index], int(bar_set.at(index))))

    series.hovered.connect(on_hover)
    chart = _chart(series)
    stops_axis = QBarCategoryAxis()
    stops_axis.append(labels)
    _attach(chart, series, stops_axis, Qt.AlignBottom)
    top = max((count for _, count in buckets), default=0)
    _attach(chart, series, _value_axis(top, integer=True), Qt.AlignLeft)
    return _view(chart)


def month_bar_chart(
    ms: MonthSeries, colors: dict[str, str], unit: str, stacked: bool
) -> QChartView:
    """One bar per month; stacked by series (with a legend) when ``stacked``.

    Bars sit at index positions on a hidden value axis. A category axis over
    the same range carries the thinned labels from ``month_axis_labels``.
    Whole-number data (photo counts) shows whole numbers on the axis and in
    tooltips; anything else (video minutes) shows one decimal place."""
    n = len(ms.months)
    whole = all(float(v).is_integer() for vs in ms.series.values() for v in vs)
    value_format = ",.0f" if whole else ",.1f"
    series = QStackedBarSeries() if stacked else QBarSeries()
    series.setBarWidth(0.8)
    for rank, (name, values) in enumerate(ms.series.items()):
        color = colors.get(name) or color_for(name, rank)
        series.append(_bar_set(name, list(values), color))

    def on_hover(state: bool, index: int, bar_set: QBarSet) -> None:
        name = bar_set.label() if stacked else None
        text = month_tooltip(
            ms.months[index], bar_set.at(index), unit, name, value_format
        )
        _show_tip(state, text)

    series.hovered.connect(on_hover)
    chart = _chart(series, legend=stacked)

    positions = QValueAxis()
    positions.setRange(-0.5, n - 0.5)
    positions.setVisible(False)
    _attach(chart, series, positions, Qt.AlignBottom)
    month_axis = QCategoryAxis()
    month_axis.setLabelsPosition(QCategoryAxis.AxisLabelsPositionOnValue)
    month_axis.setStartValue(-0.5)
    labels = month_axis_labels(ms.months)
    for index, label in labels:
        month_axis.append(label, index)
    month_axis.setRange(-0.5, n - 0.5)
    if len(labels) > _ROTATE_MONTH_LABELS:
        month_axis.setLabelsAngle(-45)
    chart.addAxis(month_axis, Qt.AlignBottom)

    if stacked:
        top = max((sum(col) for col in zip(*ms.series.values())), default=0)
    else:
        top = max((v for values in ms.series.values() for v in values), default=0)
    _attach(chart, series, _value_axis(top, integer=whole), Qt.AlignLeft)
    return _view(chart)


def _bar_set(label: str, values, color: str) -> QBarSet:
    bar_set = QBarSet(label)
    bar_set.append([float(v) for v in values])
    bar_set.setProperty(COLOR_PROPERTY, color)
    return bar_set


def _chart(series, legend: bool = False) -> QChart:
    chart = QChart()
    # Axis numbers with thousands separators, like the "1,234" in tooltips.
    chart.setLocalizeNumbers(True)
    chart.setLocale(_NUMBER_LOCALE)
    chart.setAnimationOptions(QChart.NoAnimation)
    chart.addSeries(series)
    chart.legend().setVisible(legend)
    return chart


def _attach(chart: QChart, series, axis, alignment) -> None:
    chart.addAxis(axis, alignment)
    series.attachAxis(axis)


def _value_axis(top: float, integer: bool) -> QValueAxis:
    """A value axis from 0 with round tick steps (whole steps for counts)."""
    raw = top / 4 if top > 0 else 0.25
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if raw <= m * magnitude)
    if integer:
        step = max(1, step)
    axis = QValueAxis()
    axis.setRange(0, step * max(1, math.ceil(top / step)))
    axis.setTickType(QValueAxis.TicksDynamic)
    axis.setTickAnchor(0)
    axis.setTickInterval(step)
    axis.setLabelFormat("%.0f" if integer else "%g")
    return axis


def _view(chart: QChart) -> QChartView:
    view = QChartView(chart)
    view.setRenderHint(QPainter.Antialiasing)
    view.setRubberBand(QChartView.RubberBand.NoRubberBand)
    apply_theme(view)
    return view


# --- theming ---------------------------------------------------------------


def _style_hints():
    return QGuiApplication.styleHints()


def _color_scheme() -> Qt.ColorScheme:
    # Not via _style_hints(): tests swap that for a fake signal source only.
    return QGuiApplication.styleHints().colorScheme()


def apply_theme(view: QChartView) -> None:
    """Light or dark chart theme from the OS, colours and fonts from the app."""
    chart = view.chart()
    dark = _color_scheme() == Qt.ColorScheme.Dark
    chart.setTheme(QChart.ChartThemeDark if dark else QChart.ChartThemeLight)

    palette = QApplication.palette()
    font = QApplication.font()
    window = palette.color(QPalette.Window)
    text = palette.color(QPalette.WindowText)
    grid = QColor(text)
    grid.setAlpha(40)
    view.setBackgroundBrush(window)
    chart.setBackgroundBrush(window)
    for axis in chart.axes():
        axis.setLabelsColor(text)
        axis.setLabelsFont(font)
        axis.setGridLineColor(grid)
        axis.setLinePenColor(grid)
    chart.legend().setLabelColor(text)
    chart.legend().setFont(font)
    end_labels = [i for i in chart.childItems() if i.data(0) == END_LABEL]
    for item in end_labels:
        item.setBrush(text)
        item.setFont(font)
    if end_labels:  # re-place them for the new font
        chart.plotAreaChanged.emit(chart.plotArea())

    for series in chart.series():
        if isinstance(series, QPieSeries):
            for slice_ in series.slices():
                color = QColor(slice_.property(COLOR_PROPERTY))
                slice_.setColor(color)
                slice_.setBorderColor(window)
                outside = slice_.labelPosition() == QPieSlice.LabelOutside
                slice_.setLabelColor(text if outside else _text_on(color))
                slice_.setLabelFont(font)
        else:
            for bar_set in series.barSets():
                color = QColor(bar_set.property(COLOR_PROPERTY))
                bar_set.setColor(color)
                bar_set.setBorderColor(color)


def _text_on(color: QColor) -> QColor:
    """Black or white, whichever reads better on ``color``."""
    luma = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
    return QColor(Qt.black if luma > 150 else Qt.white)


class ThemeWatcher(QObject):
    """Re-themes its chart views when the OS switches light/dark.

    Views whose C++ object is gone are dropped on the next switch, so a panel
    removed from the Dashboard must be ``deleteLater()``-ed, not just hidden
    or taken out of its layout, or it stays registered here."""

    def __init__(self, views: list[QChartView], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._views = list(views)
        _style_hints().colorSchemeChanged.connect(self._schedule)

    def add(self, view: QChartView) -> None:
        self._views.append(view)

    def _schedule(self, *_args) -> None:
        # Let the application palette catch up with the new scheme first.
        QTimer.singleShot(0, self, self.reapply)

    def reapply(self) -> None:
        self._views = [v for v in self._views if shiboken6.isValid(v)]
        for view in self._views:
            apply_theme(view)
