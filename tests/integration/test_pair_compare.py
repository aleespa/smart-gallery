"""Picture check against the real R8 CR3 fixture. Needs ExifTool and Pillow."""

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageEnhance, ImageOps  # noqa: E402

from smart_gallery.storage import compare  # noqa: E402
from smart_gallery.storage.check_cache import CheckCache  # noqa: E402
from smart_gallery.storage.compare import (  # noqa: E402
    DIFFERENT,
    MATCH,
    UNREADABLE,
    check_pairs,
    compare_images,
    extract_previews,
)
from smart_gallery.storage.pairs import FileRef, Pair  # noqa: E402

pytestmark = pytest.mark.exiftool


def _ref(root, relpath, i):
    p = root / relpath
    if p.exists():
        st = p.stat()
        return FileRef(
            i, relpath, p.suffix.lower(), st.st_size, st.st_mtime_ns, "image"
        )
    return FileRef(i, relpath, p.suffix.lower(), 1, 1, "image")


def _pair(root, raw_rel, jpeg_rel):
    return Pair("", "IMG", _ref(root, raw_rel, 1), _ref(root, jpeg_rel, 2), None)


def _preview(tmp_path, copy_image):
    raw = copy_image(tmp_path, "test.CR3", "IMG.CR3")
    previews = extract_previews([raw], tmp_path / "pv")
    return raw, previews[raw]


def _run(pairs, root, cache):
    got = {}
    check_pairs(pairs, root, cache, lambda k, v: got.__setitem__(k, v))
    return got


def test_reencoded_preview_matches(tmp_path, copy_image):
    _, pv = _preview(tmp_path, copy_image)
    img = Image.open(pv)
    img.save(tmp_path / "a.jpg", quality=95)
    ImageEnhance.Brightness(img).enhance(1.1).save(tmp_path / "b.jpg", quality=90)
    assert compare_images(pv, tmp_path / "a.jpg") == MATCH
    assert compare_images(pv, tmp_path / "b.jpg") == MATCH


def test_cropped_is_different(tmp_path, copy_image):
    _, pv = _preview(tmp_path, copy_image)
    img = Image.open(pv)
    w, h = img.size
    img.crop((w // 4, 0, w // 4 + w // 2, h)).save(tmp_path / "c.jpg")
    assert compare_images(pv, tmp_path / "c.jpg") == DIFFERENT


def test_inverted_is_different(tmp_path, copy_image):
    _, pv = _preview(tmp_path, copy_image)
    ImageOps.invert(Image.open(pv).convert("RGB")).save(tmp_path / "i.jpg")
    assert compare_images(pv, tmp_path / "i.jpg") == DIFFERENT


def test_other_shot_is_different(tmp_path, copy_image):
    _, pv = _preview(tmp_path, copy_image)
    other = copy_image(tmp_path, "test.JPG", "other.jpg")
    assert compare_images(pv, other) == DIFFERENT


def test_missing_jpeg_is_unreadable(tmp_path, copy_image):
    copy_image(tmp_path, "test.CR3", "IMG.CR3")
    pair = _pair(tmp_path, "IMG.CR3", "IMG.JPG")
    got = _run([pair], tmp_path, CheckCache(tmp_path / "c.json"))
    assert got == {pair.key: UNREADABLE}


def test_no_preview_is_unreadable(tmp_path, copy_image):
    raw = tmp_path / "junk.CR3"
    raw.write_bytes(b"junk")
    (tmp_path / "junk.JPG").write_bytes(b"x")
    assert raw not in extract_previews([raw], tmp_path / "pv")
    pair = _pair(tmp_path, "junk.CR3", "junk.JPG")
    got = _run([pair], tmp_path, CheckCache(tmp_path / "c.json"))
    assert got == {pair.key: UNREADABLE}


def test_extract_previews_non_ascii(tmp_path, copy_image):
    raw = copy_image(tmp_path, "test.CR3", "Año 2026/IMG.CR3")
    out = extract_previews([raw], tmp_path / "pv")
    assert list(out) == [raw]
    assert out[raw].stat().st_size > 0


def test_check_pairs_uses_cache(tmp_path, copy_image, monkeypatch):
    raw, pv = _preview(tmp_path, copy_image)
    Image.open(pv).save(tmp_path / "IMG.JPG", quality=95)
    pair = _pair(tmp_path, "IMG.CR3", "IMG.JPG")
    cache = CheckCache(tmp_path / "c.json")
    assert _run([pair], tmp_path, cache) == {pair.key: MATCH}

    def boom(*a, **k):
        raise AssertionError("should not extract")

    monkeypatch.setattr(compare, "extract_previews", boom)
    assert _run([pair], tmp_path, CheckCache(tmp_path / "c.json")) == {pair.key: MATCH}
