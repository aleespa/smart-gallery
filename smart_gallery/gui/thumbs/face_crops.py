"""Face crops for the People page: the saved JPEG, made lazily when missing.

The scan writes crops for new faces; faces scanned earlier get theirs here, the
first time the GUI asks. Pillow only (the ``gui`` extra), no cv2.
"""

from __future__ import annotations

import io
from pathlib import Path

from smart_gallery.config import to_abspath
from smart_gallery.face_crops import CROP_EDGE, crop_box, crop_path, write_crop
from smart_gallery.gui.thumbs.cache import RAW_EXTENSIONS, load_preview
from smart_gallery.gui.thumbs.loader import _PoolLoader

PREVIEW_EDGE = 4096
NATIVE_EDGE = 1 << 20  # no downscaling


def _oriented_width(abspath: Path) -> int:
    """Width of the photo as displayed (EXIF rotation applied), from its header."""
    from PIL import Image

    with Image.open(abspath) as im:
        width, height = im.size
        orientation = im.getexif().get(0x0112, 1)
    return height if orientation in (5, 6, 7, 8) else width


def ensure_face_crop(
    root: Path,
    face_id: int,
    abspath: Path,
    ext: str,
    bbox: tuple[float, float, float, float],
    img_size: tuple[int, int] | None,
) -> Path | None:
    """The crop file for a face, cutting it from the photo when it is missing.

    None when the photo cannot be read; nothing is written then, so the next
    request tries again.
    """
    path = crop_path(root, face_id)
    if path.exists():
        return path
    try:
        # The box refers to the image the scanner decoded: the whole oriented
        # photo for plain images, the embedded preview for RAW.
        if (ext or "").lower() in RAW_EXTENSIONS:
            # Embedded previews are modest; keep the native size so a box from
            # an old scan (no img_size) lines up as is.
            preview = load_preview(abspath, ext, max_edge=NATIVE_EDGE)
            if preview is None:
                return None
            full_w = img_size[0] if img_size else preview.width
        else:
            preview = load_preview(abspath, ext, max_edge=PREVIEW_EDGE)
            if preview is None:
                return None
            full_w = img_size[0] if img_size else _oriented_width(abspath)
        scale = preview.width / full_w
        x, y, w, h = (v * scale for v in bbox)
        box = crop_box((x, y, w, h), preview.width, preview.height)
        from PIL import Image

        part = (
            preview.convert("RGB")
            .crop(box)
            .resize((CROP_EDGE, CROP_EDGE), Image.Resampling.LANCZOS)
        )
        buf = io.BytesIO()
        part.save(buf, "JPEG", quality=85)
        write_crop(root, face_id, buf.getvalue())
        return path
    except Exception:
        return None


class FaceCropLoader(_PoolLoader):
    """Loads face crops off the GUI thread.

    ``ready(root, face_id, path)`` is emitted on the GUI thread; ``path`` is
    "" when the crop could not be made.
    """

    def request(
        self,
        root: Path,
        face_id: int,
        relpath: str,
        ext: str,
        bbox: tuple[float, float, float, float],
        img_size: tuple[int, int] | None,
    ) -> None:
        self._submit((root, face_id, to_abspath(relpath, root), ext, bbox, img_size))

    def _work(self, job: tuple) -> Path | None:
        root, face_id, abspath, ext, bbox, img_size = job
        return ensure_face_crop(root, face_id, abspath, ext, bbox, img_size)
