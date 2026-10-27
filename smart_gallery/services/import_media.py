"""import — copy media from a source (SD card / dump folder) into the drive and
insert only the new items into the catalog.

ExifTool runs once, over just the imported files. Metadata is reused verbatim
for the catalog row; only path/size/mtime are taken from the destination.

With mirrors, each file placed on the working drive is then copied from that
working copy to every mirror at the same relpath. Files matching the working
drive's offload rules go from the source straight to the mirrors and get an
offloaded row on the working drive. A mirror problem never undoes the working
drive's import; it only counts as pending for that mirror.
"""

import copy
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from loguru import logger

from smart_gallery.analysis import analyze_paths
from smart_gallery.config import PARTIAL_SUFFIX, iter_files, to_relpath
from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.types import BaselineRow, key
from smart_gallery.models import MediaItem
from smart_gallery.organize import (
    FilterOptions,
    Options,
    matches,
    organize_items,
    target_dir_for,
)
from smart_gallery.organize.organize import _unique_destination, copy_progress
from smart_gallery.progress import Cancelled, ProgressCallback
from smart_gallery.services.mirror import (
    _baseline_row,
    _copy,
    offload_rule_matches,
)


@dataclass
class ImportReport:
    analyzed: int = 0
    copied: int = 0
    skipped: int = 0
    excluded: int = 0
    inserted: int = 0
    # Per mirror, keyed by its db_uuid.
    mirrored: Dict[str, int] = field(default_factory=dict)
    mirror_pending: Dict[str, int] = field(default_factory=dict)


def _gather_files(sources: Sequence) -> List[Path]:
    """Files to import. Folders are walked like ``sync`` walks a drive, so a
    source holding a gallery never imports its catalog folder (thumbnails
    included) or a leftover partial copy."""
    files: List[Path] = []
    for source in sources:
        source = Path(source)
        if source.is_dir():
            files.extend(iter_files(source))
        elif source.is_file() and not source.name.lower().endswith(PARTIAL_SUFFIX):
            files.append(source)
    return files


class _Mirror:
    """One mirror's catalog and baseline rows and its offloaded files from
    this import, written at the end (or when the import stops)."""

    def __init__(self, repo: GalleryRepository) -> None:
        self.repo = repo
        self.uuid = repo.get_meta("db_uuid")
        self.rows: List[MediaItem] = []
        self.baseline: List[BaselineRow] = []
        self.received: List[str] = []

    def receive(
        self,
        source: Path,
        relpath: str,
        item: MediaItem,
        report: ImportReport,
        working_file: Optional[Path] = None,
    ) -> bool:
        """Put ``source`` at ``relpath`` on this mirror. True when the mirror
        now holds it. A different file already there is never overwritten;
        the same file (size and mtime) is kept as is."""
        dest = self.repo.drive_root / relpath
        try:
            if dest.exists():
                src, have = source.stat(), dest.stat()
                if (have.st_size, have.st_mtime_ns) != (src.st_size, src.st_mtime_ns):
                    logger.warning(f"Not mirrored: a different file is at {dest}")
                    report.mirror_pending[self.uuid] += 1
                    return False
            else:
                _copy(source, dest)
                report.mirrored[self.uuid] += 1
                logger.debug(f"Mirrored: {dest}")
            if working_file is not None:
                base = _baseline_row(relpath, working_file, dest)
            else:  # offloaded: the working row keeps the source's size/mtime
                mirror_mtime = dest.stat().st_mtime_ns
                base = BaselineRow(
                    relpath, item.size_bytes, item.mtime_ns, mirror_mtime
                )
        except OSError as exc:
            logger.warning(
                f"Could not mirror {relpath} to {self.repo.drive_root}: {exc}"
            )
            report.mirror_pending[self.uuid] += 1
            return False
        self.rows.append(copy.copy(item).relocate(dest, self.repo.drive_root))
        self.baseline.append(base)
        return True

    def flush(self, working: GalleryRepository) -> None:
        try:
            if self.rows:
                self.repo.upsert_many(self.rows)
            if self.baseline:
                working.update_baseline(self.uuid, self.baseline, [])
        except (OSError, sqlite3.Error) as exc:
            logger.error(
                f"Could not record the import for {self.repo.drive_root}: {exc}"
            )
        self.rows, self.baseline = [], []


def _mirror_placed(
    placed: Sequence[MediaItem],
    mirrors: Sequence[_Mirror],
    report: ImportReport,
    progress_callback: Optional[ProgressCallback],
) -> None:
    """Copy each file placed on the working drive, read from that working
    copy, to every mirror at the same relpath."""
    total = len(placed) * len(mirrors)
    done = held = 0
    for m in mirrors:
        for item in placed:
            working_file = Path(item.abspath)
            if m.receive(working_file, item.relpath, item, report, working_file):
                held += 1
            done += 1
            if progress_callback:
                progress_callback(copy_progress(done, total, held, 0))


