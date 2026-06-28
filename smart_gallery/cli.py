"""smart-gallery command-line interface.

Verbs: init, import, sync, export, dashboard, report. Filter flags are shared
across import/export/report via a parent parser and compiled to a FilterOptions.
"""

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from loguru import logger

from smart_gallery.config import resolve_db_path
from smart_gallery.db import GalleryRepository
from smart_gallery.organize import FilterOptions, Options, normalize_extensions
from smart_gallery.services import (
    cluster_faces,
    export_media,
    import_media,
    init_drive,
    scan_faces,
    split_person,
    sync_drive,
)


class TqdmProgress:
    def __init__(self, desc="Processing"):
        try:
            from tqdm import tqdm

            self.pbar = tqdm(total=100, desc=desc)
        except ImportError:
            self.pbar = None
        self.last = 0

    def __call__(self, progress: float):
        val = int(progress * 100)
        if val > self.last and self.pbar:
            self.pbar.update(val - self.last)
            self.last = val

    def close(self):
        if self.pbar:
            if self.last < 100:
                self.pbar.update(100 - self.last)
            self.pbar.close()


def _filter_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--file-types", nargs="+", choices=["image", "video", "other"])
    p.add_argument("--extensions", nargs="+")
    p.add_argument("--date-start", type=date.fromisoformat)
    p.add_argument("--date-end", type=date.fromisoformat)
    p.add_argument("--cameras", nargs="+")
    p.add_argument("--lenses", nargs="+")
    p.add_argument("--min-aperture", type=float)
    p.add_argument("--max-aperture", type=float)
    p.add_argument("--min-iso", type=int)
    p.add_argument("--max-iso", type=int)
    p.add_argument("--min-shutter-speed")
    p.add_argument("--max-shutter-speed")
    p.add_argument(
        "--people", nargs="+", metavar="NAME",
        help="Only media containing these named people (face recognition).",
    )
    p.add_argument(
        "--person-ids", nargs="+", type=int, metavar="ID", dest="person_ids",
        help="Only media containing these person cluster ids (works before naming).",
    )
    return p


