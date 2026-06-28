"""init — create a catalog on a drive and populate it with a full scan."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from smart_gallery.analysis import analyze_paths
from smart_gallery.config import iter_files, resolve_db_path
from smart_gallery.db import GalleryRepository


@dataclass
class InitReport:
    db_path: Path
    indexed: int


def init_drive(
    target,
    *,
    label: Optional[str] = None,
    hashing: bool = False,
    overwrite: bool = False,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> InitReport:
    """Create ``<target>/.smart_gallery/gallery.db`` and index every file under it."""
    db_path = resolve_db_path(target)
    if db_path.exists() and not overwrite:
        raise FileExistsError(
            f"A catalog already exists at {db_path}. Use overwrite=True / --overwrite "
            f"to re-create it, or run `sync` to update it."
        )
    if db_path.exists() and overwrite:
        db_path.unlink()

    repo = GalleryRepository.create(target, label=label, hashing=hashing)
    try:
        files = list(iter_files(repo.drive_root))
        logger.info(f"Indexing {len(files):,} files under {repo.drive_root}...")
        items = analyze_paths(
            files, drive_root=repo.drive_root, progress_callback=progress_callback
        )
        repo.upsert_many(items)
        logger.success(f"Catalog initialized: {db_path} ({len(items):,} items)")
        return InitReport(db_path=db_path, indexed=len(items))
    finally:
        repo.close()
