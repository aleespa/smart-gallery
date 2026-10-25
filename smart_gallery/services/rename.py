"""Rename a gallery: the name lives in the catalog, so it travels with the drive."""

from smart_gallery.db import GalleryRepository

NAME_KEY = "drive_label"  # the meta key ``init --label`` writes
MAX_NAME = 80


def rename_gallery(repo: GalleryRepository, name: str) -> str:
    """Store ``name`` (trimmed) as the gallery's name and return it.

    Raises ValueError for a blank name or one longer than ``MAX_NAME``.
    """
    name = " ".join(name.split())  # trim, and fold runs of whitespace
    if not name:
        raise ValueError("A gallery name cannot be blank.")
    if len(name) > MAX_NAME:
        raise ValueError(f"A gallery name can be at most {MAX_NAME} characters.")
    repo.set_meta(NAME_KEY, name)
    return name
