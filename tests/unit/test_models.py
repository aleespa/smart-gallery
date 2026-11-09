import sqlite3

import pytest

from smart_gallery.db.schema import CREATE_MEDIA_ITEMS
from smart_gallery.models import DB_COLUMNS, MediaItem, display_size
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


def test_from_exiftool_unset_camera_clock_has_no_date():
    """Cameras with an unset clock write '0000:00:00 00:00:00'; that is no date."""
    record = {
        "SourceFile": "C:/x/a.jpg",
        "FileTypeExtension": "jpg",
        "MIMEType": "image/jpeg",
        "DateTimeOriginal": "0000:00:00 00:00:00",
    }
    item = MediaItem.from_exiftool(record)
    assert (item.date_taken, item.time_taken, item.taken_ts) == (None, None, None)


@pytest.mark.parametrize(
    "o,expected",
    [
        (1, (6000, 4000)),
        (3, (6000, 4000)),
        (5, (4000, 6000)),
        (6, (4000, 6000)),
        (7, (4000, 6000)),
        (8, (4000, 6000)),
        (None, (6000, 4000)),
        (2, (6000, 4000)),
        (4, (6000, 4000)),
    ],
)
def test_display_size(o, expected):
    assert display_size(6000, 4000, o) == expected


def test_display_size_missing_dims():
    assert display_size(None, 4000, 6) == (4000, None)


def test_from_exiftool_photo_orientation():
    rec = {
        "SourceFile": "E:/a.jpg",
        "FileName": "a.jpg",
        "Orientation": 6,
        "ImageWidth": 6000,
        "ImageHeight": 4000,
    }
    item = MediaItem.from_exiftool(rec)
    assert (item.width, item.height, item.orientation) == (6000, 4000, 6)


def test_from_exiftool_photo_without_tag_is_1():
    rec = {"SourceFile": "E:/a.jpg", "FileName": "a.jpg"}
    assert MediaItem.from_exiftool(rec).orientation == 1


@pytest.mark.parametrize(
    "deg,o", [(0, 1), (90, 6), (180, 3), (270, 8), (-90, 8), (45, 1)]
)
def test_from_exiftool_video_rotation(deg, o):
    rec = {"SourceFile": "E:/v.mp4", "FileName": "v.mp4", "Rotation": deg}
    assert MediaItem.from_exiftool(rec).orientation == o


def test_from_path_leaves_orientation_unread(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("x")
    assert MediaItem.from_path(f).orientation is None
