"""The recycle service and command: catalog files go to the Recycle Bin."""

import sys

import pytest

from smart_gallery import cli, recycle_bin
from smart_gallery.progress import RECYCLING, Cancelled
from smart_gallery.services import recycle
from smart_gallery.services.recycle import (
    RecycleEntry,
    read_list,
    recycle_files,
    write_list,
)


@pytest.fixture
def recorder(monkeypatch):
    """Stand-in for the Recycle Bin: records each path and unlinks the file."""
    calls = []

    def fake_move(path):
        calls.append(path)
        path.unlink()

    monkeypatch.setattr(recycle, "supports_recycle_bin", lambda path: True)
    monkeypatch.setattr(recycle, "move_to_recycle_bin", fake_move)
    return calls


def _catalogued(repo, drive, drive_file, make_item, relpath, content=b"x" * 16):
    """A real file on the drive and a catalog row that agrees with it."""
    path = drive_file(drive, relpath, content)
    st = path.stat()
    repo.upsert_many(
        [make_item(relpath, size_bytes=st.st_size, mtime_ns=st.st_mtime_ns)]
    )
    return path, RecycleEntry(relpath, st.st_size, st.st_mtime_ns)


def test_recycles_and_drops_rows(repo, drive, drive_file, make_item, recorder):
    path_a, a = _catalogued(repo, drive, drive_file, make_item, "x/a.jpg", b"a" * 10)
    path_b, b = _catalogued(repo, drive, drive_file, make_item, "x/b.cr3", b"b" * 25)
    path_c, _ = _catalogued(repo, drive, drive_file, make_item, "keep/c.jpg")

    report = recycle_files(repo, [a, b])

    assert (report.recycled, report.skipped, report.failures) == (2, 0, [])
    assert report.freed_bytes == 10 + 25
    assert repo.index_for_sync().keys() == {"keep/c.jpg"}
    assert not path_a.exists() and not path_b.exists() and path_c.exists()
    assert len(recorder) == 2


def test_changed_or_missing_file_is_skipped(
    repo, drive, drive_file, make_item, recorder
):
    path_a, a = _catalogued(repo, drive, drive_file, make_item, "a.jpg", b"a" * 10)
    path_b, b = _catalogued(repo, drive, drive_file, make_item, "b.jpg", b"b" * 10)
    path_c = drive_file(drive, "c.jpg", b"c" * 10)
    st_c = path_c.stat()
    c = RecycleEntry("c.jpg", st_c.st_size, st_c.st_mtime_ns)  # no catalog row

    path_a.write_bytes(b"a" * 11)  # rewritten: size differs
    path_b.unlink()  # gone

    report = recycle_files(repo, [a, b, c])

    assert (report.recycled, report.skipped, report.freed_bytes) == (0, 3, 0)
    assert recorder == []
    assert repo.index_for_sync().keys() == {"a.jpg", "b.jpg"}
    assert path_a.exists() and path_c.exists()


def test_mtime_mismatch_against_catalog_or_disk_is_skipped(
    repo, drive, drive_file, make_item, recorder
):
    _, a = _catalogued(repo, drive, drive_file, make_item, "a.jpg")
    _, b = _catalogued(repo, drive, drive_file, make_item, "b.jpg")
    # The list disagrees with disk and catalog about a's mtime; b has no mtime.
    stale = RecycleEntry(a.relpath, a.size_bytes, a.mtime_ns + 1)
    unknown = RecycleEntry(b.relpath, b.size_bytes, None)

    report = recycle_files(repo, [stale, unknown])

    assert (report.recycled, report.skipped) == (0, 2)
    assert recorder == []


def test_folder_is_never_recycled(repo, drive, make_item, recorder):
    folder = drive / "album"
    folder.mkdir()
    (folder / "inside.jpg").write_bytes(b"x")
    st = folder.stat()
    repo.upsert_many(
        [make_item("album", size_bytes=st.st_size, mtime_ns=st.st_mtime_ns)]
    )

    report = recycle_files(repo, [RecycleEntry("album", st.st_size, st.st_mtime_ns)])

    assert report.skipped == 1
    assert recorder == []
    assert (folder / "inside.jpg").exists()


def test_refuses_without_recycle_bin(
    repo, drive, drive_file, make_item, recorder, monkeypatch
):
    path_a, a = _catalogued(repo, drive, drive_file, make_item, "a.jpg")
    monkeypatch.setattr(recycle, "supports_recycle_bin", lambda path: False)

    with pytest.raises(ValueError, match="Recycle Bin is not available"):
        recycle_files(repo, [a])

    assert recorder == []
    assert repo.index_for_sync().keys() == {"a.jpg"}
    assert path_a.exists()


