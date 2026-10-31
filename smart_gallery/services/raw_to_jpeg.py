"""Replace listed CR3 photos with their camera JPEG, keeping their faces.

Each CR3's embedded ``JpgFromRaw`` is written to a staging folder inside
``.smart_gallery`` (which sync never walks) with all of the CR3's metadata and
its mtime, then checked (decodes, same stored pixel size as the CR3, same
Orientation). Only then, per photo and in one transaction, the JPEG is moved
beside the CR3 as ``<stem>.JPG`` (never over an existing file), its row is
added, the CR3's faces move to it, the CR3's row is dropped and the CR3 goes to
the Recycle Bin; the commit follows a successful recycle. Any failure leaves
the CR3 and its row exactly as they were and deletes the new JPEG. ExifTool
work is batched, ``BATCH`` photos at a time. A run killed outright leaves at
most staged files, which the next run clears, plus, in the instant between
recycling one CR3 and committing, that one photo's JPEG (see the README).

Face boxes are stored in the frame the scanner decoded. The scanner applies an
embedded preview's own EXIF Orientation, so boxes are rotated by the CR3's
Orientation only when the preview had none (the scan saw stored pixels).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence

from loguru import logger

from smart_gallery.analysis.extract import (
    _get_exiftool_path,
    analyze_paths,
    run_exiftool_argfile,
)
from smart_gallery.config import DB_DIRNAME, drive_root_of, to_relpath
from smart_gallery.db.repository import GalleryRepository
from smart_gallery.face_crops import crop_path
from smart_gallery.faces_move import move_faces, rotate_box
from smart_gallery.mirror.faces import ensure_not_mirror
from smart_gallery.models import MediaItem
from smart_gallery.progress import (
    CONVERTING,
    Cancelled,
    ProgressCallback,
    ProgressEvent,
    check_cancelled,
)
from smart_gallery.recycle_bin import (
    move_to_recycle_bin,
    recycle_bin_capacity,
    supports_recycle_bin,
)
from smart_gallery.services.convert_cr3 import _copy_metadata
from smart_gallery.services.recycle import RecycleEntry, _skip_reason
from smart_gallery.storage.tree import format_bytes

BATCH = 10  # photos per ExifTool pass
STAGING = "staging"  # under .smart_gallery, which config.IGNORE_DIRS skips
FRAME_TOLERANCE = 0.1  # how far a box may stray past an implied frame
_ORIENTATION_TAG = 0x0112


class RawToJpegRefused(ValueError):
    """The job was refused before touching anything (a mirror, or no Recycle
    Bin), so its list can be run again."""


class RawToJpegCommitFailed(ValueError):
    """The catalog could not be saved after a CR3 went to the Recycle Bin."""


@dataclass
class RawToJpegReport:
    converted: int = 0
    skipped: int = 0
    freed_bytes: int = 0  # CR3s (and .xmp) recycled, less the JPEGs written
    faces_moved: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class _Photo:
    entry: RecycleEntry
    cr3: Path
    jpeg: Path  # where it goes, beside the CR3
    staged: Optional[Path] = None  # where it is written and checked first
    src_id: int = 0  # the CR3's catalog id
    xmp: Optional[Path] = None
    skip: Optional[str] = None
    error: Optional[str] = None
    width: int = 0
    height: int = 0
    orientation: Optional[int] = None  # the CR3's
    preview_orientation: Optional[int] = None  # the embedded JPEG's own tag
    item: Optional[MediaItem] = None

    @property
    def ok(self) -> bool:
        return self.skip is None and self.error is None


def _key(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _read_tags(paths: list[Path]) -> dict[Path, dict]:
    """ImageWidth, ImageHeight and Orientation of each path, in one ExifTool
    call. A tag the file lacks is missing from its dict."""
    if not paths:
        return {}
    args = ["-j", "-n", "-ImageWidth", "-ImageHeight", "-Orientation"]
    with tempfile.TemporaryDirectory() as tmp:
        argfile = Path(tmp) / "args.txt"
        argfile.write_text("\n".join(args + list(map(str, paths))) + "\n", "utf-8")
        result = subprocess.run(
            [_get_exiftool_path(), "-charset", "filename=utf8", "-@", str(argfile)],
            capture_output=True,
        )
    try:
        records = json.loads(result.stdout.decode("utf-8", errors="replace") or "[]")
    except ValueError:
        records = []
    by_key = {_key(r.get("SourceFile", "")): r for r in records}
    return {p: by_key.get(_key(p), {}) for p in paths}


def _int_tag(tags: dict, name: str) -> Optional[int]:
    value = tags.get(name)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _extract(photos: list[_Photo]) -> str:
    """Write each CR3's ``JpgFromRaw`` to its staged path, in one ExifTool
    process (one ``-execute`` group per photo, as the names differ). ``-w``
    never overwrites. Returns ExifTool's stderr."""
    args = []
    for p in photos:
        # The staged name is ``<prefix><stem>.JPG``; %f is the CR3's stem.
        prefix = p.staged.name[: -len(p.cr3.stem) - len(".JPG")]
        template = (p.staged.parent / f"{prefix}%f.JPG").as_posix()
        args += ["-b", "-JpgFromRaw", "-w", template, str(p.cr3), "-execute"]
    return run_exiftool_argfile(args)


