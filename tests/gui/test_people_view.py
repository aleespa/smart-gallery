import json

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QInputDialog, QMessageBox

from smart_gallery.config import drive_root_of
from smart_gallery.gui import paths
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.jobspec import JOB_RUNNING
from smart_gallery.gui.people.list_view import HEADER_ROLE, PERSON_ROLE
from smart_gallery.services.label_faces import LabelEntry
from smart_gallery.gui.people.view import (
    FIND_TITLE,
    NEEDS_UPGRADE,
    NO_EXTRA,
    NO_FACES,
    NO_GALLERY,
    PENDING,
    PICK,
    PeopleView,
)

_BLOB = b"\x00" * 8


class _Jobs(QObject):
    finished = Signal(object)

    def __init__(self, result=True):
        super().__init__()
        self.result = result
        self.running = False
        self.specs = []

    def start(self, spec):
        self.specs.append(spec)
        return self.result


class Cat:
    def __init__(self, repo, make_item):
        self.repo = repo
        self.make_item = make_item
        self.n = 0

    def media(self):
        self.n += 1
        relpath = f"p{self.n}.jpg"
        self.repo.upsert_many([self.make_item(relpath)])
        return self.repo.conn.execute(
            "SELECT id FROM media_items WHERE relpath=?", (relpath,)
        ).fetchone()[0]

    def person(self, name=None, photos=1, hidden=False, pid=None, suggested=0):
        cur = self.repo.conn.execute(
            "INSERT INTO persons (id, name, hidden) VALUES (?,?,?)",
            (pid, name, int(hidden)),
        )
        person = cur.lastrowid
        for _ in range(photos):
            self.face(self.media(), person)
        for _ in range(suggested):
            self.face(self.media(), None, state="suggested", suggested=person)
        self.repo.conn.commit()
        return person

    def face(self, media, person, state="auto", suggested=None):
        self.repo.conn.execute(
            "INSERT INTO faces (media_id, bbox_x, bbox_y, bbox_w, bbox_h,"
            " embedding, person_id, state, suggested_person_id)"
            " VALUES (?,1,2,3,4,?,?,?,?)",
            (media, _BLOB, person, state, suggested),
        )
        self.repo.conn.commit()


@pytest.fixture
def cat(repo, make_item):
    return Cat(repo, make_item)


@pytest.fixture(autouse=True)
def _faces_extra(monkeypatch):
    monkeypatch.setattr(paths, "faces_available", lambda: True)


@pytest.fixture(autouse=True)
def _app_data(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_data_dir", lambda: tmp_path / "appdata")


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


def _reload(qtbot, view, signal):
    view.loaded = False
    signal()
    qtbot.waitUntil(lambda: view.loaded)


def _rows(view):
    out = []
    lst = view.list.list
    for i in range(lst.count()):
        item = lst.item(i)
        if lst.isRowHidden(i):
            continue
        header = item.data(HEADER_ROLE)
        if header is not None:
            out.append(header)
        else:
            row = item.data(PERSON_ROLE)
            out.append((item.text(), row.photos, row.suggestions))
    return out


def _item(view, pid):
    lst = view.list.list
    for i in range(lst.count()):
        row = lst.item(i).data(PERSON_ROLE)
        if row is not None and row.id == pid:
            return lst.item(i)
    raise AssertionError(pid)


def _root(repo):
    return str(drive_root_of(repo.db_path))


def test_list_shows_named_then_groups_with_counts(qtbot, repo, cat):
    cat.person("Bob", photos=1)
    cat.person("Alice", photos=3, suggested=2)
    cat.person(None, photos=2)
    cat.person(None, photos=1, hidden=True)
    view = _view(qtbot, _ctx(repo))
    assert _rows(view) == [
        "Named",
        ("Alice", 3, 2),
        ("Bob", 1, 0),
        "To name (1)",
        ("Unnamed", 2, 0),
    ]
    assert view.list.show_hidden.text() == "Show hidden faces"


def test_search_filters_rows(qtbot, repo, cat):
    cat.person("Alice")
    cat.person("Bob")
    cat.person(None)
    view = _view(qtbot, _ctx(repo))
    view.list.search.setText("LIC")
    assert _rows(view) == ["Named", ("Alice", 1, 0)]
    view.list.search.setText("")
    assert len(_rows(view)) == 5


def test_selection_survives_reload(qtbot, repo, cat):
    a = cat.person("Alice")
    b = cat.person("Bob")
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    seen = []
    view.list.selection_changed.connect(seen.append)
    _item(view, b).setSelected(True)
    _item(view, a).setSelected(True)
    assert seen[-1] == [b, a]
    cat.person("Carol", photos=5)
    _reload(qtbot, view, ctx.catalog_updated.emit)
    assert view.list.selected_ids() == [b, a]
    assert _rows(view)[1][0] == "Carol"


def test_find_faces_starts_job(qtbot, repo, cat):
    cat.person("Alice")
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    view.find_button.click()
    (spec,) = ctx.jobs.specs
    assert spec.title == FIND_TITLE
    assert spec.argvs == (("find-faces", _root(repo)),)


def test_find_faces_when_job_running_tells_user(qtbot, repo, cat, monkeypatch):
    cat.person("Alice")
    view = _view(qtbot, _ctx(repo, _Jobs(result=False)))
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda p, t, text: shown.append(text)
    )
    view.find_faces()
    assert shown == [JOB_RUNNING]


