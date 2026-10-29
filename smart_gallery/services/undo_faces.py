"""undo-faces: revert the latest face-correction batch."""

from __future__ import annotations

from dataclasses import dataclass

from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.faces import ensure_not_mirror


@dataclass
class UndoReport:
    summary: str
    faces_restored: int


def undo_faces(repo: GalleryRepository) -> UndoReport:
    """Undo the last ``label-faces`` batch; raises if its faces changed since."""
    ensure_not_mirror(repo)
    info, restored = repo.undo_last()
    return UndoReport(info.summary, restored)
