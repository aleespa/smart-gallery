"""Compile a ``FilterOptions`` query into a parameterized SQL WHERE clause.

This mirrors ``organize.filters.matches`` exactly (a parity test enforces it),
so SQL-side filtering returns the same rows as the in-memory predicate. Values
are always bound as parameters — never string-interpolated.
"""

from typing import List, Tuple

from smart_gallery.organize.filters import (
    FilterOptions,
    has_photo_options,
    normalize_extensions,
)
from smart_gallery.util import parse_shutter


def _qmarks(values) -> str:
    return ",".join("?" for _ in values)


def _add_range(clauses: List[str], params: list, column: str, rng) -> None:
    if not rng:
        return
    low, high = rng
    if low is None and high is None:
        return
    if low is not None:
        clauses.append(f"{column} >= ?")
        params.append(low)
    if high is not None:
        clauses.append(f"{column} <= ?")
        params.append(high)


def build_where(query: FilterOptions) -> Tuple[str, list]:
    """Return ``(" WHERE ...", params)`` (or ``("", [])`` when no filters)."""
    clauses: List[str] = []
    params: list = []

    if query.filetypes:
        clauses.append(f"media_type IN ({_qmarks(query.filetypes)})")
        params.extend(query.filetypes)

    if has_photo_options(query):
        clauses.append("media_type = ?")
        params.append("image")

    extensions = normalize_extensions(query.extensions)
    if extensions:
        clauses.append(f"ext IN ({_qmarks(extensions)})")
        params.extend(extensions)

    if query.date_range:
        start, end = query.date_range
        if start:
            clauses.append("date_taken >= ?")
            params.append(start.isoformat())
        if end:
            clauses.append("date_taken <= ?")
            params.append(end.isoformat())

    if query.cameras:
        clauses.append(f"camera IN ({_qmarks(query.cameras)})")
        params.extend(query.cameras)

    if query.lenses:
        clauses.append(f"lens IN ({_qmarks(query.lenses)})")
        params.extend(query.lenses)

    _add_range(clauses, params, "aperture", query.aperture_range)
    _add_range(clauses, params, "iso", query.iso_range)

    if query.shutter_speed_range:
        low_s, high_s = query.shutter_speed_range
        _add_range(
            clauses,
            params,
            "shutter_speed_sec",
            (parse_shutter(low_s), parse_shutter(high_s)),
        )

    # People filter (face recognition): media that contain a face assigned to a
    # named person. SQL-only — there is no MediaItem-level equivalent, so this
    # clause has no counterpart in organize.filters.matches().
    if query.people:
        clauses.append(
            "id IN (SELECT f.media_id FROM faces f "
            "JOIN persons p ON p.id = f.person_id "
            f"WHERE p.name IN ({_qmarks(query.people)}))"
        )
        params.extend(query.people)

    if query.person_ids:
        clauses.append(
            "id IN (SELECT media_id FROM faces "
            f"WHERE person_id IN ({_qmarks(query.person_ids)}))"
        )
        params.extend(query.person_ids)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
