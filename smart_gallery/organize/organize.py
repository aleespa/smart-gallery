"""Folder-template placement engine.

Given media items (each carrying its source ``abspath`` and metadata), copy each
into a destination tree built from a structure like ``Year/Month/Model/Lens`` and
return the placed items re-anchored to the drive. Copy-only — never moves or
deletes the source. Shared by the import and export services.
"""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Literal, Optional

from loguru import logger

from smart_gallery import progress
from smart_gallery.config import PARTIAL_SUFFIX
from smart_gallery.models import MediaItem
from smart_gallery.progress import (
    COPYING,
    Cancelled,
    ProgressCallback,
    ProgressEvent,
)

StructurePart = Literal["Year", "Month", "Model", "Lens"]

# Extra "this destination is already in use" check beside the disk (e.g. a
# catalog row whose file is offloaded to a mirror).
Taken = Callable[[Path], bool]

# A stop request is checked between chunks, so cancelling never waits for a
# multi-GB video to finish copying.
COPY_CHUNK = 8 * 1024 * 1024


@dataclass
class Options:
    by_media_type: bool = True
    structure: List[str] = field(default_factory=lambda: ["Year", "Month"])
    on_exist: Literal["rename", "skip"] = "rename"
    dry_run: bool = False
    verbose: bool = True


@dataclass
class OrganizeReport:
    placed: List[MediaItem] = field(default_factory=list)
    copied: int = 0
    skipped: int = 0
    excluded: List[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def total(self) -> int:
        return self.copied + self.skipped + len(self.excluded)


def target_dir_for(output: Path, options: Options, item: MediaItem) -> Path:
    """Compute the destination directory for an item under ``output``."""
    output = Path(output)
    is_image = item.media_type == "image"
    is_video = item.media_type == "video"

    target = output
    if options.by_media_type and (is_image or is_video):
        target = target / ("Photos" if is_image else "Videos")

    meta = item.metadata_for_organization()
    if meta is None:
        return target / "No Info"

    if is_video and not options.structure:
        return target / "No Info"

    for part in options.structure:
        value = meta.get(part)
        if value is not None:
            target = target / value
        else:
            target = target / "No Info"
            break
    return target


def _unique_destination(
    target_dir: Path,
    filename: str,
    on_exist: str,
    taken: Optional[Taken] = None,
) -> Optional[Path]:
    """Resolve the final destination path, applying the on_exist policy.
    Returns None when the policy is 'skip' and the file already exists.
    ``taken`` marks further destinations as in use besides existing files."""

    def used(path: Path) -> bool:
        return path.exists() or (taken is not None and taken(path))

    destination = target_dir / filename
    if not used(destination):
        return destination
    if on_exist == "skip":
        return None
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 1
    while used(destination):
        destination = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    return destination


def organize_items(
    items: List[MediaItem],
    output: Path,
    drive_root: Path,
    options: Options,
    progress_callback: Optional[ProgressCallback] = None,
    taken: Optional[Taken] = None,
) -> OrganizeReport:
    """Place each item under ``output`` and return them anchored to ``drive_root``.
    ``taken`` marks destinations in use beyond existing files; ``on_exist``
    applies to them too."""
    report = OrganizeReport()
    output = Path(output)

    for done, item in enumerate(items, 1):
        try:
            _place(item, output, drive_root, options, report, taken)
            if progress_callback:
                progress_callback(
                    copy_progress(done, len(items), report.copied, report.skipped)
                )
        except Cancelled:
            # Every item placed so far is complete (a copy cut short leaves
            # no file behind); report them, then stop.
            report.cancelled = True
            break

    logger.info(
        f"Organize report — copied={report.copied} skipped={report.skipped} "
        f"excluded={len(report.excluded)} into {output}"
    )
    return report


def copy_atomic(source: Path, destination: Path) -> None:
    """Copy like ``shutil.copy2`` but never leave a truncated ``destination``:
    copy to a partial file beside it, then rename into place. Raises
    ``Cancelled`` between chunks once a stop is requested."""
    source, destination = Path(source), Path(destination)
    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
    try:
        with open(source, "rb") as src, open(partial, "wb") as dst:
            chunk = src.read(COPY_CHUNK)
            while chunk:
                dst.write(chunk)
                chunk = src.read(COPY_CHUNK)
                if chunk:
                    progress.check_cancelled()
        shutil.copystat(source, partial)
        os.replace(partial, destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def copy_progress(done: int, total: int, copied: int, skipped: int) -> ProgressEvent:
    """A copy step's progress; whatever was neither copied nor skipped failed."""
    failed = done - copied - skipped
    return ProgressEvent(
        COPYING,
        done,
        total,
        outcomes=(("copied", copied), ("skipped", skipped), ("failed", failed)),
    )


def _place(
    item: MediaItem,
    output: Path,
    drive_root: Path,
    options: Options,
    report: OrganizeReport,
    taken: Optional[Taken] = None,
) -> None:
    if not item.abspath:
        report.excluded.append(item.relpath or item.name or "<unknown>")
        return
    source = Path(item.abspath)
    try:
        target_dir = target_dir_for(output, options, item)
        filename = source.name

        if options.dry_run:
            destination = target_dir / filename
            item.relocate(destination, drive_root)
            report.placed.append(item)
            report.copied += 1
            return

        target_dir.mkdir(parents=True, exist_ok=True)
        destination = _unique_destination(target_dir, filename, options.on_exist, taken)

        if destination is None:  # skip: already present on the drive
            destination = target_dir / filename
            report.skipped += 1
            if not destination.exists():
                # Taken without a file here (e.g. offloaded): leave its row be.
                if options.verbose:
                    logger.debug(f"Skipped (name in use): {destination}")
                return
            item.relocate(destination, drive_root)
            report.placed.append(item)
            if options.verbose:
                logger.debug(f"Skipped (already exists): {destination}")
            return

        copy_atomic(source, destination)
        item.relocate(destination, drive_root)
        report.placed.append(item)
        report.copied += 1
        if options.verbose:
            logger.debug(f"Copied: {destination}")
    except OSError as exc:
        logger.error(f"Failed to place {source}: {exc}")
        report.excluded.append(str(source))
