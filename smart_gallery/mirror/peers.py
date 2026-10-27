"""Find a paired catalog on whichever drive letter it is mounted now."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from smart_gallery.db import GalleryRepository
from smart_gallery.gui.galleries.store import Gallery, existing_drives, locate
from smart_gallery.mirror.types import MirrorError, Pairing, Peer


def _uuid_at(path: Path) -> Optional[str]:
    try:
        with GalleryRepository.open(path, read_only=True) as repo:
            return repo.get_meta("db_uuid")
    except (OSError, sqlite3.Error, FileNotFoundError):
        return None


def find_peer(peer: Peer) -> Optional[Path]:
    """The peer's catalog: at its stored path, else under another drive letter."""
    stored = Path(peer.db_path)
    if stored.is_file() and _uuid_at(stored) == peer.uuid:
        return stored
    found = locate(Gallery(peer.uuid, peer.db_path, ""), existing_drives())
    return Path(found.db_path) if found else None


def choose_mirror(pairing: Pairing, override: Optional[Path] = None) -> Peer:
    if override is not None:
        uuid = _uuid_at(Path(override))
        for peer in pairing.peers:
            if uuid and peer.uuid == uuid:
                return peer
        raise MirrorError("That catalog is not a mirror of this gallery.")
    if len(pairing.peers) == 1:
        return pairing.peers[0]
    if not pairing.peers:
        raise MirrorError("This gallery has no mirror.")
    raise MirrorError("This gallery has several mirrors; choose one with --mirror.")
