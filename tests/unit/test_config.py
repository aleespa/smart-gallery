from pathlib import Path

from smart_gallery.config import (
    DB_DIRNAME,
    DB_FILENAME,
    PARTIAL_SUFFIX,
    classify,
    drive_root_of,
    iter_files,
    resolve_db_path,
    to_abspath,
    to_relpath,
)


def test_resolve_db_path_from_directory(tmp_path):
    db = resolve_db_path(tmp_path)
    assert db.name == DB_FILENAME
    assert db.parent.name == DB_DIRNAME
    assert db.parent.parent == tmp_path.resolve()


def test_resolve_db_path_passthrough_for_db_file(tmp_path):
    explicit = tmp_path / "custom.db"
    assert resolve_db_path(explicit) == explicit.resolve()


def test_drive_root_of_roundtrip(tmp_path):
    db = resolve_db_path(tmp_path)
    assert drive_root_of(db) == tmp_path.resolve()


def test_classify():
    assert classify(".JPG") == "image"
    assert classify(".mp4") == "video"
    assert classify(".txt") == "other"


def test_relpath_roundtrip(tmp_path):
    p = tmp_path / "a" / "b" / "c.jpg"
    rel = to_relpath(p, tmp_path)
    assert rel == "a/b/c.jpg"
    assert to_abspath(rel, tmp_path) == tmp_path / "a" / "b" / "c.jpg"


def test_iter_files_prunes_catalog_dir(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    catalog = tmp_path / DB_DIRNAME
    catalog.mkdir()
    (catalog / "gallery.db").write_text("db")
    found = {p.name for p in iter_files(tmp_path)}
    assert found == {"keep.txt"}


def test_iter_files_skips_partials(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "a.jpg.sg-partial").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "B.JPG.SG-PARTIAL").write_bytes(b"x")
    (tmp_path / "sub" / "b.jpg").write_bytes(b"x")
    assert PARTIAL_SUFFIX == ".sg-partial"
    assert sorted(p.name for p in iter_files(tmp_path)) == ["a.jpg", "b.jpg"]
