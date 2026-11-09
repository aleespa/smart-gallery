"""GroupedGrid lays its tiles out in justified rows (one height per row,
full rows flush with the side padding) under collapsible headings."""

import random
import time

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel, QWheelEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QFrame

from smart_gallery.gui.library.grid import (
    ASPECT_ROLE,
    HEADER_HEIGHT,
    SIDE_PAD,
    GroupedGrid,
)
from smart_gallery.gui.library.justify import (
    GAP,
    ROW_HEIGHT_DEFAULT,
    ROW_HEIGHT_MAX,
    ROW_HEIGHT_MIN,
    ROW_HEIGHT_STEP,
)
from smart_gallery.gui.library.model import RowGroup


def _grid(qtbot, aspects, groups=None, width=1000, height=600):
    """A shown grid whose viewport is exactly ``width`` x ``height``."""
    grid = GroupedGrid()
    qtbot.addWidget(grid)
    grid.setFrameShape(QFrame.Shape.NoFrame)
    grid.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    model = QStandardItemModel(grid)
    items = []
    for aspect in aspects:
        item = QStandardItem()
        if aspect is not None:
            item.setData(aspect, ASPECT_ROLE)
        items.append(item)
    if items:
        model.appendColumn(items)
    grid.setModel(model)
    grid.set_groups(groups if groups is not None else [RowGroup(0, len(aspects), "A")])
    grid.resize(width, height)
    grid.show()
    assert grid.viewport().width() == width
    return grid, model


def _rect(grid, model, row):
    return grid.visualRect(model.index(row, 0))


def test_full_row_tiles_end_flush_with_padding(qtbot):
    grid, model = _grid(qtbot, [1.5] * 20)
    first, last = _rect(grid, model, 0), _rect(grid, model, 3)
    assert first.x() == SIDE_PAD
    assert first.top() == HEADER_HEIGHT
    assert last.x() + last.width() == 1000 - SIDE_PAD
    assert last.top() == first.top() and last.height() == first.height()
    # The next row starts at the left, one gap below.
    below = _rect(grid, model, 4)
    assert below.x() == SIDE_PAD
    assert below.top() == first.bottom() + 1 + GAP


def test_tiles_keep_their_aspect(qtbot):
    aspects = [1.5, 1.5, 1.5, 1.5, 0.75, 1.0]
    grid, model = _grid(qtbot, aspects)
    for row in (4, 5):  # the last row: not stretched, target height
        rect = _rect(grid, model, row)
        assert rect.height() == ROW_HEIGHT_DEFAULT
        assert rect.width() / rect.height() == pytest.approx(aspects[row], abs=0.01)
    assert _rect(grid, model, 5).x() == _rect(grid, model, 4).right() + 1 + GAP


def test_missing_aspect_falls_back_to_three_by_two(qtbot):
    grid, model = _grid(qtbot, [None, 0.01])
    first = _rect(grid, model, 0)
    assert first.width() / first.height() == pytest.approx(1.5, abs=0.01)
    squeezed = _rect(grid, model, 1)  # clamped to the narrowest shape
    assert squeezed.width() / squeezed.height() == pytest.approx(0.4, abs=0.01)


def test_index_at_and_visual_rect_agree(qtbot):
    rng = random.Random(7)
    aspects = [rng.uniform(0.4, 3.0) for _ in range(200)]
    groups = [RowGroup(0, 70, "A"), RowGroup(70, 1, "B"), RowGroup(71, 129, "C")]
    grid, model = _grid(qtbot, aspects, groups)
    for row in range(200):
        rect = _rect(grid, model, row)
        assert rect.width() > 0 and rect.height() > 0
        assert grid.indexAt(rect.center()).row() == row
        assert grid.indexAt(rect.topLeft()).row() == row
        assert grid.indexAt(rect.bottomRight()).row() == row


def test_gap_is_not_a_tile(qtbot):
    grid, model = _grid(qtbot, [1.5] * 20)
    first = _rect(grid, model, 0)
    between = QPoint(first.right() + 2, first.center().y())
    assert not grid.indexAt(between).isValid()
    below = QPoint(first.center().x(), first.bottom() + 2)
    assert not grid.indexAt(below).isValid()
    left_pad = QPoint(SIDE_PAD - 2, first.center().y())
    assert not grid.indexAt(left_pad).isValid()


