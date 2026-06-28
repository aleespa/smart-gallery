"""The SQL WHERE compiler must select exactly the rows the in-memory predicate
keeps. This is the contract that lets the dashboard/export filter in SQL."""

from datetime import date

import pytest

from smart_gallery.models import MediaItem
from smart_gallery.organize.filters import FilterOptions, matches


def _dataset():
    return [
        MediaItem(relpath="a.jpg", media_type="image", ext=".jpg",
                  date_taken="2026-01-15", camera="Canon EOS R6", lens="RF24-70mm",
                  aperture=2.8, iso=400, shutter_speed_sec=0.004),
        MediaItem(relpath="b.jpg", media_type="image", ext=".jpg",
                  date_taken="2026-06-20", camera="Sony A7", lens="FE50mm",
                  aperture=8.0, iso=100, shutter_speed_sec=0.5),
        MediaItem(relpath="c.png", media_type="image", ext=".png",
                  date_taken="2025-12-01", camera="Canon EOS R6", lens=None,
                  aperture=None, iso=None, shutter_speed_sec=None),
        MediaItem(relpath="d.mp4", media_type="video", ext=".mp4",
                  date_taken="2026-03-10"),
        MediaItem(relpath="e.txt", media_type="other", ext=".txt"),
        MediaItem(relpath="f.jpg", media_type="image", ext=".jpg",
                  date_taken=None, camera="Nikon Z6", lens="Z24-70",
                  aperture=4.0, iso=800, shutter_speed_sec=0.008),
    ]


QUERIES = [
    FilterOptions(),
    FilterOptions(filetypes=["image"]),
    FilterOptions(filetypes=["video", "other"]),
    FilterOptions(extensions=["jpg"]),
    FilterOptions(extensions=[".png", ".mp4"]),
    FilterOptions(date_range=(date(2026, 1, 1), date(2026, 12, 31))),
    FilterOptions(date_range=(date(2026, 1, 1), None)),
    FilterOptions(date_range=(None, date(2025, 12, 31))),
    FilterOptions(cameras=["Canon EOS R6"]),
    FilterOptions(cameras=["Canon EOS R6", "Sony A7"]),
    FilterOptions(lenses=["RF24-70mm"]),
    FilterOptions(aperture_range=(2.0, 4.0)),
    FilterOptions(aperture_range=(None, 4.0)),
    FilterOptions(iso_range=(200, 1000)),
    FilterOptions(shutter_speed_range=("1/500", "1s")),
    FilterOptions(cameras=["Sony A7"], filetypes=["video"]),
    FilterOptions(filetypes=["image"], aperture_range=(2.0, 9.0), iso_range=(50, 500)),
]


@pytest.mark.parametrize("query", QUERIES)
def test_sql_matches_inmemory(repo, query):
    items = _dataset()
    repo.upsert_many(items)

    sql_set = set(repo.query_relpaths(query))
    mem_set = {it.relpath for it in items if matches(it, query)}
    assert sql_set == mem_set, query
