"""Move listed catalog files to the Recycle Bin and drop their catalog rows.

A file is only touched when its catalog row, the list, and the file on disk all
agree on size and mtime, and it is a regular file inside the drive root. Each
recycled file's row is removed in its own commit, so a cancelled or interrupted
run leaves the catalog matching the disk.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from loguru import logger

from smart_gallery.config import drive_root_of
from smart_gallery.db.repository import GalleryRepository
from smart_gallery.progress import RECYCLING, ProgressCallback, ProgressEvent
from smart_gallery.recycle_bin import (
    move_to_recycle_bin,
    recycle_bin_capacity,
    supports_recycle_bin,
)
from smart_gallery.storage.tree import format_bytes


@dataclass(frozen=True)
class RecycleEntry:
    relpath: str
    size_bytes: int
    mtime_ns: int | None


@dataclass
class RecycleReport:
    recycled: int = 0
    skipped: int = 0
    freed_bytes: int = 0
    failures: list[str] = field(default_factory=list)


def read_list(path: Path) -> list[RecycleEntry]:
    """Parse a UTF-8 list of ``relpath<TAB>size<TAB>mtime_ns`` lines. An empty
    mtime reads as None; blank lines are skipped."""
    entries = []
    text = Path(path).read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0]:
            raise ValueError(f"{path}, line {number}: expected relpath, size, mtime.")
        relpath, size, mtime = parts
        try:
            entries.append(
                RecycleEntry(relpath, int(size), int(mtime) if mtime.strip() else None)
            )
        except ValueError:
            raise ValueError(
                f"{path}, line {number}: size and mtime must be whole numbers."
            ) from None
    return entries


def write_list(path: Path, entries: Iterable[RecycleEntry]) -> None:
    """Write entries in the format ``read_list`` reads."""
    lines = []
    for e in entries:
        if any(ch in e.relpath for ch in "\t\r\n"):
            raise ValueError(f"Path can't be written to a list: {e.relpath!r}")
        mtime = "" if e.mtime_ns is None else str(e.mtime_ns)
        lines.append(f"{e.relpath}\t{e.size_bytes}\t{mtime}\n")
    Path(path).write_text("".join(lines), encoding="utf-8", newline="\n")


def _skip_reason(
    root: Path, entry: RecycleEntry, catalog: dict
) -> tuple[Optional[str], Path]:
    """Why ``entry`` must not be recycled (None when it may be), and its path."""
    path = root / entry.relpath
    if entry.relpath not in catalog:
        return "not in the catalog", path
    if catalog[entry.relpath] != (entry.size_bytes, entry.mtime_ns):
        return "the catalog has a different size or date", path
    if entry.mtime_ns is None:
        return "no modification time to check", path
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(path)
    try:
        inside = os.path.commonpath([real_root, real_path]) == real_root
    except ValueError:  # different drives
        inside = False
    if not inside or real_path == real_root:
        return "outside the drive", path
    try:
        st = os.lstat(path)
    except OSError:
        return "file is missing", path
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file", path
    if (st.st_size, st.st_mtime_ns) != (entry.size_bytes, entry.mtime_ns):
        return "file changed on disk", path
    return None, path


def recycle_files(
    repo: GalleryRepository,
    entries: Sequence[RecycleEntry],
    progress_callback: ProgressCallback | None = None,
) -> RecycleReport:
    """Move each entry's file to the Recycle Bin and drop its catalog row."""
    root = drive_root_of(repo.db_path)
    if not supports_recycle_bin(root):
        raise ValueError(
            f"The Recycle Bin is not available on {root}; nothing was moved."
        )
    capacity = recycle_bin_capacity(root)  # None: no limit known
    report = RecycleReport()
    total = len(entries)

    def emit(done: int) -> None:
        if progress_callback is not None:
            progress_callback(
                ProgressEvent(
                    RECYCLING,
                    done,
                    total,
                    outcomes=(
                        ("recycled", report.recycled),
                        ("skipped", report.skipped),
                        ("failed", len(report.failures)),
                    ),
                )
            )

    catalog = repo.index_for_sync()
    emit(0)
    for done, entry in enumerate(entries, 1):
        reason, path = _skip_reason(root, entry, catalog)
        if reason is not None:
            report.skipped += 1
            logger.warning(f"Skipped: {entry.relpath} ({reason})")
        elif capacity is not None and entry.size_bytes > capacity:
            # Windows would delete it for good rather than keep it.
            report.failures.append(
                f"{entry.relpath}: too big for the Recycle Bin "
                f"(limit {format_bytes(capacity)})"
            )
            logger.warning(f"Not recycled: {report.failures[-1]}")
        else:
            try:
                move_to_recycle_bin(path)
            except OSError as exc:
                report.failures.append(f"{entry.relpath}: {exc}")
                logger.warning(f"Not recycled: {entry.relpath}: {exc}")
            else:
                repo.delete_by_relpaths([entry.relpath])
                catalog.pop(entry.relpath, None)
                report.recycled += 1
                report.freed_bytes += entry.size_bytes
                logger.debug(f"Recycled: {entry.relpath}")
        emit(done)
    return report
