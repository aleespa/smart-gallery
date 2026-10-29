"""The People page's right side: one person, several people, hidden faces."""

import numpy as np
import pytest
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QInputDialog, QMessageBox

from smart_gallery.config import drive_root_of
from smart_gallery.gui import paths
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.people.list_view import PERSON_ROLE
from smart_gallery.gui.people.view import PeopleView
from smart_gallery.services.label_faces import LabelEntry

_BLOB = b"\x00" * 8


class _Jobs:
    def __init__(self):
        self.specs = []

    def start(self, spec):
        self.specs.append(spec)
        return True


class Cat:
    """A small face catalog: photos, people and faces with states and scores."""

    def __init__(self, repo, make_item):
        self.repo = repo
        self.make_item = make_item
        self.n = 0

    def photo(self, date="2026-06-01"):
        self.n += 1
        relpath = f"p{self.n}.jpg"
        self.repo.upsert_many([self.make_item(relpath, date_taken=date)])
        return self.repo.conn.execute(
            "SELECT id FROM media_items WHERE relpath=?", (relpath,)
        ).fetchone()[0]

    def person(self, name=None, pid=None, hidden=False):
        cur = self.repo.conn.execute(
            "INSERT INTO persons (id, name, hidden) VALUES (?,?,?)",
            (pid, name, int(hidden)),
        )
        self.repo.conn.commit()
        return cur.lastrowid

    def face(
        self, media, person=None, state="auto", score=None, suggested=None, emb=None
    ) -> int:
        cur = self.repo.conn.execute(
            "INSERT INTO faces (media_id, bbox_x, bbox_y, bbox_w, bbox_h,"
            " embedding, person_id, state, suggested_person_id, match_score)"
            " VALUES (?,1,2,3,4,?,?,?,?,?)",
            (
                media,
                _BLOB if emb is None else emb.tobytes(),
                person,
                state,
                suggested,
                score,
            ),
        )
        self.repo.conn.commit()
        return cur.lastrowid


@pytest.fixture
def cat(repo, make_item):
    return Cat(repo, make_item)


@pytest.fixture(autouse=True)
def _faces_extra(monkeypatch):
    monkeypatch.setattr(paths, "faces_available", lambda: True)


def _ctx(repo, jobs=None):
    ctx = AppContext()
    ctx.jobs = jobs
    ctx.set_gallery(Gallery("uuid", str(repo.db_path), "test"))
    return ctx


def _view(qtbot, ctx):
    view = PeopleView(ctx)
    qtbot.addWidget(view)
    qtbot.waitUntil(lambda: view.loaded)
    return view


def _root(repo):
    return str(drive_root_of(repo.db_path))


def _select(view, *pids):
    lst = view.list.list
    lst.clearSelection()
    for pid in pids:  # in click order
        for i in range(lst.count()):
            row = lst.item(i).data(PERSON_ROLE)
            if row is not None and row.id == pid:
                lst.item(i).setSelected(True)


def _open_person(qtbot, view, pid):
    page = view.person_page
    page.loaded = False
    _select(view, pid)
    assert view.right.currentWidget() is page
    qtbot.waitUntil(lambda: page.loaded)
    return page


def _open_faces(qtbot, page):
    page.faces_loaded = False
    page.faces_button.click()
    assert page.body.currentWidget() is page.faces_page
    qtbot.waitUntil(lambda: page.faces_loaded)


def _pick(grid, *face_ids):
    model = grid.model()
    selection = grid.selectionModel()
    selection.clearSelection()
    for i in range(model.rowCount()):
        index = model.index(i)
        if grid.tile(i).face_id in face_ids:
            selection.select(index, QItemSelectionModel.SelectionFlag.Select)


def _grid_ids(grid):
    return [grid.tile(i).face_id for i in range(grid.model().rowCount())]


def _menu_action(bar, text):
    for act in bar.move_menu.actions():
        if act.text() == text:
            return act
    raise AssertionError([a.text() for a in bar.move_menu.actions()])


def test_person_page_header_and_photos(qtbot, repo, cat):
    alice = cat.person("Alice")
    cat.face(cat.photo("2024-03-01"), alice)
    cat.face(cat.photo("2026-05-01"), alice)
    cat.face(cat.photo("2025-01-01"), alice)
    cat.photo("2026-07-01")  # nobody in it
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    assert page.name_label.text() == "Alice"
    assert not page.name_label.isHidden()
    assert page.name_edit.isHidden()
    assert page.subtitle.text() == "3 photos · 2024–2026"
    assert not page.rename_button.isHidden()
    assert not page.library_button.isHidden()
    assert page.photos_button.isChecked()
    assert page.body.currentWidget() is page.photos
    assert page.suggest_strip.isHidden()
    rows = page.photos.model.rows
    assert [r.relpath for r in rows] == ["p2.jpg", "p3.jpg", "p1.jpg"]
    # No one selected: the hint again.
    _select(view)
    assert view.right.currentWidget() is view.message_page


