"""Folder-template placement engine.

Given media items (each carrying its source ``abspath`` and metadata), copy each
into a destination tree built from a structure like ``Year/Month/Model/Lens`` and
return the placed items re-anchored to the drive. Copy-only — never moves or
deletes the source. Shared by the import and export services.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional

from loguru import logger

from smart_gallery.models import MediaItem

StructurePart = Literal["Year", "Month", "Model", "Lens"]


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


def _unique_destination(target_dir: Path, filename: str, on_exist: str) -> Optional[Path]:
    """Resolve the final destination path, applying the on_exist policy.
    Returns None when the policy is 'skip' and the file already exists."""
    destination = target_dir / filename
    if not destination.exists():
        return destination
    if on_exist == "skip":
        return None
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 1
    while destination.exists():
        destination = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    return destination


def organize_items(
    items: List[MediaItem],
    output: Path,
    drive_root: Path,
    options: Options,
) -> OrganizeReport:
    """Place each item under ``output`` and return them anchored to ``drive_root``."""
    report = OrganizeReport()
    output = Path(output)

    for item in items:
        if not item.abspath:
            report.excluded.append(item.relpath or item.name or "<unknown>")
            continue
        source = Path(item.abspath)
        try:
            target_dir = target_dir_for(output, options, item)
            filename = source.name

            if options.dry_run:
                destination = target_dir / filename
                item.relocate(destination, drive_root)
                report.placed.append(item)
                report.copied += 1
                continue

            target_dir.mkdir(parents=True, exist_ok=True)
            destination = _unique_destination(target_dir, filename, options.on_exist)

            if destination is None:  # skip: already present on the drive
                destination = target_dir / filename
                item.relocate(destination, drive_root)
                report.placed.append(item)
                report.skipped += 1
                if options.verbose:
                    logger.debug(f"Skipped (already exists): {destination}")
                continue

            shutil.copy2(source, destination)
            item.relocate(destination, drive_root)
            report.placed.append(item)
            report.copied += 1
            if options.verbose:
                logger.debug(f"Copied: {destination}")
        except OSError as exc:
            logger.error(f"Failed to place {source}: {exc}")
            report.excluded.append(str(source))

    logger.info(
        f"Organize report — copied={report.copied} skipped={report.skipped} "
        f"excluded={len(report.excluded)} into {output}"
    )
    return report
