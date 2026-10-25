"""RAW + JPEG pairs stored twice in one folder, and the keep choices on them.

No Qt: pairs are found from the catalog alone (``relpath``, ``ext``, sizes),
scoped to a folder, and the user's per-pair choices are resolved into the exact
list of files to send to the Recycle Bin. Nothing here touches the disk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from smart_gallery.db.repository import GalleryRepository

RAW_EXTS = frozenset({".cr2", ".cr3"})
JPEG_EXTS = frozenset({".jpg", ".jpeg"})
SIDECAR_EXT = ".xmp"
KEEP_RAW, KEEP_JPEG, KEEP_BOTH = "raw", "jpeg", "both"

PairKey = tuple[str, str]  # (raw relpath casefolded, jpeg relpath casefolded)


@dataclass(frozen=True)
class FileRef:
    id: int
    relpath: str
    ext: str
    size_bytes: int
    mtime_ns: int | None
    media_type: str


@dataclass(frozen=True)
class Pair:
    folder: str  # POSIX parent of relpath, "" at the root, first spelling seen
    stem: str  # first spelling seen
    raw: FileRef
    jpeg: FileRef
    sidecar: FileRef | None

    @property
    def key(self) -> PairKey:
        return (self.raw.relpath.casefold(), self.jpeg.relpath.casefold())


_ALL_EXTS = sorted(RAW_EXTS | JPEG_EXTS | {SIDECAR_EXT})
_QUERY = (
    "SELECT id, relpath, ext, size_bytes, mtime_ns, media_type FROM media_items "
    f"WHERE lower(ext) IN ({', '.join('?' * len(_ALL_EXTS))})"
)


def find_pairs(repo: GalleryRepository) -> list[Pair]:
    """Every RAW paired with every JPEG of the same folder and stem."""
    groups: dict[tuple[str, str], list[tuple[str, str, FileRef]]] = {}
    for row in repo.conn.execute(_QUERY, _ALL_EXTS):
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
            (folder, path.stem, ref)
        )

    pairs: list[Pair] = []
    for members in groups.values():
        members.sort(key=lambda m: m[2].relpath)
        raws = [m for m in members if m[2].ext in RAW_EXTS]
        jpegs = [m for m in members if m[2].ext in JPEG_EXTS]
        if not raws or not jpegs:
            continue
        sidecar = next((m[2] for m in members if m[2].ext == SIDECAR_EXT), None)
        folder, stem = members[0][0], members[0][1]
        for _, _, raw in raws:
            for _, _, jpeg in jpegs:
                pairs.append(Pair(folder, stem, raw, jpeg, sidecar))
    pairs.sort(
        key=lambda p: (
            p.folder.casefold(),
            p.stem.casefold(),
            p.raw.relpath,
            p.jpeg.relpath,
        )
    )
    return pairs


def in_scope(pair: Pair, folder: str | None) -> bool:
    """True when the pair sits in ``folder`` or under it (None/"" = everything)."""
    if not folder:
        return True
    f = folder.casefold()
    mine = pair.folder.casefold()
    return mine == f or mine.startswith(f + "/")


def resolve_removals(
    pairs: Sequence[Pair], choices: Mapping[PairKey, str]
) -> list[FileRef]:
    """Files to recycle for the given choices. A keep always beats a removal."""
    removed: dict[int, FileRef] = {}
    kept: set[int] = set()
    for pair in pairs:
        choice = choices.get(pair.key)
        if choice == KEEP_RAW:
            kept.add(pair.raw.id)
            removed[pair.jpeg.id] = pair.jpeg
        elif choice == KEEP_JPEG:
            kept.add(pair.jpeg.id)
            removed[pair.raw.id] = pair.raw
        elif choice == KEEP_BOTH:
            kept.update((pair.raw.id, pair.jpeg.id))
    result = {i: f for i, f in removed.items() if i not in kept}

    # A sidecar goes only when every RAW in its pairs goes.
    raws_of: dict[int, tuple[FileRef, list[int]]] = {}
    for pair in pairs:
        if pair.sidecar is not None:
            raws_of.setdefault(pair.sidecar.id, (pair.sidecar, []))[1].append(
                pair.raw.id
            )
    for sidecar, raw_ids in raws_of.values():
        if all(i in result for i in raw_ids):
            result[sidecar.id] = sidecar
    return sorted(result.values(), key=lambda f: f.relpath.casefold())