def _place(staged: Path, target: Path) -> None:
    """Move ``staged`` to ``target``, failing if ``target`` exists."""
    if os.name == "nt":
        os.rename(staged, target)  # refuses an existing target
    else:  # rename would overwrite; a hard link refuses
        os.link(staged, target)
        os.unlink(staged)


def _validate(photo: _Photo) -> Optional[str]:
    """Why the staged JPEG can't replace the CR3 (None when it can)."""
    from PIL import Image  # a core dependency through streamlit

    try:
        if photo.staged.stat().st_size == 0:
            return "the JPEG is empty"
        with Image.open(photo.staged) as img:
            fmt, size = img.format, img.size
            orientation = img.getexif().get(_ORIENTATION_TAG)
            img.load()  # decodes every pixel
    except (OSError, SyntaxError, ValueError) as exc:
        return f"the JPEG does not decode ({exc})"
    if fmt != "JPEG":
        return f"the embedded image is {fmt}, not JPEG"
    if size != (photo.width, photo.height):
        return (
            f"the JPEG is {size[0]}x{size[1]}, the CR3 " f"{photo.width}x{photo.height}"
        )
    if (orientation or 1) != (photo.orientation or 1):
        return "the CR3's orientation was not copied to the JPEG"
    return None


def _boxes_outside(conn, src: int, photo: _Photo, rotate, frame) -> bool:
    """True when a face or carry-over box, rotated as it will be, lies
    outside ``frame`` (the JPEG's oriented frame) by more than the tolerance."""
    fw, fh = frame
    tol = FRAME_TOLERANCE
    rows = conn.execute(
        "SELECT bbox_x, bbox_y, bbox_w, bbox_h FROM faces WHERE media_id=? "
        "UNION ALL SELECT bbox_x, bbox_y, bbox_w, bbox_h FROM face_carryover "
        "WHERE media_id=?",
        (src, src),
    ).fetchall()
    for row in rows:
        x, y, w, h = rotate_box(tuple(row), rotate, photo.width, photo.height)
        if (
            x < -tol * fw
            or y < -tol * fh
            or x + w > (1 + tol) * fw
            or y + h > (1 + tol) * fh
        ):
            return True
    return False


def _face_frame_problem(conn: sqlite3.Connection, src: int, photo: _Photo):
    """Why the CR3's faces can't move as the rotation rule says (None if they
    can). The scan's recorded image size must be the frame the rule implies;
    when the scan recorded none, every box must fit that frame instead."""
    has_faces = conn.execute(
        "SELECT 1 FROM faces WHERE media_id=? "
        "UNION ALL SELECT 1 FROM face_carryover WHERE media_id=? LIMIT 1",
        (src, src),
    ).fetchone()
    if not has_faces:
        return None
    if photo.preview_orientation is None:
        expected = (photo.width, photo.height)  # the scan saw stored pixels
    else:
        if photo.preview_orientation != (photo.orientation or 1):
            return "face frame mismatch: the preview and CR3 orientations differ"
        swap = photo.preview_orientation in (5, 6, 7, 8)
        expected = (photo.height, photo.width) if swap else (photo.width, photo.height)
    row = conn.execute(
        "SELECT img_w, img_h FROM face_scan_state WHERE media_id=?", (src,)
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        rotate = photo.orientation if photo.preview_orientation is None else 1
        swap = rotate in (5, 6, 7, 8)
        frame = (expected[1], expected[0]) if swap else expected
        try:
            outside = _boxes_outside(conn, src, photo, rotate, frame)
        except ValueError as exc:  # an orientation boxes can't follow
            return f"face frame mismatch: {exc}"
        if outside:
            return (
                "face frame mismatch: a face lies outside the "
                f"{frame[0]}x{frame[1]} frame"
            )
        return None
    if (row[0], row[1]) != expected:
        return (
            f"face frame mismatch: faces were found in {row[0]}x{row[1]}, "
            f"expected {expected[0]}x{expected[1]}"
        )
    return None


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"Could not delete {path}: {exc}")


