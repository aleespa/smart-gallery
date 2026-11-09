import pytest

from smart_gallery.gui.library.justify import GAP, justify, tile_aspect


def test_full_rows_end_flush():
    rows = justify([1.5] * 10, 1000, 190)
    for r in rows:
        if r.full:
            assert sum(r.widths) + GAP * (len(r.widths) - 1) == 1000


def test_last_row_keeps_target_height_and_is_not_stretched():
    rows = justify([1.5] * 5, 1000, 190)
    assert rows[-1].full is False and rows[-1].height == 190
    assert sum(rows[-1].widths) < 1000


def test_rows_cover_every_item_in_order():
    rows = justify([1.5, 0.67, 1.0, 3.0, 1.33] * 7, 800, 150)
    assert [r.start for r in rows] == sorted(r.start for r in rows)
    assert sum(len(r.widths) for r in rows) == 35


def test_single_very_wide_item_fills_a_row_lower():
    [r] = justify([3.0], 300, 190)
    assert r.full and r.widths == (300,) and r.height == 100


def test_empty():
    assert justify([], 800, 190) == []


@pytest.mark.parametrize(
    "args,expected",
    [
        ((6000, 4000, 1, "image"), 1.5),
        ((6000, 4000, 6, "image"), 4000 / 6000),
        ((1920, 1080, 6, "video"), 1080 / 1920),
        ((None, None, None, "image"), 1.5),
        ((0, 1080, 1, "video"), 16 / 9),
        ((None, None, None, "other"), 1.0),
        ((12000, 2000, 1, "image"), 3.0),
        ((500, 5000, 1, "image"), 0.4),
    ],
)
def test_tile_aspect(args, expected):
    assert tile_aspect(*args) == pytest.approx(expected)
