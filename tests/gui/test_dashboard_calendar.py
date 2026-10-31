from datetime import date

from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage

from smart_gallery.dashboard import data
from smart_gallery.gui.dashboard import calendar as cal
from smart_gallery.gui.dashboard.calendar import PhotoCalendar

DAYS = {
    date(2026, 10, 12): 14,
    date(2026, 1, 1): 1,
    date(2024, 2, 29): 3,
}


def _calendar(qtbot, days=DAYS, year=None, width=1000):
    widget = PhotoCalendar(days, year)
    qtbot.addWidget(widget)
    widget.resize(width, 300)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def _center(grid, day: date) -> QPoint:
    week, row = data.calendar_cell(day)
    pitch = grid.cell()
    return QPoint(
        int(grid._left + (week + 0.4) * pitch), int(grid._top + (row + 0.4) * pitch)
    )


def test_starts_on_the_most_recent_year(qtbot):
    widget = _calendar(qtbot)
    assert [widget.year_box.itemText(i) for i in range(widget.year_box.count())] == [
        "2026",
        "2024",
    ]
    assert widget.year == 2026
    assert widget.heading.text() == "15 photos in 2026"


def test_picking_a_year_redraws_it(qtbot):
    widget = _calendar(qtbot)
    widget.year_box.setCurrentText("2024")
    assert widget.year == 2024
    assert widget.heading.text() == "3 photos in 2024"
    assert widget.grid.day_at(_center(widget.grid, date(2024, 2, 29))) == date(
        2024, 2, 29
    )


def test_given_year_is_kept_unless_it_has_no_photos(qtbot):
    assert _calendar(qtbot, year=2024).year == 2024
    assert _calendar(qtbot, year=1999).year == 2026


def test_day_under_the_pointer(qtbot):
    widget = _calendar(qtbot)
    grid = widget.grid
    assert grid.day_at(_center(grid, date(2026, 10, 12))) == date(2026, 10, 12)
    assert grid.day_at(_center(grid, date(2026, 12, 31))) == date(2026, 12, 31)
    assert grid.day_at(QPoint(1, 1)) is None  # the day labels' corner
    # Sun 28 Dec 2025 heads the first column but belongs to the year before.
    first_sunday = QPoint(_center(grid, date(2026, 1, 1)).x(), grid._top + 1)
    assert grid.day_at(first_sunday) is None


def test_squares_grow_with_the_width_within_bounds(qtbot):
    widget = _calendar(qtbot, width=2000)
    assert widget.grid.cell() == cal.MAX_CELL
    widget.resize(500, 300)
    qtbot.waitUntil(lambda: widget.grid.cell() < cal.MAX_CELL)
    assert widget.grid.cell() >= cal.MIN_CELL


def test_narrow_panel_scrolls_instead_of_squashing(qtbot):
    widget = _calendar(qtbot, width=200)
    grid = widget.grid
    assert grid.cell() == cal.MIN_CELL
    assert grid.width() >= grid.minimumWidth() > widget.scroll.width()
    # The scroll area leaves room for its scroll bar below the grid.
    assert widget.scroll.height() > grid.height()


def test_paints_shades_and_empty_days(qtbot):
    widget = _calendar(qtbot)
    grid = widget.grid
    image = QImage(grid.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    grid.render(image)
    busy = image.pixelColor(_center(grid, date(2026, 10, 12)))
    empty = image.pixelColor(_center(grid, date(2026, 10, 13)))
    assert busy.name().upper() in {c.upper() for c in cal.colors.CALENDAR_SHADES}
    # The busiest day of the year takes the strongest shade.
    assert busy.name().upper() == cal.colors.CALENDAR_SHADES[-1].upper()
    shades = {c.upper() for c in cal.colors.CALENDAR_SHADES}
    assert empty.name().upper() not in shades
    # Faint over the background, not the background itself.
    assert empty != image.pixelColor(QPoint(grid.width() - 1, grid._top - 2))
