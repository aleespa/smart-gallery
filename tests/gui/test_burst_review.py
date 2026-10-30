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