def test_old_catalog_shows_update_message(qtbot, repo):
    # A v4 catalog: faces without the state column, opened read-only.
    repo.conn.execute("DROP TABLE faces")
    repo.conn.execute(
        "CREATE TABLE faces (id INTEGER PRIMARY KEY, media_id INTEGER,"
        " person_id INTEGER)"
    )
    repo.conn.commit()
    view = _view(qtbot, _ctx(repo, _Jobs()))
    assert view.right.currentWidget() is view.message_page
    assert view.message.text() == NEEDS_UPGRADE
    assert not view.message_action.isHidden()
    view.message_action.click()
    assert view.ctx.jobs.specs[0].argvs[0][0] == "find-faces"


def test_no_faces_state(qtbot, repo):
    view = _view(qtbot, _ctx(repo, _Jobs()))
    assert view.message.text() == NO_FACES
    assert not view.message_action.isHidden()
    assert view.message_action.isEnabled()
    assert view.find_button.isEnabled()


def test_faces_extra_missing_disables_find(qtbot, repo, monkeypatch):
    monkeypatch.setattr(paths, "faces_available", lambda: False)
    view = _view(qtbot, _ctx(repo, _Jobs()))
    assert view.message.text() == NO_EXTRA
    assert "uv sync --extra faces" in NO_EXTRA
    assert not view.find_button.isEnabled()
    assert view.message_action.isHidden() or not view.message_action.isEnabled()


def test_no_gallery_message(qtbot):
    view = PeopleView(AppContext())
    qtbot.addWidget(view)
    assert view.message.text() == NO_GALLERY
    assert view.message_action.isHidden()


def test_disconnected_message(qtbot, tmp_path):
    ctx = AppContext()
    ctx.set_gallery(Gallery("u", str(tmp_path / "gone" / "gallery.db"), "Photos"))
    view = PeopleView(ctx)
    qtbot.addWidget(view)
    qtbot.waitUntil(lambda: view.message.text().startswith("Drive not connected."))
    assert "Photos" in view.message.text()


def test_rename_to_existing_name_offers_merge(qtbot, repo, cat, monkeypatch):
    cat.person("Alice", pid=3)
    cat.person(None, pid=7)
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (" alice ", True))
    asked = []

    def question(parent, title, text, *a, **k):
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    view.list.rename_requested.emit(7)
    assert asked == ["Merge with the existing Alice?"]
    (spec,) = ctx.jobs.specs
    assert spec.argvs == (("name-person", _root(repo), "7", "Alice", "--merge"),)


def test_rename_new_name(qtbot, repo, cat, monkeypatch):
    cat.person(None, pid=7)
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Dana", True))
    view.list.rename_requested.emit(7)
    assert ctx.jobs.specs[0].argvs == (("name-person", _root(repo), "7", "Dana"),)


def test_merge_two_named_asks_which_name(qtbot, repo, cat, monkeypatch):
    cat.person("Alice", pid=3)
    cat.person("Ally", pid=4)
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    offered = []

    def get_item(parent, title, label, items, *a, **k):
        offered.append(list(items))
        return "Ally", True

    monkeypatch.setattr(QInputDialog, "getItem", get_item)
    view.list.merge_requested.emit(3, [4])
    assert offered == [["Alice", "Ally"]]
    (spec,) = ctx.jobs.specs
    root = _root(repo)
    assert spec.argvs == (
        ("merge-persons", root, "3", "4"),
        ("name-person", root, "3", "Ally"),
    )


