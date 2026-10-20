import pytest
import shiboken6
from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QCategoryAxis,
    QChart,
    QHorizontalStackedBarSeries,
    QPieSeries,
    QPieSlice,
    QStackedBarSeries,
    QValueAxis,
)
from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QGraphicsTextItem

from smart_gallery import colors
from smart_gallery.dashboard.data import (
    APERTURE,
    FOCAL,
    ISO,
    OTHER,
    OTHER_COLOR,
    PALETTE,
    SETTINGS_COLORS,
    SHUTTER,
    VIDEO_COLOR,
    Counts,
    MonthSeries,
    file_type_colors,
)
from smart_gallery.gui.dashboard import charts

ELLIPSES = ("...", "…")


def _view(qtbot, view):
    qtbot.addWidget(view)
    return view


@pytest.fixture
def desktop_font():
    """The Windows desktop font, so labels are measured as the app draws them."""
    app = QApplication.instance()
    old = app.font()
    app.setFont(QFont("Segoe UI", 9))
    yield
    app.setFont(old)


def _rendered(qtbot, view, width: int, height: int) -> list[str]:
    """The text of every visible label Qt drew, after showing at this size."""
    view.resize(width, height)
    view.show()
    qtbot.waitExposed(view)
    QApplication.processEvents()
    texts = []

    def walk(item):
        for child in item.childItems():
            if isinstance(child, QGraphicsTextItem) and child.isVisible():
                if child.toPlainText():
                    texts.append(child.toPlainText())
            walk(child)

    walk(view.chart())
    return texts


def _not_elided(texts):
    return not any(e in t for t in texts for e in ELLIPSES)


def _months(start_year: int, start_month: int, n: int) -> tuple[str, ...]:
    out = []
    for i in range(n):
        m = start_month - 1 + i
        out.append(f"{start_year + m // 12}-{m % 12 + 1:02d}")
    return tuple(out)


def _tips(monkeypatch):
    shown = []
    monkeypatch.setattr(
        charts, "_show_tip", lambda state, text: shown.append((state, text))
    )
    return shown


def _assert_static(view):
    chart = view.chart()
    assert view.renderHints() & QPainter.Antialiasing
    assert chart.animationOptions() == QChart.NoAnimation
    assert view.rubberBand() == view.RubberBand.NoRubberBand


def test_donut_slices_and_labels(qtbot, monkeypatch):
    items = (("Images", 96), ("Videos", 3), ("Others", 1))
    view = _view(qtbot, charts.donut_chart(items, file_type_colors(items)))
    _assert_static(view)
    chart = view.chart()
    assert not chart.legend().isVisible()
    (series,) = chart.series()
    assert isinstance(series, QPieSeries)
    # The hole is 40 % of the pie's diameter (Qt sizes both by the chart).
    assert series.holeSize() / series.pieSize() == pytest.approx(0.4)
    slices = series.slices()
    # Inside the ring the label breaks after the name, so it fits across it.
    assert [s.label() for s in slices] == [
        "Images<br>96 %",
        "Videos 3 %",
        "Others 1 %",
    ]
    # The two thin slices straddle 12 o'clock, so their labels go left and right.
    assert series.pieStartAngle() == pytest.approx(180 * 4 / 100)
    assert series.pieEndAngle() == pytest.approx(series.pieStartAngle() + 360)
    assert [s.value() for s in slices] == [96, 3, 1]
    assert [s.labelPosition() for s in slices] == [
        QPieSlice.LabelInsideHorizontal,
        QPieSlice.LabelOutside,
        QPieSlice.LabelOutside,
    ]
    assert all(s.isLabelVisible() for s in slices)
    assert [s.color() for s in slices] == [
        QColor(colors.FILE_TYPE_COLORS["Images"]),
        QColor(colors.FILE_TYPE_COLORS["Videos"]),
        QColor(colors.FILE_TYPE_COLORS["Others"]),
    ]

    assert charts.slice_tooltip("Images", 12345) == "Images: 12,345"
    shown = _tips(monkeypatch)
    series.hovered.emit(slices[1], True)
    series.hovered.emit(slices[1], False)
    assert shown == [(True, "Videos: 3"), (False, "Videos: 3")]