def test_switch_to_faces_lists_least_sure_first(qtbot, repo, cat):
    alice = cat.person("Alice")
    sure = cat.face(cat.photo("2024-01-01"), alice, score=0.9)
    unsure = cat.face(cat.photo("2026-01-01"), alice, score=0.5)
    mine = cat.face(cat.photo("2025-01-01"), alice, state="user")
    unscored = cat.face(cat.photo("2023-01-01"), alice)
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    _open_faces(qtbot, page)
    assert page.sort.currentData() == "least_sure"
    assert _grid_ids(page.faces_grid) == [unscored, unsure, sure, mine]
    assert page.faces_grid.tile(3).state == "user"
    page.faces_loaded = False
    page.sort.setCurrentIndex(page.sort.findData("newest"))
    qtbot.waitUntil(lambda: page.faces_loaded)
    assert _grid_ids(page.faces_grid) == [unsure, mine, sure, unscored]


def test_move_to_emits_move_entry(qtbot, repo, cat):
    alice = cat.person("Alice")
    bob = cat.person("Bob")
    group = cat.person(None)
    f1 = cat.face(cat.photo(), alice)
    f2 = cat.face(cat.photo(), alice)
    cat.face(cat.photo(), bob)
    cat.face(cat.photo(), group)
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    _open_faces(qtbot, page)
    sent = []
    page.labels.connect(sent.append)
    bar = page.action_bar
    assert not bar.move_button.isEnabled()
    _pick(page.faces_grid, f1, f2)
    assert bar.count_label.text() == "2 faces selected"
    assert bar.move_button.text() == "Move to… ▾"
    texts = [a.text() for a in bar.move_menu.actions() if a.text()]
    assert texts == ["Bob", "Unnamed · 1 photo", "New person…"]
    _menu_action(bar, "Bob").trigger()
    assert sent == [[LabelEntry("move", (f1, f2), person_id=bob)]]
    # PeopleView keeps them for sending (Task 12 batches them into a job).
    assert view._pending == [LabelEntry("move", (f1, f2), person_id=bob)]


def test_not_person_emits_reject_entry(qtbot, repo, cat):
    alice = cat.person("Alice")
    f1 = cat.face(cat.photo(), alice)
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    _open_faces(qtbot, page)
    page.labels.disconnect(view.send_labels)  # keep the tiles for a 2nd click
    sent = []
    page.labels.connect(sent.append)
    _pick(page.faces_grid, f1)
    bar = page.action_bar
    assert bar.count_label.text() == "1 face selected"
    assert bar.not_button.text() == "Not Alice"
    bar.not_button.click()
    bar.ignore_button.click()
    assert bar.ignore_button.text() == "Ignore"
    assert sent == [
        [LabelEntry("reject", (f1,), person_id=alice)],
        [LabelEntry("ignore", (f1,))],
    ]


def test_new_person_emits_new_person_entry(qtbot, repo, cat, monkeypatch):
    alice = cat.person("Alice")
    f1 = cat.face(cat.photo(), alice)
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    _open_faces(qtbot, page)
    sent = []
    page.labels.connect(sent.append)
    _pick(page.faces_grid, f1)
    answer = [("", False)]
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: answer[0])
    _menu_action(page.action_bar, "New person…").trigger()
    assert sent == []
    answer[0] = (" Carol ", True)
    _menu_action(page.action_bar, "New person…").trigger()
    assert sent == [[LabelEntry("new_person", (f1,), name="Carol")]]


def test_review_confirm_all_emits_confirm_entry(qtbot, repo, cat):
    alice = cat.person("Alice")
    cat.face(cat.photo(), alice)
    s1 = cat.face(cat.photo(), None, "suggested", score=0.8, suggested=alice)
    s2 = cat.face(cat.photo(), None, "suggested", score=0.6, suggested=alice)
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    assert not page.suggest_strip.isHidden()
    assert page.suggest_label.text() == "Is this Alice? 2 suggestions"
    page.labels.disconnect(view.send_labels)  # keep the tiles for a 2nd click
    sent = []
    page.labels.connect(sent.append)
    page.review_button.click()
    assert page.body.currentWidget() is page.review_page
    assert _grid_ids(page.review_grid) == [s1, s2]
    assert page.confirm_button.text() == "✓"
    assert page.reject_button.text() == "✕"
    _pick(page.review_grid, s2)
    page.reject_button.click()
    page.confirm_all_button.click()
    assert page.confirm_all_button.text() == "✓ all"
    assert page.reject_all_button.text() == "✕ all"
    assert sent == [
        [LabelEntry("reject", (s2,), person_id=alice)],
        [LabelEntry("confirm", (s1, s2), person_id=alice)],
    ]
    page.photos_button.click()
    assert page.body.currentWidget() is page.photos


