"""Read-only comparison of two Smart Gallery catalogs."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Optional

from smart_gallery.db import GalleryRepository
from smart_gallery.models import DB_COLUMNS


@dataclass(frozen=True, slots=True)
class CompareRow:
    status: str
    key: Optional[str] = None
    path_a: Optional[str] = None
    path_b: Optional[str] = None
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompareReport:
    match_by: str
    rows: tuple[CompareRow, ...]

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(row.status for row in self.rows)
        return {key: counts[key] for key in (
            "only_a", "only_b", "changed", "unchanged", "ambiguous",
            "unmatched_a", "unmatched_b",
        )}


def _load_records(repo: GalleryRepository) -> list[dict]:
    columns = ", ".join(f'"{column}"' for column in DB_COLUMNS)
    try:
        return [dict(row) for row in repo.conn.execute(
            f"SELECT {columns} FROM media_items ORDER BY relpath COLLATE NOCASE"
        )]
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Invalid or incompatible gallery database: {repo.db_path}") from exc


def _norm_path(value: str) -> str:
    return value.replace("\\", "/").casefold()


def _match_key(row: dict, match_by: str) -> Optional[str]:
    if match_by == "path":
        return _norm_path(row["relpath"])
    if match_by == "name":
        return PurePosixPath(_norm_path(row["relpath"])).name.casefold()
    value = row.get("content_hash")
    return str(value).strip().casefold() if value and str(value).strip() else None


def _changed_fields(row_a: dict, row_b: dict) -> tuple[str, ...]:
    # Paths are matching keys in path mode; compare the remaining catalog data.
    return tuple(
        column for column in DB_COLUMNS
        if column != "relpath" and row_a.get(column) != row_b.get(column)
    )


def compare_galleries(gallery_a, gallery_b, *, match_by: str = "path") -> CompareReport:
    """Compare catalogs without writing to either database or media drive.

    ``gallery_a`` and ``gallery_b`` may be drive roots or direct database paths.
    Name and hash matching only pair keys that occur once per catalog; duplicate
    keys are reported as ambiguous instead of being paired arbitrarily.
    """
    if match_by not in {"path", "name", "hash"}:
        raise ValueError("match_by must be 'path', 'name', or 'hash'.")

    with GalleryRepository.open(gallery_a, read_only=True) as repo_a:
        records_a = _load_records(repo_a)
    with GalleryRepository.open(gallery_b, read_only=True) as repo_b:
        records_b = _load_records(repo_b)

    if match_by == "hash":
        if not any(_match_key(row, "hash") for row in records_a):
            raise ValueError(f"No content hashes are stored in gallery A ({gallery_a}).")
        if not any(_match_key(row, "hash") for row in records_b):
            raise ValueError(f"No content hashes are stored in gallery B ({gallery_b}).")

    keyed_a: dict[str, list[dict]] = defaultdict(list)
    keyed_b: dict[str, list[dict]] = defaultdict(list)
    rows: list[CompareRow] = []
    for record in records_a:
        key = _match_key(record, match_by)
        if key is None:
            rows.append(CompareRow("unmatched_a", path_a=record["relpath"]))
        else:
            keyed_a[key].append(record)
    for record in records_b:
        key = _match_key(record, match_by)
        if key is None:
            rows.append(CompareRow("unmatched_b", path_b=record["relpath"]))
        else:
            keyed_b[key].append(record)

    for key in sorted(keyed_a.keys() | keyed_b.keys()):
        group_a, group_b = keyed_a.get(key, []), keyed_b.get(key, [])
        if len(group_a) > 1 or len(group_b) > 1:
            # Preserve each path in the CSV/JSON output, paired by side only.
            rows.extend(CompareRow("ambiguous", key, path_a=row["relpath"])
                        for row in group_a)
            rows.extend(CompareRow("ambiguous", key, path_b=row["relpath"])
                        for row in group_b)
        elif not group_a:
            rows.append(CompareRow("only_b", key, path_b=group_b[0]["relpath"]))
        elif not group_b:
            rows.append(CompareRow("only_a", key, path_a=group_a[0]["relpath"]))
        else:
            changed = _changed_fields(group_a[0], group_b[0])
            rows.append(CompareRow(
                "changed" if changed else "unchanged", key,
                path_a=group_a[0]["relpath"], path_b=group_b[0]["relpath"],
                changed_fields=changed,
            ))

    rows.sort(key=lambda row: (
        row.status, (row.key or "").casefold(),
        (row.path_a or "").casefold(), (row.path_b or "").casefold(),
    ))
    return CompareReport(match_by=match_by, rows=tuple(rows))


def format_compare_report(report: CompareReport, *, output_format: str = "text",
                          details: bool = False) -> str:
    """Render a comparison as stable text, JSON, or CSV."""
    counts = report.counts
    if output_format == "json":
        return json.dumps({
            "match_by": report.match_by,
            "counts": counts,
            "rows": [
                {**asdict(row), "changed_fields": list(row.changed_fields)}
                for row in report.rows
            ],
        }, indent=2, ensure_ascii=False)
    if output_format == "csv":
        from io import StringIO

        stream = StringIO(newline="")
        writer = csv.DictWriter(
            stream, fieldnames=("status", "key", "path_a", "path_b", "changed_fields"),
        )
        writer.writeheader()
        for row in report.rows:
            writer.writerow({
                **asdict(row), "changed_fields": ";".join(row.changed_fields),
            })
        return stream.getvalue()
    if output_format != "text":
        raise ValueError("output_format must be 'text', 'json', or 'csv'.")

    lines = [f"Compared galleries by {report.match_by}:"]
    lines.extend(f"  {label}: {counts[key]}" for key, label in (
        ("only_a", "Only in A"), ("only_b", "Only in B"),
        ("changed", "Changed"), ("unchanged", "Unchanged"),
        ("ambiguous", "Ambiguous"), ("unmatched_a", "Unmatched in A"),
        ("unmatched_b", "Unmatched in B"),
    ))
    if details:
        for row in report.rows:
            paths = " | ".join(path for path in (row.path_a, row.path_b) if path)
            changed = f" [{', '.join(row.changed_fields)}]" if row.changed_fields else ""
            lines.append(f"{row.status}: {row.key or '(no key)'}: {paths}{changed}")
    return "\n".join(lines)


def write_compare_report(report: CompareReport, destination,
                         *, output_format: str = "text", details: bool = False) -> Path:
    """Render report to a UTF-8 file and return its resolved path."""
    path = Path(destination)
    path.write_text(
        format_compare_report(report, output_format=output_format, details=details),
        encoding="utf-8",
        newline="",
    )
    return path
