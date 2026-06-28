"""``MediaItem`` — the single source of truth for the catalog schema.

Every column in the ``media_items`` table is a field here, in storage order
(see ``DB_COLUMNS``). This class also owns the ExifTool-tag -> column mapping
that used to be scattered across ``analysis._map_exiftool_result`` /
``_empty_df`` in the old project.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from smart_gallery.config import classify, to_abspath, to_relpath
from smart_gallery.util import clean_text, format_shutter, to_float, to_int

# Columns persisted in ``media_items``, in INSERT/SELECT order. ``id``,
# ``created_at`` and ``updated_at`` are managed by the database itself.
DB_COLUMNS = (
    "relpath",
    "media_type",
    "name",
    "ext",
    "directory_rel",
    "size_bytes",
    "mtime_ns",
    "content_hash",
    "date_taken",
    "time_taken",
    "taken_ts",
    "camera",
    "lens",
    "focal_length",
    "aperture",
    "iso",
    "shutter_speed_text",
    "shutter_speed_sec",
    "latitude",
    "longitude",
    "altitude",
    "width",
    "height",
    "duration_ms",
    "codec",
    "frame_rate",
)


def _parse_capture(value) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Parse an ExifTool date/time ('2026:06:28 14:30:00') into
    (date_taken 'YYYY-MM-DD', time_taken 'HH:MM:SS', taken_ts epoch seconds)."""
    if not value:
        return None, None, None
    text = str(value).strip()
    if not text:
        return None, None, None

    date_part, _, time_part = text.partition(" ")
    date_part = date_part.replace(":", "-")  # 2026:06:28 -> 2026-06-28
    time_part = time_part.strip() or None

    date_taken = None
    taken_ts = None
    try:
        bits = [int(b) for b in date_part.split("-")[:3]]
        if len(bits) == 3:
            date_taken = f"{bits[0]:04d}-{bits[1]:02d}-{bits[2]:02d}"
    except (ValueError, TypeError):
        date_taken = None

    if date_taken:
        stamp = f"{date_taken} {time_part}" if time_part else date_taken
        fmt = "%Y-%m-%d %H:%M:%S" if time_part else "%Y-%m-%d"
        try:
            taken_ts = int(datetime.strptime(stamp, fmt).timestamp())
        except (ValueError, OverflowError, OSError):
            taken_ts = None

    return date_taken, time_part, taken_ts