def test_add_name_on_group_runs_name_person(qtbot, repo, cat, monkeypatch):
    cat.person("Alice", pid=3)
    cat.face(cat.photo(), 3)
    cat.person(None, pid=7)
    cat.face(cat.photo(), 7)
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    page = _open_person(qtbot, view, 7)
    assert page.name_label.isHidden()
    assert not page.name_edit.isHidden()
    assert page.name_edit.placeholderText() == "Add a name"
    assert page.rename_button.isHidden()
    page.name_edit.setText(" Dana ")
    page.name_edit.returnPressed.emit()
    root = _root(repo)
    assert ctx.jobs.specs[-1].argvs == (("name-person", root, "7", "--", "Dana"),)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    page.name_edit.setText("alice")
    page.name_edit.returnPressed.emit()
    assert ctx.jobs.specs[-1].argvs == (
        ("name-person", root, "7", "--merge", "--", "Alice"),
    )


def test_several_people_all_and_any_counts(qtbot, repo, cat):
    alice = cat.person("Alice")
    bob = cat.person("Bob")
    p1, p2, p3 = cat.photo(), cat.photo(), cat.photo()
    cat.face(p1, alice)
    cat.face(p2, alice)
    cat.face(p2, bob)
    cat.face(p3, bob)
    view = _view(qtbot, _ctx(repo))
    page = view.several_page
    page.loaded = False
    _select(view, alice, bob)
    assert view.right.currentWidget() is page
    qtbot.waitUntil(lambda: page.loaded)
    assert page.all_button.isChecked()
    assert page.match == "all"
    assert page.count.text() == "1 photo"
    assert [r.relpath for r in page.photos.model.rows] == ["p2.jpg"]
    page.loaded = False
    page.any_button.click()
    qtbot.waitUntil(lambda: page.loaded)
    assert page.match == "any"
    assert page.count.text() == "3 photos"


def test_show_in_library_sets_filters_and_navigates(qtbot, repo, cat):
    alice = cat.person("Alice")
    bob = cat.person("Bob")
    p1 = cat.photo()
    cat.face(p1, alice)
    cat.face(p1, bob)
    ctx = _ctx(repo)
    ctx.set_filters(ctx.filters.__class__(cameras=["Canon EOS R6"]))
    went = []
    ctx.navigate.connect(went.append)
    view = _view(qtbot, ctx)
    page = view.several_page
    page.loaded = False
    _select(view, bob, alice)
    qtbot.waitUntil(lambda: page.loaded)
    page.any_button.click()
    assert page.library_button.isEnabled()
    page.library_button.click()
    assert ctx.filters.people == ["Bob", "Alice"]
    assert ctx.filters.people_match == "any"
    assert ctx.filters.cameras == ["Canon EOS R6"]
    assert went == ["library"]

    person = _open_person(qtbot, view, alice)
    person.library_button.click()
    assert ctx.filters.people == ["Alice"]
    assert went == ["library", "library"]


def test_show_in_library_disabled_with_unnamed_group(qtbot, repo, cat):
    alice = cat.person("Alice")
    group = cat.person(None)
    p1 = cat.photo()
    cat.face(p1, alice)
    cat.face(p1, group)
    view = _view(qtbot, _ctx(repo))
    page = view.several_page
    page.loaded = False
    _select(view, alice, group)
    qtbot.waitUntil(lambda: page.loaded)
    assert not page.library_button.isEnabled()
    assert page.count.text() == "1 photo"
    person = _open_person(qtbot, view, group)
    assert person.library_button.isHidden()


