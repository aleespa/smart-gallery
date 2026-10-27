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


def test_sync_keeps_offloaded_rows(drive, drive_file):
    from smart_gallery.mirror.types import Peer

    drive_file(drive, "a.txt")
    init_drive(drive)
    (drive / "a.txt").unlink()
    with GalleryRepository.open(drive) as repo:
        assert sync_drive(repo, dry_run=True).deleted == 1
        repo.add_peer("working", Peer("d1", "D:/g.db"))
        repo.add_offloaded(["A.txt"], "d1")
        assert sync_drive(repo).deleted == 0
        assert repo.counts()["total"] == 1


def test_sync_clears_offloaded_flag_when_file_returns(drive, drive_file):
    from smart_gallery.mirror.types import Peer

    drive_file(drive, "a.txt")
    init_drive(drive)
    with GalleryRepository.open(drive) as repo:
        repo.add_peer("working", Peer("d1", "D:/g.db"))
        repo.add_offloaded(["a.txt"], "d1")
        sync_drive(repo, dry_run=True)
        assert repo.offloaded_relpaths() == {"a.txt"}
        sync_drive(repo)
        assert repo.offloaded_relpaths() == set()
