"""Long-running services report named steps with real counts, and copy steps
split finished files into copied / skipped / failed."""

from smart_gallery.analysis import extract
from smart_gallery.db import GalleryRepository
from smart_gallery.models import MediaItem
from smart_gallery.organize import Options, organize_items
from smart_gallery.progress import COPYING, FINDING, READING, ProgressEvent
from smart_gallery.services import export_media, import_media, init_drive, sync_drive


def _steps(events):
    seen = []
    for event in events:
        if event.step not in seen:
            seen.append(event.step)
    return seen


def _last(events, step):
    return [e for e in events if e.step == step][-1]


def test_reading_metadata_counts_files_as_batches_finish(tmp_path, monkeypatch):
    files = [tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "notes.txt"]
    for f in files:
        f.write_bytes(b"x")
    # Stand in for the ExifTool binary: one record per file in the batch.
    monkeypatch.setattr(
        extract,
        "run_exiftool_batch",
        lambda batch, profile: [{"SourceFile": str(p)} for p in batch],
    )
    events = []
    extract.analyze_paths(files, progress_callback=events.append)
    assert {e.step for e in events} == {READING}
    assert {e.total for e in events} == {3}
    assert events[0].done == 1  # the .txt is recorded without ExifTool
    assert events[-1] == ProgressEvent(READING, 3, 3)


def test_copying_splits_files_into_copied_skipped_and_failed(tmp_path, drive):
    src = tmp_path / "card"
    src.mkdir()
    (src / "new.txt").write_bytes(b"1")
    (src / "dup.txt").write_bytes(b"2")
    items = [
        MediaItem.from_path(src / "new.txt"),
        MediaItem.from_path(src / "dup.txt"),
        MediaItem.from_path(src / "gone.txt"),  # vanished before the copy
    ]
    (drive / "No Info").mkdir()
    (drive / "No Info" / "dup.txt").write_bytes(b"old")

    events = []
    report = organize_items(
        items, drive, drive, Options(on_exist="skip"), progress_callback=events.append
    )
    assert (report.copied, report.skipped, len(report.excluded)) == (1, 1, 1)
    assert events[-1] == ProgressEvent(
        COPYING, 3, 3, outcomes=(("copied", 1), ("skipped", 1), ("failed", 1))
    )


def test_export_keeps_going_past_files_it_cannot_copy(tmp_path, drive, drive_file):
    drive_file(drive, "ok/a.txt")
    drive_file(drive, "blocked/b.txt")
    drive_file(drive, "gone/c.txt")
    init_drive(drive)
    (drive / "gone" / "c.txt").unlink()
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "blocked").write_bytes(b"a file where a folder is needed")

    events = []
    with GalleryRepository.open(drive, read_only=True) as repo:
        report = export_media(
            repo, dest, mirror=True, manifest=False, progress_callback=events.append
        )
    assert report.copied == 1
    assert len(report.excluded) == 2
    assert (dest / "ok" / "a.txt").exists()
    assert events[-1] == ProgressEvent(
        COPYING, 3, 3, outcomes=(("copied", 1), ("skipped", 0), ("failed", 2))
    )


def test_init_finds_files_then_reads_their_metadata(drive, drive_file):
    for name in ("a.txt", "b.txt", "sub/c.txt"):
        drive_file(drive, name)
    events = []
    init_drive(drive, progress_callback=events.append)
    assert _steps(events) == [FINDING, READING]
    assert _last(events, FINDING) == ProgressEvent(FINDING, 3, 3)
    assert _last(events, READING) == ProgressEvent(READING, 3, 3)


def test_sync_reads_metadata_only_for_changed_files(drive, drive_file):
    drive_file(drive, "a.txt")
    drive_file(drive, "b.txt")
    init_drive(drive)
    drive_file(drive, "c.txt")
    events = []
    with GalleryRepository.open(drive) as repo:
        sync_drive(repo, progress_callback=events.append)
    assert _steps(events) == [FINDING, READING]
    assert _last(events, FINDING) == ProgressEvent(FINDING, 3, 3)
    assert _last(events, READING) == ProgressEvent(READING, 1, 1)


def test_import_reads_metadata_then_copies(tmp_path, repo):
    src = tmp_path / "card"
    src.mkdir()
    for name in ("a.txt", "b.txt"):
        (src / name).write_bytes(b"x")
    events = []
    import_media(repo, [src], progress_callback=events.append)
    assert _steps(events) == [READING, COPYING]
    assert events[-1] == ProgressEvent(
        COPYING, 2, 2, outcomes=(("copied", 2), ("skipped", 0), ("failed", 0))
    )
