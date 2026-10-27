"""Library query layer: folder tree and filter + folder row selection."""

import pytest

from smart_gallery.gui.library.model import (
    FolderNode,
    LibraryQuery,
    folder_clause,
    folder_tree,
    group_rows,
    library_rows,
    library_stats,
)
from smart_gallery.organize.filters import FilterOptions
from test_where_parity import QUERIES, _dataset


@pytest.mark.parametrize("query", QUERIES)
def test_rows_match_export_selection(repo, query):
    repo.upsert_many(_dataset())

    rows = library_rows(repo, LibraryQuery(filters=query))

    assert sorted(r.relpath for r in rows) == sorted(repo.query_relpaths(query))


def _add(repo, make_item, *relpaths, **kwargs):
    repo.upsert_many([make_item(r, **kwargs) for r in relpaths])


def _find(node, path):
    if node.path == path:
        return node
    for child in node.children:
        if path == child.path or path.startswith(child.path + "/"):
            return _find(child, path)
    raise KeyError(path)


def test_folder_tree_counts(repo, make_item):
    _add(repo, make_item, "a/x.jpg", "a/b/y.jpg", "z.jpg")

    root = folder_tree(repo)

    assert isinstance(root, FolderNode)
    assert (root.path, root.count) == ("", 3)
    a = _find(root, "a")
    assert (a.name, a.count) == ("a", 2)
    assert _find(root, "a/b").count == 1
    assert [c.path for c in root.children] == ["a"]


def test_folder_tree_sorted_case_insensitively(repo, make_item):
    _add(repo, make_item, "b/1.jpg", "A/2.jpg", "c/3.jpg")

    assert [c.name for c in folder_tree(repo).children] == ["A", "b", "c"]


def test_folder_tree_empty(repo):
    root = folder_tree(repo)
    assert (root.path, root.count, root.children) == ("", 0, ())


def test_folder_recursive_vs_direct(repo, make_item):
    _add(repo, make_item, "a/x.jpg", "a/b/y.jpg", "a/b/c/z.jpg", "top.jpg")

    def rels(folder, recursive):
        rows = library_rows(repo, LibraryQuery(folder=folder, recursive=recursive))
        return sorted(r.relpath for r in rows)

    assert rels("a", True) == ["a/b/c/z.jpg", "a/b/y.jpg", "a/x.jpg"]
    assert rels("a", False) == ["a/x.jpg"]
    assert rels("", True) == ["a/b/c/z.jpg", "a/b/y.jpg", "a/x.jpg", "top.jpg"]
    assert rels("", False) == ["top.jpg"]


def test_folder_input_is_normalized(repo, make_item):
    _add(repo, make_item, "a/b/y.jpg", "a/x.jpg")

    for folder in ("a/b", "/a/b/", "a\\b"):
        rows = library_rows(repo, LibraryQuery(folder=folder))
        assert [r.relpath for r in rows] == ["a/b/y.jpg"], folder


def test_folder_with_like_wildcards(repo, make_item):
    _add(repo, make_item, "DCIM/100_CANON/a.jpg", "DCIM/100XCANON/b.jpg")

    for folder in ("DCIM/100_CANON", "dcim/100_canon"):
        rows = library_rows(repo, LibraryQuery(folder=folder))
        assert [r.relpath for r in rows] == ["DCIM/100_CANON/a.jpg"], folder


def test_folder_with_percent_and_backslash(repo, make_item):
    _add(repo, make_item, "100%/a.jpg", "100x/b.jpg", "100%x/c.jpg")

    pct = library_rows(repo, LibraryQuery(folder="100%"))
    assert [r.relpath for r in pct] == ["100%/a.jpg"]


def test_folder_combines_with_filters(repo, make_item):
    _add(repo, make_item, "a/x.jpg", "a/y.mp4", media_type="image")
    repo.upsert_many([make_item("a/v.mp4", media_type="video", ext=".mp4")])
    repo.upsert_many([make_item("b/w.mp4", media_type="video", ext=".mp4")])

    q = LibraryQuery(folder="a", filters=FilterOptions(filetypes=["video"]))

    assert [r.relpath for r in library_rows(repo, q)] == ["a/v.mp4"]
    assert library_stats(repo, q) == (1, 1_000_000)


def test_folder_clause_shapes():
    assert folder_clause("", True) == ("", [])
    assert folder_clause("", False) == ("relpath NOT LIKE '%/%'", [])
    sql, params = folder_clause("a_b", False)
    assert sql == (r"relpath LIKE ? ESCAPE '\' AND relpath NOT LIKE ? ESCAPE '\'")
    assert params == [r"a\_b/%", r"a\_b/%/%"]


def test_library_rows_fields(repo, make_item):
    _add(repo, make_item, "a/x.jpg", mtime_ns=42)

    (row,) = library_rows(repo, LibraryQuery())

    assert row.id == repo.conn.execute("SELECT id FROM media_items").fetchone()["id"]
    assert (row.relpath, row.media_type, row.ext, row.mtime_ns) == (
        "a/x.jpg",
        "image",
        ".jpg",
        42,
    )
    assert row.duration_ms is None