def test_failure_keeps_row(repo, drive, drive_file, make_item, monkeypatch):
    path_a, a = _catalogued(repo, drive, drive_file, make_item, "a.jpg")
    path_b, b = _catalogued(repo, drive, drive_file, make_item, "b.jpg")

    def flaky_move(path):
        if path.name == "a.jpg":
            raise OSError("Recycle Bin refused")
        path.unlink()

    monkeypatch.setattr(recycle, "supports_recycle_bin", lambda path: True)
    monkeypatch.setattr(recycle, "move_to_recycle_bin", flaky_move)

    report = recycle_files(repo, [a, b])

    assert report.recycled == 1
    assert len(report.failures) == 1 and "a.jpg" in report.failures[0]
    assert repo.index_for_sync().keys() == {"a.jpg"}
    assert path_a.exists() and not path_b.exists()


def test_progress_events_carry_outcomes(repo, drive, drive_file, make_item, recorder):
    _, a = _catalogued(repo, drive, drive_file, make_item, "a.jpg")
    missing = RecycleEntry("gone.jpg", 1, 1)
    events = []

    recycle_files(repo, [a, missing], progress_callback=events.append)

    assert all(e.step == RECYCLING and e.total == 2 for e in events)
    last = events[-1]
    assert last.done == 2
    assert last.outcomes == (("recycled", 1), ("skipped", 1), ("failed", 0))


def test_cancel_keeps_catalog_in_step(repo, drive, drive_file, make_item, recorder):
    path_a, a = _catalogued(repo, drive, drive_file, make_item, "a.jpg")
    path_b, b = _catalogued(repo, drive, drive_file, make_item, "b.jpg")
    path_c, c = _catalogued(repo, drive, drive_file, make_item, "c.jpg")

    def cb(event):
        if event.done >= 1:
            raise Cancelled()

    with pytest.raises(Cancelled):
        recycle_files(repo, [a, b, c], progress_callback=cb)

    assert not path_a.exists()
    assert path_b.exists() and path_c.exists()
    assert repo.index_for_sync().keys() == {"b.jpg", "c.jpg"}


def test_read_list_utf8(tmp_path):
    p = tmp_path / "list.txt"
    entries = [
        RecycleEntry("Año 2026/IMG_1.CR3", 10, 5),
        RecycleEntry("x.JPG", 3, None),
    ]
    write_list(p, entries)
    assert read_list(p) == entries


def test_read_list_skips_blank_lines(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("a.jpg\t1\t2\n\n  \nb.jpg\t3\t\n", encoding="utf-8")
    assert read_list(p) == [RecycleEntry("a.jpg", 1, 2), RecycleEntry("b.jpg", 3, None)]


def test_cli_recycle(
    repo, drive, drive_file, make_item, recorder, monkeypatch, tmp_path
):
    path_a, a = _catalogued(repo, drive, drive_file, make_item, "a.jpg")
    _catalogued(repo, drive, drive_file, make_item, "b.jpg")
    list_file = tmp_path / "recycle.txt"
    write_list(list_file, [a])

    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "logs")

    cli.main(["recycle", str(drive), "--list", str(list_file)])

    assert not path_a.exists()
    assert repo.index_for_sync().keys() == {"b.jpg"}


def test_cli_recycle_refuses_with_exit_1(
    repo, drive, drive_file, make_item, recorder, monkeypatch, tmp_path
):
    path_a, a = _catalogued(repo, drive, drive_file, make_item, "a.jpg")
    list_file = tmp_path / "recycle.txt"
    write_list(list_file, [a])
    monkeypatch.setattr(recycle, "supports_recycle_bin", lambda path: False)
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "logs")

    with pytest.raises(SystemExit) as exc:
        cli.main(["recycle", str(drive), "--list", str(list_file)])

    assert exc.value.code == 1
    assert path_a.exists()


def test_unsupported_drives(tmp_path, monkeypatch):
    monkeypatch.setattr(recycle_bin.sys, "platform", "linux")
    assert recycle_bin.supports_recycle_bin(tmp_path) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Recycle Bin")
def test_real_recycle_bin(tmp_path):
    if not recycle_bin.supports_recycle_bin(tmp_path):
        pytest.skip(f"No Recycle Bin on {tmp_path.anchor}")
    target = tmp_path / "smart_gallery_recycle_test.txt"
    target.write_text("safe to delete: smart_gallery test file", encoding="utf-8")

    recycle_bin.move_to_recycle_bin(target)

    assert not target.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Recycle Bin")
def test_real_recycle_bin_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        recycle_bin.move_to_recycle_bin(tmp_path / "does-not-exist.txt")
