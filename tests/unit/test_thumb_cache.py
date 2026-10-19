import io
import os
import shutil
import subprocess
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
    # A missing source is a genuine failure: it is recorded.
    assert thumb_path(drive, 1, 100).with_suffix(".none").is_file()


def _video(drive, rel="v.mp4"):
    return _ensure(drive, rel, ext=".mp4", media_type="video")


def _marker(drive):
    return thumb_path(drive, 1, 100).with_suffix(".none")


def _jpeg(size=(640, 360), colour="blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "JPEG")
    return buf.getvalue()


class _Result:
    def __init__(self, stdout=b"", returncode=0):
        self.stdout, self.returncode = stdout, returncode


def _fake_tools(monkeypatch, ffmpeg=None, exiftool=None):
    """Route subprocess.run to fakes: ``ffmpeg(args)``/``exiftool(args)``
    return stdout bytes or raise. ffmpeg=None means it is not installed."""
    calls = []
    monkeypatch.setattr(cache, "_ffmpeg_path", lambda: "ffmpeg" if ffmpeg else None)

    def run(args, **kwargs):
        assert kwargs["timeout"] > 0
        tool = "ffmpeg" if args[0] == "ffmpeg" else "exiftool"
        calls.append(tool)
        fake = ffmpeg if tool == "ffmpeg" else exiftool
        if fake is None:
            raise FileNotFoundError(tool)
        return _Result(fake(args))

    monkeypatch.setattr(cache.subprocess, "run", run)
    return calls


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH"
)


def _make_clip(path, seconds):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=640x360:rate=25:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=60,
    )


@needs_ffmpeg
def test_video_thumbnail_is_a_frame(drive):
    _make_clip(drive / "v.mp4", 4)
    out = _video(drive)
    assert out is not None
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert max(im.size) == THUMB_EDGE
        assert im.size[0] > im.size[1]  # keeps the clip's 16:9 shape


@needs_ffmpeg
def test_clip_shorter_than_the_seek_still_gets_a_frame(drive):
    _make_clip(drive / "v.mp4", 0.4)
    assert _video(drive) is not None


def test_video_without_ffmpeg_uses_its_embedded_thumbnail(drive, monkeypatch):
    calls = _fake_tools(
        monkeypatch, exiftool=lambda args: _jpeg() if "-ThumbnailImage" in args else b""
    )
    (drive / "v.mp4").write_bytes(b"video")
    out = _video(drive)
    assert out is not None and calls[0] == "exiftool"
    with Image.open(out) as im:
        assert max(im.size) == THUMB_EDGE


def test_undecodable_video_falls_back_to_embedded(drive, monkeypatch):
    calls = _fake_tools(
        monkeypatch,
        ffmpeg=lambda args: b"",
        exiftool=lambda args: _jpeg() if "-PreviewImage" in args else b"",
    )
    (drive / "v.mp4").write_bytes(b"video")
    assert _video(drive) is not None
    assert calls[0] == "ffmpeg" and "exiftool" in calls


def test_video_without_ffmpeg_or_embedded_waits_for_ffmpeg(drive, monkeypatch):
    """No marker: the thumbnail appears once ffmpeg is installed."""
    _fake_tools(monkeypatch, exiftool=lambda args: b"")
    (drive / "v.mp4").write_bytes(b"video")
    assert _video(drive) is None
    assert not _marker(drive).exists()


def test_video_nothing_can_read_records_failure(drive, monkeypatch):
    _fake_tools(monkeypatch, ffmpeg=lambda args: b"", exiftool=lambda args: b"")
    (drive / "v.mp4").write_bytes(b"video")
    assert _video(drive) is None
    assert _marker(drive).is_file()


def test_ffmpeg_timeout_leaves_no_marker(drive, monkeypatch):
    def slow(args):
        raise subprocess.TimeoutExpired(args, 1)

    _fake_tools(monkeypatch, ffmpeg=slow, exiftool=lambda args: b"")
    (drive / "v.mp4").write_bytes(b"video")
    assert _video(drive) is None
    assert not _marker(drive).exists()


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


def test_raw_exiftool_not_found_leaves_no_marker(drive, monkeypatch):
    def not_found(args, **kwargs):
        raise FileNotFoundError("exiftool")

    monkeypatch.setattr(cache.subprocess, "run", not_found)
    (drive / "x.cr3").write_bytes(b"raw")
    assert _ensure(drive, "x.cr3", ext=".cr3") is None
    assert not thumb_path(drive, 1, 100).with_suffix(".none").exists()


def test_raw_exiftool_timeout_leaves_no_marker(drive, monkeypatch):
    def timed_out(args, **kwargs):
        raise cache.subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(cache.subprocess, "run", timed_out)
    (drive / "x.cr3").write_bytes(b"raw")
    assert _ensure(drive, "x.cr3", ext=".cr3") is None
    assert not thumb_path(drive, 1, 100).with_suffix(".none").exists()


def test_raw_with_no_preview_in_any_tag_records_failure(drive, monkeypatch):
    class Empty:
        returncode = 0
        stdout = b""

    monkeypatch.setattr(cache.subprocess, "run", lambda args, **kw: Empty())
    (drive / "x.cr3").write_bytes(b"raw")
    assert _ensure(drive, "x.cr3", ext=".cr3") is None
    assert thumb_path(drive, 1, 100).with_suffix(".none").is_file()


@pytest.mark.exiftool
def test_raw_preview(copy_image, drive):
    copy_image(drive, "test.CR3", "raw/test.CR3")
    out = _ensure(drive, "raw/test.CR3", ext=".cr3")
    assert out is not None and out.is_file()
    with Image.open(out) as im:
        assert 0 < max(im.size) <= THUMB_EDGE


def test_load_preview_fits_max_edge(drive):
    Image.new("RGB", (3000, 1500), "blue").save(drive / "big.png")
    image = cache.load_preview(drive / "big.png", ".PNG", max_edge=1000)
    assert image.mode == "RGB"
    assert image.size == (1000, 500)


def test_load_preview_raw_uses_embedded_preview(drive, monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), "red").save(buf, "JPEG")

    class Result:
        returncode = 0
        stdout = buf.getvalue()

    monkeypatch.setattr(cache.subprocess, "run", lambda args, **kw: Result())
    (drive / "x.cr3").write_bytes(b"raw")
    image = cache.load_preview(drive / "x.cr3", ".cr3")
    assert image.size == (600, 400)


def test_load_preview_unreadable_returns_none(drive):
    (drive / "bad.jpg").write_bytes(b"junk")
    assert cache.load_preview(drive / "bad.jpg", ".jpg") is None
    assert cache.load_preview(drive / "missing.jpg", ".jpg") is None