def test_slice_label_escapes_inside_name():
    assert charts.slice_label("Images", 72, 100) == "Images 72 %"
    assert charts.slice_label("A<B", 72, 100, inside=True) == "A&lt;B<br>72 %"


def test_ranked_bar_full_long_names(qtbot):
    long_name = "Canon EOS R5 Mark II with a very long model id"[:45]
    assert len(long_name) == 45
    counts = Counts(((long_name, 50), ("B", 20), (OTHER, 5)), 80)
    view = _view(qtbot, charts.ranked_bar_chart(counts))
    _assert_static(view)
    chart = view.chart()
    assert not chart.legend().isVisible()
    (series,) = chart.series()
    assert isinstance(series, QHorizontalStackedBarSeries)
    (axis,) = [a for a in chart.axes(Qt.Vertical) if isinstance(a, QBarCategoryAxis)]
    # Qt's category axis runs bottom to top, so the largest is the last one.
    assert axis.categories() == [OTHER, "B", long_name]
    assert not axis.truncateLabels()

    sets = series.barSets()
    assert [s.label() for s in sets] == [long_name, "B", OTHER]
    # Each set holds its count at its own category only, so bars stay centred.
    assert [list(s.at(i) for i in range(s.count())) for s in sets] == [
        [0, 0, 50],
        [0, 20, 0],
        [5, 0, 0],
    ]
    assert [s.color() for s in sets] == [
        QColor(PALETTE[0]),
        QColor(PALETTE[1]),
        QColor(OTHER_COLOR),
    ]


def test_ranked_bar_counts_past_bar_ends(qtbot):
    counts = Counts((("A", 1000), ("B", 30), ("C", 3)), 1100)
    view = _view(qtbot, charts.ranked_bar_chart(counts))
    chart = view.chart()
    (series,) = chart.series()
    assert not series.isLabelsVisible()  # no "0" labels on the empty segments
    labels = [i for i in chart.childItems() if i.data(0) == charts.END_LABEL]
    assert sorted(i.text() for i in labels) == ["1,000", "3", "30"]
    text = QApplication.palette().color(QPalette.WindowText)
    assert all(i.brush().color() == text for i in labels)

    view.resize(500, 300)
    view.show()
    qtbot.waitExposed(view)
    by_text = {i.text(): i for i in labels}
    for index, (name, count) in zip((2, 1, 0), counts.items):
        end = chart.mapToPosition(QPointF(count, index), series)
        label = by_text[f"{count:,}"]
        assert label.pos().x() > end.x()
        assert label.sceneBoundingRect().contains(QPointF(label.pos().x() + 1, end.y()))
    # The largest count still fits inside the plot area.
    (axis,) = [a for a in chart.axes(Qt.Horizontal) if type(a) is QValueAxis]
    assert axis.max() - 1000 >= 0.2 * axis.max()
    assert by_text["1,000"].sceneBoundingRect().right() <= chart.plotArea().right()


def test_ranked_bar_tooltip_share(qtbot, monkeypatch):
    # Two of the nine images have no camera; the share is out of all nine.
    counts = Counts((("A", 3), ("B", 3), ("C", 1)), 9)
    assert charts.ranked_tooltip("A", 3, 9) == "A: 3 (33.3% of 9 images)"
    assert (
        charts.ranked_tooltip("X", 1234, 10000) == "X: 1,234 (12.3% of 10,000 images)"
    )

    view = _view(qtbot, charts.ranked_bar_chart(counts))
    (series,) = view.chart().series()
    sets = series.barSets()
    shown = _tips(monkeypatch)
    series.hovered.emit(True, 0, sets[2])
    series.hovered.emit(True, 2, sets[0])
    series.hovered.emit(False, 2, sets[0])
    assert shown == [
        (True, "C: 1 (11.1% of 9 images)"),
        (True, "A: 3 (33.3% of 9 images)"),
        (False, "A: 3 (33.3% of 9 images)"),
    ]


