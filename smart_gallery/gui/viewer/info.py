"""What the photo viewer's info panel shows, and who is in a photo. No Qt.

``build_info`` turns a catalog row into the panel's sections; the window only
lays them out. ``photo_people`` reads the faces of one photo (read-only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from smart_gallery.models import MediaItem
from smart_gallery.util import format_shutter

EMPTY = "—"
DATE_UNKNOWN = "Date unknown"
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)  # fmt: skip
_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)  # fmt: skip


@dataclass(frozen=True)
class InfoSection:
    """One block of the info panel. ``key`` is date, exposure, camera,
    people, video or file; ``heading`` is the large (Georgia) line;
    ``accent_chips`` are named people."""

    key: str
    label: str
    lines: tuple[str, ...] = ()
    chips: tuple[str, ...] = ()
    accent_chips: tuple[str, ...] = ()
    heading: str = ""


@dataclass(frozen=True)
class PhotoFace:
    face_id: int
    name: Optional[str]
    bbox: tuple[float, float, float, float]
    img_size: Optional[tuple[int, int]]


@dataclass(frozen=True)
class PhotoPeople:
    names: tuple[str, ...]  # named people, each once, by name
    unnamed: int  # faces of nobody named (ignored and hidden ones left out)
    faces: tuple[PhotoFace, ...] = ()


def format_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return EMPTY
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            break
        size /= 1024
    else:
        unit = "TB"
    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"


def format_duration(ms: float | None) -> str:
    if ms is None:
        return EMPTY
    total = int(round(ms / 1000))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _date_section(item: MediaItem) -> InfoSection:
    import datetime

    try:
        day = datetime.date.fromisoformat(item.date_taken or "")
    except ValueError:
        return InfoSection("date", "Date", heading=DATE_UNKNOWN)
    heading = f"{day.day} {_MONTHS[day.month - 1]} {day.year}"
    when = _WEEKDAYS[day.weekday()]
    if item.time_taken:
        when += f" · {item.time_taken[:5]}"
    return InfoSection("date", "Date", (when,), heading=heading)


def _shutter(item: MediaItem) -> str | None:
    text = item.shutter_speed_text or format_shutter(item.shutter_speed_sec)
    if not text:
        return None
    text = text.strip()
    if text.endswith("s") and not text.endswith(" s"):
        text = text[:-1].rstrip() + " s"
    return text


def _exposure(item: MediaItem) -> InfoSection | None:
    chips = []
    if item.aperture:
        chips.append(f"f/{item.aperture:g}")
    if shutter := _shutter(item):
        chips.append(shutter)
    if item.iso:
        chips.append(f"ISO {item.iso}")
    if item.focal_length:
        chips.append(f"{item.focal_length:g} mm")
    return InfoSection("exposure", "Exposure", chips=tuple(chips)) if chips else None


def _camera(item: MediaItem) -> InfoSection | None:
    lines = tuple(x for x in (item.camera, item.lens) if x)
    return InfoSection("camera", "Camera", lines) if lines else None


def _people(people: PhotoPeople | None) -> InfoSection | None:
    if people is None or not (people.names or people.unnamed):
        return None
    chips = (f"+{people.unnamed} unnamed",) if people.unnamed else ()
    return InfoSection("people", "People", chips=chips, accent_chips=people.names)


def _video(item: MediaItem) -> InfoSection | None:
    if item.media_type != "video":
        return None
    lines = []
    if item.duration_ms is not None:
        lines.append(format_duration(item.duration_ms))
    detail = [
        x
        for x in (item.codec, f"{item.frame_rate:g} fps" if item.frame_rate else None)
        if x
    ]
    if detail:
        lines.append(" · ".join(detail))
    return InfoSection("video", "Video", tuple(lines)) if lines else None


def _file(item: MediaItem) -> InfoSection:
    first = format_size(item.size_bytes)
    if item.width and item.height:
        first = f"{item.width} × {item.height} · {first}"
    lines = [first, item.relpath]
    if item.latitude is not None and item.longitude is not None:
        lines.append(f"{item.latitude:.5f}, {item.longitude:.5f}")
    return InfoSection("file", "File", tuple(lines))


def build_info(item: MediaItem, people: PhotoPeople | None) -> list[InfoSection]:
    """The panel's sections in order; empty ones left out (the date always
    shows, as "Date unknown" when there is none)."""
    parts = (
        _date_section(item),
        _exposure(item),
        _camera(item),
        _people(people),
        _video(item),
        _file(item),
    )
    return [p for p in parts if p is not None]


def photo_people(repo, media_id: int) -> PhotoPeople | None:
    """Who is in this photo: named people, the count of other faces, and
    every face with its box. Ignored faces and hidden groups are left out.
    None when the catalog has no face tables."""
    has = repo.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='faces'"
    ).fetchone()
    if not has:
        return None
    rows = repo.conn.execute(
        "SELECT f.id, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h,"
        " CASE WHEN p.name IS NOT NULL AND p.name <> '' THEN p.name END AS name,"
        " s.img_w, s.img_h"
        " FROM faces f LEFT JOIN persons p ON p.id = f.person_id"
        " LEFT JOIN face_scan_state s ON s.media_id = f.media_id"
        " WHERE f.media_id = ? AND f.state <> 'ignored'"
        " AND (p.hidden IS NULL OR p.hidden = 0)"
        " ORDER BY f.id",
        (media_id,),
    ).fetchall()
    faces = tuple(
        PhotoFace(
            r["id"],
            r["name"],
            (r["bbox_x"], r["bbox_y"], r["bbox_w"], r["bbox_h"]),
            (r["img_w"], r["img_h"]) if r["img_w"] and r["img_h"] else None,
        )
        for r in rows
    )
    names = tuple(sorted({f.name for f in faces if f.name}, key=str.lower))
    return PhotoPeople(names, sum(1 for f in faces if not f.name), faces)
