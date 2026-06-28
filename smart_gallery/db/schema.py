"""SQLite schema for the per-drive catalog.

The ``media_items`` column list here is kept in lock-step with
``models.DB_COLUMNS`` (a test asserts they match), so the dataclass remains the
single source of truth.
"""

import sqlite3

from smart_gallery.config import SCHEMA_VERSION

CREATE_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

CREATE_MEDIA_ITEMS = """
CREATE TABLE IF NOT EXISTS media_items (
    id                 INTEGER PRIMARY KEY,
    relpath            TEXT NOT NULL COLLATE NOCASE,
    media_type         TEXT NOT NULL CHECK (media_type IN ('image','video','other')),
    name               TEXT,
    ext                TEXT,
    directory_rel      TEXT,
    size_bytes         INTEGER,
    mtime_ns           INTEGER,
    content_hash       TEXT,
    date_taken         TEXT,
    time_taken         TEXT,
    taken_ts           INTEGER,
    camera             TEXT,
    lens               TEXT,
    focal_length       REAL,
    aperture           REAL,
    iso                INTEGER,
    shutter_speed_text TEXT,
    shutter_speed_sec  REAL,
    latitude           REAL,
    longitude          REAL,
    altitude           REAL,
    width              INTEGER,
    height             INTEGER,
    duration_ms        REAL,
    codec              TEXT,
    frame_rate         REAL,
    created_at         TEXT DEFAULT (datetime('now')),
    updated_at         TEXT DEFAULT (datetime('now'))
);
"""

# ── face recognition (schema v2) ─────────────────────────────────────────────
# Faces/persons are separate entities keyed to ``media_items.id`` — never columns
# on media_items — so a sync upsert (which rewrites every media column) can never
# clobber face data. ``FaceColumns``/``PersonColumns`` in models.py stay in
# lock-step with the column lists below (a test asserts it).
CREATE_PERSONS = """
CREATE TABLE IF NOT EXISTS persons (
    id            INTEGER PRIMARY KEY,
    name          TEXT,
    cluster_id    INTEGER,
    centroid      BLOB,
    face_count    INTEGER NOT NULL DEFAULT 0,
    cover_face_id INTEGER,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
"""

CREATE_FACES = """
CREATE TABLE IF NOT EXISTS faces (
    id          INTEGER PRIMARY KEY,
    media_id    INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    bbox_x      REAL NOT NULL,
    bbox_y      REAL NOT NULL,
    bbox_w      REAL NOT NULL,
    bbox_h      REAL NOT NULL,
    det_score   REAL,
    embedding   BLOB NOT NULL,
    person_id   INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    cluster_id  INTEGER
);
"""

CREATE_FACE_SCAN_STATE = """
CREATE TABLE IF NOT EXISTS face_scan_state (
    media_id    INTEGER PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
    scanned_at  TEXT DEFAULT (datetime('now')),
    n_faces     INTEGER NOT NULL DEFAULT 0,
    det_version TEXT
);
"""

CREATE_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_media_relpath ON media_items(relpath);",
    "CREATE INDEX IF NOT EXISTS ix_media_type ON media_items(media_type);",
    "CREATE INDEX IF NOT EXISTS ix_media_date ON media_items(date_taken);",
    "CREATE INDEX IF NOT EXISTS ix_media_type_date ON media_items(media_type, date_taken);",
    "CREATE INDEX IF NOT EXISTS ix_media_camera ON media_items(camera);",
    "CREATE INDEX IF NOT EXISTS ix_media_lens ON media_items(lens);",
    "CREATE INDEX IF NOT EXISTS ix_media_hash ON media_items(content_hash);",
    "CREATE INDEX IF NOT EXISTS ix_faces_media ON faces(media_id);",
    "CREATE INDEX IF NOT EXISTS ix_faces_person ON faces(person_id);",
    "CREATE INDEX IF NOT EXISTS ix_faces_cluster ON faces(cluster_id);",
]


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables/indexes if missing and seed the schema version."""
    conn.execute(CREATE_META)
    conn.execute(CREATE_MEDIA_ITEMS)
    conn.execute(CREATE_PERSONS)
    conn.execute(CREATE_FACES)
    conn.execute(CREATE_FACE_SCAN_STATE)
    for stmt in CREATE_INDEXES:
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO NOTHING;",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    """Bring an older catalog up to ``SCHEMA_VERSION`` in place.

    Idempotent: every statement is ``IF NOT EXISTS``, so it is safe to run on
    every read-write open. v1 catalogs gain the face tables here without a
    re-``init``. Called by ``GalleryRepository.open`` on the read-write path.
    """
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    current = int(row["value"]) if row and row["value"] is not None else 0
    if current >= SCHEMA_VERSION:
        return

    if current < 2:
        conn.execute(CREATE_PERSONS)
        conn.execute(CREATE_FACES)
        conn.execute(CREATE_FACE_SCAN_STATE)
        for stmt in CREATE_INDEXES:
            conn.execute(stmt)

    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
