"""import — copy media from a source (SD card / dump folder) into the drive and
insert only the new items into the catalog.

ExifTool runs once, over just the imported files. Metadata is reused verbatim
for the catalog row; only path/size/mtime are taken from the destination.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from loguru import logger

from smart_gallery.analysis import analyze_paths
from smart_gallery.db import GalleryRepository
from smart_gallery.organize import (
    FilterOptions,
    Options,
    matches,
    organize_items,
)


@dataclass
class ImportReport:
    analyzed: int = 0
    copied: int = 0
    skipped: int = 0
    excluded: int = 0
    inserted: int = 0


def _gather_files(sources: Sequence) -> List[Path]:
    files: List[Path] = []
    for source in sources:
        source = Path(source)
        if source.is_dir():
            files.extend(f for f in source.rglob("*") if f.is_file())
        elif source.is_file():
            files.append(source)
    return files


def import_media(
    repo: GalleryRepository,
    sources: Sequence,
    *,
    output_dir=None,
    options: Optional[Options] = None,
    query: Optional[FilterOptions] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> ImportReport:
    """Import files from ``sources`` into ``output_dir`` (default: the drive root)."""
    options = options or Options()
    output_dir = Path(output_dir) if output_dir else repo.drive_root

    files = _gather_files(sources)
    if not files:
        logger.warning("No files found to import.")
        return ImportReport()

    items = analyze_paths(files, progress_callback=progress_callback)
    if query is not None:
        items = [it for it in items if matches(it, query)]

    report = ImportReport(analyzed=len(items))
    org = organize_items(items, output_dir, repo.drive_root, options)
    report.copied = org.copied
    report.skipped = org.skipped
    report.excluded = len(org.excluded)

    if not options.dry_run and org.placed:
        report.inserted = repo.insert_many(org.placed)

    logger.success(
        f"Import complete — copied={report.copied} skipped={report.skipped} "
        f"inserted={report.inserted}"
    )
    return report
