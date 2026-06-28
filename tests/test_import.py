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