class _Taken:
    """Working-drive destinations already in use besides files on disk:
    catalog rows (an offloaded file has a row but no file), offloaded
    relpaths and names this import has claimed. Relpaths compare
    case-insensitively. Shared by kept and offloaded-on-arrival items."""

    def __init__(self, repo: GalleryRepository) -> None:
        self.repo = repo
        self.offloaded = {key(r) for r in repo.offloaded_relpaths()}
        self.claimed: Set[str] = set()

    def __call__(self, path: Path) -> bool:
        rel = to_relpath(path, self.repo.drive_root)
        k = key(rel)
        return k in self.claimed or k in self.offloaded or self.repo.has_relpath(rel)

    def claim(self, path: Path) -> None:
        self.claimed.add(key(to_relpath(path, self.repo.drive_root)))


def _import_offloaded(
    repo: GalleryRepository,
    items: Sequence[MediaItem],
    output_dir: Path,
    options: Options,
    mirrors: Sequence[_Mirror],
    report: ImportReport,
    taken: _Taken,
) -> None:
    """Copy offloaded-on-arrival files from the source straight to each
    mirror and give each an offloaded row on the working drive. A file no
    mirror received is copied to the working drive instead."""
    rows: List[MediaItem] = []
    try:
        for item in items:
            if not item.abspath:
                report.excluded += 1
                continue
            source = Path(item.abspath)
            target_dir = target_dir_for(output_dir, options, item)
            dest = _unique_destination(target_dir, source.name, options.on_exist, taken)
            if dest is None:
                report.skipped += 1
                continue
            rel = to_relpath(dest, repo.drive_root)
            taken.claim(dest)
            received: List[_Mirror] = []
            try:
                for m in mirrors:
                    if m.receive(source, rel, item, report):
                        received.append(m)
            finally:
                if received:  # recorded even when a later mirror's copy stops
                    rows.append(copy.copy(item).relocate(dest, repo.drive_root))
                    for m in received:
                        m.received.append(rel)
            if received:
                continue
            try:
                _copy(source, dest)
            except OSError as exc:
                logger.error(f"Failed to place {source}: {exc}")
                report.excluded += 1
                continue
            rows.append(copy.copy(item).relocate(dest, repo.drive_root))
            report.copied += 1
            logger.debug(f"Copied (no mirror received it): {dest}")
    finally:
        if rows:
            report.inserted += repo.insert_many(rows)
        for m in mirrors:
            if m.received:
                repo.add_offloaded(m.received, m.uuid)
                m.received = []


def import_media(
    repo: GalleryRepository,
    sources: Sequence,
    *,
    output_dir=None,
    options: Optional[Options] = None,
    query: Optional[FilterOptions] = None,
    mirrors: Sequence[GalleryRepository] = (),
    progress_callback: Optional[ProgressCallback] = None,
) -> ImportReport:
    """Import files from ``sources`` into ``output_dir`` (default: the drive
    root) and onto every repository in ``mirrors`` at the same relpath."""
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
    targets = [] if options.dry_run else [_Mirror(m) for m in mirrors]
    for m in targets:
        report.mirrored[m.uuid] = 0
        report.mirror_pending[m.uuid] = 0

    pairing = repo.get_pairing() if targets else None
    rules = pairing.offload_rules if pairing else []
    offload: List[MediaItem] = []
    kept: List[MediaItem] = []
    for it in items:
        (offload if rules and offload_rule_matches(rules, it) else kept).append(it)

    taken = _Taken(repo)
    org = organize_items(
        kept, output_dir, repo.drive_root, options, progress_callback, taken
    )
    report.copied = org.copied
    report.skipped = org.skipped
    report.excluded = len(org.excluded)

    if not options.dry_run and org.placed:
        report.inserted = repo.insert_many(org.placed)

    if org.cancelled:
        # A stop request ends the import here: what reached the working drive
        # is catalogued, and the next mirror sync copies it to the mirrors.
        logger.warning(f"Import cancelled — copied={report.copied}")
        raise Cancelled()

    try:
        _mirror_placed(org.placed, targets, report, progress_callback)
        if offload:
            _import_offloaded(
                repo, offload, output_dir, options, targets, report, taken
            )
    finally:
        # A mirror copy cut short by a stop request leaves finished rows here.
        for m in targets:
            m.flush(repo)

    mirrored = "".join(
        f" mirror {u}: copied={n} pending={report.mirror_pending[u]}"
        for u, n in report.mirrored.items()
    )
    logger.success(
        f"Import complete — copied={report.copied} skipped={report.skipped} "
        f"inserted={report.inserted}{mirrored}"
    )
    return report