def test_hidden_faces_view_and_unhide_job(qtbot, repo, cat):
    alice = cat.person("Alice")
    cat.face(cat.photo(), alice)
    tiny = cat.face(cat.photo("2026-02-01"), None, "hidden")
    ignored = cat.face(cat.photo("2025-02-01"), None, "ignored")
    strangers = cat.person(None, pid=9, hidden=True)
    cat.face(cat.photo(), strangers, "ignored")
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    _select(view, alice)
    page = view.hidden_page
    page.loaded = False
    view.list.show_hidden.click()
    assert view.list.selected_ids() == []
    assert view.right.currentWidget() is page
    qtbot.waitUntil(lambda: page.loaded)
    assert _grid_ids(page.faces_grid) == [tiny, ignored]
    bar = page.action_bar
    assert bar.not_button.isHidden()
    assert bar.ignore_button.isHidden()
    assert not bar.move_button.isHidden()
    sent = []
    page.labels.connect(sent.append)
    _pick(page.faces_grid, ignored)
    _menu_action(bar, "Alice").trigger()
    assert sent == [[LabelEntry("move", (ignored,), person_id=alice)]]
    # The hidden group: only "Show again", no naming or split.
    assert list(page.unhide_buttons) == [strangers]
    button = page.unhide_buttons[strangers]
    assert button.text() == "Show again"
    button.click()
    assert ctx.jobs.specs[-1].argvs == (
        ("hide-person", _root(repo), str(strangers), "--unhide"),
    )
    # A reload keeps the hidden view up.
    page.loaded = False
    view.loaded = False
    ctx.catalog_updated.emit()
    qtbot.waitUntil(lambda: view.loaded and page.loaded)
    assert view.right.currentWidget() is page


def test_old_catalog_without_faces_extra_shows_install_text(qtbot, repo, monkeypatch):
    monkeypatch.setattr(paths, "faces_available", lambda: False)
    repo.conn.execute("DROP TABLE faces")
    repo.conn.execute(
        "CREATE TABLE faces (id INTEGER PRIMARY KEY, media_id INTEGER,"
        " person_id INTEGER)"
    )
    repo.conn.commit()
    from smart_gallery.gui.people.view import NO_EXTRA

    view = _view(qtbot, _ctx(repo, _Jobs()))
    assert view.message.text() == NO_EXTRA
    assert view.message_action.isHidden()


def test_face_grid_shows_crops_when_ready(qtbot, repo, cat, tmp_path):
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QPixmap

    from smart_gallery.gui.people.faces_grid import FaceGrid
    from smart_gallery.gui.people.model import person_faces

    class Crops(QObject):
        ready = Signal(str, int, str)

        def __init__(self):
            super().__init__()
            self.ids = []

        def request(self, root, face_id, *_args):
            self.ids.append(face_id)

    alice = cat.person("Alice")
    mine = cat.face(cat.photo(), alice, state="user")
    other = cat.face(cat.photo(), alice)
    ctx = _ctx(repo)
    ctx.face_crops = Crops()
    grid = FaceGrid(ctx)
    qtbot.addWidget(grid)
    grid.resize(400, 300)
    grid.show()
    root = drive_root_of(repo.db_path)
    grid.set_tiles(person_faces(repo, alice, "least_sure"), root)
    qtbot.waitUntil(lambda: sorted(ctx.face_crops.ids) == sorted([mine, other]))
    placeholder = grid.model().index(0).data(Qt.ItemDataRole.DecorationRole)
    crop = tmp_path / "crop.png"
    pixmap = QPixmap(40, 60)
    pixmap.fill()
    assert pixmap.save(str(crop))
    ctx.face_crops.ready.emit(str(root), other, str(crop))
    ctx.face_crops.ready.emit(str(root), mine, "")  # no crop: placeholder stays
    shown = grid.model().index(0).data(Qt.ItemDataRole.DecorationRole)
    assert grid.tile(0).face_id == other
    assert shown.cacheKey() != placeholder.cacheKey()
    kept = grid.model().index(1).data(Qt.ItemDataRole.DecorationRole)
    assert kept.cacheKey() == placeholder.cacheKey()
    grid.grab()  # paints the tick on the user's face without errors
    grid.remove_faces([other])
    assert [t.face_id for t in grid.tiles()] == [mine]


def test_face_grid_clicks_tick_faces_like_checkboxes(qtbot, repo, cat):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from smart_gallery.gui.people.faces_grid import FaceGrid, checkbox_rect
    from smart_gallery.gui.people.model import person_faces

    alice = cat.person("Alice")
    faces = [cat.face(cat.photo(), alice) for _ in range(3)]
    grid = FaceGrid(_ctx(repo))
    qtbot.addWidget(grid)
    grid.resize(400, 300)
    grid.show()
    grid.set_tiles(person_faces(repo, alice, "least_sure"))
    order = [t.face_id for t in grid.tiles()]

    def click(row, modifier=Qt.KeyboardModifier.NoModifier, on_box=False):
        rect = grid.visualRect(grid.model().index(row))
        point = checkbox_rect(rect).center().toPoint() if on_box else rect.center()
        QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, modifier, point)

    click(0, on_box=True)
    click(2)  # a plain click adds to the selection instead of replacing it
    assert grid.selected_face_ids() == [order[0], order[2]]
    click(0)  # and a second click unticks
    assert grid.selected_face_ids() == [order[2]]
    click(2, Qt.KeyboardModifier.ShiftModifier)  # Shift still selects a range
    assert sorted(grid.selected_face_ids()) == sorted(faces)
    grid.grab()  # paints the outline and the ticked checkboxes without errors


