"""What the Export page needs that isn't widgets. No Qt here.

The page edits an ``ExportSettings`` and its own filters (a ``FilterOptions``),
and runs the ``export`` command built by ``build_export_job``. ``filter_argv``
writes the filters back as the CLI's filter flags, so the job selects exactly
what the page counted with ``export_summary``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from smart_gallery.db.repository import GalleryRepository
from smart_gallery.db.where import build_where
from smart_gallery.gui.galleries.store import Gallery, is_connected
from smart_gallery.gui.jobspec import JobSpec
from smart_gallery.organize.filters import FilterOptions
from smart_gallery.services.export import split_for_jpeg_export

LEVELS = ("Year", "Month", "Model", "Lens")  # the CLI's --structure choices
LEVEL_LABELS = {"Year": "Year", "Month": "Month", "Model": "Camera", "Lens": "Lens"}
_EXAMPLE = {"Year": "2026", "Month": "06", "Model": "Canon EOS R6", "Lens": "RF24-70mm"}
TITLE = "Export · {label}"
DRY_RUN_SUFFIX = " (dry run)"
FOLDER_FORMAT = "Export %Y%m%d %H%M"  # no ":", which Windows refuses in names
_BAD_NAME_CHARS = set('<>:"/\\|?*')
BAD_FOLDER = (
    "The folder name can't contain < > : \" / \\ | ? * or end with a dot or space."
)


@dataclass
class ExportSettings:
    dest: str = ""
    folder: str = ""  # made under dest; empty means default_folder_name()
    keep_folders: bool = False  # the gallery's own folders (--mirror)
    structure: list[str] = field(default_factory=lambda: ["Year", "Month"])
    by_media_type: bool = True  # Photos/ and Videos/
    on_exist: str = "rename"
    jpeg_only: bool = False
    manifest: bool = True
    portable_db: bool = False
    dry_run: bool = False


def _number(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def filter_argv(filters: FilterOptions | None) -> list[str]:
    """The CLI filter flags that select what ``filters`` selects."""
    if filters is None:
        return []
    argv: list[str] = []
    for flag, values in (
        ("--file-types", filters.filetypes),
        ("--extensions", filters.extensions),
        ("--cameras", filters.cameras),
        ("--lenses", filters.lenses),
        ("--people", filters.people),
        ("--person-ids", filters.person_ids),
    ):
        if values:
            argv += [flag, *map(str, values)]
    for low_flag, high_flag, bounds in (
        ("--date-start", "--date-end", filters.date_range),
        ("--min-aperture", "--max-aperture", filters.aperture_range),
        ("--min-iso", "--max-iso", filters.iso_range),
        ("--min-shutter-speed", "--max-shutter-speed", filters.shutter_speed_range),
    ):
        low, high = bounds or (None, None)
        for flag, value in ((low_flag, low), (high_flag, high)):
            if value is not None and value != "":
                text = value.isoformat() if hasattr(value, "isoformat") else value
                argv += [flag, _number(text)]
    if filters.people_match == "all" and (filters.people or filters.person_ids):
        argv += ["--people-match", "all"]
    return argv


def default_folder_name(now: datetime | None = None) -> str:
    """The export folder's name when none is typed, e.g. Export 20261028 2217."""
    return (now or datetime.now()).strftime(FOLDER_FORMAT)


def folder_name(settings: ExportSettings, now: datetime | None = None) -> str:
    """The folder the export goes into. Raises ``ValueError`` for a name
    Windows can't use or one that would leave the destination."""
    name = settings.folder.strip() or default_folder_name(now)
    if (
        name in (".", "..")
        or name.endswith((".", " "))
        or any(c in _BAD_NAME_CHARS or ord(c) < 32 for c in name)
    ):
        raise ValueError(BAD_FOLDER)
    return name


def build_export_job(
    gallery: Gallery | None,
    settings: ExportSettings,
    filters: FilterOptions | None,
    now: datetime | None = None,
) -> JobSpec:
    """The ``export`` job for ``settings``, into a new folder under the
    destination named at ``now`` unless one is given. Raises ``ValueError``
    with what is missing. Export only reads the gallery, so the job never
    writes a catalog."""
    if gallery is None:
        raise ValueError("Open a gallery to export from it.")
    if not is_connected(gallery):
        raise ValueError(f"{gallery.label} is not connected.")
    if not settings.dest.strip():
        raise ValueError("Choose a destination folder.")
    if not settings.keep_folders and not settings.structure:
        raise ValueError("Choose at least one folder level.")
    dest = Path(settings.dest.strip()) / folder_name(settings, now)
    argv = ["export", "--from", str(gallery.root), "--to", str(dest)]
    if settings.keep_folders:
        argv.append("--mirror")
    else:
        argv += ["--structure", *settings.structure]
        if not settings.by_media_type:
            argv.append("--no-by-media-type")
    argv += ["--on-exist", settings.on_exist]
    argv += filter_argv(filters)
    if not settings.manifest:
        argv.append("--no-manifest")
    if settings.portable_db:
        argv.append("--portable-db")
    if settings.jpeg_only:
        argv.append("--jpeg-only")
    if settings.dry_run:
        argv.append("--dry-run")
    title = TITLE.format(label=gallery.label)
    if settings.dry_run:
        title += DRY_RUN_SUFFIX
    return JobSpec(title, (tuple(argv),), writes_catalog=False)


def example_path(settings: ExportSettings, now: datetime | None = None) -> str:
    """Where a typical photo would land, as a path under the destination."""
    name = "IMG_0001.jpg" if settings.jpeg_only else "IMG_0001.CR3"
    folder = settings.folder.strip() or default_folder_name(now)
    if settings.keep_folders:
        return f"{folder}/(the gallery's folders)/{name}"
    parts = [folder, "Photos"] if settings.by_media_type else [folder]
    parts += [_EXAMPLE[level] for level in settings.structure]
    return str(PurePosixPath(*parts, name))


@dataclass(frozen=True)
class ExportSummary:
    matched: int = 0
    size_bytes: int = 0
    as_is: int = 0  # with JPEG only: JPEGs and videos, copied as they are
    raws: int = 0  # with JPEG only: converted
    left_out: int = 0  # with JPEG only: everything else


def export_summary(
    repo: GalleryRepository, filters: FilterOptions | None
) -> ExportSummary:
    """How many files ``filters`` selects, and how a JPEG-only export splits
    them (the same split the export makes)."""
    where, params = build_where(filters) if filters else ("", [])
    rows = repo.conn.execute(
        f"SELECT relpath, size_bytes FROM media_items{where}", params
    ).fetchall()
    as_is, raws, left_out = split_for_jpeg_export(r["relpath"] for r in rows)
    return ExportSummary(
        matched=len(rows),
        size_bytes=sum(r["size_bytes"] or 0 for r in rows),
        as_is=len(as_is),
        raws=len(raws),
        left_out=len(left_out),
    )
