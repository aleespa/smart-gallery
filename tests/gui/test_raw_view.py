import dataclasses

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from smart_gallery.gui.storage import raw_view
from smart_gallery.gui.storage.raw_view import (
    BIN_WARNING,
    CONFIRM,
    DROPPED,
    FOOTER,
    FOOTER_FOLDERS,
    SCOPE_ALL,
    SCOPE_ONE,
    RawView,
)
from smart_gallery.storage.pairs import FileRef
from smart_gallery.storage.raw_jpeg import (
    RawCandidate,
    estimate_jpeg_bytes,
    replacement_entries,
)
from smart_gallery.storage.tree import format_bytes

MB = 1024 * 1024
EDITED_ROLE = raw_view.EDITED_ROLE
PICKED_ROLE = raw_view.PICKED_ROLE


class _Ctx(QObject):
    gallery_changed = Signal()

    def open_repo(self):
        raise FileNotFoundError("no catalog in tests")


def _cand(i, folder, size=20 * MB, sidecar=False):
    stem = f"IMG_{i:04d}"
    rel = f"{folder}/{stem}.CR3"
    raw = FileRef(i, rel, ".cr3", size, 1000 + i, "image")
    side = FileRef(i + 500, f"{folder}/{stem}.xmp", ".xmp", 100, 1, "other")
    return RawCandidate(folder, stem, raw, side if sidecar else None, i)


def _cands():
    return [
        _cand(1, "a"),
        _cand(2, "a"),
        _cand(3, "a", sidecar=True),
        _cand(4, "b/x"),
        _cand(5, "b/x"),
    ]


@pytest.fixture
def view(qtbot, tmp_path):
    v = RawView(_Ctx())
    qtbot.addWidget(v)
    v.set_candidates(_cands(), tmp_path)
    return v


def _answer(monkeypatch, button):
    asked = []

    def question(*args, **kwargs):
        asked.append(args)
        return button

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def _key(i, folder):
    return _cand(i, folder).key


def _center(view, key):
    row = view.model.row_of(key)
    return view.list.visualRect(view.model.index(row)).center()


def test_grid_shows_scope_only(view):
    assert view.model.rowCount() == 5
    assert view.scope_label.text() == SCOPE_ALL.format(
        n=5, size=format_bytes(sum(c.size_bytes for c in _cands()))
    )
    view.set_scope("b")
    assert view.scope() == "b"
    assert view.model.rowCount() == 2
    two = [c for c in _cands() if c.folder == "b/x"]
    assert view.scope_label.text() == SCOPE_ONE.format(
        folder="b", n=2, size=format_bytes(sum(c.size_bytes for c in two))
    )
    assert view.show_all.isVisibleTo(view)


def test_click_toggles_and_footer_counts(view, qtbot):
    view.show()
    view.set_scope("b")
    k4, k5 = _key(4, "b/x"), _key(5, "b/x")
    QTest.mouseClick(
        view.list.viewport(), Qt.MouseButton.LeftButton, pos=_center(view, k4)
    )
    QTest.mouseClick(
        view.list.viewport(), Qt.MouseButton.LeftButton, pos=_center(view, k5)
    )
    assert view.picks() == {k4, k5}
    picked = [c for c in _cands() if c.key in view.picks()]
    raw = 40 * MB
    assert view.footer_label.text() == FOOTER.format(
        n=2,
        raw=format_bytes(raw),
        jpeg=format_bytes(estimate_jpeg_bytes(picked)),
        freed=format_bytes(raw - estimate_jpeg_bytes(picked)),
    )
    assert view.footer_label.text().startswith("2 picked · 40.0 MB of CR3 → about ")
    assert view.convert_button.text() == "Convert 2 and recycle the CR3s"
    view.toggle(k4)
    assert view.picks() == {k5}


def test_shift_click_picks_range(view):
    view.show()
    k = [_key(i, "a") for i in (1, 2, 3)]
    QTest.mouseClick(
        view.list.viewport(), Qt.MouseButton.LeftButton, pos=_center(view, k[0])
    )
    QTest.mouseClick(
        view.list.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        _center(view, k[2]),
    )
    assert view.picks() == set(k)


def test_pick_range_without_anchor_picks_one(view):
    view.pick_range(_key(2, "a"))
    assert view.picks() == {_key(2, "a")}


