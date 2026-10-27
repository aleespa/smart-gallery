"""Plain data types shared by the mirror store and the mirror services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional, Tuple

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


@dataclass
class MirrorPlan:
    """What a sync to one mirror would do, decided from catalog rows only."""

    copy_new: List[str] = field(default_factory=list)
    overwrite: List[str] = field(default_factory=list)
    trash: List[str] = field(default_factory=list)
    moves: List[Tuple[str, str]] = field(default_factory=list)
    only_on_mirror: List[str] = field(default_factory=list)
    changed_on_mirror: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    adopt: List[BaselineRow] = field(default_factory=list)
    offloaded_changed: List[str] = field(default_factory=list)
    offloaded_missing: List[str] = field(default_factory=list)

    @property
    def delete_count(self) -> int:
        return len(self.trash)

    @property
    def is_empty(self) -> bool:
        """True when nothing needs copying, trashing, moving, adopting or asking."""
        return not (
            self.copy_new
            or self.overwrite
            or self.trash
            or self.moves
            or self.adopt
            or self.only_on_mirror
            or self.changed_on_mirror
            or self.conflicts
        )
