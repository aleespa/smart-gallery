"""Face crop geometry, layout and encoding."""

from pathlib import Path

import numpy as np
import pytest

from smart_gallery.face_crops import (
    CROP_EDGE,
    crop_box,
    crop_path,
    encode_crop,
    write_crop,
)


def test_crop_box_square_clamped():
    # A face in the top-left corner: the grown box would leave the image.
    x1, y1, x2, y2 = crop_box((0.0, 0.0, 100.0, 100.0), 1000, 800)
    assert (x1, y1) == (0, 0)
    assert x2 - x1 == y2 - y1 == 180  # 100 * (1 + 2 * 0.4)

    # Bottom-right corner, non-square face: square on the longer side.
    x1, y1, x2, y2 = crop_box((950.0, 760.0, 50.0, 40.0), 1000, 800)
    assert x2 - x1 == y2 - y1 == 90
    assert (x2, y2) == (1000, 800)

    # A face too big for the room: square as large as the short side allows.
    x1, y1, x2, y2 = crop_box((10.0, 10.0, 300.0, 300.0), 400, 320)
    assert 0 <= x1 < x2 <= 400 and 0 <= y1 < y2 <= 320
    assert x2 - x1 == y2 - y1 == 320


def test_crop_path_layout():
    root = Path("E:/")
    assert crop_path(root, 7) == root / ".smart_gallery" / "faces" / "0" / "7.jpg"
    assert crop_path(root, 12345) == (
        root / ".smart_gallery" / "faces" / "12" / "12345.jpg"
    )


def test_encode_and_write_crop(tmp_path):
    cv2 = pytest.importorskip("cv2")
    img = np.zeros((400, 600, 3), dtype=np.uint8)
    img[100:200, 100:200] = (0, 0, 255)
    data = encode_crop(img, crop_box((100.0, 100.0, 100.0, 100.0), 600, 400))
    write_crop(tmp_path, 1234, data)

    out = cv2.imread(str(crop_path(tmp_path, 1234)))
    assert out.shape == (CROP_EDGE, CROP_EDGE, 3)
    assert list(crop_path(tmp_path, 1234).parent.iterdir()) == [
        crop_path(tmp_path, 1234)
    ]
