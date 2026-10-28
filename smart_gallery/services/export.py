"""export — copy a filtered subset of the catalog out to another directory.

Metadata comes entirely from the database; the drive is never re-analyzed. The
destination layout is customizable from the call: pass an ``Options`` to lay
files out by ``Year/Month`` (or ``Camera``/``Lens``), or set ``mirror=True`` to
preserve each file's relative path on the drive.

``jpeg_only`` exports photos as JPEGs: JPEG files and videos are copied,
CR2/CR3 files become JPEGs (their embedded image, written by ``convert_cr3``
as ``convert-raw`` does) and everything else is left out. A RAW whose
same-named JPEG is also being exported is left out too, since the camera's
JPEG is already there.
"""

import csv
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from loguru import logger

from smart_gallery.config import VIDEO_EXTENSIONS, resolve_db_path, to_abspath
from smart_gallery.db import GalleryRepository
from smart_gallery.models import MediaItem
from smart_gallery.organize import FilterOptions, Options, target_dir_for
from smart_gallery.organize.organize import (
    _unique_destination,
    copy_atomic,
    copy_progress,
)
from smart_gallery.progress import Cancelled, ProgressCallback
from smart_gallery.services.convert_cr3 import convert_cr3

MANIFEST_NAME = "_smart_gallery_manifest.csv"
_MANIFEST_FIELDS = [
    "relpath",
    "media_type",
    "date_taken",
    "camera",
    "lens",
    "size_bytes",
]
JPEG_EXTS = frozenset({".jpg", ".jpeg"})
RAW_EXTS = frozenset({".cr2", ".cr3"})
RAW_BATCH = 50  # RAWs per ExifTool run: starting ExifTool is the slow part
# RAW copies are converted here (inside the destination, so each JPEG is only
# renamed into place); the folder is removed afterwards.
STAGING_DIRNAME = ".smart_gallery_raw_staging"


@dataclass
class ExportReport:
    matched: int = 0
    copied: int = 0
    skipped: int = 0
    converted: int = 0  # RAWs exported as JPEG (also counted in ``copied``)
    left_out: int = 0  # matched but not JPEG, RAW or video, with ``jpeg_only``
    excluded: List[str] = field(default_factory=list)
    manifest_path: Optional[Path] = None
    portable_db_path: Optional[Path] = None


def _destination(dest: Path, item: MediaItem, options: Optional[Options], mirror: bool):
    """Return (target_dir, filename) for an item under ``dest``."""
    filename = Path(item.relpath).name
    if mirror or options is None:
        return (dest / item.relpath).parent, filename
    return target_dir_for(dest, options, item), filename


def _stem_key(relpath: str) -> Tuple[str, str]:
    path = Path(relpath)
    return path.parent.as_posix().casefold(), path.stem.casefold()


def split_for_jpeg_export(
    relpaths: Iterable[str],
) -> Tuple[List[str], List[str], List[str]]:
    """Sort matched relpaths into ``(as_is, raws, left_out)`` for a JPEG-only
    export: JPEGs and videos are copied as they are, RAWs converted. A RAW
    with a same-named JPEG in the same folder among ``relpaths`` is left out:
    that JPEG is exported instead."""
    relpaths = list(relpaths)
    jpeg_keys = {
        _stem_key(rel) for rel in relpaths if Path(rel).suffix.lower() in JPEG_EXTS
    }
    as_is: List[str] = []
    raws: List[str] = []
    left_out: List[str] = []
    for rel in relpaths:
        ext = Path(rel).suffix.lower()
        if ext in JPEG_EXTS or ext in VIDEO_EXTENSIONS:
            as_is.append(rel)
        elif ext in RAW_EXTS and _stem_key(rel) not in jpeg_keys:
            raws.append(rel)
        else:
            left_out.append(rel)
    return as_is, raws, left_out


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
    jpeg_only: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> ExportReport:
    dest = Path(dest)
    items = repo.query(filters)
    report = ExportReport(matched=len(items))
    placed: List[MediaItem] = []

    raws: List[MediaItem] = []
    if jpeg_only:
        as_is, raw_rels, left_out = split_for_jpeg_export(i.relpath for i in items)
        by_rel = {i.relpath: i for i in items}
        report.left_out = len(left_out)
        items = [by_rel[rel] for rel in as_is]
        raws = [by_rel[rel] for rel in raw_rels]
    # Plain copies first, then RAWs in batches, so a converted RAW's name
    # never takes the place of a JPEG that is copied as it is.
    batches = [raws[i : i + RAW_BATCH] for i in range(0, len(raws), RAW_BATCH)]
    total = len(items) + len(raws)

    def tick(done: int) -> None:
        if progress_callback:
            progress_callback(copy_progress(done, total, report.copied, report.skipped))

    cancelled = False
    done = 0
    try:
        for item in items:
            _export_one(
                repo, item, dest, options, mirror, on_exist, dry_run, report, placed
            )
            done += 1
            tick(done)
        for batch in batches:
            _export_raws(
                repo, batch, dest, options, mirror, on_exist, dry_run, report, placed
            )
            done += len(batch)
            tick(done)
    except Cancelled:  # mid-copy or between items; placed items are complete
        cancelled = True

    if not dry_run and placed:
        if manifest:
            report.manifest_path = _write_manifest(dest, placed)
        if portable_db:
            report.portable_db_path = _write_portable_db(dest, placed)

    if cancelled:
        # What was copied is described on disk; now let the cancel through.
        logger.warning(f"Export cancelled — copied={report.copied} into {dest}")
        raise Cancelled()

    logger.success(
        f"Export complete — matched={report.matched} copied={report.copied} "
        f"skipped={report.skipped} excluded={len(report.excluded)} -> {dest}"
    )
    if jpeg_only:
        logger.info(
            f"JPEG only — {report.converted} RAW file(s) converted to JPEG; "
            f"{report.left_out} other file(s) left out."
        )
    return report


