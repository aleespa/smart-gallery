"""Import into the working drive and every mirror in one pass."""

import importlib
from pathlib import Path

import pytest

from smart_gallery.db import GalleryRepository
from smart_gallery.models import MediaItem
from smart_gallery.organize import Options
from smart_gallery.organize import organize as organize_module
from smart_gallery.organize.filters import FilterOptions
from smart_gallery.services import import_media, init_drive, pair_galleries
from smart_gallery.services import mirror as mirror_service

# ``smart_gallery.services.import_media`` is shadowed by the function of the
# same name, so fetch the module itself.
import_module = importlib.import_module("smart_gallery.services.import_media")

FLAT = Options(by_media_type=False, structure=[], on_exist="skip")
VIDEO = FilterOptions(filetypes=["video"])


def _fake_analyze(paths, drive_root=None, progress_callback=None, **_):
    """Stands in for ExifTool: stat-only items; ``.mp4`` files are videos."""
    items = []
    for p in paths:
        item = MediaItem.from_path(p, drive_root)
        if Path(p).suffix.lower() == ".mp4":
            item.media_type = "video"
        items.append(item)
    return items


@pytest.fixture(autouse=True)
def _no_exiftool(monkeypatch):
    monkeypatch.setattr(import_module, "analyze_paths", _fake_analyze)


@pytest.fixture
def sd(tmp_path, drive_file):
    source = tmp_path / "sd"
    drive_file(source, "a.txt", b"alpha")
    return source


def _paired(*roots):
    """Open the working drive (first root) and pair every other root with it."""
    working = GalleryRepository.open(roots[0])
    mirrors = []
    for root in roots[1:]:
        m = GalleryRepository.open(root)
        pair_galleries(working, m)
        mirrors.append(m)
    return working, mirrors


def _uuid(repo):
    return repo.get_meta("db_uuid")


def _row(repo, rel):
    return repo.conn.execute(
        "SELECT relpath, size_bytes FROM media_items WHERE relpath=?", (rel,)
    ).fetchone()


def _under(root, path):
    return Path(root).resolve() in Path(path).resolve().parents


def _spy_copies(monkeypatch, fail_under=None):
    """Record every ``copy_atomic`` (source, destination); raise OSError for
    destinations under ``fail_under``."""
    calls = []
    real = organize_module.copy_atomic

    def spy(source, destination):
        calls.append((Path(source), Path(destination)))
        if fail_under is not None and _under(fail_under, destination):
            raise OSError("mirror drive unplugged")
        real(source, destination)

    monkeypatch.setattr(organize_module, "copy_atomic", spy)
    monkeypatch.setattr(mirror_service, "copy_atomic", spy)
    return calls


def test_import_lands_on_both_drives_with_same_relpath(two_drives, sd):
    e, d = two_drives
    w, (m,) = _paired(e, d)
    with w, m:
        report = import_media(w, [sd], options=FLAT, mirrors=[m])
        assert (e / "No Info" / "a.txt").read_bytes() == b"alpha"
        assert (d / "No Info" / "a.txt").read_bytes() == b"alpha"
        assert _row(w, "No Info/a.txt") is not None
        assert _row(m, "No Info/a.txt") is not None
        assert report.mirrored == {_uuid(m): 1}
        assert report.mirror_pending == {_uuid(m): 0}
        assert report.copied == 1 and report.inserted == 1
        src = (sd / "a.txt").stat().st_mtime_ns
        assert (d / "No Info" / "a.txt").stat().st_mtime_ns == src


def test_import_without_mirror_is_unchanged(two_drives, sd):
    e, d = two_drives
    w, (m,) = _paired(e, d)
    with w, m:
        report = import_media(w, [sd], options=FLAT)
        assert (e / "No Info" / "a.txt").exists()
        assert not (d / "No Info").exists()
        assert report.mirrored == {} and report.mirror_pending == {}
        assert report.copied == 1 and report.inserted == 1
        assert _row(m, "No Info/a.txt") is None


def test_mirror_copy_is_read_from_working_copy(two_drives, sd, monkeypatch):
    e, d = two_drives
    calls = _spy_copies(monkeypatch)
    w, (m,) = _paired(e, d)
    with w, m:
        import_media(w, [sd], options=FLAT, mirrors=[m])
    to_mirror = [src for src, dest in calls if _under(d, dest)]
    assert len(to_mirror) == 1
    assert _under(e, to_mirror[0])


