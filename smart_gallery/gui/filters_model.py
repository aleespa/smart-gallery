"""Values edited by the filter panel and their conversion to ``FilterOptions``.

No Qt here. Text fields hold what the owner typed; ``to_filter_options``
parses them the way the CLI parses its filter flags, so a panel query and the
matching ``build_filter_query`` result compare equal.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from smart_gallery.db.repository import GalleryRepository
from smart_gallery.organize.filters import FilterOptions, normalize_extensions
from smart_gallery.util import parse_shutter

DATE_ERROR = "Use a date like 2026-06-30."
NUMBER_ERROR = "Use a number like 2.8."
WHOLE_ERROR = "Use a whole number like 400."
SHUTTER_ERROR = "Use a shutter time like 1/250 or 2s."


@dataclass
class FilterValues:
    date_from: str = ""
    date_to: str = ""
    cameras: tuple[str, ...] = ()
    lenses: tuple[str, ...] = ()
    filetypes: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    min_aperture: str = ""
    max_aperture: str = ""
    min_iso: str = ""
    max_iso: str = ""
    min_shutter: str = ""
    max_shutter: str = ""
    people: tuple[str, ...] = ()
    people_match: str = "any"


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


def _parse_aperture(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise ValueError(text)
    return value


def _parse_iso(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise ValueError(text)
    return value


def _parse_shutter_text(text: str) -> str:
    # FilterOptions keeps the shutter text, as the CLI does; only validate it.
    seconds = parse_shutter(text)
    if seconds is None or seconds <= 0:
        raise ValueError(text)
    return text


_RANGES = (
    ("date_range", "date_from", "date_to", _parse_date, DATE_ERROR),
    ("aperture_range", "min_aperture", "max_aperture", _parse_aperture, NUMBER_ERROR),
    ("iso_range", "min_iso", "max_iso", _parse_iso, WHOLE_ERROR),
    (
        "shutter_speed_range",
        "min_shutter",
        "max_shutter",
        _parse_shutter_text,
        SHUTTER_ERROR,
    ),
)


def to_filter_options(v: FilterValues) -> tuple[FilterOptions | None, dict[str, str]]:
    """Parse ``v``. Returns ``(options, errors)``: ``errors`` maps a field name
    to a message, and ``options`` is None when there are errors or when every
    value is blank (so ``is_query_empty`` holds)."""
    errors: dict[str, str] = {}
    ranges = {}
    for option, low_field, high_field, parse, message in _RANGES:
        bounds = []
        for name in (low_field, high_field):
            text = getattr(v, name).strip()
            if not text:
                bounds.append(None)
                continue
            try:
                bounds.append(parse(text))
            except ValueError:
                errors[name] = message
                bounds.append(None)
        ranges[option] = tuple(bounds) if any(b is not None for b in bounds) else None
    if errors:
        return None, errors
    options = FilterOptions(
        filetypes=list(v.filetypes) or None,
        extensions=normalize_extensions(v.extensions),
        cameras=list(v.cameras) or None,
        lenses=list(v.lenses) or None,
        people=list(v.people) or None,
        people_match=v.people_match if v.people else "any",
        **ranges,
    )
    return (None if options == FilterOptions() else options), {}


def _text(value) -> str:
    return "" if value is None else str(value)


def from_filter_options(options: FilterOptions | None) -> FilterValues:
    """The panel values that show ``options`` (``person_ids`` has no field)."""
    if options is None:
        return FilterValues()
    fields = {}
    for option, low_field, high_field, _parse, _message in _RANGES:
        low, high = getattr(options, option) or (None, None)
        if isinstance(low, date):
            low = low.isoformat()
        if isinstance(high, date):
            high = high.isoformat()
        fields[low_field], fields[high_field] = _text(low), _text(high)
    return FilterValues(
        cameras=tuple(options.cameras or ()),
        lenses=tuple(options.lenses or ()),
        filetypes=tuple(options.filetypes or ()),
        extensions=tuple(options.extensions or ()),
        people=tuple(options.people or ()),
        people_match=options.people_match,
        **fields,
    )


def _has_face_data(repo: GalleryRepository) -> bool:
    try:
        row = repo.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM persons) OR EXISTS(SELECT 1 FROM faces)"
        ).fetchone()
    except sqlite3.OperationalError:
        return False  # an old catalog without the face tables
    return bool(row[0])


def filter_choices(repo: GalleryRepository) -> dict[str, list]:
    """The values the panel offers. ``cameras``, ``lenses``, ``extensions``
    and ``people`` are ``(value, photo count)`` pairs, most-used first;
    ``people`` (named persons) is there only when the catalog has face data.
    ``date_bounds`` is ``[first, last]`` as ISO dates, or empty."""
    low, high = repo.date_bounds()
    choices = {
        "cameras": repo.value_counts("camera"),
        "lenses": repo.value_counts("lens"),
        "extensions": repo.value_counts("ext"),
        "date_bounds": [low.isoformat(), high.isoformat()] if low and high else [],
    }
    if _has_face_data(repo):
        choices["people"] = _people_counts(repo)
    return choices


def _people_counts(repo: GalleryRepository) -> list[tuple[str, int]]:
    """Each person's name with the photos their faces are in. Persons that
    share a name are one choice, as the filter matches by name."""
    rows = repo.conn.execute(
        "SELECT p.name AS name, COUNT(DISTINCT f.media_id) AS n FROM persons p "
        "LEFT JOIN faces f ON f.person_id = p.id "
        "WHERE p.name IS NOT NULL AND p.name != '' "
        "GROUP BY p.name ORDER BY n DESC, lower(p.name), p.name"
    )
    return [(r["name"], r["n"]) for r in rows]