def test_merge_group_into_named_does_not_ask(qtbot, repo, cat, monkeypatch):
    cat.person("Alice", pid=3)
    cat.person(None, pid=8)
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: pytest.fail("asked"))
    view.list.merge_requested.emit(3, [8])
    assert ctx.jobs.specs[0].argvs == (("merge-persons", _root(repo), "3", "8"),)


def test_drop_onto_row_merges(qtbot, repo, cat):
    a = cat.person("Alice")
    g = cat.person(None)
    view = _view(qtbot, _ctx(repo))
    seen = []
    view.list.merge_requested.connect(lambda d, s: seen.append((d, s)))
    _item(view, g).setSelected(True)
    view.list.list.drop_onto(_item(view, a))
    assert seen == [(a, [g])]


def test_context_menu_actions(qtbot, repo, cat, monkeypatch):
    a = cat.person("Alice")
    g = cat.person(None)
    view = _view(qtbot, _ctx(repo))

    def actions(pid):
        menu = view.list.menu_for(_item(view, pid))
        return {act.text(): act.isEnabled() for act in menu.actions() if act.text()}

    _item(view, a).setSelected(True)
    assert actions(a) == {
        "Rename": True,
        "Merge": False,
        "Split": True,
        "Delete": True,
    }
    _item(view, g).setSelected(True)
    assert actions(g) == {"Rename": True, "Merge": True, "Split": True, "Hide": True}
    monkeypatch.setattr(paths, "faces_available", lambda: False)
    assert actions(g)["Split"] is False


def test_hide_group_job(qtbot, repo, cat):
    g = cat.person(None)
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    view.list.hide_requested.emit(g, True)
    view.list.hide_requested.emit(g, False)
    root = _root(repo)
    assert [s.argvs for s in ctx.jobs.specs] == [
        (("hide-person", root, str(g)),),
        (("hide-person", root, str(g), "--unhide"),),
    ]


def test_split_job(qtbot, repo, cat):
    g = cat.person(None)
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    view.list.split_requested.emit(g)
    assert ctx.jobs.specs[0].argvs == (("split-person", _root(repo), str(g)),)


def test_delete_asks_first(qtbot, repo, cat, monkeypatch):
    a = cat.person("Alice")
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    answer = [QMessageBox.StandardButton.No]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer[0])
    view.list.delete_requested.emit(a)
    assert ctx.jobs.specs == []
    answer[0] = QMessageBox.StandardButton.Yes
    view.list.delete_requested.emit(a)
    assert ctx.jobs.specs[0].argvs == (("delete-person", _root(repo), str(a)),)


def test_pending_count_in_header(qtbot, repo, cat, make_item):
    cat.person("Alice")
    repo.upsert_many([make_item(f"new{i}.jpg") for i in range(3)])
    view = _view(qtbot, _ctx(repo))
    expected = PENDING.format(n=view.data.pending_images)
    assert view.data.pending_images >= 3
    assert view.status.text() == expected
    assert view.header_controls.isAncestorOf(view.find_button)


def test_cover_crops_requested(qtbot, repo, cat, tmp_path):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QPixmap

    from smart_gallery.gui.people.list_view import COVER_ROLE

    class Crops(QObject):
        ready = Signal(str, int, str)

        def __init__(self):
            super().__init__()
            self.ids = []

        def request(self, root, face_id, *_args):
            self.ids.append(face_id)

    a = cat.person("Alice")
    face = repo.conn.execute("SELECT id FROM faces WHERE person_id=?", (a,)).fetchone()
    repo.conn.execute("UPDATE persons SET cover_face_id=? WHERE id=?", (face[0], a))
    repo.conn.commit()
    ctx = _ctx(repo)
    ctx.face_crops = Crops()
    view = _view(qtbot, ctx)
    assert ctx.face_crops.ids == [face[0]]
    crop = tmp_path / "crop.png"
    pixmap = QPixmap(40, 60)
    pixmap.fill()
    assert pixmap.save(str(crop))
    ctx.face_crops.ready.emit(_root(repo), face[0], str(crop))
    cover = _item(view, a).data(COVER_ROLE)
    assert cover.width() == cover.height() == 32
    # A reload reuses the round crop instead of asking again.
    _reload(qtbot, view, ctx.catalog_updated.emit)
    assert ctx.face_crops.ids == [face[0]]
    assert not _item(view, a).data(COVER_ROLE).isNull()


