"""CR3 photos that have no JPEG beside them, and the user's picks on them.

No Qt: candidates come from the catalog alone. Nothing here touches the disk.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import PurePosixPath

from smart_gallery.db.repository import GalleryRepository
from smart_gallery.storage.pairs import SIDECAR_EXT, FileRef

CR3_EXT = ".cr3"
BLOCKING_EXTS = frozenset({".jpg", ".jpeg", ".heic"})
JPEG_RATIO = 0.05


@dataclass(frozen=True)
class RawCandidate:
    folder: str
    stem: str
    raw: FileRef
    sidecar: FileRef | None
    taken_ts: int | None

    @property
    def key(self) -> str:
        return self.raw.relpath.casefold()

    @property
    def edited(self) -> bool:
        return self.sidecar is not None

    @property
    def size_bytes(self) -> int:
        return self.raw.size_bytes + (self.sidecar.size_bytes if self.sidecar else 0)


_EXTS = sorted({CR3_EXT, SIDECAR_EXT} | BLOCKING_EXTS)
_QUERY = (
    "SELECT id, relpath, ext, size_bytes, mtime_ns, media_type, taken_ts "
    f"FROM media_items WHERE lower(ext) IN ({', '.join('?' * len(_EXTS))})"
)


def load_raw_candidates(repo: GalleryRepository) -> list[RawCandidate]:
    """CR3s with no JPEG/HEIC of the same folder and stem, skipping offloaded."""
    try:
        offloaded = {p.casefold() for p in repo.offloaded_relpaths()}
    except sqlite3.OperationalError:
        offloaded = set()
    groups: dict[tuple[str, str], list[tuple[str, str, FileRef, int | None]]] = {}
    for row in repo.conn.execute(_QUERY, _EXTS):
        relpath = row["relpath"]
        path = PurePosixPath(relpath)
        folder = "" if str(path.parent) == "." else str(path.parent)
        ref = FileRef(
            id=row["id"],
            relpath=relpath,
            ext=(row["ext"] or "").lower(),
            size_bytes=row["size_bytes"] or 0,
            mtime_ns=row["mtime_ns"],
            media_type=row["media_type"],
        )
        groups.setdefault((folder.casefold(), path.stem.casefold()), []).append(
            (folder, path.stem, ref, row["taken_ts"])
        )

    out: list[RawCandidate] = []
    for members in groups.values():
        members.sort(key=lambda m: m[2].relpath)
        if any(m[2].ext in BLOCKING_EXTS for m in members):
            continue
        raws = [m for m in members if m[2].ext == CR3_EXT]
        sidecar = next((m[2] for m in members if m[2].ext == SIDECAR_EXT), None)
        for folder, stem, raw, ts in raws:
            if raw.relpath.casefold() in offloaded:
                continue
            out.append(
                RawCandidate(folder, stem, raw, sidecar if len(raws) == 1 else None, ts)
            )
    out.sort(key=lambda c: (c.taken_ts is None, c.taken_ts or 0, c.raw.relpath))
    return out


def in_scope(c: RawCandidate, folder: str | None) -> bool:
    """True when the candidate sits in ``folder`` or under it (None/"" = all)."""
    if not folder:
        return True
    f = folder.casefold()
    mine = c.folder.casefold()
    return mine == f or mine.startswith(f + "/")


def replacement_entries(cands: Iterable[RawCandidate]) -> list[FileRef]:
    """The CR3s to convert; the job finds each sidecar itself."""
    return sorted((c.raw for c in cands), key=lambda f: f.relpath.casefold())


def reconcile_picks(
    cands: Sequence[RawCandidate],
    picks: AbstractSet[str],
    before: Mapping[str, RawCandidate],
) -> set[str]:
    """Keep a pick only while its CR3 is still a candidate, unchanged."""
    now = {c.key: c for c in cands}
    kept: set[str] = set()
    for key in picks:
        cur, old = now.get(key), before.get(key)
        if (
            cur is not None
            and old is not None
            and cur.raw.size_bytes == old.raw.size_bytes
            and cur.raw.mtime_ns == old.raw.mtime_ns
        ):
            kept.add(key)
    return kept


def estimate_jpeg_bytes(cands: Iterable[RawCandidate]) -> int:
    return round(sum(c.raw.size_bytes for c in cands) * JPEG_RATIO)