def test_stop_bar_values(qtbot, monkeypatch):
    buckets = (("f/1.8", 4), ("f/2", 0), ("f/2.8", 1200))
    view = _view(qtbot, charts.stop_bar_chart(buckets, "#ff7f50"))
    _assert_static(view)
    chart = view.chart()
    assert not chart.legend().isVisible()
    (series,) = chart.series()
    assert isinstance(series, QBarSeries)
    (bar_set,) = series.barSets()
    assert [bar_set.at(i) for i in range(bar_set.count())] == [4, 0, 1200]
    assert bar_set.color() == QColor("#ff7f50")
    (axis,) = [a for a in chart.axes(Qt.Horizontal) if isinstance(a, QCategoryAxis)]
    assert axis.categoriesLabels() == ["f/1.8", "f/2", "f/2.8"]
    assert [axis.endValue(label) for label in axis.categoriesLabels()] == [0, 1, 2]
    assert not axis.truncateLabels()
    assert not axis.isGridLineVisible()
    (hidden,) = [a for a in chart.axes(Qt.Horizontal) if type(a) is QValueAxis]
    assert not hidden.isVisible()
    assert (hidden.min(), hidden.max()) == (-0.5, 2.5)
    assert hidden in series.attachedAxes()

    assert charts.stop_tooltip("ISO 400", 1234) == "ISO 400: 1,234"
    shown = _tips(monkeypatch)
    series.hovered.emit(True, 2, bar_set)
    assert shown == [(True, "f/2.8: 1,200")]


def test_month_bar_axis_labels_long_span(qtbot):
    months = _months(2015, 3, 130)  # Mar 2015 .. Dec 2025
    ms = MonthSeries(months, {"Minutes": tuple(float(i % 7) for i in range(130))})
    view = _view(
        qtbot, charts.month_bar_chart(ms, {"Minutes": VIDEO_COLOR}, "min", False)
    )
    _assert_static(view)
    chart = view.chart()
    (axis,) = [a for a in chart.axes(Qt.Horizontal) if isinstance(a, QCategoryAxis)]
    assert axis.count() == 11
    assert axis.categoriesLabels() == ["Mar 2015"] + [str(y) for y in range(2016, 2026)]
    assert axis.labelsPosition() == QCategoryAxis.AxisLabelsPositionOnValue
    assert axis.endValue("2016") == 10  # Jan 2016 is index 10
    (hidden,) = [a for a in chart.axes(Qt.Horizontal) if type(a) is QValueAxis]
    assert not hidden.isVisible()
    assert (hidden.min(), hidden.max()) == (-0.5, 129.5)
    (series,) = chart.series()
    assert isinstance(series, QBarSeries)
    assert hidden in series.attachedAxes()


def test_month_bar_single_month(qtbot, monkeypatch):
    ms = MonthSeries(("2026-03",), {"Minutes": (12.25,)})
    view = _view(
        qtbot, charts.month_bar_chart(ms, {"Minutes": VIDEO_COLOR}, "min", False)
    )
    chart = view.chart()
    assert not chart.legend().isVisible()
    (axis,) = [a for a in chart.axes(Qt.Horizontal) if isinstance(a, QCategoryAxis)]
    assert axis.categoriesLabels() == ["Mar 2026"]
    (hidden,) = [a for a in chart.axes(Qt.Horizontal) if type(a) is QValueAxis]
    assert (hidden.min(), hidden.max()) == (-0.5, 0.5)
    (series,) = chart.series()
    (bar_set,) = series.barSets()
    assert bar_set.at(0) == 12.25
    assert bar_set.color() == QColor(VIDEO_COLOR)
    (y_axis,) = chart.axes(Qt.Vertical)
    assert y_axis.max() >= 12.25

    view.show()
    qtbot.waitExposed(view)  # draws one bar without axis errors

    assert charts.month_tooltip("2026-03", 12.25, "min") == "Mar 2026: 12.2 min"
    shown = _tips(monkeypatch)
    series.hovered.emit(True, 0, bar_set)
    assert shown == [(True, "Mar 2026: 12.2 min")]


