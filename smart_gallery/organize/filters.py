"""``FilterOptions`` query model and the in-memory predicate.

``matches()`` is the in-Python reference filter. ``db/where.py`` compiles the
same semantics to SQL; a parity test asserts the two agree, so the dashboard and
export get identical results whether they filter in SQL or in memory.
"""

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

from smart_gallery.models import MediaItem
from smart_gallery.util import parse_shutter


@dataclass
class FilterOptions:
    filetypes: Optional[List[str]] = None  # "image", "video", "other"
    extensions: Optional[List[str]] = None
    date_range: Optional[Tuple[Optional[date], Optional[date]]] = None
    cameras: Optional[List[str]] = None
    lenses: Optional[List[str]] = None
    aperture_range: Optional[Tuple[Optional[float], Optional[float]]] = None
    iso_range: Optional[Tuple[Optional[int], Optional[int]]] = None
    shutter_speed_range: Optional[Tuple[Optional[str], Optional[str]]] = None


def normalize_extensions(extensions) -> Optional[List[str]]:
    """Lowercase + ensure a leading dot, e.g. 'JPG' -> '.jpg'."""
    if not extensions:
        return None
    out = []
    for ext in extensions:
        e = str(ext).strip().lower()
        if not e:
            continue
        out.append(e if e.startswith(".") else f".{e}")
    return out or None


def has_photo_options(query: FilterOptions) -> bool:
    return bool(
        query.cameras
        or query.lenses
        or query.aperture_range
        or query.iso_range
        or query.shutter_speed_range
    )


def is_query_empty(query: Optional[FilterOptions]) -> bool:
    if not query:
        return True
    return not (
        query.filetypes
        or query.extensions
        or query.date_range
        or query.cameras
        or query.lenses
        or query.aperture_range
        or query.iso_range
        or query.shutter_speed_range
    )


def _in_range(value, rng) -> bool:
    if not rng:
        return True
    low, high = rng
    if low is None and high is None:
        return True
    if value is None:
        return False
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def matches(item: MediaItem, query: FilterOptions) -> bool:
    """True if ``item`` satisfies every active clause of ``query``."""
    if query.filetypes and item.media_type not in query.filetypes:
        return False

    if has_photo_options(query) and item.media_type != "image":
        return False

    extensions = normalize_extensions(query.extensions)
    if extensions and (item.ext or "").lower() not in extensions:
        return False

    if query.date_range:
        start, end = query.date_range
        if start or end:
            dt = item.date_taken_date
            if dt is None:
                return False
            if start and dt < start:
                return False
            if end and dt > end:
                return False

    if query.cameras and (not item.camera or item.camera not in query.cameras):
        return False

    if query.lenses and (not item.lens or item.lens not in query.lenses):
        return False

    if not _in_range(item.aperture, query.aperture_range):
        return False
    if not _in_range(item.iso, query.iso_range):
        return False

    if query.shutter_speed_range:
        low_s, high_s = query.shutter_speed_range
        rng = (parse_shutter(low_s), parse_shutter(high_s))
        if not _in_range(item.shutter_speed_sec, rng):
            return False

    return True