@dataclass(slots=True)
class MediaItem:
    relpath: str
    media_type: str  # 'image' | 'video' | 'other'

    name: Optional[str] = None
    ext: Optional[str] = None
    directory_rel: Optional[str] = None

    size_bytes: Optional[int] = None
    mtime_ns: Optional[int] = None
    content_hash: Optional[str] = None

    date_taken: Optional[str] = None  # 'YYYY-MM-DD'
    time_taken: Optional[str] = None  # 'HH:MM:SS'
    taken_ts: Optional[int] = None

    camera: Optional[str] = None
    lens: Optional[str] = None
    focal_length: Optional[float] = None
    aperture: Optional[float] = None
    iso: Optional[int] = None
    shutter_speed_text: Optional[str] = None
    shutter_speed_sec: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None

    width: Optional[int] = None
    height: Optional[int] = None

    duration_ms: Optional[float] = None
    codec: Optional[str] = None
    frame_rate: Optional[float] = None

    # Working handle: the absolute filesystem path. Not a DB column; set during
    # extraction/organize and when reading rows back from the database.
    abspath: Optional[str] = field(default=None, repr=False)

    # ── schema introspection ────────────────────────────────────────────────
    @staticmethod
    def columns() -> tuple[str, ...]:
        return DB_COLUMNS

    def as_params(self) -> tuple:
        """Ordered values matching ``DB_COLUMNS`` for executemany()."""
        return tuple(getattr(self, col) for col in DB_COLUMNS)

    @property
    def date_taken_date(self) -> Optional[date]:
        if not self.date_taken:
            return None
        try:
            return date.fromisoformat(self.date_taken)
        except ValueError:
            return None

    # ── anchoring to a drive ────────────────────────────────────────────────
    def anchor(self, drive_root, *, stat: bool = True) -> "MediaItem":
        """Set ``relpath``/``directory_rel`` from ``abspath`` relative to a drive
        root, and fill size/mtime from the filesystem."""
        if not self.abspath:
            return self
        self.relpath = to_relpath(self.abspath, drive_root)
        self.directory_rel = os.path.dirname(self.relpath) or None
        if stat:
            try:
                st = os.stat(self.abspath)
                if self.size_bytes is None:
                    self.size_bytes = st.st_size
                self.mtime_ns = st.st_mtime_ns
            except OSError:
                pass
        return self

    def relocate(self, dest, drive_root) -> "MediaItem":
        """Re-anchor to a new on-drive location after the file was copied there.
        EXIF stays; path-derived fields and size/mtime are refreshed."""
        dest = Path(dest)
        self.abspath = str(dest)
        self.name = dest.stem
        self.ext = dest.suffix.lower()
        self.relpath = to_relpath(dest, drive_root)
        self.directory_rel = os.path.dirname(self.relpath) or None
        try:
            st = dest.stat()
            self.size_bytes = st.st_size
            self.mtime_ns = st.st_mtime_ns
        except OSError:
            pass
        return self

    # ── constructors ────────────────────────────────────────────────────────
    @classmethod
    def from_path(cls, path, drive_root=None) -> "MediaItem":
        """Build a stat-only item for a file (used for 'other' files, no EXIF)."""
        path = Path(path)
        size_bytes = None
        mtime_ns = None
        try:
            st = path.stat()
            size_bytes = st.st_size
            mtime_ns = st.st_mtime_ns
        except OSError:
            pass
        item = cls(
            relpath="",
            media_type=classify(path.suffix),
            name=path.stem,
            ext=path.suffix.lower(),
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            abspath=str(path),
        )
        if drive_root is not None:
            item.anchor(drive_root, stat=False)
        return item

    @classmethod
    def from_exiftool(cls, exif: dict, drive_root=None) -> Optional["MediaItem"]:
        """Map one ExifTool JSON record to a MediaItem."""
        source = exif.get("SourceFile") or ""
        if not source:
            return None
        abspath = os.path.abspath(source)
        ext = os.path.splitext(source)[1].lower()
        media_type = classify(ext)
        date_taken, time_taken, taken_ts = _parse_capture(
            exif.get("DateTimeOriginal") or exif.get("CreateDate")
        )

        item = cls(
            relpath="",
            media_type=media_type,
            name=os.path.splitext(os.path.basename(source))[0],
            ext=ext,
            size_bytes=to_int(exif.get("FileSize")),
            date_taken=date_taken,
            time_taken=time_taken,
            taken_ts=taken_ts,
            abspath=abspath,
        )

        if media_type == "image":
            item.camera = clean_text(exif.get("Model"))
            item.lens = clean_text(exif.get("LensModel") or exif.get("LensID"))
            item.focal_length = to_float(exif.get("FocalLength"))
            item.aperture = to_float(exif.get("FNumber") or exif.get("Aperture"))
            item.iso = to_int(exif.get("ISO"))
            item.shutter_speed_sec = to_float(exif.get("ExposureTime"))
            item.shutter_speed_text = format_shutter(item.shutter_speed_sec)
            item.latitude = to_float(exif.get("GPSLatitude"))
            item.longitude = to_float(exif.get("GPSLongitude"))
            item.altitude = to_float(exif.get("GPSAltitude"))
            item.width = to_int(exif.get("ImageWidth") or exif.get("ExifImageWidth"))
            item.height = to_int(exif.get("ImageHeight") or exif.get("ExifImageHeight"))
        elif media_type == "video":
            item.width = to_int(exif.get("ImageWidth"))
            item.height = to_int(exif.get("ImageHeight"))
            duration = to_float(exif.get("Duration"))
            item.duration_ms = duration * 1000 if duration is not None else None
            item.codec = clean_text(
                exif.get("VideoCodecID") or exif.get("CompressorID")
            )
            item.frame_rate = to_float(exif.get("VideoFrameRate"))

        if drive_root is not None:
            item.anchor(drive_root)
        return item

    @classmethod
    def from_row(cls, row, drive_root=None) -> "MediaItem":
        """Build from a ``sqlite3.Row`` / mapping of DB columns."""
        data = {col: row[col] for col in DB_COLUMNS}
        item = cls(**data)
        if drive_root is not None and item.relpath:
            item.abspath = str(to_abspath(item.relpath, drive_root))
        return item

    # Organize/import treats brand-new placements as inserts; semantically the
    # same as an upsert, so this is just a readable alias.
    def metadata_for_organization(self) -> Optional[dict]:
        """Folder-template values {Year, Month, Model, Lens} for this item, or
        None when there is nothing date-like to organize by."""
        year = month = None
        if self.date_taken:
            parts = self.date_taken.split("-")
            if len(parts) >= 2:
                year, month = parts[0], parts[1]

        if self.media_type == "image":
            if year or month or self.camera or self.lens:
                return {
                    "Year": year,
                    "Month": month,
                    "Model": _sanitize(self.camera),
                    "Lens": _sanitize(self.lens),
                }
            return None
        if self.media_type == "video":
            if year or month:
                return {"Year": year, "Month": month, "Model": None, "Lens": None}
            return None
        return None


def _sanitize(name: Optional[str]) -> Optional[str]:
    import re

    if not name:
        return None
    return re.sub(r"[^\w\-_. ]", "_", name)