def test_library_rows_duration_for_videos(repo, make_item):
    _add(repo, make_item, "v.mp4", media_type="video", ext=".mp4", duration_ms=83500.4)

    (row,) = library_rows(repo, LibraryQuery())

    assert row.duration_ms == 83500


def test_sort_orders(repo, make_item):
    repo.upsert_many(
        [
            make_item("c.jpg", date_taken="2026-01-01", size_bytes=10),
            make_item("a.jpg", date_taken="2026-03-01", size_bytes=30),
            make_item("b.jpg", date_taken="2026-01-01", size_bytes=20),
            make_item("n.jpg", date_taken=None, size_bytes=5),
        ]
    )

    def order(sort, descending=False):
        q = LibraryQuery(sort=sort, descending=descending)
        return [r.relpath for r in library_rows(repo, q)]

    assert order("date") == ["b.jpg", "c.jpg", "a.jpg", "n.jpg"]
    assert order("name") == ["a.jpg", "b.jpg", "c.jpg", "n.jpg"]
    assert order("size") == ["n.jpg", "c.jpg", "b.jpg", "a.jpg"]


def test_sort_orders_descending(repo, make_item):
    repo.upsert_many(
        [
            make_item("c.jpg", date_taken="2026-01-01", size_bytes=10),
            make_item("a.jpg", date_taken="2026-03-01", size_bytes=30),
            make_item("b.jpg", date_taken="2026-01-01", size_bytes=20),
            make_item("n.jpg", date_taken=None, size_bytes=5),
        ]
    )

    def order(sort):
        q = LibraryQuery(sort=sort, descending=True)
        return [r.relpath for r in library_rows(repo, q)]

    # Undated items stay last whichever way the dates run.
    assert order("date") == ["a.jpg", "c.jpg", "b.jpg", "n.jpg"]
    assert order("name") == ["n.jpg", "c.jpg", "b.jpg", "a.jpg"]
    assert order("size") == ["a.jpg", "b.jpg", "c.jpg", "n.jpg"]


def test_library_stats(repo, make_item):
    _add(repo, make_item, "a/x.jpg", "a/y.jpg", "z.jpg", size_bytes=100)

    assert library_stats(repo, LibraryQuery()) == (3, 300)
    assert library_stats(repo, LibraryQuery(folder="a")) == (2, 200)
    assert library_stats(repo, LibraryQuery(folder="nope")) == (0, 0)


def test_name_sort_keeps_each_folders_files_together(repo, make_item):
    _add(repo, make_item, "a/2024/x.jpg", "a/IMG_1.jpg", "A/IMG_0.jpg", "b.jpg")

    rows = library_rows(repo, LibraryQuery(sort="name"))

    assert [r.relpath for r in rows] == [
        "b.jpg",
        "A/IMG_0.jpg",
        "a/IMG_1.jpg",
        "a/2024/x.jpg",
    ]
    groups = group_rows(rows, "name")
    assert [(g.start, g.count, g.label) for g in groups] == [
        (0, 1, "Top folder"),
        (1, 2, "A"),
        (3, 1, "a/2024"),
    ]


def test_group_rows_by_month_and_size(repo, make_item):
    repo.upsert_many(
        [
            make_item("a.jpg", date_taken="2026-03-31", size_bytes=1 << 30),
            make_item("b.jpg", date_taken="2026-03-01", size_bytes=(1 << 30) - 1),
            make_item("c.jpg", date_taken="2025-03-01", size_bytes=0),
            make_item("n.jpg", date_taken=None, size_bytes=None),
        ]
    )
    by_date = library_rows(repo, LibraryQuery(sort="date", descending=True))
    assert [(g.label, g.count) for g in group_rows(by_date, "date")] == [
        ("March 2026", 2),
        ("March 2025", 1),
        ("No date", 1),
    ]
    by_size = library_rows(repo, LibraryQuery(sort="size", descending=True))
    assert [(g.label, g.count) for g in group_rows(by_size, "size")] == [
        ("1 GB and over", 1),
        ("100 MB to 1 GB", 1),
        ("Under 1 MB", 2),
    ]
    assert group_rows([], "date") == []


def test_offloaded_rows_are_flagged(repo, make_item):
    from smart_gallery.mirror.types import Peer

    _add(repo, make_item, "a/x.jpg", "a/y.jpg")
    repo.add_peer("working", Peer("d1", "D:/g.db"))
    repo.add_offloaded(["A/X.jpg"], "d1")  # matched ignoring case, like relpath

    rows = {r.relpath: r.offloaded for r in library_rows(repo, LibraryQuery())}

    assert rows == {"a/x.jpg": True, "a/y.jpg": False}


def test_unmigrated_catalog_loads_without_offloaded_table(repo, make_item):
    _add(repo, make_item, "a/x.jpg")
    repo.conn.execute("DROP TABLE mirror_offloaded")
    repo.conn.commit()

    (row,) = library_rows(repo, LibraryQuery())

    assert row.relpath == "a/x.jpg" and row.offloaded is False
