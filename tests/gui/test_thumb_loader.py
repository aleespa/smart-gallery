import threading

import pytest

pytest.importorskip("PIL")

from smart_gallery.gui.thumbs import loader as loader_mod  # noqa: E402
from smart_gallery.gui.thumbs.cache import thumb_path  # noqa: E402
from smart_gallery.gui.thumbs.loader import ThumbnailLoader  # noqa: E402


def _gated(monkeypatch):
    """Make ensure_thumbnail block on a gate; returns (gate, started ids)."""
    gate = threading.Event()
    started = []
    real = loader_mod.ensure_thumbnail

    def slow(*args):
        started.append(args[1])
        gate.wait(10)
        return real(*args)

    monkeypatch.setattr(loader_mod, "ensure_thumbnail", slow)
    return gate, started


@pytest.fixture
def make_loader():
    """Build loaders and wait for their pool threads at teardown."""
    made = []

    def _make():
        loader = ThumbnailLoader()
        made.append(loader)
        return loader

    yield _make
    for loader in made:
        loader._pool.waitForDone(10000)
        loader.deleteLater()


def test_loader_emits_ready(qtbot, make_loader, copy_image, drive):
    copy_image(drive, "test.JPG", "a/test.JPG")
    loader = make_loader()
    with qtbot.waitSignal(loader.ready, timeout=10000) as blocker:
        loader.request(drive, 5, 100, "a/test.JPG", ".jpg", "image")
    assert blocker.args == [str(drive), 5, str(thumb_path(drive, 5, 100))]


def test_loader_emits_empty_path_for_failure(qtbot, make_loader, drive):
    (drive / "bad.jpg").write_bytes(b"junk")
    loader = make_loader()
    with qtbot.waitSignal(loader.ready, timeout=10000) as blocker:
        loader.request(drive, 6, 100, "bad.jpg", ".jpg", "image")
    assert blocker.args == [str(drive), 6, ""]


def test_ready_slot_runs_on_gui_thread(qtbot, make_loader, copy_image, drive):
    copy_image(drive, "test.JPG", "a/test.JPG")
    loader = make_loader()
    seen = []

    def slot(root, media_id, path):
        seen.append(threading.current_thread() is threading.main_thread())

    loader.ready.connect(slot)
    loader.request(drive, 1, 1, "a/test.JPG", ".jpg", "image")
    qtbot.waitUntil(lambda: bool(seen), timeout=10000)
    assert seen == [True]


def test_duplicate_requests_are_deduped(
    qtbot, make_loader, copy_image, drive, monkeypatch
):
    copy_image(drive, "test.JPG", "a/test.JPG")
    gate, started = _gated(monkeypatch)
    loader = make_loader()
    got = []
    loader.ready.connect(lambda root, media_id, path: got.append(media_id))
    for _ in range(3):
        loader.request(drive, 9, 1, "a/test.JPG", ".jpg", "image")
    gate.set()
    qtbot.waitUntil(lambda: bool(got), timeout=10000)
    qtbot.wait(100)
    assert started == [9] and got == [9]


def test_cancel_pending_drops_queued_requests(
    qtbot, make_loader, copy_image, drive, monkeypatch
):
    copy_image(drive, "test.JPG", "a/test.JPG")
    gate, started = _gated(monkeypatch)
    loader = make_loader()
    got = []
    loader.ready.connect(lambda root, media_id, path: got.append(media_id))
    for media_id in range(1, 21):
        loader.request(drive, media_id, 1, "a/test.JPG", ".jpg", "image")
    qtbot.waitUntil(lambda: len(started) == 4, timeout=5000)
    loader.cancel_pending()
    gate.set()
    qtbot.waitUntil(lambda: len(got) >= 4, timeout=10000)
    qtbot.wait(200)
    assert len(started) == 4 and sorted(got) == sorted(started)
    # A cancelled id can be requested again.
    loader.request(drive, 20, 1, "a/test.JPG", ".jpg", "image")
    qtbot.waitUntil(lambda: 20 in got, timeout=10000)


def test_same_id_in_two_roots_is_delivered_per_root(
    qtbot, make_loader, copy_image, tmp_path, monkeypatch
):
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        copy_image(root, "test.JPG", "x/test.JPG")
    gate, started = _gated(monkeypatch)
    loader = make_loader()
    got = []
    loader.ready.connect(
        lambda root, media_id, path: got.append((root, media_id, path))
    )
    for root in roots:
        loader.request(root, 5, 1, "x/test.JPG", ".jpg", "image")
    gate.set()
    qtbot.waitUntil(lambda: len(got) == 2, timeout=10000)
    assert sorted(got) == sorted(
        (str(root), 5, str(thumb_path(root, 5, 1))) for root in roots
    )
