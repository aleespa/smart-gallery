"""Bursts: photos taken within seconds of each other, and the marks on them.

No Qt: shots are built from the catalog alone (``relpath``, ``taken_ts``,
``camera``), grouped into bursts, and the user's per-shot marks are resolved
into the exact list of files to send to the Recycle Bin. Nothing here touches
the disk.

One shot is one stem in one folder (case-insensitive): every image file with
that stem (CR3 + JPG, JPG + HEIC...) plus its ``.xmp`` sidecar, which only
comes along when no other file of that stem stays behind.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import PurePosixPath

from smart_gallery.db.repository import GalleryRepository
from smart_gallery.storage.pairs import SIDECAR_EXT, FileRef

DEFAULT_GAP = 2
MIN_GAP = 1
MAX_GAP = 10
SPAN_CAP = 10
EDITED_SUFFIX = "-edited"

ShotKey = tuple[str, str]  # (folder casefolded, stem casefolded)


@dataclass(frozen=True)
class Shot:
    folder: str  # POSIX parent of relpath, "" at the root, first spelling seen
    stem: str  # first spelling seen
    taken_ts: int
    camera: str | None
    files: tuple[FileRef, ...]  # candidate files, ordered by relpath
    sidecar: FileRef | None
    shutter: str | None  # first file's
    aperture: float | None
    iso: int | None

    @property
    def key(self) -> ShotKey:
        return (self.folder.casefold(), self.stem.casefold())

    @property
    def formats(self) -> str:
        names: list[str] = []
        for f in self.files:
            name = f.ext.lstrip(".").upper()
            name = "JPG" if name == "JPEG" else name
            if name not in names:
                names.append(name)
        return "+".join(names)

    @property
    def all_files(self) -> tuple[FileRef, ...]:
        return self.files + ((self.sidecar,) if self.sidecar else ())

    @property
    def size_bytes(self) -> int:
        return sum(f.size_bytes for f in self.all_files)


@dataclass(frozen=True)
class Burst:
    shots: tuple[Shot, ...]  # in time order, then relpath
    folder: str
    key: ShotKey  # first shot's
    span: int  # last ts - first ts
    size_bytes: int


_COLUMNS = "id, relpath, ext, size_bytes, mtime_ns, media_type"
_IMAGES = (
    f"SELECT {_COLUMNS}, taken_ts, camera, shutter_speed_text, aperture, iso "
    "FROM media_items WHERE media_type = 'image' AND taken_ts IS NOT NULL"
)
_ALL = f"SELECT {_COLUMNS} FROM media_items"


def _ref(row: sqlite3.Row) -> FileRef:
    return FileRef(
        id=row["id"],
        relpath=row["relpath"],
        ext=(row["ext"] or "").lower(),
        size_bytes=row["size_bytes"] or 0,
        mtime_ns=row["mtime_ns"],
        media_type=row["media_type"],
    )


def _split(relpath: str) -> tuple[str, str]:
    path = PurePosixPath(relpath)
    folder = "" if str(path.parent) == "." else str(path.parent)
    return folder, path.stem


def load_shots(repo: GalleryRepository) -> list[Shot]:
    """Every shot with a capture time, skipping ``-edited`` and offloaded files."""
    try:
        offloaded = {p.casefold() for p in repo.offloaded_relpaths()}
    except sqlite3.OperationalError:
        offloaded = set()

    groups: dict[ShotKey, list[tuple[str, str, sqlite3.Row]]] = {}
    for row in repo.conn.execute(_IMAGES):
        relpath = row["relpath"]
        folder, stem = _split(relpath)
        if stem.casefold().endswith(EDITED_SUFFIX) or relpath.casefold() in offloaded:
            continue
        groups.setdefault((folder.casefold(), stem.casefold()), []).append(
            (folder, stem, row)
        )

    # A sidecar belongs to the shot only when every other file of its stem
    # (any kind, offloaded or without a capture time) is in the shot, as in
    # pairs.resolve_removals: it goes only with the last file it describes.
    sidecars: dict[ShotKey, FileRef] = {}
    stem_ids: dict[ShotKey, set[int]] = {}
    for row in repo.conn.execute(_ALL):
        folder, stem = _split(row["relpath"])
        key = (folder.casefold(), stem.casefold())
        if key not in groups:
            continue
        if (row["ext"] or "").lower() == SIDECAR_EXT:
            sidecars.setdefault(key, _ref(row))
        else:
            stem_ids.setdefault(key, set()).add(row["id"])

    shots: list[Shot] = []
    for key, members in groups.items():
        members.sort(key=lambda m: m[2]["relpath"].casefold())
        folder, stem, first = members[0]
        sidecar = sidecars.get(key)
        if sidecar is not None and not stem_ids[key] <= {m[2]["id"] for m in members}:
            sidecar = None
        shots.append(
            Shot(
                folder=folder,
                stem=stem,
                taken_ts=min(m[2]["taken_ts"] for m in members),
                camera=next((m[2]["camera"] for m in members if m[2]["camera"]), None),
                files=tuple(_ref(m[2]) for m in members),
                sidecar=sidecar,
                shutter=first["shutter_speed_text"],
                aperture=first["aperture"],
                iso=first["iso"],
            )
        )
    return shots


def group_bursts(
    shots: Sequence[Shot], gap: int = DEFAULT_GAP, span_cap: int = SPAN_CAP
) -> list[Burst]:
    """Runs of shots in one folder and camera, each within ``gap`` s of the last."""
    ordered = sorted(
        shots,
        key=lambda s: (
            s.folder.casefold(),
            s.camera or "",
            s.camera is None,
            s.taken_ts,
            s.files[0].relpath.casefold(),
        ),
    )
    groups: list[list[Shot]] = []
    current: list[Shot] = []
    for shot in ordered:
        if current:
            prev, first = current[-1], current[0]
            if (
                shot.folder.casefold() == prev.folder.casefold()
                and shot.camera == prev.camera
                and 0 <= shot.taken_ts - prev.taken_ts <= gap
                and shot.taken_ts - first.taken_ts <= span_cap
            ):
                current.append(shot)
                continue
            groups.append(current)
        current = [shot]
    if current:
        groups.append(current)

    bursts = [
        Burst(
            shots=tuple(g),
            folder=g[0].folder,
            key=g[0].key,
            span=g[-1].taken_ts - g[0].taken_ts,
            size_bytes=sum(s.size_bytes for s in g),
        )
        for g in groups
        if len(g) > 1
    ]
    bursts.sort(key=lambda b: (-len(b.shots), -b.size_bytes, b.shots[0].taken_ts))
    return bursts


def can_mark(burst: Burst, marks: AbstractSet[ShotKey], key: ShotKey) -> bool:
    """False only when marking ``key`` would leave no shot of the burst kept."""
    if key in marks:
        return True
    return any(s.key != key and s.key not in marks for s in burst.shots)


def burst_removals(shots: Iterable[Shot]) -> list[FileRef]:
    """Every file of the given shots, sidecars included, by relpath."""
    files: dict[int, FileRef] = {}
    for shot in shots:
        for f in shot.all_files:
            files[f.id] = f
    return sorted(files.values(), key=lambda f: f.relpath.casefold())


def _signature(shot: Shot) -> list[tuple[str, int, int | None]]:
    return [(f.relpath, f.size_bytes, f.mtime_ns) for f in shot.all_files]


def reconcile_marks(
    old: Mapping[ShotKey, Shot],
    marks: AbstractSet[ShotKey],
    bursts: Sequence[Burst],
) -> tuple[set[ShotKey], int]:
    """Marks that survive a regroup, and how many were dropped.

    A mark stays only if its shot is still in a burst with the same files, and
    no burst ends up with every shot marked. Reloads never add marks.
    """
    new = {s.key: s for b in bursts for s in b.shots}
    kept = {
        k
        for k in marks
        if k in new and k in old and _signature(old[k]) == _signature(new[k])
    }
    for burst in bursts:
        if all(s.key in kept for s in burst.shots):
            kept -= {s.key for s in burst.shots}
    return kept, len(marks) - len(kept)