def test_set_row_height_clamps_and_signals(qtbot):
    grid, _ = _grid(qtbot, [1.5] * 20)
    assert grid.row_height() == ROW_HEIGHT_DEFAULT
    seen = []
    grid.row_height_changed.connect(seen.append)
    grid.set_row_height(1000)
    assert grid.row_height() == ROW_HEIGHT_MAX
    grid.set_row_height(ROW_HEIGHT_MAX)
    assert seen == [ROW_HEIGHT_MAX]
    grid.set_row_height(0)
    assert grid.row_height() == ROW_HEIGHT_MIN
    assert seen == [ROW_HEIGHT_MAX, ROW_HEIGHT_MIN]


def _wheel(grid, dy, modifiers):
    pos = QPointF(100, 100)
    event = QWheelEvent(
        pos,
        QPointF(grid.viewport().mapToGlobal(pos.toPoint())),
        QPoint(),
        QPoint(0, dy),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(grid.viewport(), event)
    return event


def test_ctrl_wheel_changes_row_height(qtbot):
    grid, model = _grid(qtbot, [1.5] * 201)  # the last tile is alone
    bar = grid.verticalScrollBar()
    bar.setValue(50)
    event = _wheel(grid, 120, Qt.KeyboardModifier.ControlModifier)
    assert event.isAccepted()
    assert grid.row_height() == ROW_HEIGHT_DEFAULT + ROW_HEIGHT_STEP
    assert _rect(grid, model, 200).height() == ROW_HEIGHT_DEFAULT + ROW_HEIGHT_STEP
    _wheel(grid, -120, Qt.KeyboardModifier.ControlModifier)
    _wheel(grid, -120, Qt.KeyboardModifier.ControlModifier)
    assert grid.row_height() == ROW_HEIGHT_DEFAULT - ROW_HEIGHT_STEP
    assert grid.visible_rows()[0] == 0  # still at the first rows
    before = bar.value()
    _wheel(grid, -120, Qt.KeyboardModifier.NoModifier)  # a plain wheel scrolls
    assert grid.row_height() == ROW_HEIGHT_DEFAULT - ROW_HEIGHT_STEP
    assert bar.value() > before


def test_collapsed_group_has_no_tiles(qtbot):
    groups = [RowGroup(0, 4, "A"), RowGroup(4, 4, "B")]
    grid, model = _grid(qtbot, [1.5] * 8, groups)
    grid.set_collapsed("A", True)
    assert grid.visualRect(model.index(0, 0)).isNull()
    assert grid.isIndexHidden(model.index(0, 0))
    assert grid.heading_at(QPoint(20, HEADER_HEIGHT // 2)) == 0
    assert grid.heading_at(QPoint(20, HEADER_HEIGHT + 5)) == 1
    assert _rect(grid, model, 4).top() == 2 * HEADER_HEIGHT
    assert grid.visible_rows() == [4, 5, 6, 7]


def test_tiny_viewport_never_makes_empty_tiles(qtbot):
    rng = random.Random(3)
    aspects = [rng.uniform(0.4, 3.0) for _ in range(40)]
    grid, model = _grid(qtbot, aspects, width=30)
    for row in range(40):
        rect = _rect(grid, model, row)
        assert rect.width() >= 1 and rect.height() >= 1
        assert grid.indexAt(rect.center()).row() == row


def _press(qtbot, grid, key):
    qtbot.keyClick(grid, key)
    return grid.currentIndex().row()


def test_down_moves_to_nearest_tile_below(qtbot):
    # Width 1000: four 3:2 tiles fill row 1 (x 12, 257, 502, 747); row 2 is
    # 3:1, 1:1, 1:1, 1:1 (centres about 253, 578, 742, 907); group B below.
    aspects = [1.5, 1.5, 1.5, 1.5, 3.0, 1.0, 1.0, 1.0, 1.5]
    groups = [RowGroup(0, 8, "A"), RowGroup(8, 1, "B")]
    grid, model = _grid(qtbot, aspects, groups)
    assert _rect(grid, model, 4).top() > _rect(grid, model, 3).bottom()
    grid.setFocus()
    grid.setCurrentIndex(model.index(2, 0))
    assert _press(qtbot, grid, Qt.Key.Key_Down) == 5
    assert _press(qtbot, grid, Qt.Key.Key_Up) == 2
    grid.setCurrentIndex(model.index(0, 0))
    assert _press(qtbot, grid, Qt.Key.Key_Down) == 4
    grid.setCurrentIndex(model.index(7, 0))
    assert _press(qtbot, grid, Qt.Key.Key_Down) == 8  # into the next group
    assert _press(qtbot, grid, Qt.Key.Key_Down) == 8  # nothing below
    assert _press(qtbot, grid, Qt.Key.Key_Up) == 4
    assert _press(qtbot, grid, Qt.Key.Key_Right) == 5
    grid.setCurrentIndex(model.index(3, 0))
    assert _press(qtbot, grid, Qt.Key.Key_Right) == 4  # reading order
    # A collapsed group is skipped.
    grid.set_collapsed("A", True)
    grid.setCurrentIndex(model.index(8, 0))
    assert _press(qtbot, grid, Qt.Key.Key_Up) == 8
    assert _press(qtbot, grid, Qt.Key.Key_Home) == 8


def test_page_down_moves_about_one_viewport(qtbot):
    grid, model = _grid(qtbot, [1.5] * 400, height=600)
    grid.setFocus()
    grid.setCurrentIndex(model.index(0, 0))
    row = _press(qtbot, grid, Qt.Key.Key_PageDown)
    moved = _rect(grid, model, row).top() + grid.verticalOffset() - HEADER_HEIGHT
    assert 600 - 200 <= moved <= 600 + 200
    assert _press(qtbot, grid, Qt.Key.Key_PageUp) == 0


def test_pinned_heading_toggles_its_group(qtbot):
    groups = [RowGroup(0, 40, "A"), RowGroup(40, 40, "B"), RowGroup(80, 40, "C")]
    grid, model = _grid(qtbot, [1.5] * 120, groups)
    bar = grid.verticalScrollBar()
    b_heading = _rect(grid, model, 40).top() + grid.verticalOffset() - HEADER_HEIGHT
    bar.setValue(300)  # inside A: A's heading is pinned
    assert grid.heading_at(QPoint(20, 5)) == 0
    bar.setValue(b_heading + 300)  # inside B
    assert grid.heading_at(QPoint(20, 5)) == 1
    assert not grid.indexAt(QPoint(SIDE_PAD + 20, 5)).isValid()  # covered
    assert grid.heading_at(QPoint(20, HEADER_HEIGHT + 5)) == -1
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(20, 5))
    assert grid.is_collapsed("B")
    assert not grid.is_collapsed("A")
    # The view stays on B: its heading sits at the top, no longer pinned.
    assert grid.heading_at(QPoint(20, 5)) == 1
    assert grid._pinned()[0] == -1
    assert grid.verticalOffset() == b_heading
    assert _rect(grid, model, 80).top() == 2 * HEADER_HEIGHT  # C follows


def test_pinned_heading_is_pushed_up_by_the_next(qtbot):
    groups = [RowGroup(0, 40, "A"), RowGroup(40, 40, "B")]
    grid, model = _grid(qtbot, [1.5] * 80, groups)
    b_heading = _rect(grid, model, 40).top() + grid.verticalOffset() - HEADER_HEIGHT
    grid.verticalScrollBar().setValue(b_heading - 10)  # B's heading at y 10
    assert grid.heading_at(QPoint(20, 5)) == 0  # A's, pushed up to y -30
    assert grid.heading_at(QPoint(20, 15)) == 1


def test_relayout_50k_rows_is_fast(qtbot):
    rng = random.Random(1)
    aspects = [rng.choice((0.75, 1.0, 1.5, 16 / 9)) for _ in range(50_000)]
    groups = [RowGroup(0, 50_000, "A")]
    grid, model = _grid(qtbot, aspects, groups, width=900)
    before = _rect(grid, model, 49_999)
    start = time.perf_counter()
    grid.resize(1200, 600)
    grid._relayout()
    elapsed = time.perf_counter() - start
    assert _rect(grid, model, 49_999) != before
    assert elapsed < 1.5


def test_pinned_heading_is_painted_over_the_tiles(qtbot):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QStyledItemDelegate

    class Red(QStyledItemDelegate):
        def paint(self, painter, option, index):
            painter.fillRect(option.rect, QColor("red"))

    groups = [RowGroup(0, 40, "A"), RowGroup(40, 40, "B")]
    grid, model = _grid(qtbot, [1.5] * 80, groups)
    grid.setItemDelegate(Red(grid))
    grid.verticalScrollBar().setValue(300)
    image = grid.viewport().grab().toImage()
    viewport = grid.viewport()
    back = viewport.palette().color(viewport.backgroundRole())
    x = SIDE_PAD + 40  # y 2 below: above the heading text
    assert grid.indexAt(QPoint(x, HEADER_HEIGHT + 30)).isValid()
    assert image.pixelColor(x, HEADER_HEIGHT + 30) == QColor("red")
    assert image.pixelColor(x, 2) == back  # the pinned band, not a tile


def test_scroll_to_a_tall_tile_keeps_its_top_under_the_band(qtbot):
    groups = [RowGroup(0, 40, "A")]
    grid, model = _grid(qtbot, [1.5] * 40, groups, height=150)  # rows 161 tall
    grid.scrollTo(model.index(20, 0))
    assert grid._pinned()[0] == 0
    assert _rect(grid, model, 20).top() == HEADER_HEIGHT  # just under the band
    grid.scrollTo(model.index(0, 0))
    assert grid.verticalOffset() == 0  # the first row brings its heading


# ── keeping the user's place ────────────────────────────────────────────────


def _at_top(grid, model, row):
    grid.scrollTo(model.index(row, 0), QAbstractItemView.ScrollHint.PositionAtTop)
    assert _rect(grid, model, row).top() == HEADER_HEIGHT  # just under the band


def _top_left(grid):
    """The first tile of the top row under the band."""
    return grid.indexAt(QPoint(SIDE_PAD + 1, HEADER_HEIGHT + 1)).row()


def test_row_height_change_keeps_the_row_at_the_top(qtbot):
    grid, model = _grid(qtbot, [1.5] * 2000)
    _at_top(grid, model, 800)
    for px in (300, ROW_HEIGHT_MIN, ROW_HEIGHT_DEFAULT):
        first = _top_left(grid)
        grid.set_row_height(px)
        assert _rect(grid, model, first).top() == HEADER_HEIGHT
        assert 800 in grid.visible_rows()


def test_row_height_change_scales_the_offset_into_the_top_row(qtbot):
    grid, model = _grid(qtbot, [1.5] * 2000)
    _at_top(grid, model, 800)
    half = _rect(grid, model, 800).height() // 2
    grid.verticalScrollBar().setValue(grid.verticalOffset() + half)
    old_height = _rect(grid, model, 800).height()
    grid.set_row_height(300)
    rect = _rect(grid, model, 800)
    hidden = HEADER_HEIGHT - rect.top()  # the part of its row under the band
    assert abs(hidden - half * rect.height() / old_height) <= 1


def test_width_change_keeps_the_row_at_the_top(qtbot):
    grid, model = _grid(qtbot, [1.5] * 2000)
    _at_top(grid, model, 800)
    grid.resize(700, 600)
    assert _rect(grid, model, 800).top() == HEADER_HEIGHT
    first = _top_left(grid)
    grid.resize(1400, 600)
    qtbot.waitUntil(lambda: not grid._stale)  # laid out by the timer
    assert _rect(grid, model, first).top() == HEADER_HEIGHT
    assert 800 in grid.visible_rows()


def test_size_change_keeps_the_current_tile_in_view(qtbot):
    grid, model = _grid(qtbot, [1.5] * 2000)
    _at_top(grid, model, 800)
    low = [r for r in grid.visible_rows() if _rect(grid, model, r).top() > 400]
    current = low[0]
    grid.setCurrentIndex(model.index(current, 0))
    grid.set_row_height(ROW_HEIGHT_MAX)
    rect = _rect(grid, model, current)
    assert rect.top() >= HEADER_HEIGHT and rect.bottom() < 600
    grid.resize(600, 600)
    rect = _rect(grid, model, current)
    assert rect.top() >= HEADER_HEIGHT and rect.bottom() < 600


def test_model_reset_keeps_the_scroll_value(qtbot):
    grid, model = _grid(qtbot, [1.5] * 2000)
    grid.visible_rows()  # laid out for the shown width
    grid.verticalScrollBar().setValue(5000)
    grid.set_groups([RowGroup(0, 1000, "A"), RowGroup(1000, 1000, "B")])
    assert grid.verticalOffset() == 5000


def test_width_changes_are_laid_out_once(qtbot, monkeypatch):
    grid, model = _grid(qtbot, [1.5] * 200)
    calls = []
    real = grid._relayout
    monkeypatch.setattr(
        grid, "_relayout", lambda *a, **k: (calls.append(1), real(*a, **k))
    )
    for width in (900, 800, 700):
        grid.resize(width, 600)
    assert calls == []  # coalesced: nothing laid out yet
    qtbot.waitUntil(lambda: calls == [1])
    qtbot.wait(50)
    assert calls == [1]
    last = _rect(grid, model, 3)
    assert last.x() + last.width() <= 700 - SIDE_PAD
    # A geometry query does not wait for the timer.
    grid.resize(1000, 600)
    last = _rect(grid, model, 3)
    assert last.x() + last.width() == 1000 - SIDE_PAD
    assert calls == [1, 1]
    qtbot.wait(50)
    assert calls == [1, 1]
