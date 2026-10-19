"""Filter panel values: conversion to FilterOptions and the choice lists."""

from smart_gallery.cli import build_filter_query, build_parser
from smart_gallery.gui.filters_model import (
    FilterValues,
    filter_choices,
    from_filter_options,
    to_filter_options,
)
from smart_gallery.models import Person
from smart_gallery.organize.filters import is_query_empty


def _cli_query(*flags):
    args = build_parser().parse_args(["report", "E:/", "--to", "r.xlsx", *flags])
    return build_filter_query(args)


def test_blank_values_are_empty_query():
    options, errors = to_filter_options(FilterValues())
    assert options is None
    assert errors == {}
    assert is_query_empty(options)
    # Whitespace-only text counts as blank too.
    options, errors = to_filter_options(FilterValues(min_iso="  ", date_to=" "))
    assert (options, errors) == (None, {})


def test_values_convert_to_filter_options():
    values = FilterValues(
        date_from="2026-01-01",
        date_to="2026-06-30",
        cameras=("Canon EOS R6", "iPhone 15"),
        min_iso="100",
        max_iso="1600",
        min_shutter="1/1000",
        max_shutter="2s",
    )
    options, errors = to_filter_options(values)
    assert errors == {}
    assert options == _cli_query(
        "--date-start",
        "2026-01-01",
        "--date-end",
        "2026-06-30",
        "--cameras",
        "Canon EOS R6",
        "iPhone 15",
        "--min-iso",
        "100",
        "--max-iso",
        "1600",
        "--min-shutter-speed",
        "1/1000",
        "--max-shutter-speed",
        "2s",
    )


def test_other_fields_convert_like_the_cli():
    values = FilterValues(
        date_to="2026-06-30",
        lenses=("RF24-70mm",),
        filetypes=("image",),
        extensions=("JPG", ".cr3"),
        min_aperture="2.8",
        people=("Ana",),
    )
    options, errors = to_filter_options(values)
    assert errors == {}
    assert options == _cli_query(
        "--date-end",
        "2026-06-30",
        "--lenses",
        "RF24-70mm",
        "--file-types",
        "image",
        "--extensions",
        "JPG",
        ".cr3",
        "--min-aperture",
        "2.8",
        "--people",
        "Ana",
    )


def test_bad_iso_reports_error():
    options, errors = to_filter_options(FilterValues(min_iso="abc", max_iso="1600"))
    assert options is None
    assert set(errors) == {"min_iso"}


def test_other_bad_values_report_errors():
    values = FilterValues(
        date_from="2026-13-01",
        max_aperture="wide",
        min_shutter="1/0",
        max_iso="-5",
    )
    options, errors = to_filter_options(values)
    assert options is None
    assert set(errors) == {"date_from", "max_aperture", "min_shutter", "max_iso"}


def test_from_filter_options_round_trips():
    values = FilterValues(
        date_from="2026-01-01",
        cameras=("Canon EOS R6",),
        filetypes=("image", "video"),
        extensions=(".jpg",),
        min_aperture="2.8",
        max_iso="1600",
        max_shutter="2s",
        people=("Ana",),
    )
    options, _ = to_filter_options(values)
    assert from_filter_options(options) == values
    assert from_filter_options(None) == FilterValues()


def test_filter_choices(repo, make_item):
    repo.upsert_many(
        [
            make_item("a.jpg", camera="Canon EOS R6", date_taken="2026-01-05"),
            make_item("b.cr3", camera="Sony A7", lens=None, date_taken="2026-03-01"),
            make_item("c.mp4", media_type="video", camera=None, date_taken=None),
        ]
    )
    choices = filter_choices(repo)
    # (value, photo count), most-used first, ties by name.
    assert choices["cameras"] == [("Canon EOS R6", 1), ("Sony A7", 1)]
    assert choices["lenses"] == [("RF24-70mm", 2)]
    assert choices["extensions"] == [(".cr3", 1), (".jpg", 1), (".mp4", 1)]
    assert choices["date_bounds"] == ["2026-01-05", "2026-03-01"]
    assert "people" not in choices  # no face data yet

    repo.create_person(Person(name="Ana", face_count=2))
    repo.create_person(Person(name=None, face_count=1))
    assert filter_choices(repo)["people"] == [("Ana", 0)]


def test_filter_choices_put_the_most_used_first(repo, make_item):
    repo.upsert_many(
        [
            make_item("a.jpg", camera="Sony A7"),
            make_item("b.jpg", camera="Canon EOS R6"),
            make_item("c.jpg", camera="Canon EOS R6"),
        ]
    )
    assert filter_choices(repo)["cameras"] == [("Canon EOS R6", 2), ("Sony A7", 1)]


def test_filter_choices_count_photos_per_person(repo, make_item):
    from smart_gallery.config import FACE_DET_VERSION
    from smart_gallery.models import Face

    repo.upsert_many([make_item("a.jpg"), make_item("b.jpg")])
    ids = dict(repo.conn.execute("SELECT relpath, id FROM media_items").fetchall())
    ana = repo.create_person(Person(name="Ana", face_count=3))
    faces = [
        Face.from_detection(ids["a.jpg"], (0, 0, 10, 10), 0.9, _unit()),
        Face.from_detection(ids["a.jpg"], (20, 0, 30, 10), 0.9, _unit()),
        Face.from_detection(ids["b.jpg"], (0, 0, 10, 10), 0.9, _unit()),
    ]
    for face in faces:
        face.person_id = ana
    repo.add_scan_results(
        [(ids["a.jpg"], faces[:2]), (ids["b.jpg"], faces[2:])], FACE_DET_VERSION
    )
    # Two faces in a.jpg are still one photo.
    assert filter_choices(repo)["people"] == [("Ana", 2)]


def _unit():
    import numpy as np

    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1.0
    return vector


def test_filter_choices_on_old_catalog_without_face_tables(repo, make_item):
    repo.upsert_many([make_item("a.jpg")])
    repo.conn.execute("DROP TABLE faces")
    repo.conn.execute("DROP TABLE persons")
    choices = filter_choices(repo)
    assert "people" not in choices
