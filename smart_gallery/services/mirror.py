"""Pair a working drive with a mirror and preview a sync (never writes files)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from smart_gallery.config import DB_DIRNAME, iter_files
from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.hashing import ensure_hashes
from smart_gallery.mirror.plan import build_plan
from smart_gallery.mirror.types import MirrorError, MirrorPlan, MirrorRow, Peer, key
from smart_gallery.progress import ProgressCallback
from smart_gallery.services.sync import sync_drive

DELETE_GUARD_FRACTION = 0.05
TRASH_DAYS = 30

__all__ = [
    "DELETE_GUARD_FRACTION",
    "TRASH_DAYS",
    "MirrorError",
    "MirrorPreview",
    "pair_galleries",
    "plan_mirror_sync",
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


def _check(working: GalleryRepository, mirror: GalleryRepository) -> str:
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
    if next(iter_files(working.drive_root), None) is None:
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