def test_month_bar_all_zero_values(qtbot):
    ms = MonthSeries(("2026-03", "2026-04"), {"Minutes": (0.0, 0.0)})
    view = _view(
        qtbot, charts.month_bar_chart(ms, {"Minutes": VIDEO_COLOR}, "min", False)
    )
    (y_axis,) = view.chart().axes(Qt.Vertical)
    assert y_axis.max() > y_axis.min()


def test_timeline_legend_shows_full_names_on_hover(qtbot, monkeypatch):
    ms = MonthSeries(("2026-01",), {"Canon EOS R5 Mark II": (1.0,)})
    view = _view(qtbot, charts.month_bar_chart(ms, {}, "photos", True))
    assert view.chart().legend().showToolTips()
    _scheme(monkeypatch, Qt.ColorScheme.Dark)
    charts.apply_theme(view)
    assert view.chart().legend().showToolTips()


def test_stacked_series_order_and_colors(qtbot, monkeypatch):
    ms = MonthSeries(
        ("2026-01", "2026-02"),
        {"Cam A": (3.0, 1.0), "Cam B": (0.0, 2.0), OTHER: (1.0, 1.0)},
    )
    colors = {"Cam A": PALETTE[0], "Cam B": PALETTE[1], OTHER: OTHER_COLOR}
    view = _view(qtbot, charts.month_bar_chart(ms, colors, "photos", True))
    chart = view.chart()
    assert chart.legend().isVisible()
    (series,) = chart.series()
    assert isinstance(series, QStackedBarSeries)
    sets = series.barSets()
    assert [s.label() for s in sets] == ["Cam A", "Cam B", OTHER]
    assert [s.color() for s in sets] == [QColor(c) for c in colors.values()]
    assert [s.at(1) for s in sets] == [1.0, 2.0, 1.0]
    (y_axis,) = chart.axes(Qt.Vertical)
    assert y_axis.max() >= 4  # stacked total of the busiest month

    assert (
        charts.month_tooltip("2026-02", 1234, "photos", "Cam B", ",.0f")
        == "Feb 2026: Cam B 1,234 photos"
    )
    # Whole-number data (photo counts) shows whole counts.
    shown = _tips(monkeypatch)
    series.hovered.emit(True, 1, sets[1])
    assert shown == [(True, "Feb 2026: Cam B 2 photos")]


def _scheme(monkeypatch, scheme):
    monkeypatch.setattr(charts, "_color_scheme", lambda: scheme)


def test_apply_theme_keeps_series_colors(qtbot, monkeypatch):
    ms = MonthSeries(("2026-01", "2026-02"), {"A": (1.0, 2.0), "B": (3.0, 4.0)})
    colors = {"A": "#123456", "B": "#abcdef"}
    view = _view(qtbot, charts.month_bar_chart(ms, colors, "photos", True))
    chart = view.chart()
    palette = QApplication.palette()

    for scheme, theme in (
        (Qt.ColorScheme.Dark, QChart.ChartThemeDark),
        (Qt.ColorScheme.Light, QChart.ChartThemeLight),
    ):
        _scheme(monkeypatch, scheme)
        charts.apply_theme(view)
        assert chart.theme() == theme
        (series,) = chart.series()
        assert [s.color() for s in series.barSets()] == [
            QColor(c) for c in colors.values()
        ]
        assert chart.backgroundBrush().color() == palette.color(QPalette.Window)
        text = palette.color(QPalette.WindowText)
        assert chart.legend().labelColor() == text
        assert all(a.labelsColor() == text for a in chart.axes())
        assert chart.legend().font() == QApplication.font()


def test_apply_theme_keeps_slice_colors(qtbot, monkeypatch):
    items = (("Videos", 5), ("Others", 1))
    view = _view(qtbot, charts.donut_chart(items, file_type_colors(items)))
    _scheme(monkeypatch, Qt.ColorScheme.Dark)
    charts.apply_theme(view)
    assert view.chart().theme() == QChart.ChartThemeDark
    (series,) = view.chart().series()
    assert [s.color() for s in series.slices()] == [
        QColor(colors.FILE_TYPE_COLORS["Videos"]),
        QColor(colors.FILE_TYPE_COLORS["Others"]),
    ]


