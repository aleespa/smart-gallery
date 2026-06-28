from datetime import date


def test_create_and_meta(repo):
    assert repo.schema_version == 2
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
    repo.upsert_many([
        make_item("a.jpg", date_taken="2026-01-01", camera="Canon"),
        make_item("b.jpg", date_taken="2026-12-31", camera="Sony"),
    ])
    assert repo.date_bounds() == (date(2026, 1, 1), date(2026, 12, 31))
    assert repo.distinct_values("camera") == ["Canon", "Sony"]


def test_roundtrip_item_has_abspath(repo, make_item):
    repo.upsert_many([make_item("Photos/a.jpg")])
    item = repo.query()[0]
    assert item.abspath is not None
    assert item.abspath.endswith("a.jpg")
