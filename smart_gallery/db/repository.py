"""``GalleryRepository`` — the SQLite data-access layer.

Owns one connection to a drive's catalog. All metadata flows through
``MediaItem``; pandas is only touched by ``query_df`` (for the dashboard).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path, PurePath
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from loguru import logger

from smart_gallery.config import (
    FACE_EMBEDDING_DIM,
    drive_root_of,
    resolve_db_path,
    to_abspath,
)
from smart_gallery.db.face_labels import FaceLabelsMixin
from smart_gallery.db.face_undo import FaceUndoMixin
from smart_gallery.db.mirror_store import MirrorStoreMixin
from smart_gallery.db.schema import init_schema, migrate
from smart_gallery.db.where import build_where
from smart_gallery.models import (
    DB_COLUMNS,
    FACE_COLUMNS,
    PERSON_COLUMNS,
    USER_STATES,
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


def _read_only_uri(db_path: PurePath) -> str:
    """SQLite URI opening ``db_path`` read-only. The path is percent-encoded so
    a ``#``, ``%`` or ``?`` in a folder name stays part of it; a UNC path
    (``//host/share``) gets an empty authority in front (``file:////host/...``)."""
    path = quote(db_path.as_posix(), safe="/:")
    if path.startswith("//"):
        path = "//" + path
    return f"file:{path}?mode=ro"


class GalleryRepository(FaceLabelsMixin, FaceUndoMixin, MirrorStoreMixin):
    def __init__(self, conn: sqlite3.Connection, drive_root: Path, db_path: Path):
        self.conn = conn
        self.drive_root = Path(drive_root)
        self.db_path = Path(db_path)
        # (carried, dropped) decisions of the last ``add_scan_results`` chunk.
        self.last_carryover: Tuple[int, int] = (0, 0)

    # ── lifecycle ───────────────────────────────────────────────────────────
    @staticmethod
    def _connect(db_path: Path, read_only: bool) -> sqlite3.Connection:
        if read_only:
            conn = sqlite3.connect(_read_only_uri(db_path), uri=True, timeout=5.0)
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

    def has_relpath(self, relpath: str) -> bool:
        """True when the catalog has a row at ``relpath`` (case-insensitive)."""
        row = self.conn.execute(
            "SELECT 1 FROM media_items WHERE relpath=?", (relpath,)
        ).fetchone()
        return row is not None

    def all_relpaths(self) -> set:
        return {
            r["relpath"] for r in self.conn.execute("SELECT relpath FROM media_items")
        }

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

    def get_by_id(self, media_id: int) -> Optional[MediaItem]:
        row = self.conn.execute(
            f"SELECT {_COLUMN_LIST} FROM media_items WHERE id=?", (media_id,)
        ).fetchone()
        return MediaItem.from_row(row, self.drive_root) if row else None

    def query_relpaths(self, filters: Optional[FilterOptions] = None) -> List[str]:
        where, params = build_where(filters) if filters else ("", [])
        cur = self.conn.execute(f"SELECT relpath FROM media_items{where}", params)
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

    def value_counts(self, column: str) -> List[Tuple[str, int]]:
        """Each value of ``column`` with how many items have it, most-used
        first and ties by value."""
        if column not in DB_COLUMNS:
            raise ValueError(f"Unknown column: {column}")
        cur = self.conn.execute(
            f"SELECT {column} AS v, COUNT(*) AS n FROM media_items "
            f"WHERE {column} IS NOT NULL GROUP BY {column} ORDER BY n DESC, {column}"
        )
        return [(r["v"], r["n"]) for r in cur]

    def date_bounds(self) -> Tuple[Optional[date], Optional[date]]:
        # '0001-01-01' skips the zero dates older catalogs may still hold.
        row = self.conn.execute(
            "SELECT MIN(date_taken) AS lo, MAX(date_taken) AS hi FROM media_items "
            "WHERE date_taken >= '0001-01-01'"
        ).fetchone()

        def parse(value: Optional[str]) -> Optional[date]:
            try:
                return date.fromisoformat(value) if value else None
            except ValueError:
                return None

        return (parse(row["lo"]), parse(row["hi"])) if row else (None, None)

    # ── faces: scanning (schema v2) ──────────────────────────────────────────
    def _unscanned_where(self) -> str:
        """Images not scanned at a det_version (the ``?``), leaving out files
        freed up to a mirror: they are not on this drive to decode. A catalog
        opened read-only before schema v7 has no offloaded table."""
        where = (
            "media_type='image' AND id NOT IN "
            "(SELECT media_id FROM face_scan_state WHERE det_version=?)"
        )
        has_offloaded = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='mirror_offloaded'"
        ).fetchone()
        if has_offloaded:
            where += " AND relpath NOT IN (SELECT relpath FROM mirror_offloaded)"
        return where

    def count_unscanned_images(self, det_version: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM media_items WHERE " + self._unscanned_where(),
            (det_version,),
        ).fetchone()
        return int(row["n"])

    def unscanned_images(self, det_version: str) -> List[Tuple[int, str]]:
        """Return (media_id, abspath) for images not yet scanned at this
        ``det_version``. Materialized (not a live cursor) so the scan loop can
        write faces back on the same connection while iterating."""
        cur = self.conn.execute(
            "SELECT id, relpath FROM media_items WHERE "
            + self._unscanned_where()
            + " ORDER BY id",
            (det_version,),
        )
        return [(r["id"], str(to_abspath(r["relpath"], self.drive_root))) for r in cur]

    def add_scan_results(self, results, det_version: str) -> int:
        """Persist a chunk of scan results in one transaction.

        ``results`` is an iterable of ``(media_id, list[Face], img_size)`` with
        ``img_size`` ``(width, height)`` or None. Per media: the decisions on its
        existing faces are stashed and those faces deleted (a re-scan replaces
        them), the new faces are inserted (each ``Face.id`` is set), the image
        size is recorded and the stashed decisions are carried over. Faceless
        images get an ``n_faces=0`` state row so they are not retried. An image
        that could not be decoded (``img_size`` None, no faces) only gets that
        state row: its faces and stashed decisions wait for a later scan.

        Returns faces inserted; ``last_carryover`` holds ``(carried, dropped)``
        for the chunk.
        """
        cur = self.conn.cursor()
        inserted = carried = dropped = 0
        touched: set = set()
        try:
            for media_id, faces, img_size in results:
                if img_size is None and not faces:
                    # Decoding failed: keep the existing faces and any stashed
                    # decisions for a later successful scan; only mark it
                    # scanned so it is not retried on every run.
                    cur.execute(
                        "INSERT INTO face_scan_state(media_id, n_faces, det_version) "
                        "VALUES(?, 0, ?) ON CONFLICT(media_id) DO UPDATE SET "
                        "n_faces=0, det_version=excluded.det_version, "
                        "scanned_at=datetime('now')",
                        (media_id, det_version),
                    )
                    continue
                img_w, img_h = img_size if img_size else (None, None)
                touched.update(self._face_persons(media_id))
                self.stash_decisions([media_id])
                cur.execute("DELETE FROM faces WHERE media_id=?", (media_id,))
                for face in faces:
                    cur.execute(_FACE_INSERT_SQL, face.as_params())
                    face.id = int(cur.lastrowid)
                inserted += len(faces)
                cur.execute(
                    "INSERT INTO face_scan_state(media_id, n_faces, det_version, "
                    "img_w, img_h) VALUES(?, ?, ?, ?, ?) ON CONFLICT(media_id) DO "
                    "UPDATE SET n_faces=excluded.n_faces, "
                    "det_version=excluded.det_version, img_w=excluded.img_w, "
                    "img_h=excluded.img_h, scanned_at=datetime('now')",
                    (media_id, len(faces), det_version, img_w, img_h),
                )
                c, d = self.apply_carryover(media_id)
                carried += c
                dropped += d
                touched.update(self._face_persons(media_id))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self.last_carryover = (carried, dropped)
        for pid in sorted(touched):
            self.recompute_person(pid)
        return inserted

    def _face_persons(self, media_id: int) -> set:
        return {
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT person_id FROM faces "
                "WHERE media_id=? AND person_id IS NOT NULL",
                (media_id,),
            )
        }

    def clear_faces_for(self, media_ids: Iterable[int]) -> int:
        """Delete faces + scan state for these media (so they re-scan)."""
        ids = list(media_ids)
        cur = self.conn.cursor()
        for i in range(0, len(ids), _DELETE_CHUNK):
            chunk = ids[i : i + _DELETE_CHUNK]
            ph = ", ".join("?" for _ in chunk)
            cur.execute(f"DELETE FROM faces WHERE media_id IN ({ph})", chunk)
            cur.execute(f"DELETE FROM face_scan_state WHERE media_id IN ({ph})", chunk)
        self.conn.commit()
        return len(ids)

    def reset_all_faces(self) -> None:
        """Start a full ``--rescan``: stash the user's decisions and the faces
        of named people (see ``stash_decisions``), delete every face and scan
        state, and the unnamed groups that are not hidden. Named and hidden
        persons stay, so the stash carries over to the new faces."""
        run = self.conn.execute
        try:
            media = [r[0] for r in run("SELECT DISTINCT media_id FROM faces")]
            self.stash_decisions(media)
            run("DELETE FROM faces")
            run("DELETE FROM face_scan_state")
            run("DELETE FROM persons WHERE (name IS NULL OR name='') AND hidden=0")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        for r in run("SELECT id FROM persons").fetchall():
            self.recompute_person(r["id"])

    def invalidate_faces_by_relpath(self, relpaths: Iterable[str]) -> int:
        """Clear face data for changed files (sync calls this for updated rows so
        the next scan re-processes them), keeping the user's decisions for the
        re-scan to carry over. Returns media rows invalidated."""
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
            self.stash_decisions(ids)
            self.clear_faces_for(ids)
        return len(ids)

    # ── faces: clustering / persons (schema v2) ──────────────────────────────
    def faces_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM faces").fetchone()["n"])

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
        """Return person ids and centroids, preferring supplied sample references."""
        import numpy as np

        rows = self.conn.execute(
            "SELECT id, COALESCE(reference_centroid, centroid) AS centroid "
            "FROM persons WHERE COALESCE(reference_centroid, centroid) IS NOT NULL "
            "ORDER BY id"
        ).fetchall()
        ids = np.array([r["id"] for r in rows], dtype=np.int64)
        if not rows:
            return ids, np.empty((0, FACE_EMBEDDING_DIM), dtype=np.float32)
        mat = np.stack([np.frombuffer(r["centroid"], dtype=np.float32) for r in rows])
        return ids, mat

    def identify_faces_from_samples(
        self, references, *, threshold: float, margin: float, reassign: bool = False
    ) -> dict:
        """Persist sample references and assign confident faces atomically.

        ``references`` maps person names to normalized vectors. Faces the user
        decided (``USER_STATES``) are never touched, and a face is never given
        to a person it was rejected from. With ``reassign``, only ``auto``
        faces of these named people are reconsidered; other people's faces
        remain untouched.
        """
        import numpy as np

        conn = self.conn
        assigned = unmatched = ambiguous = already_assigned = 0
        conn.execute("BEGIN")
        try:
            target_ids = {}
            for name, embedding in references.items():
                matches = conn.execute(
                    "SELECT id FROM persons WHERE name=? COLLATE NOCASE ORDER BY id",
                    (name,),
                ).fetchall()
                if len(matches) > 1:
                    raise ValueError(
                        f"Multiple existing people match the name {name!r}; "
                        "resolve duplicate names before identifying faces."
                    )
                if matches:
                    row = matches[0]
                    person_id = int(row["id"])
                else:
                    cur = conn.execute(
                        "INSERT INTO persons(name, reference_centroid) VALUES(?, ?)",
                        (name, np.asarray(embedding, dtype=np.float32).tobytes()),
                    )
                    person_id = int(cur.lastrowid)
                target_ids[name] = person_id
                conn.execute(
                    "UPDATE persons SET reference_centroid=?, updated_at=datetime('now') "
                    "WHERE id=?",
                    (np.asarray(embedding, dtype=np.float32).tobytes(), person_id),
                )

            target_ids_list = list(target_ids.values())
            if reassign and target_ids_list:
                placeholders = ",".join("?" for _ in target_ids_list)
                conn.execute(
                    f"UPDATE faces SET person_id=NULL, cluster_id=NULL, "
                    f"state='new', suggested_person_id=NULL, match_score=NULL "
                    f"WHERE state='auto' AND person_id IN ({placeholders})",
                    target_ids_list,
                )

            rejected: Dict[int, set] = {}
            for r in conn.execute("SELECT face_id, person_id FROM face_rejections"):
                rejected.setdefault(r["face_id"], set()).add(r["person_id"])

            rows = conn.execute(
                "SELECT id, embedding, person_id, state FROM faces ORDER BY id"
            ).fetchall()
            eligible = []
            for row in rows:
                if row["person_id"] is None and row["state"] not in USER_STATES:
                    eligible.append(row)
                else:
                    already_assigned += 1

            names = list(references)
            centroids = np.stack([references[name] for name in names]).astype(
                np.float32
            )
            person_ids = [target_ids[name] for name in names]
            for row in eligible:
                embedding = np.frombuffer(row["embedding"], dtype=np.float32)
                similarities = centroids @ embedding
                banned = rejected.get(row["id"])
                if banned:
                    similarities = np.where(
                        [pid in banned for pid in person_ids], -np.inf, similarities
                    )
                order = np.argsort(similarities)
                best_index = int(order[-1])
                best_score = float(similarities[best_index])
                second_score = (
                    float(similarities[order[-2]]) if len(order) > 1 else -1.0
                )
                if best_score < threshold:
                    unmatched += 1
                elif len(order) > 1 and best_score - second_score < margin:
                    ambiguous += 1
                else:
                    conn.execute(
                        "UPDATE faces SET person_id=?, cluster_id=NULL, state='auto', "
                        "suggested_person_id=NULL, match_score=? WHERE id=?",
                        (person_ids[best_index], best_score, row["id"]),
                    )
                    assigned += 1

            for person_id in set(target_ids.values()):
                person_faces = conn.execute(
                    "SELECT id, embedding, det_score FROM faces WHERE person_id=?",
                    (person_id,),
                ).fetchall()
                if person_faces:
                    mean = np.stack(
                        [
                            np.frombuffer(face["embedding"], dtype=np.float32)
                            for face in person_faces
                        ]
                    ).mean(axis=0)
                    norm = float(np.linalg.norm(mean))
                    if norm:
                        mean /= norm
                    cover = max(person_faces, key=lambda face: face["det_score"] or 0.0)
                    conn.execute(
                        "UPDATE persons SET centroid=?, face_count=?, cover_face_id=?, "
                        "updated_at=datetime('now') WHERE id=?",
                        (
                            mean.astype(np.float32).tobytes(),
                            len(person_faces),
                            cover["id"],
                            person_id,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE persons SET centroid=NULL, face_count=0, cover_face_id=NULL, "
                        "updated_at=datetime('now') WHERE id=?",
                        (person_id,),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "assigned": assigned,
            "unmatched": unmatched,
            "ambiguous": ambiguous,
            "already_assigned": already_assigned,
            "persons": len(target_ids),
        }

    def clear_persons(self) -> None:
        """Start sorting over (for ``cluster-faces --rebuild``) without losing
        what the user decided.

        Named and hidden persons stay, and so does any group still holding a
        face the user filed. Every ``user``/``ignored`` face keeps its place;
        all other faces return to the pool and the other unnamed groups are
        deleted. Rejections are left alone (those of a deleted group go with
        it through the foreign key).
        """
        run = self.conn.execute
        try:
            run(
                "UPDATE faces SET state='new', person_id=NULL, cluster_id=NULL, "
                "suggested_person_id=NULL, match_score=NULL "
                "WHERE state NOT IN ('user', 'ignored')"
            )
            run(
                "DELETE FROM persons WHERE (name IS NULL OR name='') AND hidden=0 "
                "AND id NOT IN "
                "(SELECT person_id FROM faces WHERE person_id IS NOT NULL)"
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        for r in run("SELECT id FROM persons").fetchall():
            self.recompute_person(r["id"])

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
        cover (highest det_score). An empty person is deleted unless it is named or hidden.
        """
        import numpy as np

        rows = self.conn.execute(
            "SELECT id, embedding, det_score, state FROM faces WHERE person_id=?",
            (person_id,),
        ).fetchall()
        if not rows:
            # A named or hidden person is a decision the user made; keep it.
            self.conn.execute(
                "DELETE FROM persons WHERE id=? AND "
                "(name IS NULL OR name='') AND hidden=0",
                (person_id,),
            )
            self.conn.execute(
                "UPDATE persons SET centroid=NULL, face_count=0, "
                "cover_face_id=NULL, updated_at=datetime('now') WHERE id=?",
                (person_id,),
            )
            self.conn.commit()
            return
        embs = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        centroid = embs.mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm > 0:
            centroid = centroid / norm
        filed = [r for r in rows if r["state"] in ("user", "auto", "cluster")]
        cover = max(filed or rows, key=lambda r: (r["det_score"] or 0.0))
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
        """Persons with a cover-photo relpath, biggest first. ``hidden`` is 0
        on catalogs older than schema v5 (read-only opens never migrate)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(persons)")}
        hidden = "p.hidden" if "hidden" in cols else "0"
        cur = self.conn.execute(
            f"SELECT p.id, p.name, {hidden} AS hidden, p.face_count, "
            "p.cover_face_id, m.relpath AS cover_relpath "
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

    _GROUP_FACES_TO_AUTO = (
        "UPDATE faces SET state='auto' WHERE person_id=? AND state='cluster' "
        "AND EXISTS (SELECT 1 FROM persons p WHERE p.id=faces.person_id "
        "AND p.name IS NOT NULL AND p.name <> '')"
    )

    def set_person_name(self, person_id: int, name: Optional[str]) -> bool:
        """Name a person (or clear the name). A group that gains a name turns
        its ``cluster`` faces into that person's ``auto`` faces."""
        try:
            cur = self.conn.execute(
                "UPDATE persons SET name=?, updated_at=datetime('now') WHERE id=?",
                (name, person_id),
            )
            self.conn.execute(self._GROUP_FACES_TO_AUTO, (person_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return cur.rowcount > 0

    def delete_person(self, person_id: int) -> bool:
        """Drop a person and unassign its faces (they become re-clusterable)."""
        self.conn.execute(
            "UPDATE faces SET person_id=NULL, cluster_id=NULL, state='new', "
            "suggested_person_id=NULL WHERE person_id=?",
            (person_id,),
        )
        # Faces merely suggested to this person lose the suggestion too.
        self.conn.execute(
            "UPDATE faces SET state='new', suggested_person_id=NULL, "
            "match_score=NULL WHERE suggested_person_id=? AND state='suggested'",
            (person_id,),
        )
        cur = self.conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def load_face_embeddings_for_person(self, person_id: int):
        """Return ``(face_ids[int64 N], embeddings[float32 N×D])`` for one person."""
        import numpy as np

        rows = self.conn.execute(
            "SELECT id, embedding FROM faces WHERE person_id=? ORDER BY id",
            (person_id,),
        ).fetchall()
        n = len(rows)
        ids = np.empty(n, dtype=np.int64)
        embs = np.empty((n, FACE_EMBEDDING_DIM), dtype=np.float32)
        for i, r in enumerate(rows):
            ids[i] = r["id"]
            embs[i] = np.frombuffer(r["embedding"], dtype=np.float32)
        return ids, embs

    def merge_persons(self, dst_id: int, src_ids: Iterable[int]) -> None:
        """Re-point all faces, suggestions and rejections of ``src_ids`` onto
        ``dst_id``, delete the emptied source persons, and recompute the
        destination's stats.

        A face the user filed in a merged person wins over an old rejection of
        it by ``dst_id``. An unnamed destination takes the name of its only
        named source. A named destination's ``cluster`` faces become ``auto``.
        """
        srcs = [int(s) for s in src_ids if int(s) != dst_id]
        if not srcs:
            return
        ph = ", ".join("?" for _ in srcs)
        run = self.conn.execute
        try:
            names = [
                r["name"]
                for r in run(
                    f"SELECT name FROM persons WHERE id IN ({ph}) "
                    "AND name IS NOT NULL AND name <> ''",
                    srcs,
                )
            ]
            for col in ("person_id", "suggested_person_id"):
                run(
                    f"UPDATE faces SET {col}=? WHERE {col} IN ({ph})",
                    [dst_id, *srcs],
                )
            run(
                f"UPDATE OR IGNORE face_rejections SET person_id=? "
                f"WHERE person_id IN ({ph})",
                [dst_id, *srcs],
            )
            run(f"DELETE FROM face_rejections WHERE person_id IN ({ph})", srcs)
            run(
                "DELETE FROM face_rejections WHERE person_id=? AND face_id IN "
                "(SELECT id FROM faces WHERE person_id=? AND state='user')",
                (dst_id, dst_id),
            )
            run(f"DELETE FROM persons WHERE id IN ({ph})", srcs)
            if len(names) == 1:
                run(
                    "UPDATE persons SET name=?, updated_at=datetime('now') "
                    "WHERE id=? AND (name IS NULL OR name='')",
                    (names[0], dst_id),
                )
            run(self._GROUP_FACES_TO_AUTO, (dst_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self.recompute_person(dst_id)