def test_ctrl_a_picks_scope_and_esc_clears_scope(view):
    view.show()
    view.toggle(_key(1, "a"))
    view.set_scope("b")
    view.list.setFocus()
    QTest.keyClick(view.list, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert view.picks() == {_key(1, "a"), _key(4, "b/x"), _key(5, "b/x")}
    QTest.keyClick(view.list, Qt.Key.Key_Escape)
    assert view.picks() == {_key(1, "a")}
    view.clear_picks()
    assert view.picks() == set()
    view.pick_all_in_scope()
    assert len(view.picks()) == 2
    view.clear_picks_in_scope()
    assert view.picks() == set()


def test_footer_mentions_other_folders(view):
    view.toggle(_key(1, "a"))
    assert FOOTER_FOLDERS.format(k=1) not in view.footer_label.text()
    view.set_scope("b")
    assert view.footer_label.text().endswith(FOOTER_FOLDERS.format(k=1))


def test_reload_drops_changed_picks_and_says_so(view, tmp_path):
    cands = _cands()
    view.toggle(cands[0].key)
    view.toggle(cands[1].key)
    view.toggle(cands[3].key)
    changed = dataclasses.replace(
        cands[0], raw=dataclasses.replace(cands[0].raw, mtime_ns=1)
    )
    # cands[1] left the catalog (converted): dropped without a notice
    view.set_candidates([changed, *cands[2:]], tmp_path)
    assert view.picks() == {cands[3].key}
    assert DROPPED.format(n=1) in view.scope_label.text()
    view.toggle(cands[4].key)
    assert DROPPED.format(n=1) not in view.scope_label.text()


def test_confirm_emits_cr3_refs(view, monkeypatch, qtbot):
    cands = _cands()
    view.toggle(cands[0].key)
    view.toggle(cands[3].key)
    monkeypatch.setattr(raw_view, "recycle_bin_capacity", lambda p: None)
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    with qtbot.waitSignal(view.convert_requested) as sig:
        view.convert_button.click()
    assert sig.args[0] == replacement_entries([cands[0], cands[3]])
    assert asked[0][2] == CONFIRM.format(n=2, size=format_bytes(40 * MB))


def test_confirm_declined_emits_nothing(view, monkeypatch, qtbot):
    view.toggle(_key(1, "a"))
    monkeypatch.setattr(raw_view, "recycle_bin_capacity", lambda p: None)
    _answer(monkeypatch, QMessageBox.StandardButton.No)
    with qtbot.assertNotEmitted(view.convert_requested):
        view.convert_button.click()


def test_confirm_warns_over_bin_limit(view, monkeypatch, tmp_path):
    view.toggle(_key(1, "a"))
    monkeypatch.setattr(raw_view, "recycle_bin_capacity", lambda p: MB)
    asked = _answer(monkeypatch, QMessageBox.StandardButton.No)
    view.convert_button.click()
    text = asked[0][2]
    assert BIN_WARNING.format(drive=view.root_name(), limit=format_bytes(MB)) in text
    assert text.startswith(CONFIRM.format(n=1, size=format_bytes(20 * MB)))


def test_double_click_opens_viewer_and_space_toggles(view, qtbot):
    view.show()
    key = _key(1, "a")
    QTest.mouseDClick(
        view.list.viewport(), Qt.MouseButton.LeftButton, pos=_center(view, key)
    )
    viewer = view.viewer
    assert viewer is not None and viewer.isVisible()
    qtbot.addWidget(viewer)
    assert viewer.row().relpath == _cands()[0].raw.relpath
    QTest.keyClick(viewer, Qt.Key.Key_Space)
    assert view.picks() == {key}
    assert "Picked" in viewer.windowTitle()
    QTest.keyClick(viewer, Qt.Key.Key_Space)
    assert view.picks() == set()
    assert "Not picked" in viewer.windowTitle()
    viewer.close()


def test_edited_badge_drawn_for_sidecar(view):
    plain = view.model.index(view.model.row_of(_key(1, "a")))
    edited = view.model.index(view.model.row_of(_key(3, "a")))
    assert plain.data(EDITED_ROLE) is False
    assert edited.data(EDITED_ROLE) is True
    assert edited.data(PICKED_ROLE) is False
    view.toggle(_key(3, "a"))
    assert edited.data(PICKED_ROLE) is True


def test_thumbnails_request_through_ctx(qtbot, tmp_path):
    class _Thumbs(QObject):
        ready = Signal(str, int, str)

        def __init__(self):
            super().__init__()
            self.calls = []

        def request(self, *args):
            self.calls.append(args)

    ctx = _Ctx()
    ctx.thumbs = _Thumbs()
    v = RawView(ctx)
    qtbot.addWidget(v)
    v.set_candidates(_cands(), tmp_path)
    c = _cands()[0]
    assert v.thumbnail(c.raw) is None
    assert ctx.thumbs.calls == [
        (tmp_path, c.raw.id, c.raw.mtime_ns, c.raw.relpath, c.raw.ext, "image")
    ]
    v.thumbnail(c.raw)
    assert len(ctx.thumbs.calls) == 1
    v.forget_thumb_requests()
    v.thumbnail(c.raw)
    assert len(ctx.thumbs.calls) == 2


def test_enter_opens_viewer_on_current_cell(view):
    view.show()
    key = _key(2, "a")
    QTest.mouseClick(
        view.list.viewport(), Qt.MouseButton.LeftButton, pos=_center(view, key)
    )
    QTest.keyClick(view.list, Qt.Key.Key_Return)
    assert view.viewer.isVisible()
    assert view.viewer.row().relpath == _cands()[1].raw.relpath
    view.viewer.close()


def test_viewer_arrows_step_and_space_toggles_shown(view):
    view.show()
    viewer = view.open_viewer(_key(1, "a"))
    QTest.keyClick(viewer, Qt.Key.Key_Right)
    assert viewer.row().relpath == _cands()[1].raw.relpath
    QTest.keyClick(viewer, Qt.Key.Key_Space)
    assert view.picks() == {_key(2, "a")}
    QTest.keyClick(viewer, Qt.Key.Key_Left)
    assert viewer.row().relpath == _cands()[0].raw.relpath
    viewer.close()


def test_reload_closes_open_viewer(view, tmp_path):
    viewer = view.open_viewer(_key(1, "a"))
    view.set_candidates(_cands(), tmp_path)
    assert not viewer.isVisible()
    assert view.viewer is None
