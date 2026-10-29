"""smart-gallery command-line and terminal interfaces.

Commands include init, import, sync, compare, export, dashboard, report, and ui.
Filter flags are shared across import/export/report via a parent parser and
compiled to a FilterOptions.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from smart_gallery.config import resolve_db_path
from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.faces import ensure_not_mirror
from smart_gallery.mirror.peers import choose_mirror, find_peer
from smart_gallery.organize import FilterOptions, Options, normalize_extensions
from smart_gallery.progress import (
    CANCELLED_EXIT,
    PROGRESS_ENV,
    Cancelled,
    progress_reporter,
)
from smart_gallery.services import (
    cluster_faces,
    file_faces,
    compare_galleries,
    convert_cr3,
    export_media,
    find_faces,
    format_compare_report,
    import_media,
    identify_faces,
    init_drive,
    label_faces,
    forget_undo,
    undo_faces,
    LabelEntry,
    MirrorChoices,
    MirrorError,
    apply_mirror_sync,
    bring_back,
    check_previewed,
    free_up_space,
    pair_galleries,
    plan_mirror_sync,
    read_label_list,
    read_list,
    recycle_files,
    rename_gallery,
    scan_faces,
    split_person,
    sync_drive,
    write_compare_report,
)
from smart_gallery.storage.tree import format_bytes

# Long-running commands whose per-file details (copied/skipped files, ExifTool
# chatter) go to a log file for the run instead of the terminal.
DETAIL_LOG_COMMANDS = {
    "init",
    "import",
    "sync",
    "export",
    "scan-faces",
    "file-faces",
    "label-faces",
    "undo-faces",
    "find-faces",
    "recycle",
    "sync-mirror",
    "free-up",
    "bring-back",
}
LOG_DIR = Path("logs")


def _configure_logging(command: str) -> Optional[Path]:
    """INFO and above on the terminal; for long-running commands, everything
    (DEBUG) also goes to ``logs/<command>-<timestamp>.log``. Returns that path."""
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    if command not in DETAIL_LOG_COMMANDS:
        return None
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = LOG_DIR.resolve() / f"{command}-{stamp}.log"
    try:
        LOG_DIR.mkdir(exist_ok=True)
        logger.add(path, level="DEBUG", encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Could not open a log file in {LOG_DIR.resolve()}: {exc}")
        return None
    return path


DRIVE_HELP = "Drive root (e.g. E:/), any folder on it, or a gallery.db path."
PERSON_ID_HELP = "Numeric person id, as listed by `people`."
STRUCTURE_HELP = "Folder levels to create, in order (default: Year Month)."
ON_EXIST_HELP = "On a name clash: rename (append _1, _2, ...) or skip the copy."


def _add_filter_args(parser: argparse.ArgumentParser) -> None:
    """The shared query flags (import/export/report), as a "Filters" group."""
    g = parser.add_argument_group("Filters")
    g.add_argument(
        "--file-types",
        nargs="+",
        choices=["image", "video", "other"],
        help="Keep only these media types.",
    )
    g.add_argument(
        "--extensions", nargs="+", help="Keep only these extensions, e.g. .jpg .cr3."
    )
    g.add_argument(
        "--date-start",
        type=date.fromisoformat,
        help="Taken on or after this date (YYYY-MM-DD).",
    )
    g.add_argument(
        "--date-end",
        type=date.fromisoformat,
        help="Taken on or before this date (YYYY-MM-DD).",
    )
    g.add_argument(
        "--cameras", nargs="+", help="Camera models to keep (exact, case-sensitive)."
    )
    g.add_argument(
        "--lenses", nargs="+", help="Lens models to keep (exact, case-sensitive)."
    )
    g.add_argument("--min-aperture", type=float, help="Smallest f-number to keep.")
    g.add_argument("--max-aperture", type=float, help="Largest f-number to keep.")
    g.add_argument("--min-iso", type=int, help="Lowest ISO to keep.")
    g.add_argument("--max-iso", type=int, help="Highest ISO to keep.")
    g.add_argument("--min-shutter-speed", help="Fastest shutter to keep, e.g. 1/250.")
    g.add_argument("--max-shutter-speed", help="Slowest shutter to keep, e.g. 2s.")
    g.add_argument(
        "--people",
        nargs="+",
        metavar="NAME",
        help="Only media containing these named people (face recognition).",
    )
    g.add_argument(
        "--person-ids",
        nargs="+",
        type=int,
        metavar="ID",
        dest="person_ids",
        help="Only media containing these person cluster ids (works before naming).",
    )
    g.add_argument(
        "--people-match",
        choices=["any", "all"],
        default="any",
        dest="people_match",
        help="With several people: photos with any of them, or with all of them.",
    )


def _organize_args(g) -> None:
    g.add_argument(
        "--by-media-type",
        action="store_true",
        default=True,
        help="Put images under Photos/ and videos under Videos/ (default).",
    )
    g.add_argument(
        "--no-by-media-type",
        action="store_false",
        dest="by_media_type",
        help="Don't split images and videos into Photos/ and Videos/.",
    )
    g.add_argument(
        "--structure",
        nargs="+",
        default=["Year", "Month"],
        choices=["Year", "Month", "Model", "Lens"],
        help=STRUCTURE_HELP,
    )
    g.add_argument(
        "--on-exist", choices=["rename", "skip"], default="rename", help=ON_EXIST_HELP
    )


def _clustering_args(g, *, eps: float, min_samples: int, min_cluster_size: int):
    g.add_argument(
        "--algo",
        choices=["hdbscan", "dbscan"],
        default="hdbscan",
        help="Clustering algorithm.",
    )
    g.add_argument(
        "--eps", type=float, default=eps, help="DBSCAN cosine epsilon (radius)."
    )
    g.add_argument(
        "--min-samples", type=int, default=min_samples, help="DBSCAN min samples."
    )
    g.add_argument(
        "--min-cluster-size",
        type=int,
        default=min_cluster_size,
        help="HDBSCAN min cluster size.",
    )
    g.add_argument(
        "--pca",
        type=int,
        default=0,
        metavar="DIMS",
        help="Reduce embeddings to DIMS via PCA before clustering "
        "(big speedup for large libraries; try 128). 0 = off.",
    )


def _add_mirror_arg(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("Mirror")
    g.add_argument(
        "--mirror",
        type=Path,
        default=None,
        help="The mirror drive or its gallery.db (needed with several mirrors).",
    )


def _add_folder_arg(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("Folders")
    g.add_argument(
        "--folder",
        action="append",
        default=[],
        metavar="PATH",
        help="A folder on the drive to include (repeatable).",
    )


def build_filter_query(args) -> FilterOptions:
    date_range = None
    if args.date_start or args.date_end:
        date_range = (args.date_start, args.date_end)
    aperture_range = None
    if args.min_aperture is not None or args.max_aperture is not None:
        aperture_range = (args.min_aperture, args.max_aperture)
    iso_range = None
    if args.min_iso is not None or args.max_iso is not None:
        iso_range = (args.min_iso, args.max_iso)
    shutter_range = None
    if args.min_shutter_speed or args.max_shutter_speed:
        shutter_range = (args.min_shutter_speed, args.max_shutter_speed)
    return FilterOptions(
        filetypes=args.file_types,
        extensions=normalize_extensions(args.extensions),
        date_range=date_range,
        cameras=args.cameras,
        lenses=args.lenses,
        aperture_range=aperture_range,
        iso_range=iso_range,
        shutter_speed_range=shutter_range,
        people=getattr(args, "people", None),
        person_ids=getattr(args, "person_ids", None),
        people_match=getattr(args, "people_match", "any"),
    )


def build_parser() -> argparse.ArgumentParser:
    """The full CLI definition. Argument groups double as the TUI's form
    sections, and every option's help text is shown there as its hint."""
    parser = argparse.ArgumentParser(
        prog="smart-gallery",
        description="DB-centric photo & video catalog: init, import, sync, export, dashboard, ui.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a catalog on a drive and scan it.")
    p_init.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_init.add_argument("--label", help="Human-readable name stored in the catalog.")
    p_init.add_argument(
        "--hashing",
        action="store_true",
        help="Record a content-hash column (reserved for future dedup).",
    )
    p_init.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-create the catalog if one already exists.",
    )

    p_import = sub.add_parser(
        "import",
        help="Copy media into the drive and update the catalog.",
    )
    p_import.add_argument(
        "sources",
        nargs="+",
        type=Path,
        help="Source files or folders (SD card, dump folder).",
    )
    p_import.add_argument(
        "--to",
        required=True,
        type=Path,
        dest="drive",
        help="Target drive/root, e.g. E:/",
    )
    import_layout = p_import.add_argument_group("Layout")
    import_layout.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Subdirectory on the drive to place files (default: drive root).",
    )
    _organize_args(import_layout)
    _add_filter_args(p_import)
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the placement without copying or writing to the catalog.",
    )

    import_mirror = p_import.add_argument_group("Mirror")
    import_mirror.add_argument(
        "--no-mirror",
        action="store_true",
        help="Import only into the working drive, even if it has mirrors.",
    )

    p_sync = sub.add_parser("sync", help="Reconcile the catalog with the live drive.")
    p_sync.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the add/update/delete plan without changing the catalog.",
    )

    p_pair = sub.add_parser("pair", help="Pair a working drive with a mirror drive.")
    p_pair.add_argument(
        "working", type=Path, metavar="WORKING", help="The working drive. " + DRIVE_HELP
    )
    p_pair.add_argument(
        "mirror", type=Path, metavar="MIRROR", help="The mirror drive. " + DRIVE_HELP
    )

    p_smirror = sub.add_parser(
        "sync-mirror", help="Bring a mirror drive in step with the working drive."
    )
    p_smirror.add_argument("drive", type=Path, help=DRIVE_HELP)
    _add_mirror_arg(p_smirror)
    smirror_run = p_smirror.add_argument_group("Run")
    smirror_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the sync without copying or changing anything.",
    )
    smirror_run.add_argument(
        "--plan-out",
        type=Path,
        default=None,
        help="Write the plan to this file as JSON.",
    )
    smirror_run.add_argument(
        "--expect-plan",
        type=Path,
        default=None,
        help="Apply only if nothing would be trashed, overwritten, moved or "
        "decided beyond this --plan-out preview; otherwise change nothing.",
    )
    smirror_run.add_argument(
        "--confirm-large-delete",
        action="store_true",
        help="Allow a sync that moves many mirror files to the trash.",
    )
    smirror_ask = p_smirror.add_argument_group("Differences")
    smirror_ask.add_argument(
        "--only-on-mirror",
        choices=["later", "keep-on-mirror", "copy-back"],
        default="later",
        help="Files found only on the mirror: decide later, keep them there, "
        "or copy them back to the working drive.",
    )
    smirror_ask.add_argument(
        "--changed-on-mirror",
        choices=["skip", "take-working", "keep-mirror"],
        default="skip",
        help="Files edited on the mirror: leave them, overwrite from the "
        "working drive, or keep the mirror's version.",
    )
    smirror_ask.add_argument(
        "--conflicts",
        choices=["skip", "take-working", "keep-mirror"],
        default="skip",
        help="Files edited on both drives: leave them, keep the working "
        "version, or keep the mirror's version.",
    )

    p_free = sub.add_parser(
        "free-up", help="Delete files from the working drive that the mirror holds."
    )
    p_free.add_argument("drive", type=Path, help=DRIVE_HELP)
    _add_mirror_arg(p_free)
    _add_filter_args(p_free)
    _add_folder_arg(p_free)
    free_run = p_free.add_argument_group("Run")
    free_run.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be freed without deleting anything.",
    )
    free_run.add_argument(
        "--plan-out",
        type=Path,
        default=None,
        help="Write what would be freed, and what is skipped, to this file " "as JSON.",
    )
    free_run.add_argument(
        "--expect-plan",
        type=Path,
        default=None,
        help="Delete only the files listed in this --plan-out preview.",
    )

    p_back = sub.add_parser(
        "bring-back", help="Copy freed-up files from the mirror to the working drive."
    )
    p_back.add_argument("drive", type=Path, help=DRIVE_HELP)
    _add_mirror_arg(p_back)
    _add_filter_args(p_back)
    _add_folder_arg(p_back)

    p_compare = sub.add_parser(
        "compare", help="Compare the contents of two gallery catalogs."
    )
    p_compare.add_argument(
        "gallery_a", type=Path, metavar="GALLERY_A", help="First drive or gallery.db."
    )
    p_compare.add_argument(
        "gallery_b", type=Path, metavar="GALLERY_B", help="Second drive or gallery.db."
    )
    p_compare.add_argument(
        "--match-by",
        choices=["path", "name", "hash"],
        default="path",
        help="Pair records by relative path, unique file name, or stored content hash.",
    )
    compare_out = p_compare.add_argument_group("Report output")
    compare_out.add_argument(
        "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Report format.",
    )
    compare_out.add_argument(
        "--output", type=Path, help="Write the report to this file."
    )
    compare_out.add_argument(
        "--details",
        action="store_true",
        help="Include individual records in text output.",
    )

    p_export = sub.add_parser(
        "export",
        help="Copy a filtered subset out to another directory.",
    )
    p_export.add_argument(
        "--from",
        required=True,
        type=Path,
        dest="drive",
        help="Source drive or gallery.db (opened read-only).",
    )
    p_export.add_argument(
        "--to",
        required=True,
        type=Path,
        dest="dest",
        help="Folder that receives the exported files.",
    )
    export_layout = p_export.add_argument_group("Layout")
    export_layout.add_argument(
        "--structure",
        nargs="+",
        choices=["Year", "Month", "Model", "Lens"],
        default=["Year", "Month"],
        help=STRUCTURE_HELP,
    )
    export_layout.add_argument(
        "--mirror",
        action="store_true",
        help="Preserve the drive's relative structure instead of reorganizing.",
    )
    export_layout.add_argument(
        "--no-by-media-type",
        action="store_false",
        dest="by_media_type",
        default=True,
        help="Don't split images and videos into Photos/ and Videos/.",
    )
    export_layout.add_argument(
        "--on-exist", choices=["rename", "skip"], default="rename", help=ON_EXIST_HELP
    )
    _add_filter_args(p_export)
    export_extras = p_export.add_argument_group("Extras")
    export_extras.add_argument(
        "--no-manifest",
        action="store_false",
        dest="manifest",
        default=True,
        help="Don't write _smart_gallery_manifest.csv at the destination.",
    )
    export_extras.add_argument(
        "--portable-db",
        action="store_true",
        help="Also write a self-contained catalog at the destination.",
    )
    export_extras.add_argument(
        "--jpeg-only",
        action="store_true",
        help="Photos as JPEG only: CR2/CR3 photos are converted to JPEG (as "
        "convert-raw does), videos are copied, and other files are left out.",
    )
    p_export.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be copied without copying.",
    )

    p_convert = sub.add_parser(
        "convert-raw",
        aliases=["convert-cr3"],
        help="Extract embedded JPEG previews from CR2/CR3 files.",
    )
    p_convert.add_argument(
        "sources",
        nargs="+",
        type=Path,
        help="CR2/CR3 files or directories to search recursively.",
    )

    p_recycle = sub.add_parser(
        "recycle",
        help="Move listed catalog files to the Recycle Bin and drop them from the catalog.",
    )
    p_recycle.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_recycle.add_argument(
        "--list",
        required=True,
        type=Path,
        help=(
            "Text file with one 'relpath<TAB>size<TAB>mtime_ns' line per file. "
            "It is deleted once the run starts moving files, so it can't be "
            "replayed; it is kept when the drive has no Recycle Bin."
        ),
    )

    p_rename = sub.add_parser(
        "rename",
        help="Give a gallery a new name; it is stored in the catalog on the drive.",
    )
    p_rename.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_rename.add_argument("name", help='The gallery\'s new name, e.g. "Archive 2026".')

    p_dash = sub.add_parser(
        "dashboard", help="Launch the Streamlit dashboard on a catalog."
    )
    p_dash.add_argument("drive", type=Path, help=DRIVE_HELP)

    sub.add_parser(
        "ui", aliases=["tui"], help="Open the interactive Textual terminal interface."
    )

    sub.add_parser("gui", help="Open the Windows desktop app.")

    p_report = sub.add_parser(
        "report", help="Write an Excel report (and optional figures)."
    )
    p_report.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_report.add_argument(
        "--to",
        required=True,
        type=Path,
        dest="output",
        help="Excel workbook to write (.xlsx).",
    )
    _add_filter_args(p_report)
    p_report.add_argument(
        "--figures",
        action="store_true",
        help="Also render PNG charts into a Figures/ folder (needs the 'figures' extra).",
    )

    # ── face recognition ─────────────────────────────────────────────────────
    p_scan = sub.add_parser(
        "scan-faces", help="Detect & embed faces in every catalogued image (GPU)."
    )
    p_scan.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_scan.add_argument(
        "--rescan",
        action="store_true",
        help=(
            "Scan every image again; your decisions and named people's faces "
            "carry over, unnamed groups are rebuilt by file-faces."
        ),
    )
    p_scan.add_argument(
        "--limit", type=int, default=None, help="Scan at most N images (testing)."
    )

    p_find = sub.add_parser(
        "find-faces",
        help="Scan new images for faces, then sort them into people (GPU).",
    )
    p_find.add_argument("drive", type=Path, help=DRIVE_HELP)

    p_identify = sub.add_parser(
        "identify-faces", help="Identify faces using person-named sample directories."
    )
    p_identify.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_identify.add_argument(
        "--samples",
        required=True,
        type=Path,
        help="Root directory containing one sample-photo directory per person.",
    )
    matching = p_identify.add_argument_group("Matching")
    matching.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Minimum cosine similarity for a match (default: 0.5).",
    )
    matching.add_argument(
        "--margin",
        type=float,
        default=0.05,
        help="Minimum lead over the second-best match (default: 0.05).",
    )
    matching.add_argument(
        "--reassign",
        action="store_true",
        help="Reconsider all faces assigned to people in the sample set.",
    )

    p_file = sub.add_parser(
        "file-faces",
        help="Sort scanned faces into people, suggestions and new groups (no GPU).",
    )
    p_file.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_file.add_argument(
        "--no-group",
        action="store_true",
        help="Skip forming new groups; only match faces to existing people and groups.",
    )

    p_label = sub.add_parser(
        "label-faces",
        help="Confirm, reject, move or ignore faces (corrections to the filing).",
    )
    p_label.add_argument("drive", type=Path, help=DRIVE_HELP)
    g_src = p_label.add_mutually_exclusive_group(required=True)
    g_src.add_argument(
        "--list",
        type=Path,
        metavar="PATH",
        help="A JSON list of corrections, as the desktop app writes it.",
    )
    g_src.add_argument(
        "--faces",
        type=int,
        nargs="+",
        metavar="ID",
        help="Face ids to correct; give exactly one action below.",
    )
    g_act = p_label.add_mutually_exclusive_group()
    g_act.add_argument(
        "--to",
        type=int,
        metavar="PERSON_ID",
        help="Move the faces to this person.",
    )
    g_act.add_argument(
        "--new-person",
        metavar="NAME",
        help="Move the faces to a new person with this name (or the one that has it).",
    )
    g_act.add_argument(
        "--not",
        dest="not_person",
        type=int,
        metavar="PERSON_ID",
        help="The faces are not this person; never suggest them to it again.",
    )
    g_act.add_argument(
        "--confirm",
        type=int,
        metavar="PERSON_ID",
        help="Confirm that the faces are this person.",
    )
    g_act.add_argument(
        "--ignore",
        action="store_true",
        help="Ignore the faces (not a real face, or not worth filing).",
    )
    p_label.add_argument(
        "--refile",
        action="store_true",
        help="Afterwards re-sort the other faces (as file-faces --no-group), in the same undo step.",
    )

    p_undo = sub.add_parser(
        "undo-faces",
        help="Undo the last face correction made with label-faces, if nothing has changed those faces since.",
    )
    p_undo.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_undo.add_argument(
        "--forget",
        action="store_true",
        help="Drop the last undo step without changing any faces (when it can no longer be undone).",
    )

    p_cluster = sub.add_parser(
        "cluster-faces", help="Optionally cluster faces without sample photos."
    )
    p_cluster.add_argument("drive", type=Path, help=DRIVE_HELP)
    _clustering_args(
        p_cluster.add_argument_group("Clustering"),
        eps=0.45,
        min_samples=4,
        min_cluster_size=5,
    )
    g_mode = p_cluster.add_mutually_exclusive_group()
    g_mode.add_argument(
        "--rebuild",
        action="store_true",
        help="Return undecided faces to the pool and drop unnamed groups, then "
        "sort again (named people and your choices stay).",
    )
    g_mode.add_argument(
        "--incremental",
        action="store_true",
        help="File new faces into existing people only; make no new groups.",
    )

    p_people = sub.add_parser(
        "people", help="List detected people and their photo counts."
    )
    p_people.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_people.add_argument(
        "--samples",
        type=int,
        default=3,
        metavar="N",
        help="Clickable sample photos to show per person (default 3).",
    )

    p_name = sub.add_parser(
        "name-person", help="Set (or clear) the name of a person cluster."
    )
    p_name.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_name.add_argument("person_id", type=int, help=PERSON_ID_HELP)
    p_name.add_argument(
        "name", nargs="?", default=None, help="Name to assign; omit to clear."
    )
    p_name.add_argument(
        "--merge",
        action="store_true",
        help="If another person already has this name, merge into them.",
    )

    p_hide = sub.add_parser(
        "hide-person",
        help="Hide an unnamed group of strangers, or show it again.",
    )
    p_hide.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_hide.add_argument("person_id", type=int, help=PERSON_ID_HELP)
    p_hide.add_argument(
        "--unhide",
        action="store_true",
        help="Show the group again instead of hiding it.",
    )

    p_merge = sub.add_parser(
        "merge-persons", help="Merge several person clusters into one."
    )
    p_merge.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_merge.add_argument("dest", type=int, help="Destination person id (kept).")
    p_merge.add_argument(
        "sources", type=int, nargs="+", help="Person ids merged into dest."
    )

    p_split = sub.add_parser(
        "split-person", help="Re-cluster one impure person into tighter sub-clusters."
    )
    p_split.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_split.add_argument("person_id", type=int, help=PERSON_ID_HELP)
    _clustering_args(
        p_split.add_argument_group("Clustering"),
        eps=0.30,
        min_samples=3,
        min_cluster_size=3,
    )

    p_del = sub.add_parser(
        "delete-person", help="Delete a person cluster (its faces become unassigned)."
    )
    p_del.add_argument("drive", type=Path, help=DRIVE_HELP)
    p_del.add_argument("person_id", type=int, help=PERSON_ID_HELP)

    return parser


