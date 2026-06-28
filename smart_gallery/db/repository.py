"""``GalleryRepository`` — the SQLite data-access layer.

Owns one connection to a drive's catalog. All metadata flows through
``MediaItem``; pandas is only touched by ``query_df`` (for the dashboard).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from loguru import logger

from smart_gallery.config import (
    FACE_EMBEDDING_DIM,
    drive_root_of,
    resolve_db_path,
    to_abspath,
)
from smart_gallery.db.schema import init_schema, migrate
from smart_gallery.db.where import build_where
from smart_gallery.models import (
    DB_COLUMNS,
    FACE_COLUMNS,
    PERSON_COLUMNS,
    MediaItem,
    Person,
)
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

_FACE_COLUMN_LIST = ", ".join(FACE_COLUMNS)
_FACE_PLACEHOLDERS = ", ".join("?" for _ in FACE_COLUMNS)
_FACE_INSERT_SQL = (
    f"INSERT INTO faces ({_FACE_COLUMN_LIST}) VALUES ({_FACE_PLACEHOLDERS})"
)
_PERSON_COLUMN_LIST = ", ".join(PERSON_COLUMNS)
_PERSON_PLACEHOLDERS = ", ".join("?" for _ in PERSON_COLUMNS)
_PERSON_INSERT_SQL = (
    f"INSERT INTO persons ({_PERSON_COLUMN_LIST}) VALUES ({_PERSON_PLACEHOLDERS})"
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
        # FK enforcement makes deletes cascade faces/face_scan_state for a media
        # row (and NULL out person_id when a person is removed).
        conn.execute("PRAGMA foreign_keys=ON;")
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
        elif not read_only:
            # Bring older (v1) catalogs up to date in place: adds the face tables.
            migrate(repo.conn)
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

    # ── faces: scanning (schema v2) ──────────────────────────────────────────
    def count_unscanned_images(self, det_version: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM media_items "
            "WHERE media_type='image' AND id NOT IN "
            "(SELECT media_id FROM face_scan_state WHERE det_version=?)",
            (det_version,),
        ).fetchone()
        return int(row["n"])

    def unscanned_images(self, det_version: str) -> List[Tuple[int, str]]:
        """Return (media_id, abspath) for images not yet scanned at this
        ``det_version``. Materialized (not a live cursor) so the scan loop can
        write faces back on the same connection while iterating."""
        cur = self.conn.execute(
            "SELECT id, relpath FROM media_items "
            "WHERE media_type='image' AND id NOT IN "
            "(SELECT media_id FROM face_scan_state WHERE det_version=?) "
            "ORDER BY id",
            (det_version,),
        )
        return [
            (r["id"], str(to_abspath(r["relpath"], self.drive_root))) for r in cur
        ]

    def add_scan_results(self, results, det_version: str) -> int:
        """Persist a chunk of scan results in one transaction. ``results`` is an
        iterable of ``(media_id, list[Face])``; faceless images get an
        ``n_faces=0`` state row so they are not retried. Returns faces inserted."""
        cur = self.conn.cursor()
        inserted = 0
        for media_id, faces in results:
            if faces:
                cur.executemany(_FACE_INSERT_SQL, [f.as_params() for f in faces])
                inserted += len(faces)
            cur.execute(
                "INSERT INTO face_scan_state(media_id, n_faces, det_version) "
                "VALUES(?, ?, ?) ON CONFLICT(media_id) DO UPDATE SET "
                "n_faces=excluded.n_faces, det_version=excluded.det_version, "
                "scanned_at=datetime('now')",
                (media_id, len(faces), det_version),
            )
        self.conn.commit()
        return inserted

    def clear_faces_for(self, media_ids: Iterable[int]) -> int:
        """Delete faces + scan state for these media (so they re-scan)."""
        ids = list(media_ids)
        cur = self.conn.cursor()
        for i in range(0, len(ids), _DELETE_CHUNK):
            chunk = ids[i : i + _DELETE_CHUNK]
            ph = ", ".join("?" for _ in chunk)
            cur.execute(f"DELETE FROM faces WHERE media_id IN ({ph})", chunk)
            cur.execute(
                f"DELETE FROM face_scan_state WHERE media_id IN ({ph})", chunk
            )
        self.conn.commit()
        return len(ids)

    def reset_all_faces(self) -> None:
        """Wipe all face data (faces, scan state, persons) for a full --rescan."""
        self.conn.execute("DELETE FROM faces")
        self.conn.execute("DELETE FROM face_scan_state")
        self.conn.execute("DELETE FROM persons")
        self.conn.commit()

    def invalidate_faces_by_relpath(self, relpaths: Iterable[str]) -> int:
        """Clear face data for changed files (sync calls this for updated rows so
        the next scan re-processes them). Returns media rows invalidated."""
        rels = list(relpaths)
        ids: List[int] = []
        for i in range(0, len(rels), _DELETE_CHUNK):
            chunk = rels[i : i + _DELETE_CHUNK]
            ph = ", ".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT id FROM media_items WHERE relpath IN ({ph})", chunk
            ).fetchall()
            ids.extend(r["id"] for r in rows)
        if ids:
            self.clear_faces_for(ids)
        return len(ids)

    # ── faces: clustering / persons (schema v2) ──────────────────────────────
    def faces_count(self) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) AS n FROM faces").fetchone()["n"]
        )

    def load_embeddings(self, *, only_unassigned: bool = False):
        """Return ``(face_ids[int64 N], embeddings[float32 N×D], person_ids[object N])``."""
        import numpy as np

        sql = "SELECT id, embedding, person_id FROM faces"
        if only_unassigned:
            sql += " WHERE person_id IS NULL"
        sql += " ORDER BY id"
        rows = self.conn.execute(sql).fetchall()
        n = len(rows)
        ids = np.empty(n, dtype=np.int64)
        embs = np.empty((n, FACE_EMBEDDING_DIM), dtype=np.float32)
        pids = np.empty(n, dtype=object)
        for i, r in enumerate(rows):
            ids[i] = r["id"]
            embs[i] = np.frombuffer(r["embedding"], dtype=np.float32)
            pids[i] = r["person_id"]
        return ids, embs, pids

    def load_person_centroids(self):
        """Return ``(person_ids[int64 P], centroids[float32 P×D])`` (named or not)."""
        import numpy as np

        rows = self.conn.execute(
            "SELECT id, centroid FROM persons WHERE centroid IS NOT NULL ORDER BY id"
        ).fetchall()
        ids = np.array([r["id"] for r in rows], dtype=np.int64)
        if not rows:
            return ids, np.empty((0, FACE_EMBEDDING_DIM), dtype=np.float32)
        mat = np.stack(
            [np.frombuffer(r["centroid"], dtype=np.float32) for r in rows]
        )
        return ids, mat

    def clear_persons(self) -> None:
        """Unassign every face and drop all persons (for a full --rebuild)."""
        self.conn.execute("UPDATE faces SET person_id=NULL, cluster_id=NULL")
        self.conn.execute("DELETE FROM persons")
        self.conn.commit()

    def create_person(self, person: Person) -> int:
        cur = self.conn.execute(_PERSON_INSERT_SQL, person.as_params())
        self.conn.commit()
        return int(cur.lastrowid)

    def assign_faces(
        self, person_id: Optional[int], cluster_id: Optional[int], face_ids
    ) -> None:
        ids = [int(i) for i in face_ids]
        cur = self.conn.cursor()
        for i in range(0, len(ids), _DELETE_CHUNK):
            chunk = ids[i : i + _DELETE_CHUNK]
            ph = ", ".join("?" for _ in chunk)
            cur.execute(
                f"UPDATE faces SET person_id=?, cluster_id=? WHERE id IN ({ph})",
                [person_id, cluster_id, *chunk],
            )
        self.conn.commit()

    def recompute_person(self, person_id: int) -> None:
        """Refresh a person's centroid (L2-normalized mean), face_count and
        cover (highest det_score). Deletes the person if it has no faces left."""
        import numpy as np

        rows = self.conn.execute(
            "SELECT id, embedding, det_score FROM faces WHERE person_id=?",
            (person_id,),
        ).fetchall()
        if not rows:
            self.conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
            self.conn.commit()
            return
        embs = np.stack(
            [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
        )
        centroid = embs.mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm > 0:
            centroid = centroid / norm
        cover = max(rows, key=lambda r: (r["det_score"] or 0.0))
        self.conn.execute(
            "UPDATE persons SET centroid=?, face_count=?, cover_face_id=?, "
            "updated_at=datetime('now') WHERE id=?",
            (
                centroid.astype("float32").tobytes(),
                len(rows),
                cover["id"],
                person_id,
            ),
        )
        self.conn.commit()

    def list_persons(self) -> List[dict]:
        """Persons with a cover-photo relpath, biggest first."""
        cur = self.conn.execute(
            "SELECT p.id, p.name, p.face_count, p.cover_face_id, "
            "m.relpath AS cover_relpath "
            "FROM persons p "
            "LEFT JOIN faces f ON f.id = p.cover_face_id "
            "LEFT JOIN media_items m ON m.id = f.media_id "
            "ORDER BY p.face_count DESC, p.id"
        )
        return [dict(r) for r in cur]

    def person_samples(self, limit: int = 3) -> Dict[int, List[str]]:
        """For every person, up to ``limit`` distinct sample-photo relpaths,
        highest detection score first. One query for all persons."""
        sql = """
        WITH best AS (
            SELECT f.person_id AS pid, m.id AS mid, m.relpath AS relpath,
                   MAX(f.det_score) AS score
            FROM faces f JOIN media_items m ON m.id = f.media_id
            WHERE f.person_id IS NOT NULL
            GROUP BY f.person_id, m.id
        ),
        ranked AS (
            SELECT pid, relpath,
                   ROW_NUMBER() OVER (
                       PARTITION BY pid ORDER BY score DESC, relpath
                   ) AS rn
            FROM best
        )
        SELECT pid, relpath FROM ranked WHERE rn <= ? ORDER BY pid, rn
        """
        out: Dict[int, List[str]] = {}
        for r in self.conn.execute(sql, (limit,)):
            out.setdefault(r["pid"], []).append(r["relpath"])
        return out

    def get_person(self, person_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT id, name, cluster_id, face_count, cover_face_id "
            "FROM persons WHERE id=?",
            (person_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_person_name(self, person_id: int, name: Optional[str]) -> bool:
        cur = self.conn.execute(
            "UPDATE persons SET name=?, updated_at=datetime('now') WHERE id=?",
            (name, person_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def merge_persons(self, dst_id: int, src_ids: Iterable[int]) -> None:
        """Re-point all faces of ``src_ids`` onto ``dst_id``, delete the emptied
        source persons, and recompute the destination's stats."""
        srcs = [int(s) for s in src_ids if int(s) != dst_id]
        if not srcs:
            return
        cur = self.conn.cursor()
        ph = ", ".join("?" for _ in srcs)
        cur.execute(
            f"UPDATE faces SET person_id=? WHERE person_id IN ({ph})",
            [dst_id, *srcs],
        )
        cur.execute(f"DELETE FROM persons WHERE id IN ({ph})", srcs)
        self.conn.commit()
        self.recompute_person(dst_id)