def test_mirror_failure_leaves_working_import_and_counts_pending(
    two_drives, sd, monkeypatch
):
    e, d = two_drives
    _spy_copies(monkeypatch, fail_under=d)
    w, (m,) = _paired(e, d)
    with w, m:
        report = import_media(w, [sd], options=FLAT, mirrors=[m])
        assert (e / "No Info" / "a.txt").read_bytes() == b"alpha"
        assert _row(w, "No Info/a.txt") is not None
        assert report.copied == 1 and report.inserted == 1
        assert report.mirror_pending == {_uuid(m): 1}
        assert report.mirrored == {_uuid(m): 0}
        assert not (d / "No Info" / "a.txt").exists()
        assert _row(m, "No Info/a.txt") is None
        assert w.baseline(_uuid(m)) == {}


def test_offloaded_on_arrival(two_drives, sd, drive_file):
    e, d = two_drives
    drive_file(sd, "clip.mp4", b"video-bytes")
    w, (m,) = _paired(e, d)
    with w, m:
        w.set_offload_rules([VIDEO])
        report = import_media(w, [sd], options=FLAT, mirrors=[m])
        assert (d / "No Info" / "clip.mp4").read_bytes() == b"video-bytes"
        assert not (e / "No Info" / "clip.mp4").exists()
        row = _row(w, "No Info/clip.mp4")
        assert row is not None and row["size_bytes"] == len(b"video-bytes")
        assert w.offloaded_on(_uuid(m)) == {"No Info/clip.mp4"}
        assert _row(m, "No Info/clip.mp4") is not None
        # The kept file still lands on both drives.
        assert (e / "No Info" / "a.txt").exists()
        assert (d / "No Info" / "a.txt").exists()
        assert report.mirrored == {_uuid(m): 2}
        assert report.mirror_pending == {_uuid(m): 0}
        assert report.inserted == 2
        assert "no info/clip.mp4" in w.baseline(_uuid(m))


def test_existing_different_file_on_mirror_is_pending_not_overwritten(
    two_drives, sd, drive_file
):
    e, d = two_drives
    drive_file(d, "No Info/a.txt", b"a different, longer file")
    w, (m,) = _paired(e, d)
    with w, m:
        report = import_media(w, [sd], options=FLAT, mirrors=[m])
        assert (d / "No Info" / "a.txt").read_bytes() == b"a different, longer file"
        assert (e / "No Info" / "a.txt").read_bytes() == b"alpha"
        assert report.mirror_pending == {_uuid(m): 1}
        assert report.mirrored == {_uuid(m): 0}
        assert "no info/a.txt" not in w.baseline(_uuid(m))


def test_imported_files_join_the_baseline(two_drives, sd):
    e, d = two_drives
    w, (m,) = _paired(e, d)
    with w, m:
        import_media(w, [sd], options=FLAT, mirrors=[m])
        base = w.baseline(_uuid(m))
        row = base["no info/a.txt"]
        e_st = (e / "No Info" / "a.txt").stat()
        d_st = (d / "No Info" / "a.txt").stat()
        assert row.relpath == "No Info/a.txt"
        assert row.size_bytes == e_st.st_size
        assert row.working_mtime_ns == e_st.st_mtime_ns
        assert row.mirror_mtime_ns == d_st.st_mtime_ns


def test_import_to_two_mirrors_counts_pending_per_mirror(
    two_drives, sd, tmp_path, monkeypatch
):
    e, d = two_drives
    f = tmp_path / "F"
    f.mkdir()
    init_drive(f)
    _spy_copies(monkeypatch, fail_under=f)
    w, (m_d, m_f) = _paired(e, d, f)
    with w, m_d, m_f:
        report = import_media(w, [sd], options=FLAT, mirrors=[m_d, m_f])
        assert (d / "No Info" / "a.txt").read_bytes() == b"alpha"
        assert not (f / "No Info" / "a.txt").exists()
        assert report.mirrored == {_uuid(m_d): 1, _uuid(m_f): 0}
        assert report.mirror_pending == {_uuid(m_d): 0, _uuid(m_f): 1}
        assert "no info/a.txt" in w.baseline(_uuid(m_d))
        assert w.baseline(_uuid(m_f)) == {}


def test_offloaded_item_no_mirror_received_stays_on_working(
    two_drives, sd, drive_file, monkeypatch
):
    e, d = two_drives
    drive_file(sd, "clip.mp4", b"video-bytes")
    _spy_copies(monkeypatch, fail_under=d)
    w, (m,) = _paired(e, d)
    with w, m:
        w.set_offload_rules([VIDEO])
        report = import_media(w, [sd], options=FLAT, mirrors=[m])
        assert (e / "No Info" / "clip.mp4").read_bytes() == b"video-bytes"
        assert not (d / "No Info" / "clip.mp4").exists()
        assert _row(w, "No Info/clip.mp4") is not None
        assert w.offloaded_relpaths() == set()
        assert report.mirror_pending == {_uuid(m): 2}
        assert report.copied == 2 and report.inserted == 2
