import dataclasses

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from smart_gallery.gui.storage.bursts_view import (
    DROPPED,
    NOT_DECIDED,
    BurstsView,
)
from smart_gallery.storage.bursts import Shot
from smart_gallery.storage.pairs import FileRef

MB = 1024 * 1024


def _ref(i, relpath, size=MB):
    ext = "." + relpath.rsplit(".", 1)[1].lower()
    return FileRef(i, relpath, ext, size, 1000 + i, "image")


def _shot(i, folder, ts, exts=("JPG",)):
    stem = f"IMG_{i:04d}"
    files = tuple(_ref(i * 10 + n, f"{folder}/{stem}.{e}") for n, e in enumerate(exts))
    return Shot(folder, stem, ts, "Cam", files, None, None, None, None)


@pytest.fixture
def view(qtbot, tmp_path):
    v = BurstsView(object())
    qtbot.addWidget(v)
    v.set_shots(
        [
            _shot(1, "a", 0),
            _shot(2, "a", 1),
            _shot(3, "a", 2),
            _shot(4, "b/x", 0, ("CR3", "JPG")),
            _shot(5, "b/x", 1),
        ],
        tmp_path,
    )
    return v


def _answer(monkeypatch, button):
    asked = []

    def question(*args, **kwargs):
        asked.append(args)
        return button

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def test_rows_largest_first_and_scoped(view):
    bursts = view.bursts_in_scope()
    assert [len(b.shots) for b in bursts] == [3, 2]
    view.set_scope("b")
    assert [b.folder for b in view.bursts_in_scope()] == ["b/x"]
    assert view.scope_label.text().startswith("In b · 1 bursts · 2 photos · ")


def test_all_shots_start_kept(view):
    assert view.marks() == set()
    assert view.model.index(0).data(Qt.ItemDataRole.UserRole + 3) == NOT_DECIDED


def test_toggle_and_last_kept_refused(view):
    view.set_scope("b")
    s1, s2 = (s.key for s in view.bursts_in_scope()[0].shots)
    assert view.toggle(s1) is True
    assert view.toggle(s2) is False
    assert view.marks() == {s1}


def test_footer_and_move(view, monkeypatch, qtbot):
    burst = next(b for b in view.bursts_in_scope() if b.folder == "b/x")
    shot = burst.shots[0]
    view.toggle(shot.key)
    assert view.move_button.text() == "Move 2 files to Recycle Bin"
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    with qtbot.waitSignal(view.move_requested) as sig:
        view.move_button.click()
    assert sig.args[0] == list(shot.files)


def test_footer_counts_marks_outside_scope(view):
    burst = next(b for b in view.bursts_in_scope() if b.folder == "a")
    view.toggle(burst.shots[0].key)
    view.set_scope("b")
    assert view.footer_label.text().endswith("(in 1 folders)")
    assert [f.relpath for f in view.removals()] == [burst.shots[0].files[0].relpath]


def test_gap_change_regroups_and_confirms_with_marks(qtbot, tmp_path, monkeypatch):
    v = BurstsView(object())
    qtbot.addWidget(v)
    v.set_shots([_shot(1, "a", 0), _shot(2, "a", 3), _shot(3, "a", 6)], tmp_path)
    assert v.bursts_in_scope() == []
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    assert v.set_gap(3) is True
    assert asked == []
    assert len(v.bursts_in_scope()) == 1
    v.toggle(v.bursts_in_scope()[0].shots[0].key)
    assert v.set_gap(2) is True
    assert len(asked) == 1
    assert v.marks() == set()


def test_gap_change_declined_restores_value(view, monkeypatch):
    old = view.gap()
    view.toggle(view.bursts_in_scope()[0].shots[0].key)
    _answer(monkeypatch, QMessageBox.StandardButton.No)
    assert view.set_gap(old + 3) is False
    assert view.gap_box.value() == old
    assert len(view.marks()) == 1


def test_set_shots_reconciles_and_reports(view, tmp_path):
    burst = view.bursts_in_scope()[0]
    view.toggle(burst.shots[0].key)
    changed = []
    for b in view.bursts_in_scope():
        changed.extend(b.shots)
    changed = [
        (
            dataclasses.replace(
                s,
                files=tuple(dataclasses.replace(f, mtime_ns=1) for f in s.files),
            )
            if s.key == burst.shots[0].key
            else s
        )
        for s in changed
    ]
    view.set_shots(changed, tmp_path)
    assert view.marks() == set()
    assert DROPPED.format(n=1) in view.scope_label.text()


def test_confirm_leave(view, monkeypatch):
    asked = _answer(monkeypatch, QMessageBox.StandardButton.No)
    assert view.confirm_leave() is True
    assert asked == []
    view.toggle(view.bursts_in_scope()[0].shots[0].key)
    assert view.confirm_leave() is False


# ── Task 4: expanding a burst ────────────────────────────────────────────────
from PySide6.QtCore import QPoint  # noqa: E402

from smart_gallery.gui.storage import bursts_view as bv  # noqa: E402


@pytest.fixture
def shown(view, qtbot):
    view.resize(900, 700)
    view.show()
    qtbot.waitExposed(view)
    return view


def _rect(view, row):
    return view.list.visualRect(view.model.index(row))


