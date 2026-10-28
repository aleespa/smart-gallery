"""Content hashes for deciding whether two same-named files are the same."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Iterable, Optional

from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.types import MirrorRow, key
from smart_gallery.progress import HASHING, ProgressCallback, ProgressEvent

logger = logging.getLogger(__name__)

CHUNK = 1024 * 1024
HASH_BATCH = 200  # fresh hashes committed per transaction


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
    *,
    rows: Optional[dict[str, MirrorRow]] = None,
    force: bool = False,
    done_before: int = 0,
    total: Optional[int] = None,
) -> dict[str, str]:
    """Hash the requested files that have no stored hash and store the results.

    ``rows`` (``repo.mirror_rows()``) may be passed to avoid re-reading the table.
    ``force`` re-reads every requested file, ignoring stored hashes.
    Progress is one event per file; ``done_before`` and ``total`` let several
    calls report one cumulative count (``total`` defaults to this call's files).

    Returns ``{key(relpath): hash}`` for every requested file that has or got a
    hash; unreadable files are logged and left out."""
    if rows is None:
        rows = repo.mirror_rows()
    relpaths = list(relpaths)
    if total is None:
        total = done_before + len(relpaths)
    result: dict[str, str] = {}
    fresh: list[tuple[str, str]] = []
    try:
        for done, relpath in enumerate(relpaths, done_before + 1):
            row = rows.get(key(relpath))
            if not force and row is not None and row.content_hash:
                result[key(relpath)] = row.content_hash
            else:
                try:
                    digest = hash_file(repo.drive_root / relpath)
                except OSError as exc:
                    logger.warning("Could not hash %s: %s", relpath, exc)
                else:
                    result[key(relpath)] = digest
                    fresh.append((relpath, digest))
                    if len(fresh) >= HASH_BATCH:
                        repo.set_hashes(fresh)
                        fresh = []
            if progress_callback:
                progress_callback(ProgressEvent(HASHING, done, total))
    finally:
        if fresh:  # keep finished work, even on Cancelled
            repo.set_hashes(fresh)
    return result