def _source(repo, item: MediaItem) -> Path:
    if item.abspath:
        return Path(item.abspath)
    return to_abspath(item.relpath, repo.drive_root)


def _export_one(repo, item, dest, options, mirror, on_exist, dry_run, report, placed):
    """Copy one item; a file that can't be read or written counts as excluded
    (failed) instead of stopping the whole export."""
    src = _source(repo, item)
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
            # Already there (e.g. a resumed export): still part of the export,
            # so the manifest and portable catalog describe all of it.
            logger.debug(f"Skipped (already exists): {target_dir / filename}")
            item.relocate(target_dir / filename, dest)
            placed.append(item)
            report.skipped += 1
            return
        copy_atomic(src, final)
    except OSError as exc:
        logger.error(f"Failed to export {src}: {exc}")
        report.excluded.append(str(src))
        return
    logger.debug(f"Copied: {final}")
    item.relocate(final, dest)
    placed.append(item)
    report.copied += 1


def _export_raws(
    repo,
    batch: Sequence[MediaItem],
    dest: Path,
    options: Optional[Options],
    mirror: bool,
    on_exist: str,
    dry_run: bool,
    report: ExportReport,
    placed: List[MediaItem],
) -> None:
    """Export each RAW in ``batch`` as a JPEG named after it.

    Each RAW is copied into its own folder under the staging folder, already
    named after its final JPEG, the batch is converted with one
    ``convert_cr3`` call, and each JPEG is then moved into place. The source
    drive is never written. A RAW that yields no JPEG counts as excluded.
    """
    staging = dest / STAGING_DIRNAME
    reserved: set = set()  # names chosen in this batch that aren't on disk yet
    jobs: List[Tuple[MediaItem, Path, Path, Path]] = []  # item, src, staged, final
    try:
        for n, item in enumerate(batch):
            src = _source(repo, item)
            if not src.exists():
                logger.error(f"Missing on the drive, not exported: {src}")
                report.excluded.append(str(src))
                continue
            target_dir, filename = _destination(dest, item, options, mirror)
            name = Path(filename).stem + ".jpg"
            final = _unique_destination(
                target_dir, name, on_exist, lambda p: str(p).casefold() in reserved
            )
            if final is None:  # skip: already exported
                logger.debug(f"Skipped (already exists): {target_dir / name}")
                item.relocate(target_dir / name, dest)
                placed.append(item)
                report.skipped += 1
                continue
            reserved.add(str(final).casefold())
            if dry_run:
                item.relocate(final, dest)
                placed.append(item)
                report.copied += 1
                report.converted += 1
                continue
            staged = staging / str(n) / (final.stem + src.suffix)
            try:
                staged.parent.mkdir(parents=True, exist_ok=True)
                copy_atomic(src, staged)
            except OSError as exc:
                logger.error(f"Failed to export {src}: {exc}")
                report.excluded.append(str(src))
                continue
            jobs.append((item, src, staged, final))

        if not jobs:
            return
        convert_cr3([staged for _item, _src, staged, _final in jobs])
        for item, src, staged, final in jobs:
            jpeg = staged.with_suffix(".jpg")
            try:
                if not jpeg.is_file():
                    raise OSError("no JPEG could be made from it")
                final.parent.mkdir(parents=True, exist_ok=True)
                jpeg.replace(final)
            except OSError as exc:
                logger.error(f"Failed to export {src} as JPEG: {exc}")
                report.excluded.append(str(src))
                continue
            logger.debug(f"Converted: {final}")
            item.relocate(final, dest)
            placed.append(item)
            report.copied += 1
            report.converted += 1
    finally:
        if not dry_run:
            shutil.rmtree(staging, ignore_errors=True)


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
