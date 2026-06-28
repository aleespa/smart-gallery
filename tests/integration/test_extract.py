"""ExifTool-backed integration tests. Run when the binary is on PATH."""

import pytest

from smart_gallery.analysis import analyze_paths
from smart_gallery.db import GalleryRepository
from smart_gallery.services import init_drive, sync_drive

pytestmark = pytest.mark.exiftool


def test_extract_resource_images(images_dir):
    files = list(images_dir.iterdir())
    items = analyze_paths(files, drive_root=images_dir.parent)
    assert items

    by_name = {it.name: it for it in items}
    assert any(it.media_type == "image" for it in items)
    # The CR2/CR3 raws and JPG should all classify as images.
    assert {it.ext for it in items} >= {".jpg"}

    geo = by_name.get("test_with_location")
    assert geo is not None
    assert geo.latitude is not None and geo.longitude is not None


def test_init_and_sync_with_real_images(tmp_path, copy_image):
    drive = tmp_path / "d"
    drive.mkdir()
    copy_image(drive, "test.JPG", "Photos/test.JPG")
    copy_image(drive, "test_with_location.jpg", "Photos/geo.jpg")

    report = init_drive(drive)
    assert report.indexed == 2

    with GalleryRepository.open(drive) as repo:
        assert repo.counts()["image"] == 2
        (drive / "Photos" / "test.JPG").unlink()
        sync_report = sync_drive(repo)
        assert sync_report.deleted == 1
        assert repo.counts()["image"] == 1
