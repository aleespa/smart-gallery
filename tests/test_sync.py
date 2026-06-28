from smart_gallery.db import GalleryRepository
from smart_gallery.services import init_drive, sync_drive


def test_sync_add_remove_change(drive, drive_file):
    drive_file(drive, "a.txt")
    drive_file(drive, "sub/b.txt")
    init_drive(drive)

    with GalleryRepository.open(drive) as repo:
        assert repo.counts()["total"] == 2

        drive_file(drive, "c.txt")  # add
        (drive / "a.txt").unlink()  # remove
        drive_file(drive, "sub/b.txt", content=b"y" * 999)  # change (size differs)

        report = sync_drive(repo)
        assert report.added == 1
        assert report.deleted == 1
        assert report.updated == 1
        assert set(repo.query_relpaths()) == {"sub/b.txt", "c.txt"}


def test_sync_idempotent(drive, drive_file):
    drive_file(drive, "a.txt")
    init_drive(drive)
    with GalleryRepository.open(drive) as repo:
        report = sync_drive(repo)
        assert report.changed == 0
        assert report.unchanged == 1


def test_sync_only_analyzes_changed(drive, drive_file, monkeypatch):
    drive_file(drive, "a.txt")
    drive_file(drive, "b.txt")
    init_drive(drive)

    import smart_gallery.services.sync as syncmod

    seen = []
    original = syncmod.analyze_paths

    def spy(paths, **kwargs):
        paths = list(paths)
        seen.append(paths)
        return original(paths, **kwargs)

    monkeypatch.setattr(syncmod, "analyze_paths", spy)

    with GalleryRepository.open(drive) as repo:
        drive_file(drive, "c.txt")  # only this is new
        sync_drive(repo)

    assert len(seen) == 1
    assert {p.name for p in seen[0]} == {"c.txt"}
