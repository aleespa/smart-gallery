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
