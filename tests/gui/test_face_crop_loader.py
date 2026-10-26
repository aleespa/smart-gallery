import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from smart_gallery.face_crops import CROP_EDGE, crop_path, write_crop  # noqa: E402
from smart_gallery.gui.thumbs import face_crops as fc  # noqa: E402
from smart_gallery.gui.thumbs.face_crops import (  # noqa: E402
    FaceCropLoader,
    ensure_face_crop,
)

BBOX = (350.0, 250.0, 100.0, 100.0)


def _jpeg(path, size=(800, 600)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 80, 40)).save(path, "JPEG")
    return path


def test_existing_crop_is_returned_without_opening_source(tmp_path, monkeypatch):
    write_crop(tmp_path, 7, b"jpeg")

    def boom(*args, **kwargs):
        raise AssertionError("source opened")

    monkeypatch.setattr(fc, "load_preview", boom)
    got = ensure_face_crop(tmp_path, 7, tmp_path / "gone.jpg", ".jpg", BBOX, (800, 600))
    assert got == crop_path(tmp_path, 7)


def test_lazy_crop_from_jpeg(tmp_path):
    src = _jpeg(tmp_path / "a" / "p.jpg")
    got = ensure_face_crop(tmp_path, 12, src, ".jpg", BBOX, (800, 600))
    assert got == crop_path(tmp_path, 12) and got.exists()
    with Image.open(got) as im:
        assert im.size == (CROP_EDGE, CROP_EDGE)


def test_lazy_crop_without_img_size(tmp_path):
    src = _jpeg(tmp_path / "p.jpg")
    got = ensure_face_crop(tmp_path, 13, src, ".jpg", BBOX, None)
    assert got is not None and got.exists()


def test_missing_source_returns_none(tmp_path):
    got = ensure_face_crop(tmp_path, 3, tmp_path / "nope.jpg", ".jpg", BBOX, (800, 600))
    assert got is None
    assert not crop_path(tmp_path, 3).exists()


def test_loader_emits_ready(qtbot, tmp_path):
    _jpeg(tmp_path / "a" / "p.jpg")
    loader = FaceCropLoader()
    try:
        with qtbot.waitSignal(loader.ready, timeout=10000) as blocker:
            loader.request(tmp_path, 5, "a/p.jpg", ".jpg", BBOX, (800, 600))
        assert blocker.args == [str(tmp_path), 5, str(crop_path(tmp_path, 5))]
        with qtbot.waitSignal(loader.ready, timeout=10000) as blocker:
            loader.request(tmp_path, 6, "missing.jpg", ".jpg", BBOX, None)
        assert blocker.args == [str(tmp_path), 6, ""]
    finally:
        loader._pool.waitForDone(10000)
        loader.deleteLater()


def _wide(path, orientation=None):
    """6000x1000 image, blue except a red square at x 3000-3200, y 400-600."""
    im = Image.new("RGB", (6000, 1000), (0, 0, 255))
    im.paste((255, 0, 0), (3000, 400, 3200, 600))
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    im.save(path, "JPEG", exif=exif)
    return path


@pytest.mark.parametrize("img_size", [None, (6000, 1000)])
def test_lazy_crop_from_image_wider_than_preview(tmp_path, img_size):
    src = _wide(tmp_path / "w.jpg")
    got = ensure_face_crop(
        tmp_path, 1, src, ".jpg", (3000.0, 400.0, 200.0, 200.0), img_size
    )
    with Image.open(got) as im:
        assert im.getpixel((80, 80))[0] > 200  # red face region at the centre


def test_lazy_crop_without_img_size_follows_exif_rotation(tmp_path):
    # Orientation 6: displayed 1000x6000; the scanner's box is in that frame.
    src = _wide(tmp_path / "r.jpg", orientation=6)
    with Image.open(src) as im:
        from PIL import ImageOps

        shown = ImageOps.exif_transpose(im).convert("RGB")
    red = [
        (x, y)
        for x in range(0, 1000, 50)
        for y in range(0, 6000, 50)
        if shown.getpixel((x, y))[0] > 200
    ]
    x0, y0 = min(red)
    box = (x0 - 10.0, y0 - 10.0, 200.0, 200.0)
    got = ensure_face_crop(tmp_path, 2, src, ".jpg", box, None)
    with Image.open(got) as im:
        assert im.getpixel((80, 80))[0] > 200
