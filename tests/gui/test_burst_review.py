import pytest
from PIL import Image
from PySide6.QtCore import Qt

from smart_gallery.gui.storage import burst_review as mod
from smart_gallery.gui.storage.burst_review import BurstReview
from smart_gallery.gui.storage.bursts_view import BurstsView
from smart_gallery.storage.bursts import Shot
from smart_gallery.storage.pairs import FileRef

MB = 1024 * 1024


def _shot(i, folder, ts):
    stem = f"IMG_{i:04d}"
    ref = FileRef(i, f"{folder}/{stem}.jpg", ".jpg", MB, 1000 + i, "image")
    return Shot(folder, stem, ts, "Cam", (ref,), None, "1/200", 2.8, 400)


@pytest.fixture
def review(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "load_preview", lambda p, e: Image.new("RGB", (40, 30)))
    view = BurstsView(object())
    qtbot.addWidget(view)
    view.set_shots(
        [
            _shot(1, "a", 0),
            _shot(2, "a", 1),
            _shot(3, "a", 2),
            _shot(4, "b", 0),
            _shot(5, "b", 1),
        ],
        tmp_path,
    )
    r = BurstReview(view, tmp_path)
    qtbot.addWidget(r)
    r.show()
    yield r
    r.wait()


def _keys(r):
    bursts = r.bursts_view.bursts_in_scope()
    return bursts, [[s.key for s in b.shots] for b in bursts]


def test_open_and_step(review, qtbot):
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][0])
    review.step(1)
    assert review.current() == (bursts[0].key, shots[0][1])
    review.step(-5)
    assert review.current() == (bursts[0].key, shots[0][0])
    qtbot.waitUntil(
        lambda: review.image_label.pixmap() is not None
        and not review.image_label.pixmap().isNull()
    )


def test_keys_mark_and_refuse_last(review, qtbot):
    bursts, shots = _keys(review)
    review.open_at(bursts[1].key, shots[1][0])
    qtbot.keyClick(review, Qt.Key.Key_X)
    assert review.bursts_view.marks() == {shots[1][0]}
    qtbot.keyClick(review, Qt.Key.Key_K)
    assert review.bursts_view.marks() == set()
    qtbot.keyClick(review, Qt.Key.Key_X)
    review.step(1)
    qtbot.keyClick(review, Qt.Key.Key_X)
    assert review.bursts_view.marks() == {shots[1][0]}
    assert "keep at least one" in review.details.text()


def test_group_navigation_follows_list_order(review, qtbot):
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][1])
    qtbot.keyClick(review, Qt.Key.Key_Return)
    assert review.current() == (bursts[1].key, shots[1][0])
    qtbot.keyClick(review, Qt.Key.Key_PageDown)
    assert review.current()[0] == bursts[1].key
    qtbot.keyClick(review, Qt.Key.Key_PageUp)
    assert review.current()[0] == bursts[0].key
    qtbot.keyClick(review, Qt.Key.Key_PageUp)
    assert review.current()[0] == bursts[0].key


def test_escape_emits_closed_with_burst(review, qtbot):
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][0])
    with qtbot.waitSignal(review.closed) as sig:
        qtbot.keyClick(review, Qt.Key.Key_Escape)
    assert sig.args == [bursts[0].key]


def test_review_closes_when_its_burst_vanishes(review, qtbot, tmp_path):
    bursts, shots = _keys(review)
    review.open_at(bursts[1].key, shots[1][0])
    review.bursts_view.set_shots([_shot(1, "a", 0), _shot(2, "a", 1)], tmp_path)
    with qtbot.waitSignal(review.closed) as sig:
        review.refresh()
    assert sig.args == [None]


def test_space_toggles_zoom(review, qtbot):
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][0])
    assert review.image_label.hasScaledContents() is False
    assert review.fit is True
    qtbot.keyClick(review, Qt.Key.Key_Space)
    assert review.fit is False
    qtbot.keyClick(review, Qt.Key.Key_Space)
    assert review.fit is True


def test_deleted_review_leaves_no_callbacks(review, qtbot, tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent

    view = review.bursts_view
    mine = BurstReview(view, tmp_path)  # not given to qtbot: deleted below
    bursts, shots = _keys(review)
    mine.open_at(bursts[1].key, shots[1][0])
    qtbot.keyClick(mine, Qt.Key.Key_X)
    qtbot.keyClick(mine, Qt.Key.Key_Right)
    qtbot.keyClick(mine, Qt.Key.Key_X)  # refused: starts the notice timer
    mine.wait()
    mine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    view.toggle(shots[0][0])
    view.click_shot(shots[1][1])
    qtbot.wait(mod.NOTICE_MS + 300)


def _recording(monkeypatch, sizes=None):
    """load_preview that records its paths; ``sizes`` pops one size per call."""
    calls = []

    def load(path, ext):
        size = sizes.pop(0) if sizes else (40, 30)
        calls.append(path)  # after the pop: a waiter sees its size taken
        if callable(size):
            size = size()
        return Image.new("RGB", size)

    monkeypatch.setattr(mod, "load_preview", load)
    return calls


def _aspect(review):
    pixmap = review.image_label.pixmap()
    return round(pixmap.width() / pixmap.height(), 1)


def _shown(review):
    pixmap = review.image_label.pixmap()
    return pixmap is not None and not pixmap.isNull()


def test_root_change_clears_the_preview_cache(review, qtbot, tmp_path, monkeypatch):
    calls = _recording(monkeypatch)
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][0])
    qtbot.waitUntil(lambda: _shown(review))
    review.set_root(tmp_path)  # the same root: the cache stays
    review.open_at(bursts[0].key, shots[0][0])
    review.wait()
    assert len(calls) == 1
    other = tmp_path / "other"
    review.set_root(other)
    review.open_at(bursts[0].key, shots[0][0])
    review.wait()
    qtbot.waitUntil(lambda: _shown(review))
    assert len(calls) == 2
    assert calls[1].is_relative_to(other)


