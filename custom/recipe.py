"""Reusable per-device import recipe.

Replaces the old hand-edited ``organize_canon.py`` / ``organize_phone.py`` (which
each duplicated a ``_drive_db`` helper and the whole import body). A device config
is now a small declarative ``ImportRecipe``; ``run_recipe`` does the work, opening
each drive's catalog at its root while placing files into the target subfolders.

The catalog must already exist on each target drive — run ``smart-gallery init
<drive>`` once per drive before importing.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from loguru import logger

from smart_gallery.db import GalleryRepository
from smart_gallery.organize import FilterOptions, Options
from smart_gallery.services import import_media


@dataclass(frozen=True)
class ImportRecipe:
    source: Path
    photo_targets: List[Path] = field(default_factory=list)
    video_targets: List[Path] = field(default_factory=list)
    photo_extensions: List[str] = field(default_factory=lambda: [".cr3", ".jpg"])
    video_extensions: List[str] = field(default_factory=lambda: [".mp4"])
    options: Options = field(
        default_factory=lambda: Options(
            by_media_type=False, structure=["Year", "Month"], on_exist="skip"
        )
    )


def _drive_root(target: Path) -> Path:
    """The drive root that owns ``target`` (where its catalog lives)."""
    anchor = Path(target).anchor
    return Path(anchor) if anchor else Path(target)


def _import_to(target: Path, source: Path, options: Options, query: FilterOptions) -> None:
    root = _drive_root(target)
    try:
        with GalleryRepository.open(root) as repo:
            import_media(repo, [source], output_dir=target, options=options, query=query)
    except FileNotFoundError:
        logger.error(
            f"No catalog at {root}. Run `smart-gallery init {root}` once before importing."
        )


def run_recipe(recipe: ImportRecipe) -> None:
    for target in recipe.photo_targets:
        _import_to(
            target, recipe.source, recipe.options,
            FilterOptions(filetypes=["image"], extensions=recipe.photo_extensions),
        )
    for target in recipe.video_targets:
        _import_to(
            target, recipe.source, recipe.options,
            FilterOptions(filetypes=["video"], extensions=recipe.video_extensions),
        )
