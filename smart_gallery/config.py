"""Drive/DB path resolution, file classification, and scan constants.

A "drive root" is whatever directory you point smart_gallery at (``E:\\`` in
normal use, but any directory works — handy for tests). Its catalog lives at
``<root>/.smart_gallery/gallery.db``. Relative paths in the database are stored
relative to that root, so the catalog survives the drive being remounted under a
different letter.
"""

import os
from pathlib import Path
from typing import Iterator, Set

DB_DIRNAME = ".smart_gallery"
DB_FILENAME = "gallery.db"
SCHEMA_VERSION = 1

IMAGE_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".webp", ".cr2", ".cr3"}
VIDEO_EXTENSIONS: Set[str] = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".gif"}

# Directory names never indexed (compared case-insensitively). The catalog
# folder is skipped so the DB and its -wal/-shm sidecars never index themselves.
IGNORE_DIRS: Set[str] = {
    DB_DIRNAME.lower(),
    "$recycle.bin",
    "system volume information",
    "found.000",
    ".trash",
    "@eadir",
}


def resolve_db_path(target) -> Path:
    """Resolve a drive/root, any path on it, or a direct ``.db`` path to the
    canonical catalog path ``<root>/.smart_gallery/gallery.db``."""
    p = Path(target).resolve()
    if p.suffix.lower() == ".db":
        return p
    return p / DB_DIRNAME / DB_FILENAME


def drive_root_of(db_path) -> Path:
    """The root directory a catalog belongs to, derived live from its location."""
    db_path = Path(db_path).resolve()
    if db_path.parent.name == DB_DIRNAME:
        return db_path.parent.parent
    return Path(db_path.anchor)


def classify(ext: str) -> str:
    """Classify a file extension as ``image``, ``video`` or ``other``."""
    ext = ext.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "other"


def to_relpath(abs_path, drive_root) -> str:
    """Absolute path -> POSIX path relative to ``drive_root``."""
    abs_path = Path(abs_path).resolve()
    drive_root = Path(drive_root).resolve()
    try:
        return abs_path.relative_to(drive_root).as_posix()
    except ValueError:
        return abs_path.as_posix()


def to_abspath(relpath: str, drive_root) -> Path:
    """POSIX relpath + drive root -> absolute path."""
    return Path(drive_root) / Path(relpath)


def iter_files(root, ignore_dirs: Set[str] = IGNORE_DIRS) -> Iterator[Path]:
    """Yield every file under ``root``, pruning ignored directories. Walk only —
    no metadata is read here."""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in ignore_dirs]
        for filename in filenames:
            yield Path(dirpath) / filename
