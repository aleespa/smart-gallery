import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from smart_gallery.gui.storage import checker as checker_module
from smart_gallery.gui.storage.checker import PairChecker
from smart_gallery.gui.storage.pairs_view import (
    CHOICE_ROLE,
    EMPTY_ALL,
    EMPTY_FOLDER,
    KEY_ROLE,
    LEAVE,
    CHECK_FAILED,
    MOVE_NONE,
    STATUS,
    VERDICT_ROLE,
    PairDialog,
    PairsView,
)
from smart_gallery.storage.compare import DIFFERENT, MATCH, UNREADABLE
from smart_gallery.storage.pairs import (
    KEEP_BOTH,
    KEEP_JPEG,
    KEEP_RAW,
    FileRef,
    Pair,
)

MB = 1024 * 1024


class _Thumbs(QObject):
    ready = Signal(str, int, str)

    def __init__(self):
        super().__init__()
        self.requests = []

    def request(self, root, media_id, mtime_ns, relpath, ext, media_type):
        self.requests.append((str(root), media_id, relpath))


def _ref(i, relpath, size):
    ext = "." + relpath.rsplit(".", 1)[1].lower()
    return FileRef(i, relpath, ext, size, 1000 + i, "image")


def _pair(i, folder, stem, sidecar=False):
    raw = _ref(i * 10 + 1, f"{folder}/{stem}.CR3", 24 * MB)
    jpeg = _ref(i * 10 + 2, f"{folder}/{stem}.JPG", int(9.8 * MB))
    side = _ref(i * 10 + 3, f"{folder}/{stem}.xmp", 4096) if sidecar else None
    return Pair(folder, stem, raw, jpeg, side)


@pytest.fixture
def pairs():
    p1 = _pair(1, "A", "IMG_0001", sidecar=True)
    p2 = _pair(2, "A", "IMG_0002")
    p3 = _pair(3, "B", "IMG_0003")
    return [p1, p2, p3]


@pytest.fixture
def thumbs():
    return _Thumbs()


@pytest.fixture
def view(qtbot, thumbs, tmp_path):
    v = PairsView(SimpleNamespace(thumbs=thumbs))
    qtbot.addWidget(v)
    v.resize(700, 500)
    v.show()
    return v


@pytest.fixture
def loaded(view, pairs, tmp_path):
    view.set_pairs(pairs, tmp_path)
    return view


def _rows(view):
    m = view.list.model()
    return [(m.index(r).data(), m.index(r).data(KEY_ROLE)) for r in range(m.rowCount())]


def _row_of(view, key):
    m = view.list.model()
    for r in range(m.rowCount()):
        if m.index(r).data(KEY_ROLE) == key:
            return m.index(r)
    raise AssertionError(key)


def _answer(monkeypatch, button):
    asked = []

    def question(*args, **kwargs):
        asked.append(args)
        return button

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def test_rows_grouped_by_folder(qtbot, loaded, pairs):
    p1, p2, p3 = pairs
    assert _rows(loaded) == [
        ("A", None),
        ("IMG_0001", p1.key),
        ("IMG_0002", p2.key),
        ("B", None),
        ("IMG_0003", p3.key),
    ]


def test_folders_ordered_biggest_first(qtbot, view, pairs, tmp_path):
    p1, p2, p3 = pairs
    # Folder sizes from the tree: B holds more than A on disk.
    view.set_pairs(pairs, tmp_path, folder_bytes={"a": 10 * MB, "b": 99 * MB})
    assert _rows(view) == [
        ("B", None),
        ("IMG_0003", p3.key),
        ("A", None),
        ("IMG_0001", p1.key),
        ("IMG_0002", p2.key),
    ]


def test_folder_order_ties_by_name_and_falls_back_to_pair_bytes(qtbot, view, tmp_path):
    c = _pair(4, "C", "IMG_0004")
    b = _pair(5, "b", "IMG_0005")
    d1, d2 = _pair(6, "D", "IMG_0006"), _pair(7, "D", "IMG_0007")
    # find_pairs order is by folder; C and b tie on the mapping, D is unknown
    # to it and falls back to its pairs' combined bytes (two pairs > one).
    view.set_pairs([b, c, d1, d2], tmp_path, folder_bytes={"b": 1, "c": 1})
    assert [r for r, k in _rows(view) if k is None] == ["D", "B", "C"]


