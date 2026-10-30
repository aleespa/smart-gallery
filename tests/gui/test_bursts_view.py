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