def _click(qtbot, view, pos, double=False):
    vp = view.list.viewport()
    if double:
        # what a real double-click delivers: press, release, double-click, release
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=pos)
        qtbot.mouseDClick(vp, Qt.MouseButton.LeftButton, pos=pos)
        qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=pos)
    else:
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=pos)


def _row_pos(view, row):
    r = _rect(view, row)
    return QPoint(r.left() + 5, r.top() + 5)


def _cell_pos(view, row, n):
    burst = view.model.rows[row]
    d = view.list.itemDelegate()
    r = _rect(view, row)
    for y in range(r.top(), r.bottom(), 4):
        for x in range(r.left(), r.right(), 4):
            if d.shot_at(r, burst, QPoint(x, y)) == burst.shots[n].key:
                return QPoint(x, y)
    raise AssertionError("cell not found")


def test_click_row_expands_one_at_a_time(shown, qtbot):
    b0, b1 = shown.model.rows
    collapsed = shown.list.visualRect(shown.model.index(1)).height()
    _click(qtbot, shown, _row_pos(shown, 0))
    assert shown.expanded() == b0.key
    assert _rect(shown, 0).height() > collapsed
    _click(qtbot, shown, _row_pos(shown, 1))
    assert shown.expanded() == b1.key
    _click(qtbot, shown, _row_pos(shown, 1))
    assert shown.expanded() is None


def test_click_shot_toggles(shown, qtbot):
    shown.set_expanded(shown.model.rows[0].key)
    shot = shown.model.rows[0].shots[2]
    pos = _cell_pos(shown, 0, 2)
    _click(qtbot, shown, pos)
    assert shown.marks() == {shot.key}
    _click(qtbot, shown, pos)
    assert shown.marks() == set()


def test_review_button_and_double_click(shown, qtbot):
    burst = shown.model.rows[0]
    shown.set_expanded(burst.key)
    d = shown.list.itemDelegate()
    centre = d.review_rect(_rect(shown, 0)).center()
    with qtbot.waitSignal(shown.review_requested) as sig:
        _click(qtbot, shown, centre)
    assert sig.args == [burst.key, burst.shots[0].key]
    with qtbot.waitSignal(shown.review_requested) as sig:
        _click(qtbot, shown, _cell_pos(shown, 0, 1), double=True)
    assert sig.args == [burst.key, burst.shots[1].key]
    shown.list.setCurrentIndex(shown.model.index(1))
    with qtbot.waitSignal(shown.review_requested) as sig:
        qtbot.keyClick(shown.list, Qt.Key.Key_Return)
    other = shown.model.rows[1]
    assert sig.args == [other.key, other.shots[0].key]


def test_refused_toggle_shows_keep_one(shown, qtbot):
    burst = shown.model.rows[1]
    shown.set_expanded(burst.key)
    shown.toggle(burst.shots[0].key)
    shown.click_shot(burst.shots[1].key)
    assert shown.refused() == burst.shots[1].key
    qtbot.waitUntil(lambda: shown.refused() is None, timeout=3000)


def test_collapsed_strip_fills_the_row(shown):
    d = shown.list.itemDelegate()
    assert d.strip_count(900, 12) > 4
    assert d.strip_count(900, 3) == 3
    assert d.strip_count(10, 12) == 1


def test_clear_marks_clears_dropped_notice(view, tmp_path):
    burst = view.bursts_in_scope()[0]
    view.toggle(burst.shots[0].key)
    shots = [
        (
            dataclasses.replace(
                s, files=tuple(dataclasses.replace(f, mtime_ns=1) for f in s.files)
            )
            if s.key == burst.shots[0].key
            else s
        )
        for b in view.bursts_in_scope()
        for s in b.shots
    ]
    view.set_shots(shots, tmp_path)
    assert DROPPED.format(n=1) in view.scope_label.text()
    view.clear_marks()
    assert DROPPED.format(n=1) not in view.scope_label.text()


def test_constants():
    assert (bv.GRID_THUMB, bv.REVIEW, bv.KEEP_TAG, bv.REMOVE_TAG) == (
        150,
        "Review",
        "keep",
        "remove",
    )


def test_double_click_leaves_marks_unchanged(shown, qtbot):
    burst = shown.model.rows[0]
    shown.set_expanded(burst.key)
    with qtbot.waitSignal(shown.review_requested):
        _click(qtbot, shown, _cell_pos(shown, 0, 1), double=True)
    assert shown.marks() == set()
    # a click that would be refused leaves no notice behind either
    other = shown.model.rows[1]
    shown.set_expanded(other.key)
    shown.toggle(other.shots[0].key)
    with qtbot.waitSignal(shown.review_requested):
        _click(qtbot, shown, _cell_pos(shown, 1, 1), double=True)
    assert shown.marks() == {other.shots[0].key}
    assert shown.refused() is None


def test_expanded_height_follows_width(shown, qtbot):
    shown.set_expanded(shown.model.rows[0].key)
    wide = _rect(shown, 0).height()
    shown.resize(200, 700)
    qtbot.waitUntil(lambda: _rect(shown, 0).height() > wide)


def test_review_only_on_expanded_row(shown, qtbot):
    burst = shown.model.rows[0]
    centre = shown.list.itemDelegate().review_rect(_rect(shown, 0)).center()
    seen = []
    shown.review_requested.connect(lambda *a: seen.append(a))
    _click(qtbot, shown, centre)
    assert seen == []
    assert shown.expanded() == burst.key  # it was a plain row click
