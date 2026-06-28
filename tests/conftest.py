"""Shared pytest fixtures."""

import shutil
from pathlib import Path

import pytest

from smart_gallery.db import GalleryRepository
from smart_gallery.models import MediaItem

RESOURCES = Path(__file__).parent / "resources"
IMAGES = RESOURCES / "images"


@pytest.fixture
def drive(tmp_path) -> Path:
    """A temporary directory used as a 'drive root'."""
    root = tmp_path / "drive"
    root.mkdir()
    return root


@pytest.fixture
def repo(drive) -> GalleryRepository:
    """A fresh catalog created on the temp drive."""
    r = GalleryRepository.create(drive, label="test")
    yield r
    r.close()


@pytest.fixture
def make_item():
    """Factory for MediaItems with sensible image defaults."""

    def _make(relpath, **kwargs):
        defaults = dict(
            media_type="image",
            name=Path(relpath).stem,
            ext=Path(relpath).suffix.lower(),
            directory_rel=str(Path(relpath).parent.as_posix()),
            size_bytes=1_000_000,
            mtime_ns=1_700_000_000_000_000_000,
            date_taken="2026-06-01",
            camera="Canon EOS R6",
            lens="RF24-70mm",
            aperture=2.8,
            iso=400,
            shutter_speed_sec=0.004,
            shutter_speed_text="1/250s",
        )
        defaults.update(kwargs)
        return MediaItem(relpath=relpath, **defaults)

    return _make


@pytest.fixture
def images_dir() -> Path:
    return IMAGES


@pytest.fixture
def images_available() -> bool:
    return IMAGES.exists() and any(IMAGES.iterdir())


@pytest.fixture
def drive_file():
    """Callable: drive_file(root, relpath, content=...) -> Path."""
    return make_drive_file


@pytest.fixture
def copy_image():
    """Callable: copy_image(root, image_name, dest_relpath) -> Path."""
    return copy_image_to


def make_drive_file(root: Path, relpath: str, content: bytes = b"x" * 16) -> Path:
    """Create a real file at root/relpath."""
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def copy_image_to(root: Path, name: str, dest_relpath: str) -> Path:
    src = IMAGES / name
    dest = root / dest_relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest
