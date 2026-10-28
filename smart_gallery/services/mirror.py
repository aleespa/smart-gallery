"""Pair a working drive with a mirror, preview a sync, and apply it.

Planning never writes files. Applying copies atomically, moves replaced or
deleted files into the drive's trash (never a permanent delete), and records
each finished file in the baseline so an interrupted run resumes.

Free up space is the one permanent delete: it removes working-drive files
whose verified copy is on the mirror (faces scanned and transferred first) and
never touches the mirror's files. Bring back copies them home again.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Sequence, Set, Tuple

from loguru import logger

from smart_gallery.config import DB_DIRNAME, FACE_DET_VERSION, iter_files
from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.faces import FaceSyncReport, sync_face_data
from smart_gallery.mirror.fileops import (
    move_to_trash,
    purge_trash_dir,
    restore_from_trash,
    run_stamp,
)
from smart_gallery.mirror.hashing import ensure_hashes
from smart_gallery.mirror.plan import build_plan
from smart_gallery.mirror.types import (
    BaselineRow,
    MirrorError,
    MirrorPlan,
    MirrorRow,
    Peer,
    key,
)
from smart_gallery.models import DB_COLUMNS, MediaItem
from smart_gallery.organize.filters import FilterOptions, is_query_empty, matches
from smart_gallery.organize.organize import copy_atomic, copy_progress
from smart_gallery.progress import ProgressCallback
from smart_gallery.services.sync import sync_drive

DELETE_GUARD_FRACTION = 0.05
TRASH_DAYS = 30

__all__ = [
    "DELETE_GUARD_FRACTION",
    "TRASH_DAYS",
    "MirrorChoices",
    "MirrorError",
    "MirrorPreview",
    "MirrorSyncReport",
    "OffloadReport",
    "apply_mirror_sync",
    "bring_back",
    "free_up_space",
    "offload_rule_matches",
    "pair_galleries",
    "plan_mirror_sync",
    "purge_trash",
]


def pair_galleries(working: GalleryRepository, mirror: GalleryRepository) -> None:
    w_uuid = working.get_meta("db_uuid")
    m_uuid = mirror.get_meta("db_uuid")
    if w_uuid == m_uuid:
        raise MirrorError("A gallery cannot be its own mirror.")
    w_pair = working.get_pairing()
    if w_pair and w_pair.role == "mirror":
        raise MirrorError("The working gallery is itself a mirror.")
    m_pair = mirror.get_pairing()
    if m_pair and m_pair.role == "working":
        raise MirrorError("The mirror is a working gallery.")
    if m_pair and any(p.uuid != w_uuid for p in m_pair.peers):
        raise MirrorError("The mirror is already paired with another working drive.")
    working.add_peer("working", Peer(m_uuid, str(mirror.db_path)))
    mirror.add_peer("mirror", Peer(w_uuid, str(working.db_path)))


@dataclass
class MirrorPreview:
    plan: MirrorPlan
    copy_bytes: int
    mirror_free_bytes: int
    mirror_trash_bytes: int
    large_delete: bool
    working_files: int
    mirror_files: int


def _check(
    working: GalleryRepository, mirror: GalleryRepository, *, require_files=True
) -> str:
    wp, mp = working.get_pairing(), mirror.get_pairing()
    if wp is None or mp is None:
        raise MirrorError("These galleries are not paired.")
    if wp.role != "working" or mp.role != "mirror":
        raise MirrorError("Pairing roles are wrong: need a working and a mirror.")
    m_uuid, w_uuid = mirror.get_meta("db_uuid"), working.get_meta("db_uuid")
    if m_uuid not in {p.uuid for p in wp.peers} or w_uuid not in {
        p.uuid for p in mp.peers
    }:
        raise MirrorError("These galleries are not paired with each other.")
    for repo, name in ((working, "working"), (mirror, "mirror")):
        if not repo.drive_root.is_dir():
            raise MirrorError(f"The {name} drive {repo.drive_root} is not available.")
    if require_files and next(iter_files(working.drive_root), None) is None:
        raise MirrorError(
            f"The working drive {working.drive_root} has no files; "
            "it may be unmounted or empty. Refusing to plan."
        )
    return m_uuid


def _cached(
    repo: GalleryRepository,
    rows: Dict[str, MirrorRow],
    progress_callback: Optional[ProgressCallback] = None,
) -> Callable:
    cache: Dict[str, Optional[str]] = {}

    def provider(k: str) -> Optional[str]:
        if k not in cache:
            cache[k] = ensure_hashes(
                repo, [rows[k].relpath], progress_callback, rows=rows
            ).get(k)
        return cache[k]

    return provider


def plan_mirror_sync(
    working: GalleryRepository,
    mirror: GalleryRepository,
    progress_callback: Optional[ProgressCallback] = None,
) -> MirrorPreview:
    m_uuid = _check(working, mirror)
    sync_drive(working, progress_callback=progress_callback)
    sync_drive(mirror, progress_callback=progress_callback)
    w_rows, m_rows = working.mirror_rows(), mirror.mirror_rows()
    offloaded = {key(r) for r in working.offloaded_relpaths()}
    held = {key(r) for r in working.offloaded_on(m_uuid)}
    plan = build_plan(
        w_rows,
        m_rows,
        working.baseline(m_uuid),
        offloaded,
        held,
        _cached(working, w_rows, progress_callback),
        _cached(mirror, m_rows, progress_callback),
    )
    copy_bytes = sum(w_rows[key(r)].size_bytes for r in plan.copy_new + plan.overwrite)
    trash_dir = mirror.drive_root / DB_DIRNAME / "trash"
    trash_bytes = (
        sum(f.stat().st_size for f in trash_dir.rglob("*") if f.is_file())
        if trash_dir.is_dir()
        else 0
    )
    return MirrorPreview(
        plan=plan,
        copy_bytes=copy_bytes,
        mirror_free_bytes=shutil.disk_usage(mirror.drive_root).free,
        mirror_trash_bytes=trash_bytes,
        large_delete=len(plan.trash) > DELETE_GUARD_FRACTION * len(m_rows),
        working_files=len(w_rows),
        mirror_files=len(m_rows),
    )


# -- apply --------------------------------------------------------------------

BASELINE_BATCH = 200
_ITEM_SQL = f"SELECT {', '.join(DB_COLUMNS)} FROM media_items WHERE relpath=?"
_MOVE_SQL = (
    "UPDATE media_items SET relpath=?, name=?, ext=?, directory_rel=?, "
    "size_bytes=?, mtime_ns=?, updated_at=datetime('now') WHERE relpath=?"
)


@dataclass
class MirrorChoices:
    """The user's answers for the cases a sync asks about."""

    only_on_mirror: Literal["later", "keep-on-mirror", "copy-back"] = "later"
    changed_on_mirror: Literal["skip", "take-working", "keep-mirror"] = "skip"
    conflicts: Literal["skip", "take-working", "keep-mirror"] = "skip"
    confirm_large_delete: bool = False


@dataclass
class MirrorSyncReport:
    copied: int = 0
    overwritten: int = 0
    trashed: int = 0
    moved: int = 0
    copied_back: int = 0
    kept_on_mirror: int = 0
    skipped: int = 0
    faces: FaceSyncReport = field(default_factory=FaceSyncReport)
    failures: List[str] = field(default_factory=list)


def purge_trash(
    mirror: GalleryRepository,
    older_than_days: int = TRASH_DAYS,
    now: Optional[datetime] = None,
) -> int:
    """Remove the drive's trash folders older than ``older_than_days``.
    Returns the number of folders removed."""
    return purge_trash_dir(mirror.drive_root, older_than_days, now or datetime.now())


def _item(repo: GalleryRepository, relpath: str) -> Optional[MediaItem]:
    row = repo.conn.execute(_ITEM_SQL, (relpath,)).fetchone()
    return MediaItem.from_row(row, repo.drive_root) if row else None


def _row_for(source: GalleryRepository, relpath: str, dest: Path, root: Path):
    """A catalog row for ``dest`` built from the source catalog's row (no
    ExifTool); path fields, size and mtime come from ``dest``. The source's
    hash is dropped: it describes the source's bytes, not the copy's."""
    item = _item(source, relpath) or MediaItem.from_path(dest)
    item.content_hash = None
    return item.relocate(dest, root)


def _copy(source: Path, dest: Path) -> None:
    """Copy atomically and keep the source mtime; never overwrite a file."""
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    copy_atomic(source, dest)
    st = source.stat()
    os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))


def _replace(source: Path, root: Path, relpath: str, old_relpath: str, stamp: str):
    """Move ``root/old_relpath`` to the trash, then copy ``source`` to
    ``root/relpath``. If the copy fails the old file is put back."""
    old = root / old_relpath
    trashed = move_to_trash(root, old_relpath, stamp) if old.exists() else None
    try:
        _copy(source, root / relpath)
    except BaseException:
        if trashed is not None:
            restore_from_trash(trashed, old)
        raise


def _baseline_row(relpath: str, working_file: Path, mirror_file: Path) -> BaselineRow:
    ws, ms = working_file.stat(), mirror_file.stat()
    return BaselineRow(relpath, ws.st_size, ws.st_mtime_ns, ms.st_mtime_ns)


class _Pending:
    """Catalog and baseline writes for finished files, flushed in batches."""

    def __init__(self, working, mirror, mirror_uuid: str) -> None:
        self.working, self.mirror, self.uuid = working, mirror, mirror_uuid
        self._reset()

    def _reset(self) -> None:
        self.files = 0
        self.mirror_deletes: List[str] = []
        self.mirror_moves: List[Tuple[str, MediaItem]] = []
        self.mirror_upserts: List[MediaItem] = []
        self.working_upserts: List[MediaItem] = []
        self.replaced_on_working: List[str] = []
        self.offloaded: List[str] = []
        self.base_upserts: List[BaselineRow] = []
        self.base_removals: List[str] = []

    def finished(self) -> None:
        self.files += 1
        if self.files >= BASELINE_BATCH:
            self.flush()

    def flush(self) -> None:
        """Catalogs first, baseline last: a crash in between only leaves a
        file out of the baseline, which the next plan re-adopts."""
        m = self.mirror
        if self.mirror_deletes:
            m.delete_by_relpaths(self.mirror_deletes)
        if self.mirror_moves:
            m.conn.executemany(
                _MOVE_SQL,
                [
                    (i.relpath, i.name, i.ext, i.directory_rel, i.size_bytes)
                    + (i.mtime_ns, old)
                    for old, i in self.mirror_moves
                ],
            )
            m.conn.commit()
        if self.mirror_upserts:
            m.upsert_many(self.mirror_upserts)
        if self.working_upserts:
            self.working.upsert_many(self.working_upserts)
        if self.replaced_on_working:
            # E's content changed under a kept media id: drop its faces and
            # scan state (decisions go to carry-over) so the file re-scans.
            self.working.invalidate_faces_by_relpath(self.replaced_on_working)
        if self.offloaded:
            self.working.add_offloaded(self.offloaded, self.uuid)
        if self.base_upserts or self.base_removals:
            self.working.update_baseline(
                self.uuid, self.base_upserts, self.base_removals
            )
        self._reset()


class _Applier:
    def __init__(self, working, mirror, uuid, report: MirrorSyncReport) -> None:
        self.working, self.mirror = working, mirror
        self.e, self.d = working.drive_root, mirror.drive_root
        self.report = report
        self.pending = _Pending(working, mirror, uuid)
        self.stamp = run_stamp()

    def move(self, old: str, new: str) -> None:
        src, dest = self.d / old, self.d / new
        if dest.exists():
            raise FileExistsError(f"{dest} already exists")
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dest)
        item = _item(self.mirror, old)
        if item is not None:
            self.pending.mirror_moves.append((old, item.relocate(dest, self.d)))
        else:
            self.pending.mirror_upserts.append(
                _row_for(self.working, new, dest, self.d)
            )
        self.pending.base_removals.append(old)
        self.pending.base_upserts.append(_baseline_row(new, self.e / new, dest))
        self.report.moved += 1

    def trash(self, relpath: str) -> None:
        move_to_trash(self.d, relpath, self.stamp)
        self.pending.mirror_deletes.append(relpath)
        self.pending.base_removals.append(relpath)
        self.report.trashed += 1

    def to_mirror(self, relpath: str, mirror_relpath: Optional[str] = None) -> None:
        """Copy E's file to D; an existing D copy goes to D's trash first."""
        src, dest = self.e / relpath, self.d / relpath
        if mirror_relpath is None:
            _copy(src, dest)
            self.report.copied += 1
        else:
            _replace(src, self.d, relpath, mirror_relpath, self.stamp)
            self.report.overwritten += 1
        self.pending.mirror_upserts.append(
            _row_for(self.working, relpath, dest, self.d)
        )
        self.pending.base_upserts.append(_baseline_row(relpath, src, dest))

    def to_working(self, mirror_relpath: str, relpath: Optional[str] = None) -> None:
        """Copy D's file to E; an existing E copy goes to E's trash first."""
        target = relpath or mirror_relpath
        src, dest = self.d / mirror_relpath, self.e / target
        if relpath is None:
            _copy(src, dest)
        else:
            _replace(src, self.e, target, relpath, self.stamp)
            self.pending.replaced_on_working.append(target)
        self.pending.working_upserts.append(
            _row_for(self.mirror, mirror_relpath, dest, self.e)
        )
        self.pending.base_upserts.append(_baseline_row(target, dest, src))
        self.report.copied_back += 1

    def keep_on_mirror(self, relpath: str) -> None:
        src = self.d / relpath
        st = src.stat()
        item = _item(self.mirror, relpath) or MediaItem.from_path(src)
        item.content_hash = None
        item.relocate(self.e / relpath, self.e)  # no file on E: keeps D's stat
        item.size_bytes, item.mtime_ns = st.st_size, st.st_mtime_ns
        self.pending.working_upserts.append(item)
        self.pending.offloaded.append(item.relpath)
        self.pending.base_upserts.append(
            BaselineRow(item.relpath, st.st_size, st.st_mtime_ns, st.st_mtime_ns)
        )
        self.report.kept_on_mirror += 1


def _operations(
    plan: MirrorPlan, choices: MirrorChoices, a: _Applier, w_rows, m_rows
) -> List[Tuple[str, Callable[[], None]]]:
    """Every file operation of the run, in order, as (label, action)."""
    ops: List[Tuple[str, Callable[[], None]]] = []

    def e_rel(rel: str) -> str:
        return w_rows[key(rel)].relpath if key(rel) in w_rows else rel

    def d_rel(rel: str) -> str:
        return m_rows[key(rel)].relpath if key(rel) in m_rows else rel

    for old, new in plan.moves:
        ops.append((f"{old} -> {new}", lambda o=old, n=new: a.move(o, n)))
    for rel in plan.trash:
        ops.append((rel, lambda r=rel: a.trash(r)))
    for rel in plan.overwrite:
        ops.append((rel, lambda r=rel: a.to_mirror(r, d_rel(r))))
    for rel in plan.copy_new:
        ops.append((rel, lambda r=rel: a.to_mirror(r)))
    for rels, choice in (
        (plan.changed_on_mirror, choices.changed_on_mirror),
        (plan.conflicts, choices.conflicts),
    ):
        for rel in rels:
            on_e = key(rel) in w_rows  # not when deleted on E, edited on D
            if choice == "take-working" and on_e:
                ops.append((rel, lambda r=rel: a.to_mirror(e_rel(r), d_rel(r))))
            elif choice == "take-working":  # E's deletion wins
                ops.append((rel, lambda r=rel: a.trash(d_rel(r))))
            elif choice == "keep-mirror" and on_e:
                ops.append((rel, lambda r=rel: a.to_working(d_rel(r), e_rel(r))))
            elif choice == "keep-mirror":
                ops.append((rel, lambda r=rel: a.to_working(d_rel(r))))
            else:
                a.report.skipped += 1
    for rel in plan.only_on_mirror:
        if choices.only_on_mirror == "copy-back":
            ops.append((rel, lambda r=rel: a.to_working(r)))
        elif choices.only_on_mirror == "keep-on-mirror":
            ops.append((rel, lambda r=rel: a.keep_on_mirror(r)))
        else:
            a.report.skipped += 1
    return ops


def _drop_stale_baseline(working, mirror, uuid: str) -> None:
    """Forget baseline rows whose file is in neither catalog, so a later
    D-only file with that relpath is asked about instead of trashed."""
    present = set(working.mirror_rows()) | set(mirror.mirror_rows())
    stale = [b.relpath for k, b in working.baseline(uuid).items() if k not in present]
    if stale:
        working.update_baseline(uuid, [], stale)


def apply_mirror_sync(
    working: GalleryRepository,
    mirror: GalleryRepository,
    preview: MirrorPreview,
    choices: MirrorChoices = MirrorChoices(),
    progress_callback: Optional[ProgressCallback] = None,
) -> MirrorSyncReport:
    """Carry out a previewed sync from the working drive to the mirror."""
    uuid = _check(working, mirror)
    if preview.large_delete and not choices.confirm_large_delete:
        raise MirrorError(
            f"This sync would move {len(preview.plan.trash)} of "
            f"{preview.mirror_files} mirror files to the trash; "
            "confirm the large delete to go ahead."
        )
    if preview.copy_bytes > preview.mirror_free_bytes:
        raise MirrorError(
            f"The mirror has {preview.mirror_free_bytes} bytes free but this "
            f"sync copies {preview.copy_bytes} bytes."
        )
    report = MirrorSyncReport()
    applier = _Applier(working, mirror, uuid, report)
    plan = preview.plan
    ops = _operations(
        plan, choices, applier, working.mirror_rows(), mirror.mirror_rows()
    )
    applier.pending.base_upserts.extend(plan.adopt)
    done = 0
    try:
        for label, action in ops:
            try:
                action()
            except OSError as exc:
                logger.warning(f"Mirror sync failed for {label}: {exc}")
                report.failures.append(f"{label}: {exc}")
            else:
                logger.debug(f"Mirror sync: {label}")
                applier.pending.finished()
            done += 1
            if progress_callback:
                ok = done - len(report.failures)
                progress_callback(copy_progress(done, len(ops), ok, 0))
    finally:
        applier.pending.flush()  # keep finished work, even on Cancelled
    _drop_stale_baseline(working, mirror, uuid)
    report.faces = sync_face_data(working, mirror, progress_callback)
    purge_trash(mirror)
    purge_trash(working)
    return report


# -- free up space / bring back -----------------------------------------------

NO_COPY = "no verified copy on mirror"
NOT_SCANNED = "faces not scanned"
CHANGED = "changed on the working drive"
_SCANNED_SQL = (
    "SELECT mi.relpath FROM media_items mi JOIN face_scan_state s "
    "ON s.media_id = mi.id WHERE s.det_version=?"
)


@dataclass
class OffloadReport:
    files: int = 0
    bytes: int = 0
    skipped: List[str] = field(default_factory=list)


def offload_rule_matches(rules: Sequence[FilterOptions], item: MediaItem) -> bool:
    """True when ``item`` matches any offload rule."""
    return any(matches(item, rule) for rule in rules)


def _rule_key(rule: FilterOptions) -> tuple:
    """What an offload rule stores (file types, extensions, date range)."""
    return (
        tuple(rule.filetypes or ()),
        tuple(rule.extensions or ()),
        tuple(rule.date_range) if rule.date_range else None,
    )


def _storable_as_rule(selection: FilterOptions) -> bool:
    """True when the selection uses only what an offload rule can keep."""
    return is_query_empty(
        FilterOptions(
            cameras=selection.cameras,
            lenses=selection.lenses,
            aperture_range=selection.aperture_range,
            iso_range=selection.iso_range,
            shutter_speed_range=selection.shutter_speed_range,
            people=selection.people,
            person_ids=selection.person_ids,
        )
    )


def _folder_keys(folders: Sequence[str]) -> List[str]:
    """Folder prefixes as case-insensitive POSIX keys; blanks are dropped."""
    out = []
    for folder in folders:
        k = key(str(folder).replace("\\", "/").strip().strip("/"))
        if k and k != ".":
            out.append(k)
    return out