def test_scope_filters_and_labels(qtbot, loaded, pairs):
    view = loaded
    view.set_scope("a")
    assert view.scope_label.text() == "In a · 2 pairs" and view.show_all.isVisible()
    assert view.show_all.text() == "Show all 3 pairs"
    assert [k for _, k in _rows(view)] == [None, pairs[0].key, pairs[1].key]
    view.show_all.click()
    assert view.scope_label.text() == "All folders · 3 pairs"
    assert not view.show_all.isVisible()
    assert len(_rows(view)) == 5


def test_empty_states(qtbot, view, pairs, tmp_path):
    view.set_pairs([], tmp_path)
    assert view.empty_label.isVisible() and view.empty_label.text() == EMPTY_ALL
    view.set_pairs(pairs, tmp_path)
    assert not view.empty_label.isVisible()
    view.set_scope("C")
    assert view.empty_label.isVisible() and view.empty_label.text() == EMPTY_FOLDER


def test_choose_updates_footer(qtbot, loaded, pairs):
    view, p1 = loaded, pairs[0]
    assert view.move_button.text() == MOVE_NONE and not view.move_button.isEnabled()
    view.choose(p1.key, KEEP_RAW)
    assert view.footer_label.text() == "1 chosen · frees 9.80 MB"
    assert view.move_button.text() == "Move 1 files to Recycle Bin"
    assert view.move_button.isEnabled()
    assert _row_of(view, p1.key).data(CHOICE_ROLE) == KEEP_RAW
    view.choose(p1.key, KEEP_RAW)
    assert view.choices() == {}
    assert view.move_button.text() == MOVE_NONE and not view.move_button.isEnabled()


def test_keep_both_counts_as_chosen_but_frees_nothing(qtbot, loaded, pairs):
    loaded.choose(pairs[1].key, KEEP_BOTH)
    assert loaded.footer_label.text() == "1 chosen · frees 0 B"
    assert loaded.removals() == []
    assert not loaded.move_button.isEnabled()


def test_footer_mentions_other_folders(qtbot, loaded, pairs):
    loaded.choose(pairs[1].key, KEEP_RAW)
    loaded.choose(pairs[2].key, KEEP_RAW)
    assert not loaded.footer_label.text().endswith("folders)")
    loaded.set_scope("a")
    assert loaded.footer_label.text().endswith(" (in 2 folders)")
    assert loaded.footer_label.text().startswith("2 chosen · frees 19.6 MB")


def test_clear_choices(qtbot, loaded, pairs):
    loaded.choose(pairs[0].key, KEEP_JPEG)
    assert loaded.clear_button.isEnabled()
    loaded.clear_button.click()
    assert loaded.choices() == {}
    assert loaded.footer_label.text() == "0 chosen · frees 0 B"
    assert not loaded.clear_button.isEnabled()


def test_pill_click_chooses(qtbot, loaded, pairs):
    view, p2 = loaded, pairs[1]
    activated = []
    view.pair_activated.connect(activated.append)
    index = _row_of(view, p2.key)
    rect = view.list.visualRect(index)
    rects = view.list.itemDelegate().button_rects(rect)
    assert set(rects) == {KEEP_RAW, KEEP_JPEG, KEEP_BOTH}
    QTest.mouseClick(
        view.list.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        rects[KEEP_JPEG].center(),
    )
    assert view.choices() == {p2.key: KEEP_JPEG}
    assert activated == []