def parse_args(argv=None):
    return build_parser().parse_args(argv)


def _handle_init(args):
    cb = progress_reporter()
    try:
        report = init_drive(
            args.drive,
            label=args.label,
            hashing=args.hashing,
            overwrite=args.overwrite,
            progress_callback=cb,
        )
    finally:
        cb.close()
    logger.success(f"Initialized {report.db_path} with {report.indexed:,} items.")


def _mirrors_for_import(repo):
    """Every paired mirror that can be found and opened; the others stay pending."""
    pairing = repo.get_pairing()
    if pairing is None or pairing.role != "working":
        return []
    uuid = repo.get_meta("db_uuid")
    opened = []
    for peer in pairing.peers:
        path = find_peer(peer)
        if path is None:
            logger.warning(
                f"Mirror {peer.uuid} is not connected; it stays pending "
                "until the next sync-mirror."
            )
            continue
        try:
            mirror = GalleryRepository.open(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not open mirror {peer.uuid} at {path}: {exc}")
            continue
        back = mirror.get_pairing()
        if (
            back is None
            or back.role != "mirror"
            or uuid not in {p.uuid for p in back.peers}
        ):
            logger.warning(f"{path} is not paired with this drive; skipping it.")
            mirror.close()
            continue
        opened.append(mirror)
    return opened


def _handle_import(args):
    options = Options(
        by_media_type=args.by_media_type,
        structure=args.structure,
        on_exist=args.on_exist,
        dry_run=args.dry_run,
    )
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as repo:
        mirrors = [] if args.no_mirror else _mirrors_for_import(repo)
        try:
            report = import_media(
                repo,
                args.sources,
                output_dir=args.output,
                options=options,
                query=build_filter_query(args),
                mirrors=mirrors,
                progress_callback=cb,
            )
        finally:
            cb.close()
            for m in mirrors:
                m.close()
    logger.success(
        f"Imported: copied={report.copied} skipped={report.skipped} inserted={report.inserted}"
    )
    for uuid, done in report.mirrored.items():
        pending = report.mirror_pending.get(uuid, 0)
        logger.info(f"Mirror {uuid}: {done} file(s) copied, {pending} pending.")


def _handle_sync(args):
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as repo:
        try:
            report = sync_drive(repo, dry_run=args.dry_run, progress_callback=cb)
        finally:
            cb.close()
    logger.success(
        f"Sync {'(dry-run) ' if args.dry_run else ''}— "
        f"+{report.added} ~{report.updated} -{report.deleted} ={report.unchanged}"
    )


def _open_mirror_of(working, override):
    """Open the working drive's chosen mirror read-write (MirrorError if absent)."""
    pairing = working.get_pairing()
    if pairing is not None and pairing.role == "mirror":
        raise MirrorError("Run sync-mirror on the working drive.")
    if pairing is None:
        raise MirrorError("This gallery has no mirror.")
    peer = choose_mirror(pairing, override)
    path = find_peer(peer)
    if path is None:
        raise MirrorError(f"The mirror {peer.uuid} cannot be found; connect it first.")
    return GalleryRepository.open(path)


def _handle_pair(args):
    with GalleryRepository.open(args.working) as working:
        with GalleryRepository.open(args.mirror) as mirror:
            pair_galleries(working, mirror)
            w_id, m_id = working.get_meta("db_uuid"), mirror.get_meta("db_uuid")
            w_name = working.get_meta("label") or w_id
            m_name = mirror.get_meta("label") or m_id
            logger.success(
                f"Paired working {w_name} ({w_id}) with mirror {m_name} ({m_id})."
            )


_PLAN_CLASSES = (
    "copy_new",
    "overwrite",
    "trash",
    "moves",
    "only_on_mirror",
    "changed_on_mirror",
    "conflicts",
    "adopt",
    "offloaded_changed",
    "offloaded_missing",
    "offloaded_conflict",
)


def _plan_json(preview) -> dict:
    """The preview as the dict written to --plan-out."""
    plan = preview.plan
    lists = {name: list(getattr(plan, name)) for name in _PLAN_CLASSES}
    lists["adopt"] = [row.relpath for row in plan.adopt]
    lists["moves"] = [list(pair) for pair in plan.moves]
    return {
        "counts": {name: len(rels) for name, rels in lists.items()},
        **lists,
        "copy_bytes": preview.copy_bytes,
        "mirror_free_bytes": preview.mirror_free_bytes,
        "mirror_trash_bytes": preview.mirror_trash_bytes,
        "trash_bytes": preview.trash_bytes,
        "large_delete": preview.large_delete,
    }


def _log_plan(preview) -> None:
    data = _plan_json(preview)
    counts = ", ".join(f"{n}={c}" for n, c in data["counts"].items() if c)
    logger.info(
        f"Plan: {counts or 'nothing to do'}; copy {format_bytes(preview.copy_bytes)}, "
        f"to trash {format_bytes(preview.trash_bytes)} "
        f"(mirror free {format_bytes(preview.mirror_free_bytes)}, "
        f"already in its trash {format_bytes(preview.mirror_trash_bytes)})."
    )
    if preview.large_delete:
        logger.warning("This sync would trash a large share of the mirror's files.")
    missing = preview.plan.offloaded_missing
    if missing:
        logger.warning(
            f"{len(missing)} freed-up file(s) are missing from the mirror, so the "
            "only copy may be gone: " + ", ".join(missing[:10])
        )
    clash = preview.plan.offloaded_conflict
    if clash:
        logger.warning(
            f"{len(clash)} freed-up path(s) now hold a different file on the "
            "working drive; it is not mirrored until you move it elsewhere: "
            + ", ".join(clash[:10])
        )


def _handle_sync_mirror(args):
    choices = MirrorChoices(
        only_on_mirror=args.only_on_mirror,
        changed_on_mirror=args.changed_on_mirror,
        conflicts=args.conflicts,
        confirm_large_delete=args.confirm_large_delete,
    )
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as working:
        with _open_mirror_of(working, args.mirror) as mirror:
            try:
                preview = plan_mirror_sync(working, mirror, progress_callback=cb)
                if args.plan_out:
                    args.plan_out.write_text(
                        json.dumps(_plan_json(preview), indent=2), encoding="utf-8"
                    )
                _log_plan(preview)
                if args.dry_run:
                    return
                if args.expect_plan:
                    check_previewed(preview, choices, _read_json(args.expect_plan))
                report = apply_mirror_sync(
                    working, mirror, preview, choices, progress_callback=cb
                )
            finally:
                cb.close()
    logger.success(
        f"Mirror sync: copied={report.copied} overwritten={report.overwritten} "
        f"trashed={report.trashed} moved={report.moved} "
        f"copied_back={report.copied_back} skipped={report.skipped}"
    )
    for failure in report.failures:
        logger.error(failure)
    if report.failures:
        sys.exit(1)


def _read_json(path: Path) -> dict:
    """A preview written by --plan-out (ValueError when unreadable)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read the preview {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"The preview {path} is not a plan file.")
    return data


def _handle_free_up(args):
    only = None
    if args.expect_plan:
        only = list(_read_json(args.expect_plan).get("relpaths") or [])
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as working:
        with _open_mirror_of(working, args.mirror) as mirror:
            try:
                report = free_up_space(
                    working,
                    mirror,
                    build_filter_query(args),
                    args.folder,
                    dry_run=args.dry_run,
                    only=only,
                    progress_callback=cb,
                )
            finally:
                cb.close()
    if args.plan_out:
        data = {
            "files": report.files,
            "bytes": report.bytes,
            "relpaths": report.relpaths,
            "skipped": report.skipped,
        }
        args.plan_out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for note in report.skipped:
        logger.info(f"Skipped: {note}")
    verb = "Would free" if args.dry_run else "Freed"
    logger.success(f"{verb} {report.files} file(s), {format_bytes(report.bytes)}.")


def _handle_bring_back(args):
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as working:
        with _open_mirror_of(working, args.mirror) as mirror:
            try:
                report = bring_back(
                    working,
                    mirror,
                    build_filter_query(args),
                    args.folder,
                    progress_callback=cb,
                )
            finally:
                cb.close()
    logger.success(
        f"Brought back {report.files} file(s), {format_bytes(report.bytes)}."
    )
    for note in report.skipped:
        logger.info(f"Skipped: {note}")


def _handle_compare(args):
    report = compare_galleries(args.gallery_a, args.gallery_b, match_by=args.match_by)
    if args.output:
        output_path = write_compare_report(
            report,
            args.output,
            output_format=args.format,
            details=args.details,
        )
        logger.success(f"Comparison written to {output_path}")
    else:
        print(
            format_compare_report(
                report,
                output_format=args.format,
                details=args.details,
            )
        )


def _handle_export(args):
    options = (
        None
        if args.mirror
        else Options(
            by_media_type=args.by_media_type,
            structure=args.structure,
            on_exist=args.on_exist,
        )
    )
    cb = progress_reporter()
    with GalleryRepository.open(args.drive, read_only=True) as repo:
        try:
            report = export_media(
                repo,
                args.dest,
                filters=build_filter_query(args),
                options=options,
                mirror=args.mirror,
                on_exist=args.on_exist,
                dry_run=args.dry_run,
                manifest=args.manifest,
                portable_db=args.portable_db,
                jpeg_only=args.jpeg_only,
                progress_callback=cb,
            )
        finally:
            cb.close()
    logger.success(
        f"Exported {report.copied} of {report.matched} matched files to {args.dest}."
    )


def _handle_convert_cr3(args):
    convert_cr3(args.sources)


def _handle_recycle(args):
    entries = read_list(args.list)
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as repo:
        refused = False
        try:
            report = recycle_files(repo, entries, progress_callback=cb)
        except ValueError:  # no Recycle Bin: nothing moved, the list may be retried
            refused = True
            raise
        finally:
            cb.close()
            if not refused:
                # A list is used once: running it again after the files were
                # restored (and synced) would recycle them a second time.
                args.list.unlink(missing_ok=True)
    for failure in report.failures:
        logger.error(f"Not recycled: {failure}")
    logger.success(
        f"Moved {report.recycled:,} file(s) to the Recycle Bin, freeing "
        f"{format_bytes(report.freed_bytes)}; {report.skipped:,} skipped, "
        f"{len(report.failures):,} failed."
    )


def _handle_rename(args):
    with GalleryRepository.open(args.drive) as repo:
        name = rename_gallery(repo, args.name)
    logger.success(f"Renamed the gallery to {name!r}.")


def _handle_dashboard(args):
    from smart_gallery import dashboard

    app_path = Path(dashboard.__file__).parent / "app.py"
    db_path = resolve_db_path(args.drive)
    if not db_path.exists():
        logger.error(f"No catalog at {db_path}. Run `smart-gallery init` first.")
        sys.exit(1)
    env = os.environ.copy()
    env["SG_DB_PATH"] = str(db_path)
    logger.info(f"Launching dashboard for {db_path}")
    subprocess.run(["streamlit", "run", str(app_path)], env=env)


def _handle_report(args):
    from smart_gallery.reporting import export_report

    with GalleryRepository.open(args.drive, read_only=True) as repo:
        out = export_report(repo, args.output, filters=build_filter_query(args))
        logger.success(f"Report written to {out}")
        if args.figures:
            try:
                from smart_gallery.reporting.figures import generate_plots

                figures_dir = generate_plots(repo, args.output.parent / "Figures")
                logger.success(f"Figures written to {figures_dir}")
            except Exception as exc:
                logger.error(
                    f"Figure generation failed (install the 'figures' extra?): {exc}"
                )


def _handle_scan_faces(args):
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as repo:
        try:
            report = scan_faces(
                repo, rescan=args.rescan, limit=args.limit, progress_callback=cb
            )
        finally:
            cb.close()
    logger.success(
        f"Scanned {report.images_scanned:,} images — {report.faces_found:,} faces "
        f"(provider={report.provider}), {report.decisions_carried:,} decisions "
        f"carried over, {report.decisions_dropped:,} dropped."
    )


def _sorted_faces_line(report) -> str:
    return (
        f"Sorted faces: {report.auto:,} matched, {report.suggested:,} suggested, "
        f"{report.grouped:,} grouped into {report.groups_created} new group(s), "
        f"{report.hidden:,} hidden, {report.left:,} left."
    )


def _handle_file_faces(args):
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as repo:
        try:
            report = file_faces(repo, group=not args.no_group, progress_callback=cb)
        finally:
            cb.close()
    logger.success(_sorted_faces_line(report))


def _handle_find_faces(args):
    cb = progress_reporter()
    with GalleryRepository.open(args.drive) as repo:
        try:
            scanned, filed = find_faces(repo, progress_callback=cb)
        finally:
            cb.close()
    logger.success(
        f"Scanned {scanned.images_scanned:,} images — {scanned.faces_found:,} faces "
        f"(provider={scanned.provider}), {scanned.decisions_carried:,} decisions "
        f"carried over, {scanned.decisions_dropped:,} dropped. Sorted faces: "
        f"{filed.auto:,} matched, {filed.suggested:,} suggested, {filed.grouped:,} "
        f"grouped into {filed.groups_created} new group(s), {filed.hidden:,} "
        f"hidden, {filed.left:,} left."
    )


def _label_entries(args) -> list[LabelEntry]:
    actions = [
        ("move", {"person_id": args.to}, args.to is not None),
        ("new_person", {"name": args.new_person}, args.new_person is not None),
        ("reject", {"person_id": args.not_person}, args.not_person is not None),
        ("confirm", {"person_id": args.confirm}, args.confirm is not None),
        ("ignore", {}, args.ignore),
    ]
    chosen = [
        LabelEntry(a, tuple(args.faces or ()), **kw) for a, kw, on in actions if on
    ]
    if args.list is not None:
        if chosen:
            raise ValueError("--list cannot be combined with an action.")
        return read_label_list(args.list)
    if len(chosen) != 1:
        raise ValueError(
            "--faces needs exactly one of --to, --new-person, --not, --confirm, --ignore."
        )
    return chosen


def _handle_label_faces(args):
    entries = _label_entries(args)
    with GalleryRepository.open(args.drive) as repo:
        report = label_faces(
            repo, entries, skip_missing=args.list is not None, refile=args.refile
        )
    logger.success(
        f"Updated {report.faces_changed:,} face(s); "
        f"{report.people_created} new person(s); "
        f"{report.missing_faces} face id(s) and {report.missing_people} "
        "person id(s) no longer exist."
    )
    if report.filed is not None:
        logger.success(_sorted_faces_line(report.filed))


def _handle_undo_faces(args):
    with GalleryRepository.open(args.drive) as repo:
        if args.forget:
            summary = forget_undo(repo)
            logger.success(f"Forgot undo step: {summary}.")
            return
        r = undo_faces(repo)
    logger.success(f"Undid: {r.summary} ({r.faces_restored:,} faces restored).")


def _handle_cluster_faces(args):
    with GalleryRepository.open(args.drive) as repo:
        report = cluster_faces(
            repo,
            algo=args.algo,
            eps=args.eps,
            min_samples=args.min_samples,
            min_cluster_size=args.min_cluster_size,
            rebuild=args.rebuild,
            incremental=args.incremental,
            pca=args.pca,
        )
    logger.success(
        f"{report.faces_assigned:,} faces filed: {report.persons_matched} existing "
        f"people gained faces, {report.persons_created} new group(s), "
        f"{report.noise:,} hidden."
    )


def _handle_identify_faces(args):
    with GalleryRepository.open(args.drive) as repo:
        report = identify_faces(
            repo,
            args.samples,
            threshold=args.threshold,
            margin=args.margin,
            reassign=args.reassign,
        )
    logger.success(
        f"Identified {report.faces_assigned:,} face(s) across {report.persons} people; "
        f"{report.faces_unmatched:,} unmatched, {report.faces_ambiguous:,} ambiguous, "
        f"{report.faces_already_assigned:,} already assigned. "
        f"Usable samples: {report.sample_images}; skipped: {report.sample_skipped}."
    )


def _osc8_link(uri: str, text: str) -> str:
    """A terminal hyperlink (OSC 8). Terminals that don't support it (most do,
    incl. Windows Terminal) just show ``text``."""
    return f"\033]8;;{uri}\033\\{text}\033]8;;\033\\"


def _handle_people(args):
    with GalleryRepository.open(args.drive, read_only=True) as repo:
        people = repo.list_persons()
        samples = repo.person_samples(limit=args.samples)
        drive_root = repo.drive_root
    if not people:
        logger.info("No people yet. Run `find-faces` to find and sort faces.")
        return
    for p in people:
        name = p["name"] or "(unnamed)"
        if p["hidden"]:
            name += " (hidden)"
        print(f"[{p['id']:>4}]  {name:<24}  {p['face_count']:>6} faces")
        links = []
        for rel in samples.get(p["id"], []):
            try:
                uri = Path(drive_root, rel).as_uri()
            except ValueError:
                uri = ""
            links.append(_osc8_link(uri, Path(rel).name) if uri else Path(rel).name)
        if links:
            print("         " + "   ".join(links))
    print(
        f"\n{len(people)} people. Click a photo to open it. "
        'Name one with: smart-gallery name-person <drive> <id> "<name>"'
    )


def _handle_name_person(args):
    with GalleryRepository.open(args.drive) as repo:
        ensure_not_mirror(repo)
        if repo.get_person(args.person_id) is None:
            logger.error(f"No person with id {args.person_id}.")
            sys.exit(1)
        if args.name:
            others = [
                r["id"]
                for r in repo.conn.execute(
                    "SELECT id FROM persons WHERE name = ? COLLATE NOCASE "
                    "AND id <> ? ORDER BY id",
                    (args.name.strip(), args.person_id),
                )
            ]
            if others and not args.merge:
                raise ValueError(
                    f"Person {others[0]} is already named {args.name!r}; "
                    "use --merge to merge into them."
                )
            if len(others) > 1:
                raise ValueError(f"Several people are named {args.name!r}.")
            if others:
                repo.merge_persons(others[0], [args.person_id])
                logger.success(
                    f"Merged person {args.person_id} into person {others[0]} "
                    f"('{args.name}')."
                )
                return
        repo.set_person_name(args.person_id, args.name)
    if args.name:
        logger.success(f"Person {args.person_id} named '{args.name}'.")
    else:
        logger.success(f"Cleared the name of person {args.person_id}.")


def _handle_merge_persons(args):
    with GalleryRepository.open(args.drive) as repo:
        ensure_not_mirror(repo)
        if repo.get_person(args.dest) is None:
            logger.error(f"No destination person with id {args.dest}.")
            sys.exit(1)
        repo.merge_persons(args.dest, args.sources)
        dest = repo.get_person(args.dest)
    logger.success(
        f"Merged {len(args.sources)} cluster(s) into person {args.dest} "
        f"({dest['face_count']:,} faces)."
    )


def _handle_hide_person(args):
    with GalleryRepository.open(args.drive) as repo:
        ensure_not_mirror(repo)
        if repo.get_person(args.person_id) is None:
            logger.error(f"No person with id {args.person_id}.")
            sys.exit(1)
        repo.set_person_hidden(args.person_id, not args.unhide)
    if args.unhide:
        logger.success(f"Person {args.person_id} is shown again.")
    else:
        logger.success(f"Person {args.person_id} is hidden.")


def _handle_split_person(args):
    with GalleryRepository.open(args.drive) as repo:
        ensure_not_mirror(repo)
        try:
            report = split_person(
                repo,
                args.person_id,
                algo=args.algo,
                eps=args.eps,
                min_samples=args.min_samples,
                min_cluster_size=args.min_cluster_size,
                pca=args.pca,
            )
        except ValueError as exc:
            logger.error(str(exc))
            sys.exit(1)
    if report.persons_created:
        logger.success(
            f"Split off {report.persons_created} new group(s) "
            f"({report.faces_assigned:,} faces). Review with `people`."
        )


def _handle_delete_person(args):
    with GalleryRepository.open(args.drive) as repo:
        ensure_not_mirror(repo)
        if not repo.delete_person(args.person_id):
            logger.error(f"No person with id {args.person_id}.")
            sys.exit(1)
    logger.success(
        f"Deleted person {args.person_id}; its faces are now unassigned "
        f"(sort them again with `file-faces`)."
    )


def _handle_ui(args):
    from smart_gallery.tui import run

    run()


def _handle_gui(args):
    from smart_gallery.gui.app import run

    run()


_HANDLERS = {
    "init": _handle_init,
    "import": _handle_import,
    "sync": _handle_sync,
    "pair": _handle_pair,
    "sync-mirror": _handle_sync_mirror,
    "free-up": _handle_free_up,
    "bring-back": _handle_bring_back,
    "compare": _handle_compare,
    "export": _handle_export,
    "convert-raw": _handle_convert_cr3,
    "convert-cr3": _handle_convert_cr3,
    "recycle": _handle_recycle,
    "rename": _handle_rename,
    "dashboard": _handle_dashboard,
    "ui": _handle_ui,
    "tui": _handle_ui,
    "gui": _handle_gui,
    "report": _handle_report,
    "scan-faces": _handle_scan_faces,
    "file-faces": _handle_file_faces,
    "find-faces": _handle_find_faces,
    "label-faces": _handle_label_faces,
    "undo-faces": _handle_undo_faces,
    "identify-faces": _handle_identify_faces,
    "cluster-faces": _handle_cluster_faces,
    "people": _handle_people,
    "name-person": _handle_name_person,
    "merge-persons": _handle_merge_persons,
    "hide-person": _handle_hide_person,
    "split-person": _handle_split_person,
    "delete-person": _handle_delete_person,
}


def _utf8_pipes() -> None:
    """When the GUI/TUI reads us through a pipe, write UTF-8 whatever the
    console code page (a frozen exe ignores PYTHONIOENCODING)."""
    if os.environ.get(PROGRESS_ENV) != "json":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    _utf8_pipes()
    args = parse_args(argv)
    log_path = _configure_logging(args.command)
    try:
        _HANDLERS[args.command](args)
    except Cancelled:
        logger.warning("Cancelled.")
        sys.exit(CANCELLED_EXIT)
    except (FileNotFoundError, FileExistsError) as exc:
        logger.error(str(exc))
        sys.exit(1)
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Unexpected error: {exc}")
        sys.exit(1)
    finally:
        if log_path is not None:
            logger.info(f"Details: {log_path}")


if __name__ == "__main__":
    main()