def test_people_list_selected_row_keeps_normal_text(qtbot, repo, cat):
    alice = cat.person("Alice")
    cat.face(cat.photo(), alice)
    view = _view(qtbot, _ctx(repo))
    _select(view, alice)
    view.list.list.grab()  # paints the accent wash and bar without errors


def _vec(sim):
    v = np.zeros(512, dtype=np.float32)
    v[0] = sim
    v[500] = np.sqrt(1 - sim * sim)
    return v


def _more_cat(cat):
    alice = cat.person("Alice")
    cat.face(cat.photo(), alice, "user", emb=_vec(1.0))
    lo = cat.face(cat.photo(), None, "new", emb=_vec(0.7))
    hi = cat.face(cat.photo(), None, "new", emb=_vec(0.9))
    return alice, hi, lo


def _find_more(qtbot, repo, cat):
    alice, hi, lo = _more_cat(cat)
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    page.find_more_button.click()
    qtbot.waitUntil(lambda: len(page.review_grid.tiles()) == 2)
    return view, page, alice, hi, lo


def test_find_more_shows_ranked_faces(qtbot, repo, cat):
    _view_, page, alice, hi, lo = _find_more(qtbot, repo, cat)
    assert page.body.currentWidget() is page.review_page
    assert page.review_mode == "more"
    assert page.review_title.text() == "Could this be Alice?"
    assert _grid_ids(page.review_grid) == [hi, lo]


def test_find_more_confirm_emits_label(qtbot, repo, cat):
    view, page, alice, hi, lo = _find_more(qtbot, repo, cat)
    page.labels.disconnect(view.send_labels)
    sent = []
    page.labels.connect(sent.append)
    _pick(page.review_grid, hi)
    page.confirm_button.click()
    assert sent == [[LabelEntry("confirm", (hi,), person_id=alice)]]


def test_find_more_survives_reload(qtbot, repo, cat):
    _view_, page, alice, hi, lo = _find_more(qtbot, repo, cat)
    cat.face(cat.photo(), None, "suggested", score=0.8, suggested=alice, emb=_vec(0.0))
    page.loaded = False
    page.show_person(alice)
    qtbot.waitUntil(lambda: page.loaded)
    qtbot.waitUntil(lambda: len(page.review_grid.tiles()) == 2)
    assert page.review_mode == "more"
    assert _grid_ids(page.review_grid) == [hi, lo]


def test_find_more_empty_message(qtbot, repo, cat):
    alice = cat.person("Alice")
    cat.face(cat.photo(), alice, "user", emb=_vec(1.0))
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    page.find_more_button.click()
    qtbot.waitUntil(
        lambda: page.review_title.text() == "No more faces that look like Alice."
    )
    assert page.review_grid.tiles() == []


def test_review_button_returns_to_suggestions(qtbot, repo, cat):
    alice, hi, lo = _more_cat(cat)
    s1 = cat.face(
        cat.photo(), None, "suggested", score=0.8, suggested=alice, emb=_vec(0.0)
    )
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    page.find_more_button.click()
    qtbot.waitUntil(lambda: len(page.review_grid.tiles()) == 2)
    page.review_button.click()
    assert page.review_mode == "suggestions"
    assert page.review_title.text() == "Is this Alice?"
    assert _grid_ids(page.review_grid) == [s1]


def test_find_more_failure_shows_message(qtbot, repo, cat, monkeypatch):
    alice, hi, lo = _more_cat(cat)
    view = _view(qtbot, _ctx(repo))
    page = _open_person(qtbot, view, alice)
    page.review_button.click()
    assert page.review_grid.tiles() == []  # no suggestions: nothing stale

    def boom(*_a, **_k):
        raise ValueError("bad blob")

    monkeypatch.setattr("smart_gallery.gui.people.model.find_more", boom)
    page.find_more_button.click()
    qtbot.waitUntil(lambda: "bad blob" in page.review_title.text())
    assert page.review_title.text() == "Couldn't search for more faces: bad blob"
    assert page.review_grid.tiles() == []
