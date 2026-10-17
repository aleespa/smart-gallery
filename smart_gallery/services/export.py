"""export — copy a filtered subset of the catalog out to another directory.

Metadata comes entirely from the database; the drive is never re-analyzed. The
destination layout is customizable from the call: pass an ``Options`` to lay
files out by ``Year/Month`` (or ``Camera``/``Lens``), or set ``mirror=True`` to
preserve each file's relative path on the drive.
"""

import csv
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from loguru import logger

from smart_gallery.config import resolve_db_path, to_abspath
from smart_gallery.db import GalleryRepository
from smart_gallery.models import MediaItem
from smart_gallery.organize import FilterOptions, Options, target_dir_for
from smart_gallery.organize.organize import _unique_destination, copy_progress
from smart_gallery.progress import ProgressCallback

MANIFEST_NAME = "_smart_gallery_manifest.csv"
_MANIFEST_FIELDS = [
    "relpath",
    "media_type",
    "date_taken",
    "camera",
    "lens",
    "size_bytes",
]


@dataclass
class ExportReport:
    matched: int = 0
    copied: int = 0
    skipped: int = 0
    excluded: List[str] = field(default_factory=list)
    manifest_path: Optional[Path] = None
    portable_db_path: Optional[Path] = None


def _destination(dest: Path, item: MediaItem, options: Optional[Options], mirror: bool):
    """Return (target_dir, filename) for an item under ``dest``."""
    filename = Path(item.relpath).name
    if mirror or options is None:
        return (dest / item.relpath).parent, filename
    return target_dir_for(dest, options, item), filename


def export_media(
    repo: GalleryRepository,
    dest,
    *,
    filters: Optional[FilterOptions] = None,
    options: Optional[Options] = None,
    mirror: bool = False,
    on_exist: str = "rename",
    dry_run: bool = False,
    manifest: bool = True,
    portable_db: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> ExportReport:
    dest = Path(dest)
    items = repo.query(filters)
    report = ExportReport(matched=len(items))
    placed: List[MediaItem] = []

    for done, item in enumerate(items, 1):
        _export_one(
            repo, item, dest, options, mirror, on_exist, dry_run, report, placed
        )
        if progress_callback:
            progress_callback(
                copy_progress(done, len(items), report.copied, report.skipped)
            )

    if not dry_run and placed:
        if manifest:
            report.manifest_path = _write_manifest(dest, placed)
        if portable_db:
            report.portable_db_path = _write_portable_db(dest, placed)

    logger.success(
        f"Export complete — matched={report.matched} copied={report.copied} "
        f"skipped={report.skipped} excluded={len(report.excluded)} -> {dest}"
    )
    return report


def _export_one(repo, item, dest, options, mirror, on_exist, dry_run, report, placed):
    """Copy one item; a file that can't be read or written counts as excluded
    (failed) instead of stopping the whole export."""
    src = (
        Path(item.abspath)
        if item.abspath
        else to_abspath(item.relpath, repo.drive_root)
    )
    if not src.exists():
        logger.error(f"Missing on the drive, not exported: {src}")
        report.excluded.append(str(src))
        return

    target_dir, filename = _destination(dest, item, options, mirror)
    if dry_run:
        item.relocate(target_dir / filename, dest)
        placed.append(item)
        report.copied += 1
        return

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        final = _unique_destination(target_dir, filename, on_exist)
        if final is None:  # skip
            logger.debug(f"Skipped (already exists): {target_dir / filename}")
            report.skipped += 1
            return
        shutil.copy2(src, final)
    except OSError as exc:
        logger.error(f"Failed to export {src}: {exc}")
        report.excluded.append(str(src))
        return
    logger.debug(f"Copied: {final}")
    item.relocate(final, dest)
    placed.append(item)
    report.copied += 1


def _write_manifest(dest: Path, placed: List[MediaItem]) -> Path:
    path = dest / MANIFEST_NAME
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_MANIFEST_FIELDS)
        writer.writeheader()
        for item in placed:
            writer.writerow({k: getattr(item, k) for k in _MANIFEST_FIELDS})
    return path


def _write_portable_db(dest: Path, placed: List[MediaItem]) -> Path:
    """Write a self-contained catalog at the destination (relpaths now relative
    to ``dest``), so the exported set is independently browsable/dashboardable."""
    repo = GalleryRepository.create(dest, label="export")
    try:
        repo.insert_many(placed)
    finally:
        repo.close()
    return resolve_db_path(dest)
