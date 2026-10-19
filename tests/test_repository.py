from datetime import date

from smart_gallery.config import SCHEMA_VERSION


def test_create_and_meta(repo):
    assert repo.schema_version == SCHEMA_VERSION
    assert repo.get_meta("db_uuid")
    assert repo.get_meta("drive_label") == "test"


def test_insert_and_query(repo, make_item):
    repo.upsert_many([make_item("Photos/a.jpg"), make_item("Photos/b.jpg")])
    assert repo.counts() == {"image": 2, "video": 0, "other": 0, "total": 2}
    rels = set(repo.query_relpaths())
    assert rels == {"Photos/a.jpg", "Photos/b.jpg"}


def test_upsert_no_duplicate(repo, make_item):
    repo.upsert_many([make_item("Photos/a.jpg", iso=100)])
    repo.upsert_many([make_item("Photos/a.jpg", iso=800)])  # same relpath, refreshed
    items = repo.query()
    assert len(items) == 1
    assert items[0].iso == 800


def test_relpath_case_insensitive_identity(repo, make_item):
    repo.upsert_many([make_item("Photos/A.jpg")])
    repo.upsert_many([make_item("photos/a.jpg", iso=999)])  # same file, different case
    assert len(repo.query()) == 1


def test_delete_by_relpaths(repo, make_item):
    repo.upsert_many([make_item("a.jpg"), make_item("b.jpg"), make_item("c.jpg")])
    deleted = repo.delete_by_relpaths(["a.jpg", "c.jpg"])
    assert deleted == 2
    assert set(repo.query_relpaths()) == {"b.jpg"}


def test_index_for_sync(repo, make_item):
    repo.upsert_many([make_item("a.jpg", size_bytes=10, mtime_ns=111)])
    idx = repo.index_for_sync()
    assert idx == {"a.jpg": (10, 111)}


def test_date_bounds_and_distinct(repo, make_item):
    repo.upsert_many(
        [
            make_item("a.jpg", date_taken="2026-01-01", camera="Canon"),
            make_item("b.jpg", date_taken="2026-12-31", camera="Sony"),
        ]
    )
    assert repo.date_bounds() == (date(2026, 1, 1), date(2026, 12, 31))
    assert repo.distinct_values("camera") == ["Canon", "Sony"]


def test_roundtrip_item_has_abspath(repo, make_item):
    repo.upsert_many([make_item("Photos/a.jpg")])
    item = repo.query()[0]
    assert item.abspath is not None
    assert item.abspath.endswith("a.jpg")


def test_get_by_id(repo, make_item):
    repo.upsert_many([make_item("Photos/a.jpg", iso=123), make_item("b.jpg")])
    media_id = repo.conn.execute(
        "SELECT id FROM media_items WHERE relpath=?", ("Photos/a.jpg",)
    ).fetchone()["id"]

    item = repo.get_by_id(media_id)

    assert item is not None
    assert item.relpath == "Photos/a.jpg"
    assert item.iso == 123
    assert item.abspath == str(repo.drive_root / "Photos" / "a.jpg")
    assert repo.get_by_id(media_id + 1000) is None


def test_read_only_open_with_uri_characters_in_the_path(tmp_path, make_item):
    import sqlite3

    import pytest

    from smart_gallery.db import GalleryRepository

    root = tmp_path / "Trip #2 100%" / "Mamá"
    root.mkdir(parents=True)
    with GalleryRepository.create(root, label="trip") as repo:
        repo.upsert_many([make_item("a.jpg")])
    with GalleryRepository.open(root, read_only=True) as repo:
        assert repo.get_meta("drive_label") == "trip"
        assert repo.query_relpaths() == ["a.jpg"]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            repo.set_meta("drive_label", "changed")


def test_read_only_uri_keeps_a_unc_host_out_of_the_authority():
    from pathlib import PureWindowsPath

    from smart_gallery.db.repository import _read_only_uri

    uri = _read_only_uri(PureWindowsPath(r"\\nas\photos\Trip #2\gallery.db"))
    assert uri == "file:////nas/photos/Trip%20%232/gallery.db?mode=ro"


def test_date_bounds_skip_invalid_dates(repo, make_item):
    repo.upsert_many(
        [
            make_item("a.jpg", date_taken="0000-00-00"),
            make_item("b.jpg", date_taken="2026-03-01"),
        ]
    )
    assert repo.date_bounds() == (date(2026, 3, 1), date(2026, 3, 1))


def test_value_counts_most_used_first(repo, make_item):
    repo.upsert_many(
        [
            make_item("a.jpg", camera="Sony"),
            make_item("b.jpg", camera="Canon"),
            make_item("c.jpg", camera="Canon"),
            make_item("d.jpg", camera=None),
        ]
    )
    assert repo.value_counts("camera") == [("Canon", 2), ("Sony", 1)]