def test_click_beside_pills_activates_pair(qtbot, loaded, pairs, monkeypatch):
    view, p2 = loaded, pairs[1]
    opened = []
    monkeypatch.setattr(view, "_open_dialog", opened.append)
    activated = []
    view.pair_activated.connect(activated.append)
    rect = view.list.visualRect(_row_of(view, p2.key))
    QTest.mouseClick(
        view.list.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        rect.center() - QPoint(rect.width() // 4, 0),
    )
    assert activated == [p2.key]
    assert view.choices() == {}


def test_thumbnails_requested_when_painted(qtbot, loaded, thumbs, pairs, tmp_path):
    loaded.list.viewport().repaint()
    ids = {media_id for _, media_id, _ in thumbs.requests}
    assert {pairs[0].raw.id, pairs[0].jpeg.id} <= ids
    thumbs.ready.emit(str(tmp_path), pairs[0].raw.id, "")  # no thumbnail: no crash


def test_verdict_shows_warning(qtbot, loaded, pairs):
    index = _row_of(loaded, pairs[0].key)
    assert index.data(VERDICT_ROLE) is None
    loaded.set_verdict(pairs[0].key, DIFFERENT)
    assert _row_of(loaded, pairs[0].key).data(VERDICT_ROLE) == DIFFERENT
    loaded.list.viewport().repaint()


def test_move_emits_removals_after_confirm(qtbot, loaded, pairs, monkeypatch):
    p1 = pairs[0]
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    loaded.choose(p1.key, KEEP_JPEG)
    with qtbot.waitSignal(loaded.move_requested) as blocker:
        loaded.move_button.click()
    assert blocker.args == [[p1.raw, p1.sidecar]]
    assert "Move 2 files (24.0 MB) to the Recycle Bin?" in asked[0]


def test_move_cancelled_emits_nothing(qtbot, loaded, pairs, monkeypatch):
    _answer(monkeypatch, QMessageBox.StandardButton.No)
    loaded.choose(pairs[0].key, KEEP_RAW)
    emitted = []
    loaded.move_requested.connect(emitted.append)
    loaded.move_button.click()
    assert emitted == []
    assert loaded.choices() == {pairs[0].key: KEEP_RAW}


def test_set_pairs_keeps_existing_choices(qtbot, loaded, pairs, tmp_path):
    p1, p2, p3 = pairs
    loaded.choose(p1.key, KEEP_RAW)
    loaded.choose(p2.key, KEEP_BOTH)
    loaded.set_pairs([p2, p3], tmp_path)
    assert loaded.choices() == {p2.key: KEEP_BOTH}
    assert loaded.footer_label.text() == "1 chosen · frees 0 B"


def test_set_pairs_drops_choices_for_changed_files(qtbot, loaded, pairs, tmp_path):
    p1, p2, p3 = pairs
    loaded.choose(p2.key, KEEP_RAW)
    loaded.choose(p3.key, KEEP_JPEG)
    changed = dataclasses.replace(
        p2, jpeg=dataclasses.replace(p2.jpeg, mtime_ns=p2.jpeg.mtime_ns + 1)
    )
    assert changed.key == p2.key
    loaded.set_pairs([p1, changed, p3], tmp_path)
    assert loaded.choices() == {p3.key: KEEP_JPEG}
    assert loaded.footer_label.text() == "1 chosen · frees 24.0 MB"


def test_headings_upper_case_and_root_name(qtbot, view, tmp_path):
    canon = _pair(1, "Canon", "IMG_0001")
    top = Pair(
        "", "IMG_0009", _ref(91, "IMG_0009.CR3", MB), _ref(92, "IMG_0009.JPG", MB), None
    )
    root = tmp_path / "Photos"
    view.set_pairs([top, canon], root)
    assert _rows(view) == [
        ("PHOTOS", None),
        ("IMG_0009", top.key),
        ("CANON", None),
        ("IMG_0001", canon.key),
    ]
    view.set_scope("")
    assert top.key in [k for _, k in _rows(view)]
    view.set_scope("canon")
    assert top.key not in [k for _, k in _rows(view)]
    view.set_scope(None)
    assert top.key in [k for _, k in _rows(view)]
    view.list.viewport().repaint()
    view.set_pairs([top], Path("E:/"))
    assert _rows(view)[0] == ("E:/", None)


def test_confirm_leave(qtbot, loaded, pairs, monkeypatch):
    asked = _answer(monkeypatch, QMessageBox.StandardButton.No)
    assert loaded.confirm_leave() is True
    assert asked == []
    loaded.choose(pairs[0].key, KEEP_RAW)
    assert loaded.confirm_leave() is False
    assert LEAVE.format(n=1) in asked[0]
    assert loaded.choices() == {pairs[0].key: KEEP_RAW}
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    assert loaded.confirm_leave() is True


def test_progress_text(qtbot, loaded):
    loaded.set_progress(412, 656)
    assert loaded.status.text() == "Checking pictures… 412 / 656"
    loaded.set_progress(656, 656)
    assert loaded.status.text() == ""


def test_pair_dialog_does_not_crash(qtbot, loaded, pairs, tmp_path):
    dialog = PairDialog(loaded, pairs[0], tmp_path, KEEP_RAW)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.buttons[KEEP_JPEG].click()
    assert loaded.choices() == {pairs[0].key: KEEP_JPEG}
    dialog.wait()


def test_checker_reports_cached_and_new(qtbot, tmp_path, monkeypatch, pairs):
    def fake(pairs_, root, cache, on_result, cancelled=lambda: False, batch=50):
        assert root == tmp_path and cache.path == tmp_path / "checks.json"
        on_result(pairs_[0].key, MATCH)
        on_result(pairs_[1].key, DIFFERENT)

    monkeypatch.setattr(checker_module, "check_pairs", fake)
    checker = PairChecker()
    checked, progress = [], []
    checker.checked.connect(lambda k, v: checked.append((k, v)))
    checker.progress.connect(lambda d, t: progress.append((d, t)))
    with qtbot.waitSignal(checker.finished, timeout=5000):
        checker.start(pairs[:2], tmp_path, tmp_path / "checks.json")
    assert checked == [(pairs[0].key, MATCH), (pairs[1].key, DIFFERENT)]
    assert progress[-1] == (2, 2)


def test_checker_drops_cancelled_results(qtbot, tmp_path, monkeypatch, pairs):
    import threading

    started, gate = threading.Event(), threading.Event()
    seen_cancelled = []

    def fake(pairs_, root, cache, on_result, cancelled=lambda: False, batch=50):
        started.set()
        gate.wait(5)
        seen_cancelled.append(cancelled())  # asserted on the test thread
        on_result(pairs_[0].key, MATCH)

    monkeypatch.setattr(checker_module, "check_pairs", fake)
    checker = PairChecker()
    seen = []
    checker.checked.connect(lambda k, v: seen.append(k))
    checker.finished.connect(lambda: seen.append("finished"))
    checker.start(pairs[:1], tmp_path, tmp_path / "checks.json")
    assert started.wait(5)  # the run is under way before it is cancelled
    checker.cancel()
    gate.set()
    assert checker.wait(5000)
    qtbot.wait(50)
    assert seen_cancelled == [True]
    assert seen == []


def test_checker_reports_failure(qtbot, tmp_path, monkeypatch, pairs):
    def fake(pairs_, root, cache, on_result, cancelled=lambda: False, batch=50):
        on_result(pairs_[0].key, MATCH)
        raise FileNotFoundError("exiftool not found")

    monkeypatch.setattr(checker_module, "check_pairs", fake)
    checker = PairChecker()
    finished = []
    checker.finished.connect(lambda: finished.append(True))
    with qtbot.waitSignal(checker.failed, timeout=5000) as blocker:
        checker.start(pairs[:2], tmp_path, tmp_path / "checks.json")
    assert blocker.args == ["exiftool not found"]
    qtbot.wait(50)
    assert finished == []


def test_checker_failure_of_a_cancelled_run_is_dropped(
    qtbot, tmp_path, monkeypatch, pairs
):
    import threading

    started, gate = threading.Event(), threading.Event()

    def fake(pairs_, root, cache, on_result, cancelled=lambda: False, batch=50):
        started.set()
        gate.wait(5)
        raise OSError("drive gone")

    monkeypatch.setattr(checker_module, "check_pairs", fake)
    checker = PairChecker()
    failed = []
    checker.failed.connect(failed.append)
    checker.start(pairs[:1], tmp_path, tmp_path / "checks.json")
    assert started.wait(5)
    checker.cancel()
    gate.set()
    assert checker.wait(5000)
    qtbot.wait(50)
    assert failed == []


def test_unchecked_rows_while_checking(qtbot, loaded, pairs):
    assert loaded.status_for(pairs[0].key) == (STATUS[None], False)
    loaded.set_verdict(pairs[0].key, DIFFERENT)
    assert loaded.status_for(pairs[0].key) == (STATUS[DIFFERENT], True)
    loaded.set_verdict(pairs[1].key, MATCH)
    assert loaded.status_for(pairs[1].key) == ("", False)


def test_check_failure_marks_unchecked_rows(qtbot, loaded, pairs):
    loaded.set_verdict(pairs[0].key, MATCH)
    loaded.set_progress(1, 3)
    loaded.check_failed("exiftool not found")
    assert loaded.status.text() == CHECK_FAILED.format(reason="exiftool not found")
    assert loaded.status_for(pairs[0].key) == ("", False)
    assert loaded.status_for(pairs[1].key) == (STATUS[UNREADABLE], True)
    loaded.list.viewport().repaint()
    # A new load starts a new check: back to "checking…".
    loaded.set_pairs(pairs, loaded._root)
    assert loaded.status_for(pairs[1].key) == (STATUS[None], False)


def test_check_finished_never_leaves_checking(qtbot, loaded, pairs):
    loaded.set_progress(0, 3)
    loaded.check_finished()
    assert loaded.status.text() == ""
    assert loaded.status_for(pairs[2].key) == (STATUS[UNREADABLE], True)
