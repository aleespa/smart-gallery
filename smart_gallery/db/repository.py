"""``GalleryRepository`` — the SQLite data-access layer.

Owns one connection to a drive's catalog. All metadata flows through
``MediaItem``; pandas is only touched by ``query_df`` (for the dashboard).
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from loguru import logger

from smart_gallery.config import drive_root_of, resolve_db_path
from smart_gallery.db.schema import init_schema
from smart_gallery.db.where import build_where
from smart_gallery.models import DB_COLUMNS, MediaItem
from smart_gallery.organize.filters import FilterOptions

_DELETE_CHUNK = 500

_ORDER_COLUMNS = {
    "relpath",
    "date_taken",
    "taken_ts",
    "size_bytes",
    "camera",
    "lens",
    "media_type",
}

_COLUMN_LIST = ", ".join(DB_COLUMNS)
_PLACEHOLDERS = ", ".join("?" for _ in DB_COLUMNS)
_UPDATE_ASSIGNMENTS = ", ".join(
    f"{col}=excluded.{col}" for col in DB_COLUMNS if col != "relpath"
)
_UPSERT_SQL = (
    f"INSERT INTO media_items ({_COLUMN_LIST}) VALUES ({_PLACEHOLDERS}) "
    f"ON CONFLICT(relpath) DO UPDATE SET {_UPDATE_ASSIGNMENTS}, "
    f"updated_at=datetime('now')"
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class GalleryRepository:
    def __init__(self, conn: sqlite3.Connection, drive_root: Path, db_path: Path):
        self.conn = conn
        self.drive_root = Path(drive_root)
        self.db_path = Path(db_path)

    # ── lifecycle ───────────────────────────────────────────────────────────
    @staticmethod
    def _connect(db_path: Path, read_only: bool) -> sqlite3.Connection:
        if read_only:
            conn = sqlite3.connect(
                f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0
            )
        else:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000;")
        if not read_only:
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                conn.execute("PRAGMA journal_mode=DELETE;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    @classmethod
    def open(
        cls, target, *, read_only: bool = False, create: bool = False
    ) -> "GalleryRepository":
        db_path = resolve_db_path(target)
        if create:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        elif not db_path.exists():
            raise FileNotFoundError(
                f"No catalog at {db_path}. Run `smart-gallery init` on this drive first."
            )
        repo = cls(cls._connect(db_path, read_only), drive_root_of(db_path), db_path)
        if create:
            repo.init_schema()
        return repo

    @classmethod
    def create(
        cls, target, *, label: Optional[str] = None, hashing: bool = False
    ) -> "GalleryRepository":
        repo = cls.open(target, create=True)
        repo.set_meta("db_uuid", uuid.uuid4().hex)
        repo.set_meta("drive_root", str(repo.drive_root))
        repo.set_meta("created_at", _now())
        repo.set_meta("hashing_enabled", "1" if hashing else "0")
        if label:
            repo.set_meta("drive_label", label)
        logger.info(f"Created catalog at {repo.db_path}")
        return repo

    def init_schema(self) -> None:
        init_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "GalleryRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── meta ────────────────────────────────────────────────────────────────
    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    @property
    def schema_version(self) -> int:
        return int(self.get_meta("schema_version") or 0)

    # ── writes ──────────────────────────────────────────────────────────────
    def _upsert(self, cur: sqlite3.Cursor, items: Iterable[MediaItem]) -> int:
        rows = [item.as_params() for item in items if item.relpath]
        if rows:
            cur.executemany(_UPSERT_SQL, rows)
        return len(rows)

    def _delete(self, cur: sqlite3.Cursor, relpaths: Iterable[str]) -> int:
        relpaths = list(relpaths)
        for i in range(0, len(relpaths), _DELETE_CHUNK):
            chunk = relpaths[i : i + _DELETE_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            cur.execute(
                f"DELETE FROM media_items WHERE relpath IN ({placeholders})", chunk
            )
        return len(relpaths)

    def upsert_many(self, items: Iterable[MediaItem]) -> int:
        cur = self.conn.cursor()
        count = self._upsert(cur, items)
        self.conn.commit()
        return count

    # Imports place known-new files; upsert handles the re-import / skip case too.
    insert_many = upsert_many

    def delete_by_relpaths(self, relpaths: Iterable[str]) -> int:
        cur = self.conn.cursor()
        count = self._delete(cur, relpaths)
        self.conn.commit()
        return count

    def apply_sync(self, to_delete: Iterable[str], items: Iterable[MediaItem]) -> None:
        """Prune missing rows and upsert new/changed items in one transaction."""
        cur = self.conn.cursor()
        self._delete(cur, to_delete)
        self._upsert(cur, items)
        cur.execute(
            "INSERT INTO meta(key, value) VALUES('last_sync_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_now(),),
        )
        self.conn.commit()

    # ── sync support ────────────────────────────────────────────────────────
    def index_for_sync(self) -> Dict[str, Tuple[Optional[int], Optional[int]]]:
        """relpath -> (size_bytes, mtime_ns) for every row. One small query."""
        cur = self.conn.execute("SELECT relpath, size_bytes, mtime_ns FROM media_items")
        return {r["relpath"]: (r["size_bytes"], r["mtime_ns"]) for r in cur}

    def all_relpaths(self) -> set:
        return {r["relpath"] for r in self.conn.execute("SELECT relpath FROM media_items")}

    # ── reads / queries ─────────────────────────────────────────────────────
    def query(
        self,
        filters: Optional[FilterOptions] = None,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
        order_by: str = "date_taken",
        descending: bool = False,
    ) -> List[MediaItem]:
        where, params = build_where(filters) if filters else ("", [])
        order_col = order_by if order_by in _ORDER_COLUMNS else "date_taken"
        sql = f"SELECT {_COLUMN_LIST} FROM media_items{where} ORDER BY {order_col}"
        sql += " DESC" if descending else " ASC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = [*params, limit, offset]
        cur = self.conn.execute(sql, params)
        return [MediaItem.from_row(row, self.drive_root) for row in cur]

    def query_relpaths(self, filters: Optional[FilterOptions] = None) -> List[str]:
        where, params = build_where(filters) if filters else ("", [])
        cur = self.conn.execute(
            f"SELECT relpath FROM media_items{where}", params
        )
        return [r["relpath"] for r in cur]

    def query_df(self, filters: Optional[FilterOptions] = None):
        """Return a pandas DataFrame for the dashboard, with convenience columns
        ``size (MB)`` and ``shutter_speed`` matching the old report schema."""
        import pandas as pd

        where, params = build_where(filters) if filters else ("", [])
        df = pd.read_sql_query(
            f"SELECT * FROM media_items{where}", self.conn, params=params
        )
        if not df.empty:
            if "size_bytes" in df.columns:
                df["size (MB)"] = (df["size_bytes"] / 1048576).round(2)
            if "shutter_speed_text" in df.columns:
                df["shutter_speed"] = df["shutter_speed_text"]
        return df

    # ── aggregates ──────────────────────────────────────────────────────────
    def counts(self) -> Dict[str, int]:
        cur = self.conn.execute(
            "SELECT media_type, COUNT(*) AS n FROM media_items GROUP BY media_type"
        )
        out = {"image": 0, "video": 0, "other": 0}
        for row in cur:
            out[row["media_type"]] = row["n"]
        out["total"] = sum(out.values())
        return out

    def total_size_bytes(self, filters: Optional[FilterOptions] = None) -> int:
        where, params = build_where(filters) if filters else ("", [])
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(size_bytes), 0) AS s FROM media_items{where}", params
        ).fetchone()
        return int(row["s"] or 0)

    def distinct_values(self, column: str) -> List[str]:
        if column not in DB_COLUMNS:
            raise ValueError(f"Unknown column: {column}")
        cur = self.conn.execute(
            f"SELECT DISTINCT {column} AS v FROM media_items "
            f"WHERE {column} IS NOT NULL ORDER BY {column}"
        )
        return [r["v"] for r in cur]

    def date_bounds(self) -> Tuple[Optional[date], Optional[date]]:
        row = self.conn.execute(
            "SELECT MIN(date_taken) AS lo, MAX(date_taken) AS hi FROM media_items "
            "WHERE date_taken IS NOT NULL"
        ).fetchone()
        lo = date.fromisoformat(row["lo"]) if row and row["lo"] else None
        hi = date.fromisoformat(row["hi"]) if row and row["hi"] else None
        return lo, hi
