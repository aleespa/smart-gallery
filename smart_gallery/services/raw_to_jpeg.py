"""Replace listed CR3 photos with their camera JPEG, keeping their faces.

Each CR3's embedded ``JpgFromRaw`` is written beside it as ``<stem>.JPG`` with
all of the CR3's metadata and its mtime, then checked (decodes, same stored
pixel size as the CR3, same Orientation). Only then, per photo and in one
transaction, the JPEG's row is added, the CR3's faces move to it, the CR3's row
is dropped and the CR3 goes to the Recycle Bin; the commit follows a
successful recycle. Any failure leaves the CR3 and its row exactly as they were
and deletes the new JPEG. ExifTool work is batched, ``BATCH`` photos at a time.

Face boxes are stored in the frame the scanner decoded. The scanner applies an
embedded preview's own EXIF Orientation, so boxes are rotated by the CR3's
Orientation only when the preview had none (the scan saw stored pixels).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence

from loguru import logger

from smart_gallery.analysis.extract import _get_exiftool_path, analyze_paths
from smart_gallery.config import drive_root_of
from smart_gallery.db.repository import GalleryRepository
from smart_gallery.face_crops import crop_path
from smart_gallery.faces_move import move_faces
from smart_gallery.mirror.faces import ensure_not_mirror
from smart_gallery.models import MediaItem
from smart_gallery.progress import CONVERTING, ProgressCallback, ProgressEvent
from smart_gallery.recycle_bin import (
    move_to_recycle_bin,
    recycle_bin_capacity,
    supports_recycle_bin,
)
from smart_gallery.services.convert_cr3 import _copy_metadata, extract_embedded
from smart_gallery.services.recycle import RecycleEntry, _skip_reason
from smart_gallery.storage.tree import format_bytes

BATCH = 50  # photos per ExifTool pass
_ORIENTATION_TAG = 0x0112


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
    jpeg: Path
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


def _validate(photo: _Photo) -> Optional[str]:
    """Why the written JPEG can't replace the CR3 (None when it can)."""
    from PIL import Image  # a core dependency through streamlit

    try:
        if photo.jpeg.stat().st_size == 0:
            return "the JPEG is empty"
        with Image.open(photo.jpeg) as img:
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


def _face_frame_problem(conn: sqlite3.Connection, src: int, photo: _Photo):
    """Why the CR3's faces can't move as the rotation rule says (None if they
    can). The scan's recorded image size must be the frame the rule implies."""
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
        self.capacity = recycle_bin_capacity(self.root)  # None: no limit known
        self.catalog = repo.index_for_sync()
        self.report = RawToJpegReport()
        self.progress_callback = progress_callback
        self.total = total
        self.done = 0
        self.uncommitted: set[Path] = set()  # JPEGs this run wrote, not committed

    # -- reporting ------------------------------------------------------------
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
        if photo.jpeg in self.uncommitted:
            _unlink(photo.jpeg)
            self.uncommitted.discard(photo.jpeg)

    # -- per batch ------------------------------------------------------------
    def check(self, entries: Sequence[RecycleEntry]) -> list[_Photo]:
        listings: dict[Path, dict[str, str]] = {}
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
                names = {p.name.casefold(): p.name for p in cr3.parent.iterdir()}
                listings[cr3.parent] = names
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
        return photos

    def write_jpegs(self, photos: list[_Photo]) -> None:
        todo = [p for p in photos if p.ok]
        if not todo:
            return
        stderr = extract_embedded([p.cr3 for p in todo], ".JPG").splitlines()
        for p in todo:
            if p.jpeg.is_file():
                self.uncommitted.add(p.jpeg)
            else:
                reason = next(
                    (line for line in stderr if p.cr3.name in line),
                    "no embedded JPEG image",
                )
                self.fail(p, f"could not extract the JPEG: {reason}")

        todo = [p for p in todo if p.ok]
        # Read before metadata is copied: the preview's own Orientation decides
        # how face boxes move.
        tags = _read_tags([p.cr3 for p in todo] + [p.jpeg for p in todo])
        for p in todo:
            cr3_tags = tags.get(p.cr3, {})
            p.width = _int_tag(cr3_tags, "ImageWidth") or 0
            p.height = _int_tag(cr3_tags, "ImageHeight") or 0
            p.orientation = _int_tag(cr3_tags, "Orientation")
            p.preview_orientation = _int_tag(tags.get(p.jpeg, {}), "Orientation")
            if not (p.width and p.height):
                self.fail(p, "ExifTool could not read the CR3's image size")

        todo = [p for p in todo if p.ok]
        if not todo:
            return
        for error in _copy_metadata([(p.cr3, p.jpeg) for p in todo]):
            named = [
                p
                for p in todo
                if p.jpeg.name.casefold() in error.casefold()
                or p.cr3.name.casefold() in error.casefold()
            ]
            for p in named or todo:  # an error naming no file fails them all
                self.fail(p, f"could not copy metadata: {error}")

        for p in todo:
            if not p.ok:
                continue
            try:
                atime = p.cr3.stat().st_atime_ns
                os.utime(p.jpeg, ns=(atime, p.entry.mtime_ns))
            except OSError as exc:
                self.fail(p, f"could not set the JPEG's date: {exc}")
                continue
            reason = _validate(p)
            if reason is not None:
                self.fail(p, reason)

        valid = [p for p in todo if p.ok]
        items = analyze_paths([p.jpeg for p in valid], drive_root=self.root)
        by_rel = {i.relpath.casefold(): i for i in items if i.relpath}
        for p in valid:
            rel = PurePosixPath(p.entry.relpath).with_suffix(".JPG").as_posix()
            p.item = by_rel.get(rel.casefold())
            if p.item is None:
                self.fail(p, "ExifTool could not read the JPEG")

    def replace(self, photo: _Photo) -> None:
        """Catalog, then recycle, then commit, for one validated photo."""
        repo, conn = self.repo, self.repo.conn
        relpath = photo.entry.relpath
        src = conn.execute(
            "SELECT id FROM media_items WHERE relpath=?", (relpath,)
        ).fetchone()[0]
        rotate = photo.orientation if photo.preview_orientation is None else 1
        problem = _face_frame_problem(conn, src, photo)
        if problem is not None:
            self.fail(photo, problem)
            self.finish(photo)
            return
        cur = conn.cursor()
        try:
            repo._upsert(cur, [photo.item])
            dst = cur.execute(
                "SELECT id FROM media_items WHERE relpath=?", (photo.item.relpath,)
            ).fetchone()[0]
            moved = move_faces(conn, src, dst, rotate, photo.width, photo.height)
            repo._delete(cur, [relpath])
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
                xmp_rel = PurePosixPath(relpath).with_name(photo.xmp.name).as_posix()
                repo._delete(cur, [xmp_rel])
                self.catalog.pop(xmp_rel, None)
                freed += xmp_size
        try:
            conn.commit()
        except sqlite3.Error:
            logger.error(
                f"{relpath} is in the Recycle Bin and {photo.jpeg.name} is on the "
                "drive, but the catalog could not be updated; run sync."
            )
            raise

        self.catalog.pop(relpath, None)
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
    ensure_not_mirror(repo)
    root = drive_root_of(repo.db_path)
    if not supports_recycle_bin(root):
        raise ValueError(
            f"The Recycle Bin is not available on {root}; nothing was converted."
        )
    job = _Job(repo, progress_callback, len(entries))
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
    finally:
        for jpeg in job.uncommitted:
            _unlink(jpeg)
        job.uncommitted.clear()
    return job.report