def _organize_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--by-media-type", action="store_true", default=True)
    p.add_argument("--no-by-media-type", action="store_false", dest="by_media_type")
    p.add_argument(
        "--structure", nargs="+", default=["Year", "Month"],
        choices=["Year", "Month", "Model", "Lens"],
    )
    p.add_argument("--on-exist", choices=["rename", "skip"], default="rename")
    p.add_argument("--dry-run", action="store_true")


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
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="smart-gallery",
        description="DB-centric photo & video catalog: init, import, sync, export, dashboard.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    filt = _filter_parser()

    p_init = sub.add_parser("init", help="Create a catalog on a drive and scan it.")
    p_init.add_argument("drive", type=Path, help="Drive/root, e.g. E:/")
    p_init.add_argument("--label")
    p_init.add_argument("--hashing", action="store_true")
    p_init.add_argument("--overwrite", action="store_true")

    p_import = sub.add_parser("import", parents=[filt], help="Copy media into the drive and update the catalog.")
    p_import.add_argument("sources", nargs="+", type=Path)
    p_import.add_argument("--to", required=True, type=Path, dest="drive", help="Target drive/root, e.g. E:/")
    p_import.add_argument("--output", type=Path, default=None, help="Subdirectory on the drive to place files (default: drive root).")
    _organize_args(p_import)

    p_sync = sub.add_parser("sync", help="Reconcile the catalog with the live drive.")
    p_sync.add_argument("drive", type=Path)
    p_sync.add_argument("--dry-run", action="store_true")

    p_export = sub.add_parser("export", parents=[filt], help="Copy a filtered subset out to another directory.")
    p_export.add_argument("--from", required=True, type=Path, dest="drive")
    p_export.add_argument("--to", required=True, type=Path, dest="dest")
    p_export.add_argument("--structure", nargs="+", choices=["Year", "Month", "Model", "Lens"], default=["Year", "Month"])
    p_export.add_argument("--mirror", action="store_true", help="Preserve the drive's relative structure instead of reorganizing.")
    p_export.add_argument("--no-by-media-type", action="store_false", dest="by_media_type", default=True)
    p_export.add_argument("--on-exist", choices=["rename", "skip"], default="rename")
    p_export.add_argument("--no-manifest", action="store_false", dest="manifest", default=True)
    p_export.add_argument("--portable-db", action="store_true")
    p_export.add_argument("--dry-run", action="store_true")

    p_dash = sub.add_parser("dashboard", help="Launch the Streamlit dashboard on a catalog.")
    p_dash.add_argument("drive", type=Path)

    p_report = sub.add_parser("report", parents=[filt], help="Write an Excel report (and optional figures).")
    p_report.add_argument("drive", type=Path)
    p_report.add_argument("--to", required=True, type=Path, dest="output")
    p_report.add_argument("--figures", action="store_true")

    # ── face recognition ─────────────────────────────────────────────────────
    p_scan = sub.add_parser("scan-faces", help="Detect & embed faces in every catalogued image (GPU).")
    p_scan.add_argument("drive", type=Path)
    p_scan.add_argument("--rescan", action="store_true", help="Clear all face data and scan everything again.")
    p_scan.add_argument("--limit", type=int, default=None, help="Scan at most N images (testing).")

    p_cluster = sub.add_parser("cluster-faces", help="Group face embeddings into people.")
    p_cluster.add_argument("drive", type=Path)
    p_cluster.add_argument("--algo", choices=["hdbscan", "dbscan"], default="hdbscan")
    p_cluster.add_argument("--eps", type=float, default=0.45, help="DBSCAN cosine epsilon.")
    p_cluster.add_argument("--min-samples", type=int, default=4, help="DBSCAN min samples.")
    p_cluster.add_argument("--min-cluster-size", type=int, default=5, help="HDBSCAN min cluster size.")
    p_cluster.add_argument("--pca", type=int, default=0, metavar="DIMS",
                           help="Reduce embeddings to DIMS via PCA before clustering "
                                "(big speedup for large libraries; try 128). 0 = off.")
    g_mode = p_cluster.add_mutually_exclusive_group()
    g_mode.add_argument("--rebuild", action="store_true", help="Drop all people and re-cluster from scratch.")
    g_mode.add_argument("--incremental", action="store_true", help="Match new faces to existing people (fast).")

    p_people = sub.add_parser("people", help="List detected people and their photo counts.")
    p_people.add_argument("drive", type=Path)
    p_people.add_argument("--samples", type=int, default=3, metavar="N",
                          help="Clickable sample photos to show per person (default 3).")

    p_name = sub.add_parser("name-person", help="Set (or clear) the name of a person cluster.")
    p_name.add_argument("drive", type=Path)
    p_name.add_argument("person_id", type=int)
    p_name.add_argument("name", nargs="?", default=None, help="Name to assign; omit to clear.")

    p_merge = sub.add_parser("merge-persons", help="Merge several person clusters into one.")
    p_merge.add_argument("drive", type=Path)
    p_merge.add_argument("dest", type=int, help="Destination person id (kept).")
    p_merge.add_argument("sources", type=int, nargs="+", help="Person ids merged into dest.")

    p_split = sub.add_parser("split-person", help="Re-cluster one impure person into tighter sub-clusters.")
    p_split.add_argument("drive", type=Path)
    p_split.add_argument("person_id", type=int)
    p_split.add_argument("--algo", choices=["hdbscan", "dbscan"], default="hdbscan")
    p_split.add_argument("--eps", type=float, default=0.30, help="DBSCAN cosine epsilon (tighter than a full run).")
    p_split.add_argument("--min-samples", type=int, default=3)
    p_split.add_argument("--min-cluster-size", type=int, default=3, help="HDBSCAN min cluster size.")
    p_split.add_argument("--pca", type=int, default=0, metavar="DIMS")

    p_del = sub.add_parser("delete-person", help="Delete a person cluster (its faces become unassigned).")
    p_del.add_argument("drive", type=Path)
    p_del.add_argument("person_id", type=int)

    return parser.parse_args(argv)


def _handle_init(args):
    cb = TqdmProgress("Indexing")
    try:
        report = init_drive(
            args.drive, label=args.label, hashing=args.hashing,
            overwrite=args.overwrite, progress_callback=cb,
        )
    finally:
        cb.close()
    logger.success(f"Initialized {report.db_path} with {report.indexed:,} items.")


def _handle_import(args):
    options = Options(
        by_media_type=args.by_media_type, structure=args.structure,
        on_exist=args.on_exist, dry_run=args.dry_run,
    )
    cb = TqdmProgress("Importing")
    with GalleryRepository.open(args.drive) as repo:
        try:
            report = import_media(
                repo, args.sources, output_dir=args.output, options=options,
                query=build_filter_query(args), progress_callback=cb,
            )
        finally:
            cb.close()
    logger.success(
        f"Imported: copied={report.copied} skipped={report.skipped} inserted={report.inserted}"
    )