class _Job:
    def __init__(self, repo, progress_callback, total):
        self.repo = repo
        self.root = drive_root_of(repo.db_path)
        self.staging = self.root / DB_DIRNAME / STAGING
        self.capacity = recycle_bin_capacity(self.root)  # None: no limit known
        self.catalog = repo.index_for_sync()
        self.report = RawToJpegReport()
        self.progress_callback = progress_callback
        self.total = total
        self.done = 0
        # JPEGs placed beside their CR3 but not committed: at most the one
        # photo ``replace`` is working on.
        self.uncommitted: set[Path] = set()

    # -- staging --------------------------------------------------------------
    def clear_staging(self) -> None:
        """Empty the staging folder, removing what a killed run left behind."""
        shutil.rmtree(self.staging, ignore_errors=True)
        self.staging.mkdir(parents=True, exist_ok=True)

    def remove_staging(self) -> None:
        shutil.rmtree(self.staging, ignore_errors=True)
        if self.staging.exists():
            logger.warning(f"Could not remove {self.staging}; the next run will.")

    # -- reporting ------------------------------------------------------------
    def tick(self) -> None:
        """A stop point inside a batch: stop if asked, and report progress."""
        check_cancelled()
        self.emit()

    def emit(self) -> None:
        if self.progress_callback is not None:
            r = self.report
            self.progress_callback(
                ProgressEvent(
                    CONVERTING,
                    self.done,
                    self.total,
                    outcomes=(
                        ("converted", r.converted),
                        ("skipped", r.skipped),
                        ("failed", len(r.failures)),
                    ),
                )
            )

    def fail(self, photo: _Photo, reason: str) -> None:
        if photo.ok:
            photo.error = reason

    def finish(self, photo: _Photo) -> None:
        """Record a photo that was skipped or failed before the catalog step."""
        if photo.skip is not None:
            self.report.skipped += 1
            logger.warning(f"Skipped: {photo.entry.relpath} ({photo.skip})")
        else:
            self.report.failures.append(f"{photo.entry.relpath}: {photo.error}")
            logger.warning(f"Not converted: {self.report.failures[-1]}")
        if photo.staged is not None:
            _unlink(photo.staged)
        if photo.jpeg in self.uncommitted:
            _unlink(photo.jpeg)
            self.uncommitted.discard(photo.jpeg)

    # -- per batch ------------------------------------------------------------
    def check(self, entries: Sequence[RecycleEntry]) -> list[_Photo]:
        listings: dict[Path, dict[str, str] | OSError] = {}
        photos = []
        for entry in entries:
            reason, cr3 = _skip_reason(self.root, entry, self.catalog)
            photo = _Photo(entry, cr3, cr3.with_suffix(".JPG"))
            photos.append(photo)
            if reason is not None:
                photo.skip = reason
                continue
            if cr3.suffix.lower() != ".cr3":
                photo.skip = "not a CR3"
                continue
            names = listings.get(cr3.parent)
            if names is None:
                try:
                    names = {p.name.casefold(): p.name for p in cr3.parent.iterdir()}
                except OSError as exc:  # fails this folder's photos only
                    names = exc
                listings[cr3.parent] = names
            if isinstance(names, OSError):
                photo.error = f"could not list its folder ({names})"
                continue
            stem = cr3.stem.casefold()
            if {stem + ".jpg", stem + ".jpeg"} & names.keys():
                photo.skip = "a JPEG with its name already exists"
                continue
            jpeg_rel = PurePosixPath(entry.relpath).with_suffix(".JPG").as_posix()
            if self.repo.has_relpath(jpeg_rel):
                photo.skip = "the catalog already has a JPEG with its name"
                continue
            if self.capacity is not None and entry.size_bytes > self.capacity:
                # Windows would delete it for good rather than keep it.
                photo.error = (
                    f"too big for the Recycle Bin "
                    f"(limit {format_bytes(self.capacity)})"
                )
                continue
            names[stem + ".jpg"] = photo.jpeg.name  # claimed by this batch
            xmp = names.get(stem + ".xmp")
            if xmp is not None:
                photo.xmp = cr3.parent / xmp
            photo.src_id = self.repo.conn.execute(
                "SELECT id FROM media_items WHERE relpath=?", (entry.relpath,)
            ).fetchone()[0]
            # Keyed by catalog id, so two folders' IMG_0001s never collide.
            photo.staged = self.staging / f"id{photo.src_id}-{cr3.stem}.JPG"
        return photos

    def write_jpegs(self, photos: list[_Photo]) -> None:
        """Write and check each photo's JPEG in the staging folder. Nothing is
        written beside any CR3 here."""
        todo = [p for p in photos if p.ok]
        if not todo:
            return
        check_cancelled()
        stderr = _extract(todo).splitlines()
        for p in todo:
            if not (p.staged.is_file() and p.staged.stat().st_size):
                reason = next(
                    (line for line in stderr if p.cr3.name in line),
                    "no embedded JPEG image",
                )
                self.fail(p, f"could not extract the JPEG: {reason}")

        todo = [p for p in todo if p.ok]
        check_cancelled()
        # Read before metadata is copied: the preview's own Orientation decides
        # how face boxes move.
        tags = _read_tags([p.cr3 for p in todo] + [p.staged for p in todo])
        for p in todo:
            cr3_tags = tags.get(p.cr3, {})
            p.width = _int_tag(cr3_tags, "ImageWidth") or 0
            p.height = _int_tag(cr3_tags, "ImageHeight") or 0
            p.orientation = _int_tag(cr3_tags, "Orientation")
            p.preview_orientation = _int_tag(tags.get(p.staged, {}), "Orientation")
            if not (p.width and p.height):
                self.fail(p, "ExifTool could not read the CR3's image size")

        todo = [p for p in todo if p.ok]
        if not todo:
            return
        check_cancelled()
        for error in _copy_metadata([(p.cr3, p.staged) for p in todo]):
            named = [
                p
                for p in todo
                if p.staged.name.casefold() in error.casefold()
                or p.cr3.name.casefold() in error.casefold()
            ]
            for p in named or todo:  # an error naming no file fails them all
                self.fail(p, f"could not copy metadata: {error}")

        for p in todo:
            self.tick()
            if not p.ok:
                continue
            try:
                atime = p.cr3.stat().st_atime_ns
                os.utime(p.staged, ns=(atime, p.entry.mtime_ns))
            except OSError as exc:
                self.fail(p, f"could not set the JPEG's date: {exc}")
                continue
            reason = _validate(p)
            if reason is not None:
                self.fail(p, reason)

        valid = [p for p in todo if p.ok]
        if not valid:
            return
        check_cancelled()
        items = analyze_paths([p.staged for p in valid], drive_root=self.root)
        by_rel = {i.relpath.casefold(): i for i in items if i.relpath}
        for p in valid:
            p.item = by_rel.get(to_relpath(p.staged, self.root).casefold())
            if p.item is None:
                self.fail(p, "ExifTool could not read the JPEG")

    def replace(self, photo: _Photo) -> None:
        """Catalog, then recycle, then commit, for one validated photo."""
        repo, conn = self.repo, self.repo.conn
        relpath = photo.entry.relpath
        src = photo.src_id
        rotate = photo.orientation if photo.preview_orientation is None else 1
        problem = _face_frame_problem(conn, src, photo)
        if problem is not None:
            self.fail(photo, problem)
            self.finish(photo)
            return
        xmp_rel = None
        if photo.xmp is not None:
            xmp_rel = PurePosixPath(relpath).with_name(photo.xmp.name).as_posix()
        try:
            _place(photo.staged, photo.jpeg)
        except OSError as exc:  # e.g. a file of that name appeared meanwhile
            self.fail(photo, f"could not move the JPEG beside the CR3 ({exc})")
            self.finish(photo)
            return
        self.uncommitted.add(photo.jpeg)
        photo.item.relocate(photo.jpeg, self.root)
        cur = conn.cursor()
        try:
            repo._upsert(cur, [photo.item])
            dst = cur.execute(
                "SELECT id FROM media_items WHERE relpath=?", (photo.item.relpath,)
            ).fetchone()[0]
            moved = move_faces(conn, src, dst, rotate, photo.width, photo.height)
            repo._delete(cur, [relpath])
            if xmp_rel is not None:
                # Dropped with the CR3's row: if recycling the file fails, the
                # next sync simply adds it back.
                repo._delete(cur, [xmp_rel])
            # From here the JPEG must outlive any interruption: once the CR3
            # may be in the Recycle Bin, the JPEG is the only copy on disk.
            self.uncommitted.discard(photo.jpeg)
            move_to_recycle_bin(photo.cr3)
        except (OSError, ValueError, sqlite3.Error) as exc:
            conn.rollback()
            _unlink(photo.jpeg)
            self.uncommitted.discard(photo.jpeg)
            photo.error = str(exc) or type(exc).__name__
            self.finish(photo)
            return

        freed = photo.entry.size_bytes
        if photo.xmp is not None:
            try:
                xmp_size = photo.xmp.stat().st_size
                move_to_recycle_bin(photo.xmp)
            except OSError as exc:
                self.report.failures.append(
                    f"{photo.xmp.name}: not recycled ({exc}); its CR3 was converted"
                )
                logger.warning(self.report.failures[-1])
            else:
                freed += xmp_size
        try:
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            # The CR3's row still owns its faces. A sync now would drop that
            # row (the file is gone) and its faces with it.
            jpeg_rel = PurePosixPath(relpath).with_name(photo.jpeg.name).as_posix()
            logger.error(
                f"The catalog could not be updated after {relpath} went to the "
                f"Recycle Bin. Restore {relpath} from the Recycle Bin and delete "
                f"{jpeg_rel}, then run raw-to-jpeg again. Do not run sync first: "
                "it would drop the CR3's faces."
            )
            raise RawToJpegCommitFailed(
                f"The catalog could not be updated after {relpath} went to the "
                "Recycle Bin; see the message above."
            ) from exc

        self.catalog.pop(relpath, None)
        if xmp_rel is not None:
            self.catalog.pop(xmp_rel, None)
        for face_id in moved:  # recut from the JPEG when next shown
            try:
                crop_path(self.root, face_id).unlink(missing_ok=True)
            except OSError:
                pass
        r = self.report
        r.converted += 1
        r.faces_moved += len(moved)
        try:
            r.freed_bytes += freed - photo.jpeg.stat().st_size
        except OSError:
            r.freed_bytes += freed
        logger.debug(f"Converted: {relpath} -> {photo.jpeg.name}")


def replace_raws(
    repo: GalleryRepository,
    entries: Sequence[RecycleEntry],
    progress_callback: ProgressCallback | None = None,
) -> RawToJpegReport:
    """Replace each listed CR3 with its embedded JPEG; see the module docstring."""
    try:
        ensure_not_mirror(repo)
    except ValueError as exc:
        raise RawToJpegRefused(str(exc)) from None
    root = drive_root_of(repo.db_path)
    if not supports_recycle_bin(root):
        raise RawToJpegRefused(
            f"The Recycle Bin is not available on {root}; nothing was converted."
        )
    job = _Job(repo, progress_callback, len(entries))
    job.clear_staging()
    job.emit()
    try:
        for start in range(0, len(entries), BATCH):
            photos = job.check(entries[start : start + BATCH])
            job.write_jpegs(photos)
            for photo in photos:
                if photo.ok:
                    job.replace(photo)
                else:
                    job.finish(photo)
                job.done += 1
                job.emit()
    except Cancelled as exc:
        exc.report = job.report  # what was done before the stop
        raise
    finally:
        for jpeg in job.uncommitted:
            _unlink(jpeg)
        job.uncommitted.clear()
        job.remove_staging()
    return job.report