def test_changed_file_is_loaded_again(review, qtbot, tmp_path, monkeypatch):
    calls = _recording(monkeypatch)
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][0])
    qtbot.waitUntil(lambda: _shown(review))
    first = review.shots()[0]
    ref = first.files[0]
    changed = Shot(
        first.folder,
        first.stem,
        first.taken_ts,
        first.camera,
        (FileRef(ref.id, ref.relpath, ref.ext, ref.size_bytes, 9999, ref.media_type),),
        None,
        first.shutter,
        first.aperture,
        first.iso,
    )
    review.bursts_view.set_shots(
        [
            changed,
            _shot(2, "a", 1),
            _shot(3, "a", 2),
            _shot(4, "b", 0),
            _shot(5, "b", 1),
        ],
        tmp_path,
    )
    review.refresh()
    review.wait()
    assert len(calls) == 2


def test_late_preview_for_an_old_file_is_not_shown(
    review, qtbot, tmp_path, monkeypatch
):
    import threading

    release = threading.Event()

    def slow():
        release.wait(5)
        return (90, 30)  # aspect 3.0: the stale picture

    # then the new one, aspect 1.0
    calls = _recording(monkeypatch, sizes=[slow, (20, 20)])
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][0])  # the slow load starts
    qtbot.waitUntil(lambda: len(calls) == 1)  # it took the slow size
    first = review.shots()[0]
    ref = first.files[0]
    changed = Shot(
        first.folder,
        first.stem,
        first.taken_ts,
        first.camera,
        (FileRef(ref.id, ref.relpath, ref.ext, ref.size_bytes, 9999, ref.media_type),),
        None,
        first.shutter,
        first.aperture,
        first.iso,
    )
    review.bursts_view.set_shots(
        [
            changed,
            _shot(2, "a", 1),
            _shot(3, "a", 2),
            _shot(4, "b", 0),
            _shot(5, "b", 1),
        ],
        tmp_path,
    )
    review.refresh()
    qtbot.waitUntil(lambda: _shown(review))
    assert _aspect(review) == 1.0
    release.set()
    review.wait()
    qtbot.wait(50)  # the late signal is delivered
    assert _aspect(review) == 1.0


def test_late_preview_from_an_old_root_is_not_shown(
    review, qtbot, tmp_path, monkeypatch
):
    import threading

    release = threading.Event()

    def slow():
        release.wait(5)
        return (90, 30)

    calls = _recording(monkeypatch, sizes=[slow, (20, 20)])
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][0])
    qtbot.waitUntil(lambda: len(calls) == 1)  # it took the slow size
    review.set_root(tmp_path / "other")
    review.refresh()
    qtbot.waitUntil(lambda: _shown(review))
    release.set()
    review.wait()
    qtbot.wait(50)
    assert _aspect(review) == 1.0


def test_failed_preview_says_no_preview(review, qtbot, monkeypatch):
    def broken(path, ext):
        raise OSError("bad file")

    monkeypatch.setattr(mod, "load_preview", broken)
    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][2])
    qtbot.waitUntil(lambda: review.image_label.text() == mod.NO_PREVIEW)


def test_film_scrolls_to_the_current_shot(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "load_preview", lambda p, e: Image.new("RGB", (40, 30)))
    view = BurstsView(object())
    qtbot.addWidget(view)
    view.set_shots([_shot(i, "a", i // 6) for i in range(1, 55)], tmp_path)
    r = BurstReview(view, tmp_path)
    qtbot.addWidget(r)
    r.resize(600, 500)
    r.show()
    qtbot.waitExposed(r)
    (burst,) = view.bursts_in_scope()
    assert len(burst.shots) == 54
    r.open_at(burst.key, burst.shots[0].key)
    bar = r.film_scroll.horizontalScrollBar()
    assert r.film.width() > r.film_scroll.viewport().width()
    assert bar.value() == 0
    r.open_at(burst.key, burst.shots[-1].key)
    assert bar.value() == bar.maximum() > 0
    r.wait()


def test_film_repaints_when_thumbnails_arrive(review, qtbot):
    from PySide6.QtCore import QEvent, QObject

    class Paints(QObject):
        n = 0

        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() == QEvent.Type.Paint:
                Paints.n += 1
            return False

    bursts, shots = _keys(review)
    review.open_at(bursts[0].key, shots[0][0])
    qtbot.wait(50)
    counter = Paints()
    review.film.installEventFilter(counter)
    Paints.n = 0
    review.bursts_view.thumbs_changed.emit()
    qtbot.waitUntil(lambda: Paints.n > 0, timeout=1000)
