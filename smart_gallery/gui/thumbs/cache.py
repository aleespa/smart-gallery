"""On-drive thumbnail cache. No Qt here; Pillow is imported inside functions.

Thumbnails live beside the catalog at
``<root>/.smart_gallery/thumbs/<id // 1000>/<id>-<mtime_ns>-s512.jpg``. The mtime in
the name makes a changed file regenerate. A ``.none`` marker with the same stem
records a failure so a broken file is only opened once.

A video's thumbnail is a frame picked by ffmpeg (when it is installed), else
the thumbnail the camera embedded in the file, read with ExifTool.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from smart_gallery.analysis.extract import _get_exiftool_path

THUMB_EDGE = 512
JPEG_QUALITY = 85
EXIFTOOL_TIMEOUT_S = 30
FFMPEG_TIMEOUT_S = 60
# ffmpeg starts this far in, then keeps the most typical of the next frames,
# so a fade-in or a black first frame is skipped.
VIDEO_SEEK_S = 1.0
VIDEO_SAMPLE_FRAMES = 50
VIDEO_PREVIEW_TAGS = ("-ThumbnailImage", "-PreviewImage", "-CoverArt")

RAW_EXTENSIONS = frozenset(
    {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".rw2", ".orf"}
)
RAW_PREVIEW_TAGS = ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage")
# The full-size embedded JPEG first: for looking closely (the viewer's zoom).
RAW_LARGEST_TAGS = ("-JpgFromRaw", "-PreviewImage", "-ThumbnailImage")
JPEG_EXTENSIONS = frozenset({".jpg", ".jpeg", ".jpe"})


class _Unavailable(Exception):
    """ExifTool could not run (missing, timed out): retry later, no marker."""


# Thumbnails were 256 px under ``<id>-<mtime>.jpg`` (RAW: ``...-r1.jpg``). The
# size in the name makes them be made again; the old files go once replaced.
THUMB_SUFFIX = f"-s{THUMB_EDGE}"
LEGACY_SUFFIXES = ("", "-r1")


def thumb_path(root: Path, media_id: int, mtime_ns: int | None, ext: str = "") -> Path:
    """``ext`` is kept for callers; every file type shares one name."""
    name = f"{media_id}-{mtime_ns or 0}{THUMB_SUFFIX}.jpg"
    return Path(root) / ".smart_gallery" / "thumbs" / str(media_id // 1000) / name


def _marker_path(thumb: Path) -> Path:
    return thumb.with_suffix(".none")


def ensure_thumbnail(
    root: Path,
    media_id: int,
    mtime_ns: int | None,
    abspath: Path,
    ext: str,
    media_type: str,
) -> Path | None:
    """Return the cached thumbnail, creating it when needed; None when there
    is none. Safe to call from several threads. Never raises."""
    try:
        return _ensure(root, media_id, mtime_ns, Path(abspath), ext, media_type)
    except Exception:
        return None


def _ensure(root, media_id, mtime_ns, abspath, ext, media_type) -> Path | None:
    dest = thumb_path(root, media_id, mtime_ns, ext)
    marker = _marker_path(dest)
    if dest.is_file():
        return dest
    if marker.exists():
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None  # read-only drive: no thumbnail and no marker

    try:
        if media_type == "video":
            data = _render_video_jpeg(abspath)
        else:
            data = _render_jpeg(abspath, ext.lower())
    except _Unavailable:
        return None  # environmental, not a property of the file
    except Exception:
        _write_marker(marker)
        return None

    tmp = dest.with_name(f"{dest.stem}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except OSError:
        # Another thread may have won the race and still hold the file open.
        return dest if dest.is_file() else None
    finally:
        tmp.unlink(missing_ok=True)
    _remove_legacy(root, media_id, mtime_ns, dest)
    return dest


def _remove_legacy(root, media_id, mtime_ns, dest: Path) -> None:
    """Delete the 256 px thumbnails and failure markers, now replaced."""
    for suffix in LEGACY_SUFFIXES:
        for ext in (".jpg", ".none"):
            path = dest.with_name(f"{media_id}-{mtime_ns or 0}{suffix}{ext}")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _write_marker(marker: Path) -> None:
    try:
        marker.touch()
    except OSError:
        pass


def _render_jpeg(abspath: Path, ext: str) -> bytes:
    from PIL import Image

    if ext in RAW_EXTENSIONS:
        image = _open_raw_preview(abspath)
    else:
        image = Image.open(abspath)
        if ext in JPEG_EXTENSIONS:
            image.draft("RGB", (THUMB_EDGE, THUMB_EDGE))
    with image:
        return _to_thumbnail_bytes(image)


def _to_thumbnail_bytes(image) -> bytes:
    image = _fit_rgb(image, THUMB_EDGE)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def _fit_rgb(image, max_edge: int):
    """Upright RGB copy of ``image`` no larger than ``max_edge`` on either side."""
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    image.thumbnail((max_edge, max_edge))
    return image


def load_preview(abspath: Path, ext: str, max_edge: int = 2048, largest: bool = False):
    """A large upright RGB Pillow image of the file for the viewer: the file
    itself for regular images, the embedded preview for RAW (with
    ``largest``, the full-size embedded JPEG first). None when it cannot be
    read. Never raises."""
    try:
        from PIL import Image

        ext = (ext or "").lower()
        if ext in RAW_EXTENSIONS:
            tags = RAW_LARGEST_TAGS if largest else RAW_PREVIEW_TAGS
            image = _open_raw_preview(Path(abspath), tags)
        else:
            image = Image.open(abspath)
            if ext in JPEG_EXTENSIONS:
                image.draft("RGB", (max_edge, max_edge))
        with image:
            return _fit_rgb(image, max_edge)
    except Exception:
        return None


_TRANSPOSE = {2: "FLIP_LEFT_RIGHT", 3: "ROTATE_180", 4: "FLIP_TOP_BOTTOM",
              5: "TRANSPOSE", 6: "ROTATE_270", 7: "TRANSVERSE", 8: "ROTATE_90"}  # fmt: skip


def _open_raw_preview(abspath: Path, tags=RAW_PREVIEW_TAGS):
    """First decodable embedded image of a RAW file, as an open Pillow image,
    turned upright.

    Cameras store the embedded previews as the sensor saw them, without an
    orientation of their own; the RAW's Orientation tag says how to turn them.
    Raises _Unavailable when ExifTool cannot run, ValueError when it ran but
    no tag yielded a usable image.
    """
    from PIL import Image

    image = _open_embedded(abspath, tags)
    if image is None:
        raise ValueError(f"no embedded preview in {abspath}")
    if image.getexif().get(0x0112, 1) != 1:
        return image  # the preview says itself; exif_transpose turns it
    method = _TRANSPOSE.get(_raw_orientation(abspath))
    if method is None:
        return image
    with image:
        return image.transpose(getattr(Image.Transpose, method))


def _raw_orientation(abspath: Path) -> int:
    """The RAW file's EXIF Orientation (1-8); 1 when it has none."""
    out = _run_tool(
        [_get_exiftool_path(), "-s3", "-n", "-Orientation", str(abspath)],
        EXIFTOOL_TIMEOUT_S,
    )
    try:
        return int(out.strip() or 1)
    except ValueError:
        return 1


