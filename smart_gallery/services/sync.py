"""sync — reconcile the catalog with the live drive.

Cheap walk (paths + stat only) → diff against the DB index → analyze only the
new/changed files with ExifTool → prune missing + upsert in one transaction.
Unchanged files are never re-read.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from loguru import logger

from smart_gallery.analysis import analyze_paths
from smart_gallery.config import iter_files, to_relpath
from smart_gallery.db import GalleryRepository

# relpath -> (absolute path, size_bytes, mtime_ns)
LiveIndex = Dict[str, Tuple[Path, int, int]]


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0

    @property
    def changed(self) -> int:
        return self.added + self.updated + self.deleted


def _scan_live(drive_root: Path) -> LiveIndex:
    live: LiveIndex = {}
    for path in iter_files(drive_root):
        try:
            st = path.stat()
        except OSError:
            continue
        live[to_relpath(path, drive_root)] = (path, st.st_size, st.st_mtime_ns)
    return live


def diff_drive(
    repo: GalleryRepository,
) -> Tuple[Set[str], Set[str], Set[str], LiveIndex]:
    """Return (to_delete, to_add, to_update, live_index)."""
    live = _scan_live(repo.drive_root)
    db_index = repo.index_for_sync()

    live_keys = set(live)
    db_keys = set(db_index)
    to_delete = db_keys - live_keys
    to_add = live_keys - db_keys
    to_update = {
        rel
        for rel in (live_keys & db_keys)
        if (live[rel][1], live[rel][2]) != db_index[rel]
    }
    return to_delete, to_add, to_update, live


def sync_drive(
    repo: GalleryRepository,
    *,
    dry_run: bool = False,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> SyncReport:
    to_delete, to_add, to_update, live = diff_drive(repo)
    report = SyncReport(
        added=len(to_add),
        updated=len(to_update),
        deleted=len(to_delete),
        unchanged=len(live) - len(to_add) - len(to_update),
    )

    logger.info(
        f"Sync plan — add={report.added} update={report.updated} "
        f"delete={report.deleted} unchanged={report.unchanged}"
    )
    if dry_run:
        return report

    changed_paths: List[Path] = [live[rel][0] for rel in (to_add | to_update)]
    items = analyze_paths(
        changed_paths, drive_root=repo.drive_root, progress_callback=progress_callback
    )
    repo.apply_sync(to_delete, items)
    logger.success(
        f"Sync complete — +{report.added} ~{report.updated} -{report.deleted}"
    )
    return report