def _handle_sync(args):
    cb = TqdmProgress("Syncing")
    with GalleryRepository.open(args.drive) as repo:
        try:
            report = sync_drive(repo, dry_run=args.dry_run, progress_callback=cb)
        finally:
            cb.close()
    logger.success(
        f"Sync {'(dry-run) ' if args.dry_run else ''}— "
        f"+{report.added} ~{report.updated} -{report.deleted} ={report.unchanged}"
    )


def _handle_export(args):
    options = None if args.mirror else Options(
        by_media_type=args.by_media_type, structure=args.structure, on_exist=args.on_exist,
    )
    with GalleryRepository.open(args.drive, read_only=True) as repo:
        report = export_media(
            repo, args.dest, filters=build_filter_query(args), options=options,
            mirror=args.mirror, on_exist=args.on_exist, dry_run=args.dry_run,
            manifest=args.manifest, portable_db=args.portable_db,
        )
    logger.success(
        f"Exported {report.copied} of {report.matched} matched files to {args.dest}."
    )


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
                logger.error(f"Figure generation failed (install the 'figures' extra?): {exc}")


def _handle_scan_faces(args):
    cb = TqdmProgress("Scanning faces")
    with GalleryRepository.open(args.drive) as repo:
        try:
            report = scan_faces(repo, rescan=args.rescan, limit=args.limit, progress_callback=cb)
        finally:
            cb.close()
    logger.success(
        f"Scanned {report.images_scanned:,} images — {report.faces_found:,} faces "
        f"(provider={report.provider})."
    )


def _handle_cluster_faces(args):
    with GalleryRepository.open(args.drive) as repo:
        report = cluster_faces(
            repo, algo=args.algo, eps=args.eps, min_samples=args.min_samples,
            min_cluster_size=args.min_cluster_size, rebuild=args.rebuild,
            incremental=args.incremental, pca=args.pca,
        )
    if args.incremental:
        logger.success(
            f"Matched {report.faces_assigned:,} faces to {report.persons_matched} people."
        )
    else:
        logger.success(
            f"{report.persons_created} people from {report.faces_assigned:,} faces "
            f"({report.noise:,} ungrouped)."
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
        logger.info("No people yet. Run `scan-faces` then `cluster-faces`.")
        return
    for p in people:
        name = p["name"] or "(unnamed)"
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
        if repo.get_person(args.person_id) is None:
            logger.error(f"No person with id {args.person_id}.")
            sys.exit(1)
        repo.set_person_name(args.person_id, args.name)
    if args.name:
        logger.success(f"Person {args.person_id} named '{args.name}'.")
    else:
        logger.success(f"Cleared the name of person {args.person_id}.")


def _handle_merge_persons(args):
    with GalleryRepository.open(args.drive) as repo:
        if repo.get_person(args.dest) is None:
            logger.error(f"No destination person with id {args.dest}.")
            sys.exit(1)
        repo.merge_persons(args.dest, args.sources)
        dest = repo.get_person(args.dest)
    logger.success(
        f"Merged {len(args.sources)} cluster(s) into person {args.dest} "
        f"({dest['face_count']:,} faces)."
    )


def _handle_split_person(args):
    with GalleryRepository.open(args.drive) as repo:
        try:
            report = split_person(
                repo, args.person_id, algo=args.algo, eps=args.eps,
                min_samples=args.min_samples, min_cluster_size=args.min_cluster_size,
                pca=args.pca,
            )
        except ValueError as exc:
            logger.error(str(exc))
            sys.exit(1)
    logger.success(
        f"Split into {report.persons_created} sub-cluster(s); "
        f"{report.noise:,} faces now ungrouped. Review with `people`."
    )


def _handle_delete_person(args):
    with GalleryRepository.open(args.drive) as repo:
        if not repo.delete_person(args.person_id):
            logger.error(f"No person with id {args.person_id}.")
            sys.exit(1)
    logger.success(
        f"Deleted person {args.person_id}; its faces are now unassigned "
        f"(re-group them with `cluster-faces`)."
    )


_HANDLERS = {
    "init": _handle_init,
    "import": _handle_import,
    "sync": _handle_sync,
    "export": _handle_export,
    "dashboard": _handle_dashboard,
    "report": _handle_report,
    "scan-faces": _handle_scan_faces,
    "cluster-faces": _handle_cluster_faces,
    "people": _handle_people,
    "name-person": _handle_name_person,
    "merge-persons": _handle_merge_persons,
    "split-person": _handle_split_person,
    "delete-person": _handle_delete_person,
}


def main(argv=None):
    args = parse_args(argv)
    try:
        _HANDLERS[args.command](args)
    except (FileNotFoundError, FileExistsError) as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Unexpected error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
