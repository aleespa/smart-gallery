from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from smart_gallery.db import GalleryRepository
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.storage.tree_view import NODE_ROLE
from smart_gallery.gui.storage.view import (
    CAPTION,
    JOB_RUNNING,
    JOB_TITLE,
    LISTS_DIR,
    NO_GALLERY,
    STATE_FILE,
    StorageView,
    load_storage,
)
from smart_gallery.services.recycle import RecycleEntry, read_list
from smart_gallery.storage.pairs import KEEP_JPEG, KEEP_RAW
from smart_gallery.storage.tree import format_bytes


def _fill(repo, make_item):
    repo.upsert_many(
        [
            make_item("a/IMG_1.CR3", size_bytes=3000),
            make_item("a/IMG_1.JPG", size_bytes=1000),
            make_item("b/IMG_2.CR3", size_bytes=3000),
            make_item("b/IMG_2.JPG", size_bytes=1000),
            make_item("b/note.txt", media_type="other", size_bytes=10),
        ]
    )


class _Jobs:
    def __init__(self, result=True):
        self.result = result
        self.specs = []

    def start(self, spec):
        self.specs.append(spec)
        return self.result


def _ctx(repo, uuid="uuid"):
    ctx = AppContext()
    ctx.set_gallery(Gallery(uuid, str(repo.db_path), "test"))
    return ctx


def _view(qtbot, ctx):
    view = StorageView(ctx)
    qtbot.addWidget(view)
    _loaded(qtbot, view)
    return view


def _loaded(qtbot, view):
    qtbot.waitUntil(lambda: view.data is not None)
    return view.data


def _reload(qtbot, view, signal):
    view.data = None
    signal()
    return _loaded(qtbot, view)


def _child(view, name):
    model = view.tree_view.tree.model()
    root = model.index(0, 0)
    for row in range(model.rowCount(root)):
        index = model.index(row, 0, root)
        if index.data(NODE_ROLE).name == name:
            return index
    raise AssertionError(name)


def test_load_storage(repo, make_item, drive):
    _fill(repo, make_item)
    data = load_storage(repo)
    assert data.root_name == drive.name
    assert data.tree.total_files == 5
    assert data.tree.total_bytes == 8010
    assert [(p.folder, p.stem) for p in data.pairs] == [("a", "IMG_1"), ("b", "IMG_2")]


def test_page_shows_both_columns(qtbot, repo, make_item, drive):
    _fill(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    assert view.caption.text() == CAPTION.format(
        root=drive.name, size=format_bytes(8010), files=5
    )
    model = view.tree_view.tree.model()
    assert model.index(0, 0).data(NODE_ROLE).relpath == ""
    assert len(view.pairs_view.choices()) == 0
    assert view.pairs_view.scope_label.text() == "All folders · 2 pairs"
    assert view.stack.currentIndex() == 0


def test_no_gallery_shows_message(qtbot):
    view = StorageView(AppContext())
    qtbot.addWidget(view)
    assert view.stack.currentWidget() is view.message
    assert view.message.text() == NO_GALLERY


def test_folder_click_scopes_pairs(qtbot, repo, make_item):
    _fill(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    assert view.pairs_view.scope_label.text() == "In a · 1 pairs"
    view.pairs_view.show_all.click()
    assert view.pairs_view.scope_label.text() == "All folders · 2 pairs"
    assert not view.tree_view.tree.currentIndex().isValid()


def test_grouping_switch_resets_scope(qtbot, repo, make_item):
    _fill(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    view.tree_view.set_grouping("type")
    assert view.pairs_view.scope_label.text() == "All folders · 2 pairs"


def test_move_starts_recycle_job(qtbot, repo, make_item, drive):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    ctx.jobs = _Jobs()
    view = _view(qtbot, ctx)
    ref = view.data.pairs[0].jpeg
    view.pairs_view.move_requested.emit([ref])
    (spec,) = ctx.jobs.specs
    assert spec.title == JOB_TITLE
    argv = spec.argvs[0]
    assert argv[:3] == ("recycle", str(drive.resolve()), "--list")
    path = Path(argv[3])
    assert path.parent == app_data_dir() / LISTS_DIR
    assert path.suffix == ".tsv"
    assert read_list(path) == [RecycleEntry(ref.relpath, ref.size_bytes, ref.mtime_ns)]


def test_move_tells_when_a_job_is_running(qtbot, repo, make_item, monkeypatch):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    ctx.jobs = _Jobs(result=False)
    view = _view(qtbot, ctx)
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text: shown.append(text)
    )
    view.pairs_view.move_requested.emit([view.data.pairs[0].jpeg])
    assert shown == [JOB_RUNNING]


def test_choices_survive_reload_for_remaining_pairs(qtbot, repo, make_item):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    p1, p2 = view.data.pairs
    view.pairs_view.choose(p1.key, KEEP_RAW)
    view.pairs_view.choose(p2.key, KEEP_JPEG)
    view.tree_view.tree.setCurrentIndex(_child(view, "b"))
    repo.delete_by_relpaths([p1.jpeg.relpath])
    _reload(qtbot, view, ctx.catalog_updated.emit)
    assert view.pairs_view.choices() == {p2.key: KEEP_JPEG}
    assert view.pairs_view.scope_label.text() == "In b · 1 pairs"


def test_catalog_reload_drops_a_vanished_scope(qtbot, repo, make_item):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    repo.delete_by_relpaths(["a/IMG_1.CR3", "a/IMG_1.JPG"])
    _reload(qtbot, view, ctx.catalog_updated.emit)
    assert view.pairs_view.scope_label.text() == "All folders · 1 pairs"


def test_gallery_change_clears_choices(qtbot, repo, make_item, tmp_path):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    p1, _ = view.data.pairs
    view.pairs_view.choose(p1.key, KEEP_RAW)
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    other_root = tmp_path / "other"
    other_root.mkdir()
    with GalleryRepository.create(other_root, label="other") as other:
        other.upsert_many([make_item("a/IMG_1.CR3"), make_item("a/IMG_1.JPG")])
        db_path = other.db_path
    _reload(qtbot, view, lambda: ctx.set_gallery(Gallery("u2", str(db_path), "o")))
    assert view.pairs_view.choices() == {}
    assert view.pairs_view.scope_label.text() == "All folders · 1 pairs"


def test_leave_guard_is_registered(qtbot, repo, make_item, monkeypatch):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    assert ctx.can_leave()
    view.pairs_view.choose(view.data.pairs[0].key, KEEP_RAW)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No
    )
    assert not ctx.can_leave()


def test_grouping_and_split_remembered(qtbot, repo, make_item):
    _fill(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.resize(1000, 600)
    view.show()
    assert abs(view.splitter.sizes()[0] - view.splitter.sizes()[1]) <= 2
    view.tree_view.set_grouping("type")
    view.splitter.setSizes([300, 690])
    view.splitter.splitterMoved.emit(300, 1)
    saved = view.splitter.sizes()

    again = _view(qtbot, _ctx(repo, "u2"))
    again.resize(1000, 600)
    again.show()
    assert again.tree_view.grouping() == "type"
    assert again.splitter.sizes() == saved


@pytest.mark.parametrize(
    "state",
    [{"grouping": "nope", "split": "x"}, {"grouping": 3, "split": [0, -1]}, []],
)
def test_bad_state_is_ignored(qtbot, repo, make_item, state):
    import json

    path = app_data_dir() / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    _fill(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.resize(1000, 600)
    view.show()
    assert view.tree_view.grouping() == "folder"
    assert abs(view.splitter.sizes()[0] - view.splitter.sizes()[1]) <= 2
