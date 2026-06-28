import sqlite3

from smart_gallery.db.schema import CREATE_MEDIA_ITEMS
from smart_gallery.models import DB_COLUMNS, MediaItem
from smart_gallery.util import format_shutter, parse_shutter


def test_db_columns_match_schema():
    """MediaItem.DB_COLUMNS must match the table definition (minus managed cols)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(CREATE_MEDIA_ITEMS)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(media_items)")]
    managed = {"id", "created_at", "updated_at"}
    schema_cols = [c for c in cols if c not in managed]
    assert schema_cols == list(DB_COLUMNS)


def test_as_params_order():
    item = MediaItem(relpath="a/b.jpg", media_type="image", camera="X")
    params = item.as_params()
    assert len(params) == len(DB_COLUMNS)
    assert params[DB_COLUMNS.index("relpath")] == "a/b.jpg"
    assert params[DB_COLUMNS.index("camera")] == "X"


def test_from_exiftool_image_mapping():
    record = {
        "SourceFile": "/d/IMG_1.JPG",
        "FileSize": 2_097_152,
        "DateTimeOriginal": "2026:06:28 14:30:00",
        "Model": "Canon EOS R6",
        "LensModel": "RF24-70mm",
        "FNumber": 2.8,
        "ISO": 400,
        "ExposureTime": 0.004,
        "ImageWidth": 6000,
        "ImageHeight": 4000,
    }
    item = MediaItem.from_exiftool(record)
    assert item.media_type == "image"
    assert item.camera == "Canon EOS R6"
    assert item.aperture == 2.8
    assert item.date_taken == "2026-06-28"
    assert item.time_taken == "14:30:00"
    assert item.shutter_speed_sec == 0.004
    assert item.shutter_speed_text == "1/250s"
    assert item.width == 6000


def test_from_exiftool_video_duration_ms():
    record = {
        "SourceFile": "/d/clip.mp4",
        "Duration": 12.5,
        "VideoCodecID": "avc1",
        "VideoFrameRate": 30.0,
    }
    item = MediaItem.from_exiftool(record)
    assert item.media_type == "video"
    assert item.duration_ms == 12500
    assert item.codec == "avc1"


def test_shutter_helpers_roundtrip():
    assert format_shutter(0.004) == "1/250s"
    assert format_shutter(2) == "2s"
    assert parse_shutter("1/250s") == 1 / 250
    assert parse_shutter("2s") == 2.0
