"""Which History jobs can be undone. No Qt here.

Only face corrections (``label-faces``) can be undone, newest first. Each one
logs the undo step it saved; a job is undoable while that step is still the
newest one in its gallery's catalog.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from smart_gallery.db import GalleryRepository
from smart_gallery.db.face_undo import parse_undo_step
from smart_gallery.gui.history.store import JobRecord

UNDO_JOB = "Undo face change"
UNDO_TIP = "Undo: {summary}"
NEWER_FIRST = "Undo the newer face changes first."
GONE = "Already undone, or too old to undo."
NO_GALLERY = "This job's gallery isn't available."


@dataclass(frozen=True)
class UndoTarget:
    root: str
    step: int
    created_at: str

    def argv(self, *extra: str) -> tuple[str, ...]:
        return ("undo-faces", self.root, "--step", str(self.step), *extra)


@dataclass(frozen=True)
class UndoCheck:
    can_undo: bool
    reason: str  # the tooltip when it can, why not when it can't


def undo_target(record: JobRecord | None) -> UndoTarget | None:
    """The undo step a finished face-correction job saved, if any."""
    if record is None or record.status != "ok":
        return None
    roots = [a[1] for a in record.argvs if len(a) > 1 and a[0] == "label-faces"]
    step = parse_undo_step(record.output_tail)
    if not roots or step is None:
        return None
    return UndoTarget(roots[-1], *step)


def check_undo(target: UndoTarget) -> UndoCheck:
    """Whether ``target`` is still the newest undo step of its catalog."""
    try:
        with GalleryRepository.open(target.root, read_only=True) as repo:
            newest = repo.last_undo()
            mine = repo.undo_step(target.step)
    except (OSError, ValueError, sqlite3.Error):
        return UndoCheck(False, NO_GALLERY)
    if mine is None or mine.created_at != target.created_at:
        return UndoCheck(False, GONE)
    if newest is None or newest.batch_id != mine.batch_id:
        return UndoCheck(False, NEWER_FIRST)
    return UndoCheck(True, UNDO_TIP.format(summary=mine.summary))
