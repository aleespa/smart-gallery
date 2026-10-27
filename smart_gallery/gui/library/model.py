"""Qt-free query layer for the Library area.

The folder condition is local to the Library (it is not a ``FilterOptions``
field): it is ANDed onto the shared filter WHERE clause here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from smart_gallery.db.repository import GalleryRepository
from smart_gallery.db.where import build_where
from smart_gallery.mirror.types import key
from smart_gallery.organize.filters import FilterOptions

_ESCAPE = r"'\'"

# {d} is ASC or DESC. NULL dates stay last either way, and the relpath
# tiebreak keeps the order stable. Name sorts by folder first (the rtrim
# strips the file name), so each folder's files stay together and come
# before its subfolders'.
_PARENT = "rtrim(relpath, replace(relpath, '/', '')) COLLATE NOCASE"
_ORDER_BY = {
    "date": "(date_taken IS NULL), date_taken {d}, relpath {d}",
    "name": _PARENT + " {d}, relpath {d}",
    "size": "size_bytes {d}, relpath {d}",
}


@dataclass(frozen=True)
class FolderNode:
    name: str
    path: str  # POSIX, relative to the drive root; "" for the root
    count: int  # items in this folder and all folders below it
    children: tuple["FolderNode", ...]


@dataclass(frozen=True)
class LibraryQuery:
    folder: str = ""
    recursive: bool = True
    filters: Optional[FilterOptions] = None
    sort: Literal["date", "name", "size"] = "date"
    descending: bool = False


@dataclass(frozen=True)
class LibraryRow:
    id: int
    relpath: str
    media_type: str
    ext: str
    mtime_ns: Optional[int]
    duration_ms: Optional[int] = None  # videos, for the tile label
    date_taken: Optional[str] = None  # 'YYYY-MM-DD', for date groups
    size_bytes: Optional[int] = None  # for size groups
    offloaded: bool = False  # freed up: the file is kept on a mirror only


@dataclass(frozen=True)
class RowGroup:
    """``count`` consecutive rows from ``start`` shown under ``label``."""

    start: int
    count: int
    label: str


def _normalize_folder(folder: str) -> str:
    return folder.replace("\\", "/").strip("/")


def _fold(name: str) -> str:
    # SQLite LIKE and COLLATE NOCASE fold ASCII letters only, so folders that
    # differ by non-ASCII case stay distinct, as they do in the queries.
    return "".join(c.lower() if c.isascii() else c for c in name)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def folder_clause(folder: str, recursive: bool) -> tuple[str, list]:
    """SQL condition (no ``WHERE``) selecting the items under ``folder``, or
    ``("", [])`` when that is everything (root, recursive).

    LIKE is ASCII-case-insensitive, which matches the ``COLLATE NOCASE`` on
    ``relpath``; the folder text is escaped so ``_``/``%`` match literally.
    """
    folder = _normalize_folder(folder)
    if not folder:
        return ("", []) if recursive else ("relpath NOT LIKE '%/%'", [])
    prefix = _escape_like(folder) + "/%"
    sql = f"relpath LIKE ? ESCAPE {_ESCAPE}"
    params = [prefix]
    if not recursive:
        sql += f" AND relpath NOT LIKE ? ESCAPE {_ESCAPE}"
        params.append(_escape_like(folder) + "/%/%")
    return sql, params


def _library_where(q: LibraryQuery) -> tuple[str, list]:
    """Filter WHERE and folder clause, ANDed; ``("", [])`` if neither applies."""
    where, params = build_where(q.filters) if q.filters else ("", [])
    clause, clause_params = folder_clause(q.folder, q.recursive)
    if not clause:
        return where, list(params)
    if where:
        # build_where returns " WHERE <c1> AND <c2>..."; append to that.
        return f"{where} AND ({clause})", [*params, *clause_params]
    return f" WHERE {clause}", clause_params


def library_rows(repo: GalleryRepository, q: LibraryQuery) -> list[LibraryRow]:
    where, params = _library_where(q)
    order = _ORDER_BY.get(q.sort, _ORDER_BY["date"])
    order = order.format(d="DESC" if q.descending else "ASC")
    cur = repo.conn.execute(
        f"SELECT id, relpath, media_type, ext, mtime_ns, duration_ms,"
        f" date_taken, size_bytes"
        f" FROM media_items{where} ORDER BY {order}",
        params,
    )
    # An unmigrated catalog (no mirror tables) reads as nothing offloaded.
    offloaded = {key(rel) for rel in repo.offloaded_relpaths()}
    return [
        LibraryRow(
            r["id"],
            r["relpath"],
            r["media_type"],
            r["ext"],
            r["mtime_ns"],
            None if r["duration_ms"] is None else round(r["duration_ms"]),
            r["date_taken"],
            r["size_bytes"],
            key(r["relpath"]) in offloaded,
        )
        for r in cur
    ]


MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
NO_DATE = "No date"
TOP_FOLDER = "Top folder"
# (lower bound in bytes, label), largest first.
SIZE_BUCKETS = (
    (1 << 30, "1 GB and over"),
    (100 << 20, "100 MB to 1 GB"),
    (10 << 20, "10 to 100 MB"),
    (1 << 20, "1 to 10 MB"),
    (0, "Under 1 MB"),
)


def _month_label(date_taken: Optional[str]) -> str:
    try:
        year, month = date_taken[:4], int(date_taken[5:7])
        return f"{MONTHS[month - 1]} {int(year)}"
    except (TypeError, ValueError, IndexError):
        return NO_DATE


def _folder_label(relpath: str) -> str:
    folder, _, _ = relpath.rpartition("/")
    return folder or TOP_FOLDER


def _size_label(size_bytes: Optional[int]) -> str:
    size = size_bytes or 0
    return next(label for bound, label in SIZE_BUCKETS if size >= bound)


def group_rows(rows: list[LibraryRow], sort: str) -> list[RowGroup]:
    """Split sorted ``rows`` into runs that share a heading: the month taken,
    the folder, or a size band. Runs follow the row order, so a heading can
    only repeat if the order interleaves it."""
    if sort == "name":
        label_of, key_of = (lambda r: _folder_label(r.relpath)), _fold
    elif sort == "size":
        label_of, key_of = (lambda r: _size_label(r.size_bytes)), str
    else:
        label_of, key_of = (lambda r: _month_label(r.date_taken)), str
    groups: list[RowGroup] = []
    start, label, key = 0, "", None
    for i, row in enumerate(rows):
        row_label = label_of(row)
        row_key = key_of(row_label)
        if row_key != key:
            if i:
                groups.append(RowGroup(start, i - start, label))
            start, label, key = i, row_label, row_key
    if rows:
        groups.append(RowGroup(start, len(rows) - start, label))
    return groups


def library_stats(repo: GalleryRepository, q: LibraryQuery) -> tuple[int, int]:
    """(item count, total bytes) for the same selection as ``library_rows``."""
    where, params = _library_where(q)
    row = repo.conn.execute(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b "
        f"FROM media_items{where}",
        params,
    ).fetchone()
    return int(row["n"]), int(row["b"])


class _Builder:
    __slots__ = ("name", "path", "count", "children")

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.count = 0
        self.children: dict[str, _Builder] = {}

    def freeze(self) -> FolderNode:
        kids = sorted(self.children.values(), key=lambda b: (b.name.lower(), b.name))
        return FolderNode(
            self.name, self.path, self.count, tuple(b.freeze() for b in kids)
        )


def folder_tree(repo: GalleryRepository) -> FolderNode:
    """Folder hierarchy of the catalog with recursive item counts, built from
    one ``SELECT relpath``. Folders differing only by ASCII case are merged."""
    root = _Builder("", "")
    for (relpath,) in repo.conn.execute("SELECT relpath FROM media_items"):
        root.count += 1
        node = root
        parts = relpath.split("/")[:-1]
        for i, part in enumerate(parts):
            key = _fold(part)
            child = node.children.get(key)
            if child is None:
                child = _Builder(part, "/".join(parts[: i + 1]))
                node.children[key] = child
            child.count += 1
            node = child
    return root.freeze()
