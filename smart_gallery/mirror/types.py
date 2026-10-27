"""Plain data types shared by the mirror store and the mirror services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

from smart_gallery.organize.filters import FilterOptions


def key(relpath: str) -> str:
    """Case-insensitive comparison key for a relpath."""
    return relpath.casefold()


@dataclass(frozen=True)
class Peer:
    uuid: str
    db_path: str


@dataclass
class Pairing:
    """A catalog's role and its peers (a mirror has exactly one peer)."""

    role: Literal["working", "mirror"]
    peers: List[Peer]
    offload_rules: List[FilterOptions] = field(default_factory=list)


@dataclass(frozen=True)
class MirrorRow:
    relpath: str
    size_bytes: int
    mtime_ns: int
    content_hash: Optional[str]


@dataclass(frozen=True)
class BaselineRow:
    relpath: str
    size_bytes: int
    working_mtime_ns: int
    mirror_mtime_ns: int
