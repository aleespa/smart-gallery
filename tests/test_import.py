import importlib
from pathlib import Path

from smart_gallery.db import GalleryRepository
from smart_gallery.organize import Options
from smart_gallery.services import import_media, init_drive


def test_import_copies_and_inserts(drive, tmp_path):
    source = tmp_path / "sd"
    source.mkdir()
    (source / "x.txt").write_bytes(b"hello")
    (source / "y.txt").write_bytes(b"world")

    init_drive(drive)
    options = Options(by_media_type=False, structure=[], on_exist="skip")

    with GalleryRepository.open(drive) as repo:
        report = import_media(repo, [source], options=options)
        assert report.copied == 2
        assert report.inserted == 2
        assert (drive / "No Info" / "x.txt").exists()
        assert repo.counts()["other"] == 2

        # Re-import is idempotent: files already present are skipped, no new rows.
        report2 = import_media(repo, [source], options=options)
        assert report2.copied == 0
        assert report2.skipped == 2
        assert repo.counts()["other"] == 2


def test_import_dry_run_writes_nothing(drive, tmp_path):
    source = tmp_path / "sd"
    source.mkdir()
    (source / "x.txt").write_bytes(b"hello")

    init_drive(drive)
    options = Options(by_media_type=False, structure=[], on_exist="skip", dry_run=True)

    with GalleryRepository.open(drive) as repo:
        report = import_media(repo, [source], options=options)
        assert report.inserted == 0
        assert repo.counts()["total"] == 0
        assert not (drive / "No Info" / "x.txt").exists()


def _fake_analyze(paths, drive_root=None, progress_callback=None, **_):
    """Stands in for ExifTool: stat-only items, reporting READING like the real one."""
    from smart_gallery.models import MediaItem
    from smart_gallery.progress import READING, ProgressEvent

    items = [MediaItem.from_path(p, drive_root) for p in paths]
    if progress_callback:
        progress_callback(ProgressEvent(READING, len(items), len(items)))
    return items


def test_copy_atomic_leaves_no_partial_on_failure(tmp_path, monkeypatch):
    import shutil

    import pytest

    from smart_gallery.organize.organize import copy_atomic

    src = tmp_path / "src.bin"
    src.write_bytes(b"0123456789")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    def fail(*args, **kwargs):  # after the data is written, before the rename
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copystat", fail)
    with pytest.raises(OSError):
        copy_atomic(src, dest_dir / "src.bin")
    assert list(dest_dir.iterdir()) == []


def test_copy_atomic_stops_mid_file_when_cancelled(tmp_path, monkeypatch):
    import pytest

    from smart_gallery import progress
    from smart_gallery.organize.organize import COPY_CHUNK, copy_atomic

    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * (20 * 1024 * 1024))
    assert COPY_CHUNK < src.stat().st_size
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    stop = tmp_path / "stop"
    monkeypatch.setenv(progress.CANCEL_ENV, str(stop))
    checks = []
    real_check = progress.check_cancelled

    def stop_after_first_chunk():
        checks.append(1)
        stop.touch()  # the user clicks Cancel while the first chunk is written
        real_check()

    monkeypatch.setattr(progress, "check_cancelled", stop_after_first_chunk)
    with pytest.raises(progress.Cancelled):
        copy_atomic(src, dest_dir / "big.bin")
    assert len(checks) == 1
    assert list(dest_dir.iterdir()) == []


def test_copy_atomic_copies_content_and_mtime(tmp_path):
    import os

    from smart_gallery.organize.organize import copy_atomic

    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    os.utime(src, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    copy_atomic(src, dest_dir / "out.bin")
    assert (dest_dir / "out.bin").read_bytes() == b"payload"
    assert (dest_dir / "out.bin").stat().st_mtime_ns == src.stat().st_mtime_ns
    assert [p.name for p in dest_dir.iterdir()] == ["out.bin"]


def test_import_cancel_mid_copy_keeps_catalog_consistent(drive, tmp_path, monkeypatch):
    import pytest

    # the package re-exports the function under the module's name
    importmod = importlib.import_module("smart_gallery.services.import_media")
    from smart_gallery.progress import COPYING, Cancelled

    monkeypatch.setattr(importmod, "analyze_paths", _fake_analyze)
    source = tmp_path / "sd"
    source.mkdir()
    for i in range(5):
        (source / f"f{i}.txt").write_bytes(b"data%d" % i)
    init_drive(drive)
    options = Options(by_media_type=False, structure=[], on_exist="skip")

    copying_events = []

    def cancel_on_third_copy(event):
        if event.step == COPYING:
            copying_events.append(event)
            if len(copying_events) == 3:
                raise Cancelled()

    with GalleryRepository.open(drive) as repo:
        with pytest.raises(Cancelled):
            importmod.import_media(
                repo, [source], options=options, progress_callback=cancel_on_third_copy
            )
        copied = {p.name for p in (drive / "No Info").iterdir()}
        assert len(copied) == 3
        assert not list(drive.rglob("*.sg-partial"))
        assert {Path(r).name for r in repo.query_relpaths()} == copied

        report = importmod.import_media(repo, [source], options=options)
        assert report.skipped == 3
        assert report.copied == 2
        assert {Path(r).name for r in repo.query_relpaths()} == {
            f"f{i}.txt" for i in range(5)
        }


def test_gather_files_skips_catalog_folder_and_partials(tmp_path):
    from smart_gallery.services.import_media import _gather_files

    source = tmp_path / "card"
    thumbs = source / ".smart_gallery" / "thumbs" / "0"
    thumbs.mkdir(parents=True)
    (thumbs / "12-1697.jpg").write_bytes(b"thumb")
    (source / "IMG_2.jpg.sg-partial").write_bytes(b"half")
    (source / "IMG_1.jpg").write_bytes(b"photo")

    assert [p.name for p in _gather_files([source])] == ["IMG_1.jpg"]
    # An explicitly named leftover partial is skipped too.
    assert _gather_files([source / "IMG_2.jpg.sg-partial"]) == []
