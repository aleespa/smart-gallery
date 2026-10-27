"""sync — reconcile the catalog with the live drive.

Cheap walk (paths + stat only) → diff against the DB index → analyze only the
new/changed files with ExifTool → prune missing + upsert in one transaction.
Unchanged files are never re-read.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger

from smart_gallery.analysis import analyze_paths
from smart_gallery.config import FACE_DET_VERSION, iter_files, to_relpath
from smart_gallery.db import GalleryRepository
from smart_gallery.progress import FINDING, ProgressCallback, counted

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


def _scan_live(
    drive_root: Path, progress_callback: Optional[ProgressCallback] = None
) -> LiveIndex:
    live: LiveIndex = {}
    for path in counted(iter_files(drive_root), FINDING, progress_callback):
        try:
            st = path.stat()
        except OSError:
            continue
        live[to_relpath(path, drive_root)] = (path, st.st_size, st.st_mtime_ns)
    return live


def diff_drive(
    repo: GalleryRepository,
    progress_callback: Optional[ProgressCallback] = None,
) -> Tuple[Set[str], Set[str], Set[str], LiveIndex]:
    """Return (to_delete, to_add, to_update, live_index)."""
    return _diff(repo, progress_callback)[:4]


def _diff(repo: GalleryRepository, progress_callback: Optional[ProgressCallback]):
    live = _scan_live(repo.drive_root, progress_callback)
    db_index = repo.index_for_sync()

    live_keys = set(live)
    db_keys = set(db_index)
    # Offloaded files are deliberately absent from this drive; keep their rows.
    offloaded = {rel.casefold() for rel in repo.offloaded_relpaths()}
    to_delete = {rel for rel in db_keys - live_keys if rel.casefold() not in offloaded}
    to_add = live_keys - db_keys
    to_update = {
        rel
        for rel in (live_keys & db_keys)
        if (live[rel][1], live[rel][2]) != db_index[rel]
    }
    return to_delete, to_add, to_update, live, db_index


def sync_drive(
    repo: GalleryRepository,
    *,
    dry_run: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> SyncReport:
    to_delete, to_add, to_update, live, db_index = _diff(repo, progress_callback)

    # An offloaded file that came back is no longer offloaded, but only when
    # it matches the catalog row's size: a different file put at that path
    # (by hand, say) must neither replace the row nor un-offload it.
    offloaded = {rel.casefold() for rel in repo.offloaded_relpaths()}
    returned: List[str] = []
    for rel in live:
        if rel.casefold() not in offloaded or rel not in db_index:
            continue
        if live[rel][1] == db_index[rel][0]:
            returned.append(rel)
        else:
            logger.warning(
                f"{rel} is offloaded but a different file is now at that path; "
                "it stays offloaded and its catalog row is unchanged."
            )
            to_update.discard(rel)

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

    if returned:
        repo.remove_offloaded(returned)

    # Deleted media cascade their faces via the FK; changed media keep their row
    # id but their pixels (and thus faces) are now stale, so drop those face rows
    # so the next scan re-processes them. New files are unscanned already.
    if to_update:
        repo.invalidate_faces_by_relpath(to_update)

    pending = repo.count_unscanned_images(FACE_DET_VERSION)
    logger.success(
        f"Sync complete — +{report.added} ~{report.updated} -{report.deleted}"
    )
    if pending:
        logger.info(
            f"{pending:,} image(s) pending face scan — run `smart-gallery find-faces`."
        )
    return report
