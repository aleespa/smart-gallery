"""undo-faces: revert the latest face-correction batch."""

from __future__ import annotations

from dataclasses import dataclass

from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.faces import ensure_not_mirror


@dataclass
class UndoReport:
    summary: str
    faces_restored: int


def undo_faces(repo: GalleryRepository, step: int | None = None) -> UndoReport:
    """Undo the last ``label-faces`` batch; raises if its faces changed since,
    or, given ``step``, if the newest batch is not that one."""
    ensure_not_mirror(repo)
    info, restored = repo.undo_last(step)
    return UndoReport(info.summary, restored)


def forget_undo(repo: GalleryRepository, step: int | None = None) -> str:
    """Drop the newest undo step (no face changes); returns its summary."""
    ensure_not_mirror(repo)
    info = repo.forget_last_undo(step)
    if info is None:
        raise ValueError("Nothing to undo.")
    return info.summary
