"""File moves for mirror sync: the per-drive trash and purging it.

"Delete" during a mirror sync always means a move into
``<root>/.smart_gallery/trash/<stamp>/<relpath>``, never a permanent delete.
The sync walk skips ``.smart_gallery``, so trashed files are never catalogued.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from smart_gallery.config import DB_DIRNAME

STAMP_FORMAT = "%Y%m%d-%H%M%S"


def trash_dir(root: Path) -> Path:
    return Path(root) / DB_DIRNAME / "trash"


def run_stamp(now: Optional[datetime] = None) -> str:
    """The trash folder name shared by every file trashed in one run."""
    return (now or datetime.now()).strftime(STAMP_FORMAT)


def unique_path(path: Path) -> Path:
    """``path``, or ``name_1.ext``, ``name_2.ext``, ... if it already exists."""
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def move_to_trash(root: Path, relpath: str, stamp: str) -> Path:
    """Move ``root/relpath`` into this run's trash folder; return where it went."""
    source = Path(root) / relpath
    if not source.is_file():
        raise FileNotFoundError(f"{source} is missing")
    dest = unique_path(trash_dir(root) / stamp / relpath)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, dest)
    return dest


def restore_from_trash(trashed: Path, original: Path) -> None:
    """Best-effort undo of ``move_to_trash`` after a failed replacement."""
    try:
        if not original.exists():
            os.rename(trashed, original)
    except OSError as exc:
        logger.warning(f"Could not restore {original} from {trashed}: {exc}")


def purge_trash_dir(root: Path, older_than_days: int, now: datetime) -> int:
    """Remove trash folders whose stamp is older than ``older_than_days``.
    Folders whose name is not a stamp are left alone."""
    folder = trash_dir(root)
    if not folder.is_dir():
        return 0
    cutoff = now - timedelta(days=older_than_days)
    removed = 0
    for entry in sorted(folder.iterdir()):
        if not entry.is_dir():
            continue
        try:
            stamped = datetime.strptime(entry.name, STAMP_FORMAT)
        except ValueError:
            continue
        if stamped < cutoff:
            shutil.rmtree(entry)
            removed += 1
    return removed
