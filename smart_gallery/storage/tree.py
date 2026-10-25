"""Storage size trees (folder and file type) built from the catalog frame.

No Qt: the trees are plain dataclasses, built off the GUI thread and shown by
the Storage page. Every number comes from the catalog's ``relpath``, ``ext``
and ``size_bytes`` columns; no file is read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from smart_gallery import colors

FOLDER, TYPE = "folder", "type"  # StorageNode.kind
NO_EXT = "(none)"
_UNITS = ("B", "KB", "MB", "GB", "TB")


@dataclass
class StorageNode:
    name: str  # folder name, or type label ("CR3")
    kind: str  # FOLDER | TYPE
    relpath: str | None  # folder this row opens, POSIX, "" = root; None = none
    bytes: int
    files: int
    ext: str | None = None  # type label for TYPE rows, else None
    children: list["StorageNode"] = field(default_factory=list)


@dataclass(frozen=True)
class StorageTree:
    by_folder: StorageNode  # root FOLDER node, relpath ""
    by_type: StorageNode  # root FOLDER node, relpath "", children are TYPE nodes
    type_colors: dict[str, str]  # type label -> "#RRGGBB"
    total_bytes: int
    total_files: int


def type_label(ext: str | None) -> str:
    """The extension upper-cased without its dot; ``(none)`` when missing."""
    if ext is None or pd.isna(ext):
        return NO_EXT
    label = str(ext).lstrip(".").upper()
    return label or NO_EXT


def format_bytes(n: int) -> str:
    """1024-based size with three significant figures (``45.6 GB``)."""
    if n < 1024:
        return f"{int(n)} B"
    value = float(n)
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    decimals = 2 if value < 10 else 1 if value < 100 else 0
    return f"{value:.{decimals}f} {_UNITS[unit]}"


def _order(nodes: list[StorageNode]) -> list[StorageNode]:
    return sorted(nodes, key=lambda n: (-n.bytes, n.name))


class _Dir:
    """A folder in the merge trie; keyed by lower-cased segment."""

    def __init__(self, name: str, relpath: str) -> None:
        self.name = name
        self.relpath = relpath
        self.children: dict[str, _Dir] = {}
        self.own: dict[str, list[int]] = {}  # type label -> [bytes, files]

    def descend(self, display_folder: str) -> "_Dir":
        node = self
        for segment in filter(None, display_folder.split("/")):
            child = node.children.get(segment.lower())
            if child is None:
                path = f"{node.relpath}/{segment}" if node.relpath else segment
                child = node.children[segment.lower()] = _Dir(segment, path)
            node = child
        return node


def _folder_node(d: _Dir, only: str | None) -> StorageNode | None:
    """Convert a trie folder; ``only`` limits it to one type, no type rows."""
    subs = [n for c in d.children.values() if (n := _folder_node(c, only))]
    own = {k: v for k, v in d.own.items() if only is None or k == only}
    types = [StorageNode(k, TYPE, d.relpath, v[0], v[1], ext=k) for k, v in own.items()]
    total = sum(n.bytes for n in subs) + sum(t.bytes for t in types)
    files = sum(n.files for n in subs) + sum(t.files for t in types)
    if not subs and not types:
        return None
    rows = _order(subs) + (_order(types) if only is None else [])
    return StorageNode(d.name, FOLDER, d.relpath, total, files, children=rows)


def build_storage(df: pd.DataFrame, root_name: str) -> StorageTree | None:
    """Both trees for a catalog frame, or None when it has no rows."""
    if df.empty:
        return None
    frame = pd.DataFrame(
        {
            "folder": df["relpath"].astype(str).str.rpartition("/")[0],
            "type": [type_label(e) for e in df["ext"]],
            "size": pd.to_numeric(df["size_bytes"], errors="coerce")
            .fillna(0)
            .astype("int64"),
        }
    )
    frame["key"] = frame["folder"].str.lower()
    groups = frame.groupby(["key", "type"], sort=False).agg(
        folder=("folder", "first"), bytes=("size", "sum"), files=("size", "size")
    )

    root = _Dir(root_name, "")
    type_totals: dict[str, int] = {}
    for (_, label), row in groups.iterrows():
        entry = root.descend(row["folder"]).own.setdefault(label, [0, 0])
        entry[0] += int(row["bytes"])
        entry[1] += int(row["files"])
        type_totals[label] = type_totals.get(label, 0) + int(row["bytes"])

    by_folder = _folder_node(root, None)
    ranked = sorted(type_totals, key=lambda t: (-type_totals[t], t))
    ranks = colors.categorical()
    type_colors = {
        t: ranks[i] if i < colors.CATEGORY_COUNT else colors.NEUTRAL
        for i, t in enumerate(ranked)
    }

    type_rows = []
    for label in ranked:
        sub = _folder_node(root, label)
        files = sub.files
        type_rows.append(
            StorageNode(
                label,
                TYPE,
                None,
                sub.bytes,
                files,
                ext=label,
                children=[c for c in sub.children if c.kind == FOLDER],
            )
        )
    total_bytes, total_files = by_folder.bytes, by_folder.files
    by_type = StorageNode(
        root_name, FOLDER, "", total_bytes, total_files, children=type_rows
    )
    return StorageTree(by_folder, by_type, type_colors, total_bytes, total_files)


def folder_sizes(tree: StorageTree | None) -> dict[str, int]:
    """Each folder's total bytes (its subfolders included), keyed by its
    case-folded relpath (``""`` = root)."""
    sizes: dict[str, int] = {}
    pending = [tree.by_folder] if tree is not None else []
    while pending:
        node = pending.pop()
        if node.kind != FOLDER:
            continue
        sizes[(node.relpath or "").casefold()] = node.bytes
        pending.extend(node.children)
    return sizes
