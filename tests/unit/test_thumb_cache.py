import io
import os
import threading

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from smart_gallery.gui.thumbs import cache  # noqa: E402
from smart_gallery.gui.thumbs.cache import (  # noqa: E402
    THUMB_EDGE,
    ensure_thumbnail,
    thumb_path,
)


def _ensure(drive, rel, media_id=1, mtime_ns=100, ext=".jpg", media_type="image"):
    return ensure_thumbnail(drive, media_id, mtime_ns, drive / rel, ext, media_type)


def _forbid_open(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the file was opened again")

    monkeypatch.setattr(Image, "open", boom)


def test_thumb_path_layout(tmp_path):
    p = thumb_path(tmp_path, 12345, 99)
    assert p == tmp_path / ".smart_gallery" / "thumbs" / "12" / "12345-99.jpg"
    assert thumb_path(tmp_path, 7, None).name == "7-0.jpg"


def test_thumbnail_jpeg(copy_image, drive):
    copy_image(drive, "test.JPG", "a/test.JPG")
    out = _ensure(drive, "a/test.JPG")
    assert out is not None and out.is_file()
    assert "thumbs" in out.parts
    with Image.open(out) as im:
        assert max(im.size) <= THUMB_EDGE


def test_thumbnail_cached_second_call(copy_image, drive, monkeypatch):
    copy_image(drive, "test.JPG", "a/test.JPG")
    first = _ensure(drive, "a/test.JPG")
    _forbid_open(monkeypatch)
    assert _ensure(drive, "a/test.JPG") == first


def test_changed_mtime_regenerates(copy_image, drive):
    copy_image(drive, "test.JPG", "a/test.JPG")
    one = _ensure(drive, "a/test.JPG", mtime_ns=1)
    two = _ensure(drive, "a/test.JPG", mtime_ns=2)
    assert one != two and one.is_file() and two.is_file()


def test_corrupt_file_records_failure_once(drive, monkeypatch):
    (drive / "bad.jpg").write_bytes(b"not an image at all")
    assert _ensure(drive, "bad.jpg") is None
    assert thumb_path(drive, 1, 100).with_suffix(".none").is_file()
    _forbid_open(monkeypatch)
    assert _ensure(drive, "bad.jpg") is None


def test_missing_file_returns_none(drive):
    assert _ensure(drive, "nope.jpg") is None


def test_video_returns_none(drive):
    (drive / "v.mp4").write_bytes(b"\0" * 32)
    assert _ensure(drive, "v.mp4", ext=".mp4", media_type="video") is None
    assert thumb_path(drive, 1, 100).with_suffix(".none").is_file()


def test_unwritable_cache_returns_none_without_marker(copy_image, drive):
    copy_image(drive, "test.JPG", "a/test.JPG")
    # A plain file where the cache directory should be makes mkdir fail.
    (drive / ".smart_gallery").write_bytes(b"in the way")
    assert _ensure(drive, "a/test.JPG") is None
    assert (drive / ".smart_gallery").is_file()


def test_concurrent_calls_share_one_result(copy_image, drive):
    copy_image(drive, "test.JPG", "a/test.JPG")
    results = []

    def work():
        results.append(_ensure(drive, "a/test.JPG"))

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results[0] is not None and set(results) == {results[0]}
    names = os.listdir(results[0].parent)
    assert names == [results[0].name]  # no temp files left behind


def test_raw_falls_through_exiftool_tags(drive, monkeypatch):
    """A tag with no output is skipped; the next tag's image is used."""
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), "red").save(buf, "JPEG")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args[2])
        assert kwargs["timeout"] > 0
        out = buf.getvalue() if args[2] == "-JpgFromRaw" else b""

        class Result:
            returncode = 0
            stdout = out

        return Result()

    monkeypatch.setattr(cache.subprocess, "run", fake_run)
    (drive / "x.cr3").write_bytes(b"raw")
    out = _ensure(drive, "x.cr3", ext=".cr3")
    assert out is not None
    assert calls == ["-PreviewImage", "-JpgFromRaw"]
    with Image.open(out) as im:
        assert max(im.size) == THUMB_EDGE


@pytest.mark.exiftool
def test_raw_preview(copy_image, drive):
    copy_image(drive, "test.CR3", "raw/test.CR3")
    out = _ensure(drive, "raw/test.CR3", ext=".cr3")
    assert out is not None and out.is_file()
    with Image.open(out) as im:
        assert 0 < max(im.size) <= THUMB_EDGE