def _selected(
    repo: GalleryRepository, selection: FilterOptions, folders: Sequence[str]
) -> List[str]:
    prefixes = _folder_keys(folders)
    rels = repo.query_relpaths(selection)
    if not prefixes:
        return rels
    return [
        r
        for r in rels
        if any(key(r) == p or key(r).startswith(p + "/") for p in prefixes)
    ]


def _keys(repo: GalleryRepository, sql: str, params: tuple = ()) -> Set[str]:
    return {key(r["relpath"]) for r in repo.conn.execute(sql, params)}


def free_up_space(
    working: GalleryRepository,
    mirror: GalleryRepository,
    selection: FilterOptions,
    folders: Sequence[str] = (),
    *,
    dry_run: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> OffloadReport:
    """Permanently delete selected files from the working drive once the
    mirror holds a verified copy and their faces are scanned and transferred.
    No file on the mirror changes; only its catalog gets the face data."""
    if is_query_empty(selection) and not _folder_keys(folders):
        raise MirrorError("Choose what to free up.")
    if not _folder_keys(folders) and not _storable_as_rule(selection):
        raise MirrorError(
            "Free up space by file type, extension, date range or folder; "
            "other filters cannot be kept as an offload rule."
        )
    uuid = _check(working, mirror)
    # Fresh catalogs: a changed file loses its stored hash and its face scan.
    sync_drive(working, progress_callback=progress_callback)
    sync_drive(mirror, progress_callback=progress_callback)
    offloaded = {key(r) for r in working.offloaded_relpaths()}
    candidates = [
        r for r in _selected(working, selection, folders) if key(r) not in offloaded
    ]
    w_rows, m_rows = working.mirror_rows(), mirror.mirror_rows()
    report = OffloadReport()

    same_size = [
        r
        for r in candidates
        if key(r) in m_rows and m_rows[key(r)].size_bytes == w_rows[key(r)].size_bytes
    ]
    # Read both copies from disk: a stored hash may have come with a copied
    # row or predate an edit, and this check guards a permanent delete.
    w_hash = ensure_hashes(
        working, same_size, progress_callback, rows=w_rows, force=True
    )
    m_hash = ensure_hashes(
        mirror,
        [m_rows[key(r)].relpath for r in same_size],
        progress_callback,
        rows=m_rows,
        force=True,
    )
    images = _keys(working, "SELECT relpath FROM media_items WHERE media_type='image'")
    scanned = _keys(working, _SCANNED_SQL, (FACE_DET_VERSION,))
    eligible: List[str] = []
    for rel in candidates:
        k = key(rel)
        if not (w_hash.get(k) and w_hash.get(k) == m_hash.get(k)):
            report.skipped.append(f"{rel}: {NO_COPY}")
        elif k in images and k not in scanned:
            report.skipped.append(f"{rel}: {NOT_SCANNED}")
        else:
            eligible.append(rel)

    if dry_run:
        report.files = len(eligible)
        report.bytes = sum(w_rows[key(r)].size_bytes for r in eligible)
        return report

    if eligible:
        # Faces reach the mirror before any working copy goes.
        sync_face_data(working, mirror, progress_callback)
        # Confirm the transfer: each image's scan state must now be on the
        # mirror's row for that file (relpaths compared case-insensitively).
        on_mirror = _keys(mirror, _SCANNED_SQL, (FACE_DET_VERSION,))
        missing = {r for r in eligible if key(r) in images and key(r) not in on_mirror}
        report.skipped.extend(f"{r}: {NOT_SCANNED}" for r in sorted(missing))
        eligible = [r for r in eligible if r not in missing]
    for done, rel in enumerate(eligible, 1):
        row, m_row = w_rows[key(rel)], m_rows[key(rel)]
        path = working.drive_root / row.relpath
        try:
            st = path.stat()
            m_size = (mirror.drive_root / m_row.relpath).stat().st_size
            if (st.st_size, st.st_mtime_ns) != (row.size_bytes, row.mtime_ns):
                report.skipped.append(f"{rel}: {CHANGED}")
            elif m_size != row.size_bytes:
                report.skipped.append(f"{rel}: {NO_COPY}")
            else:
                # Flag first, delete second: a kill or power loss in between
                # leaves a flag on a file still here, which the next sync
                # clears; the other order would leave the mirror's copy
                # looking deleted on E, and the next sync would trash it.
                working.add_offloaded([row.relpath], uuid)
                try:
                    os.remove(path)
                except OSError:
                    if path.exists():  # still here: not freed after all
                        working.remove_offloaded([row.relpath])
                    raise
                report.files += 1
                report.bytes += row.size_bytes
                logger.debug(f"Freed up: {rel}")
        except FileNotFoundError:
            report.skipped.append(f"{rel}: {NO_COPY}")
        except OSError as exc:
            logger.warning(f"Could not free up {rel}: {exc}")
            report.skipped.append(f"{rel}: {exc}")
        if progress_callback:
            progress_callback(copy_progress(done, len(eligible), report.files, 0))

    if not _folder_keys(folders):
        rules = working.get_pairing().offload_rules
        if all(_rule_key(r) != _rule_key(selection) for r in rules):
            working.set_offload_rules(rules + [selection])
    return report


def bring_back(
    working: GalleryRepository,
    mirror: GalleryRepository,
    selection: FilterOptions,
    folders: Sequence[str] = (),
    *,
    progress_callback: Optional[ProgressCallback] = None,
) -> OffloadReport:
    """Copy offloaded files held by ``mirror`` back to the working drive."""
    uuid = _check(working, mirror, require_files=False)
    held = {key(r) for r in working.offloaded_on(uuid)}
    candidates = [r for r in _selected(working, selection, folders) if key(r) in held]
    w_rows, m_rows = working.mirror_rows(), mirror.mirror_rows()
    report = OffloadReport()

    sources: List[Tuple[str, str, int]] = []
    for rel in candidates:
        m_rel = m_rows[key(rel)].relpath if key(rel) in m_rows else rel
        try:
            sources.append((rel, m_rel, (mirror.drive_root / m_rel).stat().st_size))
        except OSError:
            report.skipped.append(f"{rel}: not on the mirror")
    need = sum(size for _, _, size in sources)
    free = shutil.disk_usage(working.drive_root).free
    if need > free:
        raise MirrorError(
            f"The working drive has {free} bytes free but bringing these "
            f"files back needs {need} bytes."
        )

    restored: List[str] = []
    upserts: List[MediaItem] = []
    changed: List[str] = []
    base: List[BaselineRow] = []
    try:
        for done, (rel, m_rel, size) in enumerate(sources, 1):
            src, dest = mirror.drive_root / m_rel, working.drive_root / rel
            try:
                _copy(src, dest)
            except OSError as exc:
                logger.warning(f"Could not bring back {rel}: {exc}")
                report.skipped.append(f"{rel}: {exc}")
            else:
                upserts.append(_row_for(mirror, m_rel, dest, working.drive_root))
                old, new = w_rows[key(rel)], m_rows.get(key(rel))
                if (
                    new is None
                    or old.size_bytes != size
                    or (
                        old.content_hash
                        and new.content_hash
                        and old.content_hash != new.content_hash
                    )
                ):
                    changed.append(rel)  # different content: its faces re-scan
                base.append(_baseline_row(rel, dest, src))
                restored.append(rel)
                report.files += 1
                report.bytes += size
                logger.debug(f"Brought back: {rel}")
            if progress_callback:
                progress_callback(copy_progress(done, len(sources), report.files, 0))
    finally:
        if upserts:
            working.upsert_many(upserts)
        if changed:
            working.invalidate_faces_by_relpath(changed)
        if restored:
            working.remove_offloaded(restored)
            working.update_baseline(uuid, base, [])

    pairing = working.get_pairing()
    rules = [r for r in pairing.offload_rules if _rule_key(r) != _rule_key(selection)]
    if len(rules) != len(pairing.offload_rules):
        working.set_offload_rules(rules)
    return report
