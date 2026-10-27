"""Content hashes for deciding whether two same-named files are the same."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Iterable, Optional

from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.types import key
from smart_gallery.progress import HASHING, ProgressCallback, counted

logger = logging.getLogger(__name__)

CHUNK = 1024 * 1024


def hash_file(path: Path) -> str:
    """blake2b (20-byte digest) of a file's content, as lowercase hex."""
    digest = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_hashes(
    repo: GalleryRepository,
    relpaths: Iterable[str],
    progress_callback: Optional[ProgressCallback] = None,
) -> dict[str, str]:
    """Hash the requested files that have no stored hash and store the results.

    Returns ``{key(relpath): hash}`` for every requested file that has or got a
    hash; unreadable files are logged and left out."""
    rows = repo.mirror_rows()
    result: dict[str, str] = {}
    fresh: list[tuple[str, str]] = []
    for relpath in counted(list(relpaths), HASHING, progress_callback):
        row = rows.get(key(relpath))
        if row is not None and row.content_hash:
            result[key(relpath)] = row.content_hash
            continue
        try:
            digest = hash_file(repo.drive_root / relpath)
        except OSError as exc:
            logger.warning("Could not hash %s: %s", relpath, exc)
            continue
        result[key(relpath)] = digest
        fresh.append((relpath, digest))
    if fresh:
        repo.set_hashes(fresh)
    return result
