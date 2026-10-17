"""On-drive thumbnail cache. No Qt here; Pillow is imported inside functions.

Thumbnails live beside the catalog at
``<root>/.smart_gallery/thumbs/<id // 1000>/<id>-<mtime_ns>.jpg``. The mtime in
the name makes a changed file regenerate. A ``.none`` marker with the same stem
records a failure so a broken file is only opened once.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from smart_gallery.analysis.extract import _get_exiftool_path

THUMB_EDGE = 256
JPEG_QUALITY = 85
EXIFTOOL_TIMEOUT_S = 30

RAW_EXTENSIONS = frozenset(
    {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".rw2", ".orf"}
)
RAW_PREVIEW_TAGS = ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage")
JPEG_EXTENSIONS = frozenset({".jpg", ".jpeg", ".jpe"})


def thumb_path(root: Path, media_id: int, mtime_ns: int | None) -> Path:
    name = f"{media_id}-{mtime_ns or 0}.jpg"
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
    dest = thumb_path(root, media_id, mtime_ns)
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
            raise ValueError("videos have no thumbnail")
        data = _render_jpeg(abspath, ext.lower())
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
    return dest


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
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    image.thumbnail((THUMB_EDGE, THUMB_EDGE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def _open_raw_preview(abspath: Path):
    """First decodable embedded image of a RAW file, as an open Pillow image."""
    from PIL import Image

    for tag in RAW_PREVIEW_TAGS:
        data = _exiftool_bytes(tag, abspath)
        if not data:
            continue
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
            return image
        except Exception:
            continue
    raise ValueError(f"no embedded preview in {abspath}")


def _exiftool_bytes(tag: str, abspath: Path) -> bytes:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [_get_exiftool_path(), "-b", tag, str(abspath)],
            capture_output=True,
            timeout=EXIFTOOL_TIMEOUT_S,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return b""
    return result.stdout if result.returncode == 0 else b""