def test_gallery_change_drops_selection(qtbot, repo, cat):
    a = cat.person("Alice")
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    _item(view, a).setSelected(True)
    again = Gallery("other", str(repo.db_path), "other")
    _reload(qtbot, view, lambda: ctx.set_gallery(again))
    assert view.list.selected_ids() == []
    assert view.message.text() == PICK


def _tiles(*ids):
    from smart_gallery.gui.people.model import FaceTile

    return [
        FaceTile(i, i, f"p{i}.jpg", ".jpg", "auto", None, (1, 2, 3, 4), None)
        for i in ids
    ]


def _grid_with(view, *ids):
    grid = view.person_page.faces_grid
    grid.set_tiles(_tiles(*ids))
    return grid


def test_burst_of_corrections_becomes_one_job(qtbot, repo, cat):
    cat.person("Alice")
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    grid = _grid_with(view, 1, 2, 3)
    first = LabelEntry("ignore", (1,))
    second = LabelEntry("move", (2,), person_id=5)
    view.send_labels([first])
    view.send_labels([second])
    assert [t.face_id for t in grid.tiles()] == [3]
    assert ctx.jobs.specs == []
    qtbot.waitUntil(lambda: len(ctx.jobs.specs) == 1)
    (spec,) = ctx.jobs.specs
    root = _root(repo)
    label, file = spec.argvs
    assert spec.title == "Fix faces"
    assert label[:3] == ("label-faces", root, "--list")
    assert file == ("file-faces", root, "--no-group")
    data = json.loads(open(label[3], encoding="utf-8").read())
    assert [d["action"] for d in data] == ["ignore", "move"]
    assert data[1]["person_id"] == 5
    assert view._pending == []


def test_corrections_wait_for_running_job(qtbot, repo, cat):
    cat.person("Alice")
    jobs = _Jobs()
    jobs.running = True
    ctx = _ctx(repo, jobs)
    view = _view(qtbot, ctx)
    grid = _grid_with(view, 1, 2)
    view.send_labels([LabelEntry("ignore", (1,))])
    qtbot.wait(600)
    assert jobs.specs == []
    assert [t.face_id for t in grid.tiles()] == [2]
    jobs.running = False
    jobs.finished.emit(None)
    assert len(jobs.specs) == 1
    assert view._pending == []


def test_refused_start_keeps_entries_for_next_finish(qtbot, repo, cat):
    cat.person("Alice")
    jobs = _Jobs(result=False)
    ctx = _ctx(repo, jobs)
    view = _view(qtbot, ctx)
    view.send_labels([LabelEntry("ignore", (1,))])
    qtbot.waitUntil(lambda: len(jobs.specs) == 1)
    assert len(view._pending) == 1
    jobs.result = True
    jobs.finished.emit(None)
    assert len(jobs.specs) == 2
    assert view._pending == []


def test_leave_guard_with_pending_corrections(qtbot, repo, cat, monkeypatch):
    cat.person("Alice")
    ctx = _ctx(repo, _Jobs())
    view = _view(qtbot, ctx)
    assert ctx.can_leave()
    view.send_labels([LabelEntry("ignore", (1,))])
    Btn = QMessageBox.StandardButton
    answer = [Btn.Cancel]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer[0])
    assert not ctx.can_leave()
    assert len(view._pending) == 1 and ctx.jobs.specs == []
    answer[0] = Btn.Yes
    assert ctx.can_leave()
    assert len(ctx.jobs.specs) == 1 and view._pending == []
    view.send_labels([LabelEntry("ignore", (2,))])
    answer[0] = Btn.No
    assert ctx.can_leave()
    assert view._pending == [] and len(ctx.jobs.specs) == 1
    qtbot.wait(500)
    assert len(ctx.jobs.specs) == 1


def test_remove_faces_keeps_selection_and_scroll(qtbot):
    from smart_gallery.gui.people.faces_grid import FaceGrid

    grid = FaceGrid(AppContext())
    qtbot.addWidget(grid)
    grid.set_tiles(_tiles(*range(1, 6)))
    grid.selectionModel().select(
        grid._model.index(4), grid.selectionModel().SelectionFlag.Select
    )
    resets = []
    grid._model.modelReset.connect(lambda: resets.append(1))
    grid.remove_faces([1, 2])
    assert resets == []
    assert [t.face_id for t in grid.tiles()] == [3, 4, 5]
    assert grid.selected_face_ids() == [5]