class _FakeHints(QObject):
    colorSchemeChanged = Signal(Qt.ColorScheme)


def test_theme_watcher_reapplies_on_signal(qtbot, monkeypatch):
    hints = _FakeHints()
    monkeypatch.setattr(charts, "_style_hints", lambda: hints)
    _scheme(monkeypatch, Qt.ColorScheme.Light)
    first = _view(qtbot, charts.stop_bar_chart((("ISO 100", 3),), "#ffd700"))
    second = _view(qtbot, charts.ranked_bar_chart(Counts((("A", 2),), 2)))
    watcher = charts.ThemeWatcher([first], None)
    watcher.add(second)
    assert first.chart().theme() == QChart.ChartThemeLight

    _scheme(monkeypatch, Qt.ColorScheme.Dark)
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    qtbot.waitUntil(lambda: first.chart().theme() == QChart.ChartThemeDark)
    assert second.chart().theme() == QChart.ChartThemeDark
    (series,) = first.chart().series()
    assert series.barSets()[0].color() == QColor("#ffd700")


def test_theme_watcher_add_prunes_deleted_views(qtbot, monkeypatch):
    hints = _FakeHints()
    monkeypatch.setattr(charts, "_style_hints", lambda: hints)
    watcher = charts.ThemeWatcher([], None)
    kept = _view(qtbot, charts.stop_bar_chart((("ISO 200", 3),), "#ffd700"))
    watcher.add(kept)
    for _ in range(5):  # a reload replaces every panel
        gone = charts.stop_bar_chart((("ISO 100", 3),), "#ffd700")
        watcher.add(gone)
        shiboken6.delete(gone)
    newest = _view(qtbot, charts.stop_bar_chart((("ISO 400", 3),), "#ffd700"))
    watcher.add(newest)
    assert watcher._views == [kept, newest]


def test_theme_watcher_skips_deleted_views(qtbot, monkeypatch):
    hints = _FakeHints()
    monkeypatch.setattr(charts, "_style_hints", lambda: hints)
    gone = charts.stop_bar_chart((("ISO 100", 3),), "#ffd700")
    kept = _view(qtbot, charts.stop_bar_chart((("ISO 200", 3),), "#ffd700"))
    watcher = charts.ThemeWatcher([gone, kept], None)
    shiboken6.delete(gone)
    _scheme(monkeypatch, Qt.ColorScheme.Dark)
    watcher.reapply()
    assert kept.chart().theme() == QChart.ChartThemeDark


def test_ranked_bar_headroom_after_rounding(qtbot):
    for top in (1, 8, 40, 999, 1000, 1234, 99999):
        view = _view(qtbot, charts.ranked_bar_chart(Counts((("A", top),), top)))
        chart = view.chart()
        (axis,) = [a for a in chart.axes(Qt.Horizontal) if type(a) is QValueAxis]
        assert axis.max() - top >= 0.2 * axis.max(), top


def test_axis_numbers_have_thousands_separators(qtbot, monkeypatch):
    view = _view(qtbot, charts.stop_bar_chart((("ISO 100", 30000),), "#ffd700"))
    chart = view.chart()
    _scheme(monkeypatch, Qt.ColorScheme.Dark)
    charts.apply_theme(view)  # setTheme must not undo it
    assert chart.localizeNumbers()
    assert chart.locale().toString(30000.0, "f", 0) == "30,000"


# --- rendered axis labels (real fonts) -------------------------------------


@pytest.mark.parametrize(
    "top, numbers",
    [
        (12.3, ["0", "5", "10", "15"]),
        (0.3, ["0.0", "0.1", "0.2", "0.3"]),
        (0.04, ["0.00", "0.01", "0.02", "0.03", "0.04"]),
        (1234.5, ["0", "500", "1,000", "1,500"]),
    ],
)
def test_video_axis_numbers_are_drawn(qtbot, desktop_font, top, numbers):
    ms = MonthSeries(("2026-01", "2026-02"), {"Minutes": (top / 2, top)})
    view = _view(
        qtbot, charts.month_bar_chart(ms, {"Minutes": VIDEO_COLOR}, "min", False)
    )
    texts = _rendered(qtbot, view, 600, 300)
    assert set(numbers) <= set(texts), texts