def _open_embedded(abspath: Path, tags):
    """First of ``tags`` holding a decodable image, as an open Pillow image;
    None when none does. Raises _Unavailable when ExifTool cannot run."""
    from PIL import Image

    for tag in tags:
        data = _exiftool_bytes(tag, abspath)
        if not data:
            continue
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
            return image
        except Exception:
            continue
    return None


def _render_video_jpeg(abspath: Path) -> bytes:
    """A frame from ffmpeg, else the embedded thumbnail.

    Raises _Unavailable when neither could be tried (no ffmpeg, and ExifTool
    missing or the file has no embedded image), so nothing is recorded and
    the thumbnail appears once ffmpeg is installed. Raises ValueError when
    ffmpeg ran and still nothing gave an image.
    """
    from PIL import Image

    if not abspath.is_file():
        raise FileNotFoundError(abspath)
    ffmpeg = _ffmpeg_path()
    if ffmpeg is not None:
        frame = _ffmpeg_frame(ffmpeg, abspath)
        if frame:
            try:
                with Image.open(io.BytesIO(frame)) as image:
                    return _to_thumbnail_bytes(image)
            except Exception:
                pass  # not a picture after all: try the embedded one
    image = _open_embedded(abspath, VIDEO_PREVIEW_TAGS)
    if image is not None:
        with image:
            return _to_thumbnail_bytes(image)
    if ffmpeg is None:
        raise _Unavailable("ffmpeg is not installed")
    raise ValueError(f"no frame in {abspath}")


def _ffmpeg_path() -> str | None:
    """``$SG_FFMPEG_PATH``, else ffmpeg on PATH, else None."""
    return os.environ.get("SG_FFMPEG_PATH") or shutil.which("ffmpeg")


def _ffmpeg_frame(ffmpeg: str, abspath: Path) -> bytes:
    """One JPEG frame, scaled to fit the thumbnail; b"" when none came out.
    A clip shorter than the seek is read again from the start."""
    picks = (
        f"thumbnail={VIDEO_SAMPLE_FRAMES},"
        f"scale={THUMB_EDGE}:{THUMB_EDGE}:force_original_aspect_ratio=decrease"
    )
    for seek in (VIDEO_SEEK_S, 0):
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if seek:
            args += ["-ss", str(seek)]
        args += [
            "-i",
            str(abspath),
            "-an",
            "-frames:v",
            "1",
            "-vf",
            picks,
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "-",
        ]
        data = _run_tool(args, FFMPEG_TIMEOUT_S)
        if data:
            return data
    return b""


def _run_tool(args: list[str], timeout: float) -> bytes:
    """stdout of a successful run, b"" on a failed one. Raises _Unavailable
    when the program cannot run or takes too long."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _Unavailable(str(exc)) from exc
    return result.stdout if result.returncode == 0 else b""


def _exiftool_bytes(tag: str, abspath: Path) -> bytes:
    return _run_tool(
        [_get_exiftool_path(), "-b", tag, str(abspath)], EXIFTOOL_TIMEOUT_S
    )
