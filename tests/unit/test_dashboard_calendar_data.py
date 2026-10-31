from datetime import date

import pandas as pd
import pytest

from smart_gallery import colors
from smart_gallery.dashboard import data


def _photos(*stamps):
    return pd.DataFrame({"date_taken": pd.to_datetime(list(stamps))})


def test_photos_per_day_counts_dated_photos():
    fi = _photos(
        "2026-10-12 09:00",
        "2026-10-12 18:30",
        "2025-01-01 00:01",
        None,  # undated
        "1970-01-01 00:00",  # reset camera clock
        "2200-01-01 00:00",  # clock in the future
    )
    assert data.photos_per_day(fi) == {date(2026, 10, 12): 2, date(2025, 1, 1): 1}


def test_photos_per_day_without_dates():
    assert data.photos_per_day(pd.DataFrame()) == {}
    assert data.photos_per_day(pd.DataFrame({"name": ["a"]})) == {}
    assert data.photos_per_day(_photos(None)) == {}


def test_calendar_years_most_recent_first():
    days = {date(2024, 3, 1): 1, date(2026, 1, 1): 2, date(2024, 5, 1): 3}
    assert data.calendar_years(days) == [2026, 2024]


def test_calendar_layout_starts_on_sunday():
    # 1 Jan 2026 is a Thursday: the first column starts on Sun 28 Dec 2025.
    assert data.calendar_start(2026) == date(2025, 12, 28)
    assert data.calendar_cell(date(2026, 1, 1)) == (0, 4)
    assert data.calendar_cell(date(2026, 1, 4)) == (1, 0)  # a Sunday
    assert data.calendar_cell(date(2026, 12, 31)) == (52, 4)
    assert data.calendar_weeks(2026) == 53
    # 1 Jan 2023 is a Sunday: the column starts on that day.
    assert data.calendar_start(2023) == date(2023, 1, 1)


def test_calendar_weeks_in_a_long_year():
    # A leap year starting on a Saturday reaches into a 54th column.
    assert date(2028, 1, 1).weekday() == 5
    assert data.calendar_weeks(2028) == 54


def test_calendar_month_labels_at_the_week_of_the_first():
    labels = data.calendar_month_labels(2026)
    assert [name for _week, name in labels][:3] == ["Jan", "Feb", "Mar"]
    assert labels[0] == (0, "Jan")
    assert labels[1] == (data.calendar_cell(date(2026, 2, 1))[0], "Feb")
    weeks = [week for week, _name in labels]
    assert weeks == sorted(weeks) and weeks[-1] < 53


def test_calendar_levels_follow_quartiles_of_busy_days():
    counts = [0, 1, 2, 3, 4, 5, 6, 7, 8, 800]
    thresholds = data.calendar_thresholds(counts)
    assert len(thresholds) == data.CALENDAR_LEVELS - 1
    levels = [data.calendar_level(c, thresholds) for c in counts]
    assert levels[0] == 0
    assert levels[1] == 1 and levels[-1] == data.CALENDAR_LEVELS
    assert levels == sorted(levels)
    # One huge day does not push everything else into the faintest shade.
    assert len({lv for lv in levels if lv}) == data.CALENDAR_LEVELS
    assert len(colors.CALENDAR_SHADES) == data.CALENDAR_LEVELS


def test_calendar_levels_without_busy_days():
    thresholds = data.calendar_thresholds([0, 0])
    assert data.calendar_level(0, thresholds) == 0
    assert data.calendar_level(3, data.calendar_thresholds([3, 3, 3])) == 1


@pytest.mark.parametrize(
    "count, text",
    [
        (0, "No photos on Sat 12 Oct 2024"),
        (1, "1 photo on Sat 12 Oct 2024"),
        (1284, "1,284 photos on Sat 12 Oct 2024"),
    ],
)
def test_day_tooltip(count, text):
    assert data.day_tooltip(date(2024, 10, 12), count) == text


def test_calendar_heading():
    assert data.calendar_heading(1284, 2026) == "1,284 photos in 2026"
    assert data.calendar_heading(1, 2026) == "1 photo in 2026"
