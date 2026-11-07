"""The viewer's preview loader: off the GUI thread, cached, prefetching."""

from PIL import Image
from PySide6.QtCore import QSize

from smart_gallery.gui.viewer.previews import (
    CACHE_SIZE,
    PREVIEW_EDGE,
    ZOOM_EDGE,
    PreviewLoader,
)


def _jpeg(path, size=(900, 600)):
    Image.new("RGB", size, (90, 120, 160)).save(path, "JPEG")
    return path


def test_request_then_cache_hit(qtbot, tmp_path):
    path = _jpeg(tmp_path / "a.jpg")
    loader = PreviewLoader()
    with qtbot.waitSignal(loader.ready) as got:
        loader.request(1, path, ".jpg", "image")
    result = got.args[0]
    assert (result.key, result.edge, result.error) == (1, PREVIEW_EDGE, "")
    assert result.image.size() == QSize(900, 600)
    assert loader.cached(1, PREVIEW_EDGE) is not None
    path.unlink()  # a second request comes from the cache, not the file
    with qtbot.waitSignal(loader.ready) as again:
        loader.request(1, path, ".jpg", "image")
    assert again.args[0].image.size() == QSize(900, 600)


def test_edge_limits_the_size(qtbot, tmp_path):
    path = _jpeg(tmp_path / "big.jpg", (3000, 2000))
    loader = PreviewLoader()
    with qtbot.waitSignal(loader.ready) as small:
        loader.request(1, path, ".jpg", "image")
    assert small.args[0].image.width() == PREVIEW_EDGE
    with qtbot.waitSignal(loader.ready) as big:
        loader.request(1, path, ".jpg", "image", edge=ZOOM_EDGE)
    assert big.args[0].image.width() == 3000 and big.args[0].edge == ZOOM_EDGE


def test_missing_file_reports_missing(qtbot, tmp_path):
    loader = PreviewLoader()
    with qtbot.waitSignal(loader.ready) as got:
        loader.request(2, tmp_path / "gone.jpg", ".jpg", "image")
    assert got.args[0].image is None and got.args[0].error == "missing"


def test_unreadable_file_has_no_image(qtbot, tmp_path):
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"junk")
    loader = PreviewLoader()
    with qtbot.waitSignal(loader.ready) as got:
        loader.request(3, path, ".jpg", "image")
    assert got.args[0].image is None and got.args[0].error == ""


def test_video_is_not_read(qtbot, tmp_path, monkeypatch):
    import smart_gallery.gui.viewer.previews as mod

    monkeypatch.setattr(mod, "load_preview", lambda *a, **k: 1 / 0)
    loader = PreviewLoader()
    with qtbot.waitSignal(loader.ready) as got:
        loader.request(4, tmp_path / "clip.mp4", ".mp4", "video")
    assert got.args[0].image is None and got.args[0].error == ""


def test_prefetch_fills_the_cache_quietly(qtbot, tmp_path):
    path = _jpeg(tmp_path / "n.jpg")
    loader = PreviewLoader()
    seen = []
    loader.ready.connect(seen.append)
    loader.prefetch([(5, path, ".jpg", "image")])
    qtbot.waitUntil(lambda: loader.cached(5, PREVIEW_EDGE) is not None)
    qtbot.wait(50)
    assert seen == []


def test_cache_is_bounded(qtbot, tmp_path):
    loader = PreviewLoader()
    paths = [_jpeg(tmp_path / f"{i}.jpg", (40, 30)) for i in range(CACHE_SIZE + 2)]
    for i, path in enumerate(paths):
        with qtbot.waitSignal(loader.ready):
            loader.request(i, path, ".jpg", "image")
    kept = [i for i in range(len(paths)) if loader.cached(i, PREVIEW_EDGE) is not None]
    assert kept == list(range(2, CACHE_SIZE + 2))


def test_cancel_pending_lets_the_latest_request_go_first(qtbot, tmp_path, monkeypatch):
    """Holding → queues a decode per photo passed; on landing, the viewer
    drops the queue so the photo it stops on is not stuck behind them."""
    import time

    import smart_gallery.gui.viewer.previews as mod

    real = mod.load_preview

    def slow(*args, **kwargs):
        time.sleep(0.05)
        return real(*args, **kwargs)

    monkeypatch.setattr(mod, "load_preview", slow)
    paths = [_jpeg(tmp_path / f"{i}.jpg", (40, 30)) for i in range(41)]
    loader = PreviewLoader()
    for i in range(40):
        loader.request(i, paths[i], ".jpg", "image")
    loader.cancel_pending()
    started = time.monotonic()
    with qtbot.waitSignal(
        loader.ready, check_params_cb=lambda r: r.key == 40, timeout=3000
    ):
        loader.request(40, paths[40], ".jpg", "image")
    assert time.monotonic() - started < 0.5  # 40 queued decodes would take ~1 s


def test_only_one_full_size_preview_is_kept(qtbot, tmp_path):
    loader = PreviewLoader()
    for i in range(3):
        path = _jpeg(tmp_path / f"z{i}.jpg", (300, 200))
        with qtbot.waitSignal(loader.ready):
            loader.request(i, path, ".jpg", "image", edge=ZOOM_EDGE)
    kept = [i for i in range(3) if loader.cached(i, ZOOM_EDGE) is not None]
    assert kept == [2]