def test_24_month_labels_are_not_elided(qtbot, desktop_font):
    months = _months(2024, 1, 24)
    ms = MonthSeries(months, {"A": tuple(float(i % 5 + 1) for i in range(24))})
    view = _view(qtbot, charts.month_bar_chart(ms, {}, "photos", True))
    texts = _rendered(qtbot, view, 900, 380)
    assert _not_elided(texts), texts
    assert {f"Jan {y}" for y in (2024, 2025)} | {"Dec 2025"} <= set(texts)
    assert sum(t.endswith(("2024", "2025")) and " " in t for t in texts) == 24


@pytest.mark.parametrize("n", [60, 130, 300])
@pytest.mark.parametrize("width", [600, 1000, 1400])
def test_first_month_label_on_long_spans(qtbot, desktop_font, n, width):
    ms = MonthSeries(_months(2015, 3, n), {"A": (1.0,) * n})
    view = _view(qtbot, charts.month_bar_chart(ms, {}, "photos", True))
    texts = _rendered(qtbot, view, width, 380)
    assert "Mar 2015" in texts, texts
    assert "2016" in texts
    assert _not_elided(texts), texts
    # The label axis starts where the bars' hidden axis does, so they line up.
    chart = view.chart()
    (axis,) = [a for a in chart.axes(Qt.Horizontal) if isinstance(a, QCategoryAxis)]
    (hidden,) = [a for a in chart.axes(Qt.Horizontal) if type(a) is QValueAxis]
    assert hidden.min() <= -0.5
    assert axis.min() == axis.startValue("Mar 2015") == hidden.min()
    assert axis.max() == hidden.max() == n - 0.5


@pytest.mark.parametrize("spec", [APERTURE, SHUTTER, ISO, FOCAL], ids=lambda s: s.name)
def test_stop_labels_are_not_elided(qtbot, desktop_font, spec):
    buckets = tuple((label, 5) for label in spec.labels)
    view = _view(qtbot, charts.stop_bar_chart(buckets, SETTINGS_COLORS[spec.name]))
    texts = _rendered(qtbot, view, 450, 260)
    assert _not_elided(texts), texts
    # Every stop is drawn, including both ends (e.g. 1/4000 and 30s).
    assert set(spec.labels) <= set(texts), texts


@pytest.mark.parametrize("width, angle", [(330, -90), (450, -90), (900, 0)])
def test_stop_labels_turn_upright_when_crowded(qtbot, desktop_font, width, angle):
    buckets = tuple((label, 5) for label in SHUTTER.labels)
    view = _view(qtbot, charts.stop_bar_chart(buckets, SETTINGS_COLORS["ISO"]))
    texts = _rendered(qtbot, view, width, 260)
    (axis,) = [
        a for a in view.chart().axes(Qt.Horizontal) if isinstance(a, QCategoryAxis)
    ]
    assert axis.labelsAngle() == angle
    assert {"1/4000", "30s"} <= set(texts), texts


def test_theme_switch_keeps_label_axis_settings(qtbot, desktop_font, monkeypatch):
    buckets = tuple((label, 5) for label in SHUTTER.labels)
    view = _view(qtbot, charts.stop_bar_chart(buckets, SETTINGS_COLORS["ISO"]))
    _rendered(qtbot, view, 450, 260)
    _scheme(monkeypatch, Qt.ColorScheme.Dark)
    charts.apply_theme(view)
    texts = _rendered(qtbot, view, 450, 260)
    (axis,) = [
        a for a in view.chart().axes(Qt.Horizontal) if isinstance(a, QCategoryAxis)
    ]
    assert not axis.truncateLabels()
    assert not axis.isGridLineVisible()
    assert axis.labelsAngle() == -90
    assert set(SHUTTER.labels) <= set(texts), texts
