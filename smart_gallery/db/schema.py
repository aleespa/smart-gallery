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

CREATE_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_media_relpath ON media_items(relpath);",
    "CREATE INDEX IF NOT EXISTS ix_media_type ON media_items(media_type);",
    "CREATE INDEX IF NOT EXISTS ix_media_date ON media_items(date_taken);",
    "CREATE INDEX IF NOT EXISTS ix_media_type_date ON media_items(media_type, date_taken);",
    "CREATE INDEX IF NOT EXISTS ix_media_camera ON media_items(camera);",
    "CREATE INDEX IF NOT EXISTS ix_media_lens ON media_items(lens);",
    "CREATE INDEX IF NOT EXISTS ix_media_hash ON media_items(content_hash);",
]


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables/indexes if missing and seed the schema version."""
    conn.execute(CREATE_META)
    conn.execute(CREATE_MEDIA_ITEMS)
    for stmt in CREATE_INDEXES:
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO NOTHING;",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
