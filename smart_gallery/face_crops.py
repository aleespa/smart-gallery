"""Face thumbnails saved by the scan, one small JPEG per face.

Crops live under ``<root>/.smart_gallery/faces/<face_id // 1000>/<face_id>.jpg``
so the People page can show faces without decoding the photos again. cv2 comes
with the ``faces`` extra and is imported only inside ``encode_crop``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

CROP_EDGE = 160
CROP_MARGIN = 0.4  # the box grows by 40% of its size on each side


def crop_path(root: Path, face_id: int) -> Path:
    return (
        Path(root)
        / ".smart_gallery"
        / "faces"
        / str(face_id // 1000)
        / f"{face_id}.jpg"
    )


def crop_box(
    bbox: tuple[float, float, float, float], img_w: int, img_h: int
) -> tuple[int, int, int, int]:
    """The square around a face box ``(x, y, w, h)``: grown by ``CROP_MARGIN``
    on each side, then squared and moved (or shrunk) to fit the image.
    Returns ``(x1, y1, x2, y2)``."""
    x, y, w, h = bbox
    side = max(w, h) * (1 + 2 * CROP_MARGIN)
    side = max(1, min(int(round(side)), img_w, img_h))
    cx, cy = x + w / 2, y + h / 2
    x1 = min(max(int(round(cx - side / 2)), 0), img_w - side)
    y1 = min(max(int(round(cy - side / 2)), 0), img_h - side)
    return x1, y1, x1 + side, y1 + side


def encode_crop(img_bgr, box: tuple[int, int, int, int]) -> bytes:
    """JPEG bytes (quality 85) of ``box`` cut from a BGR image, resized to
    ``CROP_EDGE`` square."""
    import cv2

    x1, y1, x2, y2 = box
    part = img_bgr[y1:y2, x1:x2]
    if part.size == 0:
        raise ValueError(f"empty crop {box}")
    shrink = part.shape[0] >= CROP_EDGE
    part = cv2.resize(
        part,
        (CROP_EDGE, CROP_EDGE),
        interpolation=cv2.INTER_AREA if shrink else cv2.INTER_CUBIC,
    )
    ok, buf = cv2.imencode(".jpg", part, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError(f"could not encode crop {box}")
    return buf.tobytes()


def write_crop(root: Path, face_id: int, data: bytes) -> None:
    """Write a crop atomically (temp file in the same folder, then replace)."""
    path = crop_path(root, face_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
