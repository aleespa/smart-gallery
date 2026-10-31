from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from smart_gallery.db import GalleryRepository
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.state_store import load_json
from smart_gallery.gui.storage.pairs_view import PAIR_ROLE
from smart_gallery.gui.storage.tree_view import NODE_ROLE
from smart_gallery.gui.storage.view import (
    CAPTION,
    JOB_RUNNING,
    JOB_TITLE,
    LISTS_DIR,
    NO_GALLERY,
    SAVE_DELAY_MS,
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


def test_load_storage_includes_shots(repo, make_item):
    repo.upsert_many(
        [
            make_item("a/IMG_1.JPG", taken_ts=1_700_000_000),
            make_item("a/IMG_2.JPG", taken_ts=1_700_000_001),
        ]
    )
    assert len(load_storage(repo).shots) == 2


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
    qtbot.waitUntil(lambda: load_json(view.state_path).get("split") == saved)

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


def test_same_gallery_again_keeps_choices_and_scope(qtbot, repo, make_item):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    p1, p2 = view.data.pairs
    view.pairs_view.choose(p2.key, KEEP_JPEG)
    view.tree_view.tree.setCurrentIndex(_child(view, "b"))
    again = Gallery("uuid", str(repo.db_path), "test")  # a new object, same db_uuid
    _reload(qtbot, view, lambda: ctx.set_gallery(again))
    assert view.pairs_view.choices() == {p2.key: KEEP_JPEG}
    assert view.pairs_view.scope_label.text() == "In b · 1 pairs"


def test_disconnected_same_gallery_keeps_choices(qtbot, repo, make_item, tmp_path):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    p1, _ = view.data.pairs
    view.pairs_view.choose(p1.key, KEEP_RAW)
    gone = Gallery("uuid", str(tmp_path / "missing" / "gallery.db"), "test")
    ctx.set_gallery(gone)
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.message)
    assert view.message.text().startswith("Drive not connected.")
    assert view.pairs_view.choices() == {p1.key: KEEP_RAW}
    _reload(qtbot, view, lambda: ctx.set_gallery(_ctx(repo).current))
    assert view.pairs_view.choices() == {p1.key: KEEP_RAW}
    assert view.stack.currentIndex() == 0


class _FakeCheck:
    """Stands in for check_pairs: records each run's folders, optionally
    holding a run until ``gate`` opens, and can fail or report verdicts."""

    def __init__(self, verdict=None, error=None):
        import threading

        self.runs = []  # one sorted list of folders per run
        self.cancelled = []  # what each run saw at its end
        self.verdict, self.error = verdict, error
        self.gate = threading.Event()
        self.gate.set()

    def __call__(
        self, pairs, root, cache, on_result, cancelled=lambda: False, batch=50
    ):
        self.runs.append(sorted(p.folder for p in pairs))
        self.gate.wait(5)
        self.cancelled.append(cancelled())
        if self.error is not None:
            raise self.error
        if self.verdict is not None:
            for p in pairs:
                on_result(p.key, self.verdict)


@pytest.fixture
def fake_check(monkeypatch):
    from smart_gallery.gui.storage import checker as checker_module

    fake = _FakeCheck()
    monkeypatch.setattr(checker_module, "check_pairs", fake)
    return fake


def _fill_nested(repo, make_item):
    _fill(repo, make_item)
    repo.upsert_many(
        [
            make_item("a/sub/IMG_3.CR3", size_bytes=3000),
            make_item("a/sub/IMG_3.JPG", size_bytes=1000),
        ]
    )


def _settled(qtbot, view):
    view.checker.wait(5000)
    qtbot.wait(50)


def test_no_check_without_a_folder(qtbot, repo, make_item, fake_check):
    from smart_gallery.gui.storage.pairs_view import CHECK_HINT

    _fill_nested(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    _settled(qtbot, view)
    assert fake_check.runs == []
    assert view.pairs_view.status.text() == CHECK_HINT
    for pair in view.data.pairs:
        assert view.pairs_view.status_for(pair.key) == ("", False)
    # Selecting the root is the same as all folders.
    model = view.tree_view.tree.model()
    view.tree_view.tree.setCurrentIndex(model.index(0, 0))
    _settled(qtbot, view)
    assert fake_check.runs == []


def test_folder_selection_checks_only_its_pairs(qtbot, repo, make_item, fake_check):
    from smart_gallery.gui.storage.pairs_view import CHECK_HINT, STATUS

    _fill_nested(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    fake_check.gate.clear()  # hold a's run while switching
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    qtbot.waitUntil(lambda: fake_check.runs == [["a", "a/sub"]])
    a1, a3, b2 = (
        next(p for p in view.data.pairs if p.stem == s)
        for s in ("IMG_1", "IMG_3", "IMG_2")
    )
    assert view.pairs_view.status_for(a3.key) == (STATUS[None], False)
    assert view.pairs_view.status_for(b2.key) == ("", False)

    view.tree_view.tree.setCurrentIndex(_child(view, "b"))  # cancels a's run
    assert view.pairs_view.status_for(a1.key) == ("", False)
    assert view.pairs_view.status_for(b2.key) == (STATUS[None], False)
    fake_check.gate.set()
    _settled(qtbot, view)
    assert fake_check.runs == [["a", "a/sub"], ["b"]]
    assert fake_check.cancelled == [True, False]

    fake_check.gate.clear()
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    qtbot.waitUntil(lambda: len(fake_check.runs) == 3)
    view.pairs_view.show_all.click()  # cancels, checks nothing
    assert view.pairs_view.status.text() == CHECK_HINT
    fake_check.gate.set()
    _settled(qtbot, view)
    assert len(fake_check.runs) == 3 and fake_check.cancelled[-1] is True


def test_cached_verdicts_show_without_a_check(qtbot, repo, make_item, fake_check):
    from smart_gallery.gui.storage.pairs_view import STATUS
    from smart_gallery.gui.storage.view import CHECKS_DIR
    from smart_gallery.storage.check_cache import CheckCache
    from smart_gallery.storage.compare import DIFFERENT

    _fill(repo, make_item)
    pairs = load_storage(repo).pairs
    cache = CheckCache(app_data_dir() / CHECKS_DIR / "uuid.json")
    cache.put(pairs[1], DIFFERENT)  # b's pair
    cache.save()

    view = _view(qtbot, _ctx(repo))
    b2 = view.data.pairs[1]
    qtbot.waitUntil(
        lambda: view.pairs_view.status_for(b2.key) == (STATUS[DIFFERENT], True)
    )
    assert fake_check.runs == []
    # Scoping to a checks only a's pair, the one without a verdict.
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    _settled(qtbot, view)
    assert fake_check.runs == [["a"]]
    view.tree_view.tree.setCurrentIndex(_child(view, "b"))
    _settled(qtbot, view)
    assert fake_check.runs == [["a"]]  # b is known: no run


def test_same_gallery_reload_checks_the_kept_scope(qtbot, repo, make_item, fake_check):
    _fill(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    view.tree_view.tree.setCurrentIndex(_child(view, "b"))
    _settled(qtbot, view)
    again = Gallery("uuid", str(repo.db_path), "test")
    _reload(qtbot, view, lambda: ctx.set_gallery(again))
    _settled(qtbot, view)
    assert fake_check.runs == [["b"], ["b"]]


def test_failed_check_is_shown(qtbot, repo, make_item, fake_check):
    from smart_gallery.gui.storage.pairs_view import CHECK_FAILED, STATUS
    from smart_gallery.storage.compare import UNREADABLE

    fake_check.error = FileNotFoundError("exiftool is not on PATH")
    _fill(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    expected = CHECK_FAILED.format(reason="exiftool is not on PATH")
    qtbot.waitUntil(lambda: view.pairs_view.status.text() == expected)
    a1, b2 = view.data.pairs
    assert view.pairs_view.status_for(a1.key) == (STATUS[UNREADABLE], True)
    assert view.pairs_view.status_for(b2.key) == ("", False)  # not in the run


def test_finished_check_leaves_no_row_checking(qtbot, repo, make_item, fake_check):
    from smart_gallery.gui.storage.pairs_view import STATUS

    _fill(repo, make_item)  # the fake reports nothing, yet succeeds
    view = _view(qtbot, _ctx(repo))
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    key = view.data.pairs[0].key
    qtbot.waitUntil(lambda: view.pairs_view.status_for(key)[0] != STATUS[None])
    assert view.pairs_view.status.text() == ""


def test_pair_folders_follow_tree_sizes(qtbot, repo, make_item):
    _fill(repo, make_item)
    repo.upsert_many([make_item("b/big.mp4", media_type="video", size_bytes=99999)])
    view = _view(qtbot, _ctx(repo))
    model = view.pairs_view.list.model()
    headings = [
        model.index(r).data()
        for r in range(model.rowCount())
        if model.index(r).data(PAIR_ROLE) is None
    ]
    assert headings == ["B", "A"]  # b holds more on disk; find_pairs says a, b
    assert [p.folder for p in view.data.pairs] == ["a", "b"]


def test_splitter_drag_saves_once_after_it_settles(qtbot, repo, make_item, monkeypatch):
    from smart_gallery.gui.storage import view as storage_view

    saves = []
    monkeypatch.setattr(storage_view, "save_json", lambda p, d: saves.append(d))
    _fill(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.resize(1000, 600)
    view.show()
    for x in range(200, 260):
        view.splitter.splitterMoved.emit(x, 1)
    assert saves == []  # not on every pixel, not on the GUI thread's hot path
    qtbot.waitUntil(lambda: len(saves) == 1)
    qtbot.wait(SAVE_DELAY_MS + 100)
    assert len(saves) == 1


def test_unwritable_state_file_is_only_logged(qtbot, repo, make_item, monkeypatch):
    from smart_gallery.gui.storage import view as storage_view

    calls = []

    def locked(path, data):
        calls.append(data)
        raise PermissionError("locked by the indexer")

    monkeypatch.setattr(storage_view, "save_json", locked)
    _fill(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.tree_view.set_grouping("type")  # saves at once: must not raise
    view.splitter.splitterMoved.emit(300, 1)
    qtbot.waitUntil(lambda: len(calls) == 2)


def test_thumbnails_asked_again_when_page_is_shown(qtbot, repo, make_item):
    from PySide6.QtCore import QObject, Signal

    class Thumbs(QObject):
        ready = Signal(str, int, str)

        def __init__(self):
            super().__init__()
            self.ids = []

        def request(self, root, media_id, *_args):
            self.ids.append(media_id)

    _fill(repo, make_item)
    ctx = _ctx(repo)
    ctx.thumbs = Thumbs()
    view = _view(qtbot, ctx)
    view.resize(1000, 600)
    view.show()
    qtbot.waitExposed(view)
    qtbot.waitUntil(lambda: bool(ctx.thumbs.ids))
    first = list(ctx.thumbs.ids)
    assert first
    # The shared loader dropped its queue (cancel_pending) while away.
    view.hide()
    ctx.thumbs.ids.clear()
    view.show()
    qtbot.waitUntil(lambda: sorted(ctx.thumbs.ids) == sorted(first))


# -- bursts ------------------------------------------------------------------
def _fill_shots(repo, make_item):
    repo.upsert_many(
        [
            make_item("a/IMG_1.JPG", taken_ts=1_700_000_000),
            make_item("a/IMG_2.JPG", taken_ts=1_700_000_001),
            make_item("b/IMG_3.JPG", taken_ts=1_700_000_500),
            make_item("b/IMG_4.JPG", taken_ts=1_700_000_501),
        ]
    )


def _first_burst(view, folder=None):
    return next(
        b for b in view.bursts_view.bursts_in_scope() if folder in (None, b.folder)
    )


def _bursts_state(**state):
    import json

    path = app_data_dir() / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def test_switch_shows_bursts_and_is_remembered(qtbot, repo, make_item):
    _fill_shots(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    pairs_button, bursts_button, _raw_button = view.right_switch
    assert view.right_stack.currentWidget() is view.pairs_view
    bursts_button.click()
    assert view.right_stack.currentWidget() is view.bursts_view
    assert load_json(view.state_path)["right"] == "bursts"
    again = _view(qtbot, _ctx(repo, "u2"))
    assert again.right_stack.currentWidget() is again.bursts_view
    assert again.right_switch[1].isChecked()


def test_tree_folder_scopes_bursts(qtbot, repo, make_item):
    _fill_shots(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    assert view.bursts_view.scope() == "a"
    view.right_switch[1].click()
    assert view.bursts_view.scope() == "a"
    view.bursts_view.show_all.click()
    assert view.bursts_view.scope() is None
    assert not view.tree_view.tree.currentIndex().isValid()


def test_burst_move_starts_recycle_job(qtbot, repo, make_item, drive, monkeypatch):
    _fill_shots(repo, make_item)
    ctx = _ctx(repo)
    ctx.jobs = _Jobs()
    view = _view(qtbot, ctx)
    view.right_switch[1].click()
    burst = _first_burst(view)
    shot = burst.shots[0]
    assert view.bursts_view.toggle(shot.key)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes
    )
    view.bursts_view.move_button.click()
    (spec,) = ctx.jobs.specs
    argv = spec.argvs[0]
    assert argv[:3] == ("recycle", str(drive.resolve()), "--list")
    wanted = [(f.relpath, f.size_bytes, f.mtime_ns) for f in shot.files]
    got = [(e.relpath, e.size_bytes, e.mtime_ns) for e in read_list(Path(argv[3]))]
    assert set(wanted) <= set(got)


def test_gap_is_remembered(qtbot, repo, make_item):
    from smart_gallery.storage.bursts import DEFAULT_GAP

    _fill_shots(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    assert view.bursts_view.gap() == DEFAULT_GAP
    other = 4 if DEFAULT_GAP != 4 else 5
    view.bursts_view.set_gap(other)
    assert load_json(view.state_path)["burst_gap"] == other
    again = _view(qtbot, _ctx(repo, "u2"))
    assert again.bursts_view.gap() == other


@pytest.mark.parametrize("bad", [0, 99, "x", 2.5, True, None, [3]])
def test_bad_gap_and_right_fall_back(qtbot, repo, make_item, bad):
    from smart_gallery.storage.bursts import DEFAULT_GAP

    _bursts_state(burst_gap=bad, right=bad)
    _fill_shots(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    assert view.bursts_view.gap() == DEFAULT_GAP
    assert view.right_stack.currentWidget() is view.pairs_view


def test_leave_guard_counts_pair_choices_and_burst_marks(
    qtbot, repo, make_item, monkeypatch
):
    _fill(repo, make_item)
    repo.upsert_many(
        [
            make_item("a/S1.JPG", taken_ts=1_700_000_000),
            make_item("a/S2.JPG", taken_ts=1_700_000_001),
        ]
    )
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    assert ctx.can_leave()  # nothing pending: no question
    view.pairs_view.choose(view.data.pairs[0].key, KEEP_RAW)
    assert view.bursts_view.toggle(_first_burst(view).shots[0].key)
    asked = []

    def answer(parent, title, text, *rest):
        asked.append(text)
        return (
            QMessageBox.StandardButton.No
            if len(asked) == 1
            else (QMessageBox.StandardButton.Yes)
        )

    monkeypatch.setattr(QMessageBox, "question", answer)
    assert not ctx.can_leave()
    assert ctx.can_leave()
    assert asked == ["You have 2 unapplied choices. Discard them?"] * 2
    assert view.pairs_view.choices() == {} and view.bursts_view.marks() == set()


def test_review_swaps_in_and_back(qtbot, repo, make_item):
    _fill_shots(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.right_switch[1].click()
    burst = _first_burst(view)
    view.bursts_view.review_requested.emit(burst.key, burst.shots[0].key)
    assert view.stack.currentWidget() is view.review
    qtbot.keyClick(view.review, Qt.Key.Key_Escape)
    assert view.stack.currentWidget() is view.splitter
    assert view.bursts_view.expanded() == burst.key
    view.review.wait()


def test_review_refreshes_on_data_and_leaves_with_the_drive(
    qtbot, repo, make_item, tmp_path
):
    _fill_shots(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    burst = _first_burst(view)
    view.bursts_view.review_requested.emit(burst.key, burst.shots[0].key)
    _reload(qtbot, view, ctx.catalog_updated.emit)
    assert view.stack.currentWidget() is view.review
    gone = Gallery("uuid", str(tmp_path / "missing" / "gallery.db"), "test")
    ctx.set_gallery(gone)
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.message)
    view.review.wait()


def test_switching_lists_keeps_marks(qtbot, repo, make_item):
    _fill_shots(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.right_switch[1].click()
    key = _first_burst(view).shots[0].key
    view.bursts_view.toggle(key)
    view.right_switch[0].click()
    view.right_switch[1].click()
    assert view.bursts_view.marks() == {key}


def test_gallery_change_clears_marks_same_gallery_keeps(
    qtbot, repo, make_item, tmp_path
):
    _fill_shots(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    key = _first_burst(view).shots[0].key
    view.bursts_view.toggle(key)
    again = Gallery("uuid", str(repo.db_path), "test")
    _reload(qtbot, view, lambda: ctx.set_gallery(again))
    assert view.bursts_view.marks() == {key}
    other_root = tmp_path / "other"
    other_root.mkdir()
    with GalleryRepository.create(other_root, label="other") as other:
        other.upsert_many([make_item("a/IMG_1.JPG", taken_ts=1_700_000_000)])
        db_path = other.db_path
    _reload(qtbot, view, lambda: ctx.set_gallery(Gallery("u2", str(db_path), "o")))
    assert view.bursts_view.marks() == set()


def _fill_three(repo, make_item, **first):
    repo.upsert_many(
        [
            make_item("a/IMG_1.JPG", taken_ts=1_700_000_000, **first),
            make_item("a/IMG_2.JPG", taken_ts=1_700_000_001),
            make_item("a/IMG_3.JPG", taken_ts=1_700_000_002),
        ]
    )


def test_reload_notice_shows_marks_dropped_by_a_change(qtbot, repo, make_item):
    from smart_gallery.gui.storage.bursts_view import DROPPED

    _fill_three(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))  # kept over the reload
    key = _first_burst(view).shots[0].key
    assert view.bursts_view.toggle(key)
    _fill_three(repo, make_item, mtime_ns=1_800_000_000_000_000_000)
    _reload(qtbot, view, ctx.catalog_updated.emit)
    assert view.bursts_view.marks() == set()
    assert view.bursts_view.scope() == "a"
    assert DROPPED.format(n=1) in view.bursts_view.scope_label.text()


def test_reload_notice_skips_marks_whose_files_went(qtbot, repo, make_item):
    from smart_gallery.gui.storage.bursts_view import DROPPED

    _fill_three(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    key = _first_burst(view).shots[0].key
    assert view.bursts_view.toggle(key)
    repo.delete_by_relpaths(["a/IMG_1.JPG"])  # the move went through
    _reload(qtbot, view, ctx.catalog_updated.emit)
    assert view.bursts_view.marks() == set()
    assert DROPPED.format(n=1) not in view.bursts_view.scope_label.text()
    assert "cleared" not in view.bursts_view.scope_label.text()


def test_burst_thumbnails_asked_again_when_page_is_shown(qtbot, repo, make_item):
    from PySide6.QtCore import QObject, Signal

    class Thumbs(QObject):
        ready = Signal(str, int, str)

        def __init__(self):
            super().__init__()
            self.ids = []

        def request(self, root, media_id, *_args):
            self.ids.append(media_id)

    _fill_shots(repo, make_item)
    ctx = _ctx(repo)
    ctx.thumbs = Thumbs()
    view = _view(qtbot, ctx)
    view.right_switch[1].click()
    view.resize(1000, 600)
    view.show()
    qtbot.waitExposed(view)
    qtbot.waitUntil(lambda: bool(ctx.thumbs.ids))
    first = list(ctx.thumbs.ids)
    # The shared loader dropped its queue (cancel_pending) while away.
    view.hide()
    ctx.thumbs.ids.clear()
    view.show()
    qtbot.waitUntil(lambda: sorted(ctx.thumbs.ids) == sorted(first), timeout=2000)


def test_gallery_change_resets_the_review(qtbot, repo, make_item, tmp_path):
    _fill_shots(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    burst = _first_burst(view)
    view.bursts_view.review_requested.emit(burst.key, burst.shots[0].key)
    assert view.stack.currentWidget() is view.review
    other_root = tmp_path / "other"
    other_root.mkdir()
    with GalleryRepository.create(other_root, label="other") as other:
        other.upsert_many(
            [
                make_item("a/IMG_1.JPG", taken_ts=1_700_000_000),
                make_item("a/IMG_2.JPG", taken_ts=1_700_000_001),
            ]
        )
        db_path = other.db_path
    _reload(qtbot, view, lambda: ctx.set_gallery(Gallery("u2", str(db_path), "o")))
    assert view.review.current() is None
    assert view.stack.currentWidget() is view.splitter
    view.review.wait()


def test_empty_catalog_resets_the_review(qtbot, repo, make_item):
    _fill_shots(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    burst = _first_burst(view)
    view.bursts_view.review_requested.emit(burst.key, burst.shots[0].key)
    repo.delete_by_relpaths(
        [
            s.files[0].relpath
            for b in view.bursts_view.bursts_in_scope()
            for s in b.shots
        ]
    )
    _reload(qtbot, view, ctx.catalog_updated.emit)
    assert view.data.tree is None
    assert view.review.current() is None
    view.review.wait()


# ── RAW -> JPEG ─────────────────────────────────────────────────────────────
def _fill_raws(repo, make_item):
    repo.upsert_many(
        [
            make_item("a/C1.CR3", size_bytes=3000),
            make_item("a/C2.CR3", size_bytes=3000),
            make_item("b/C3.CR3", size_bytes=3000),
        ]
    )


def test_load_storage_includes_raws(repo, make_item):
    _fill_raws(repo, make_item)
    assert len(load_storage(repo).raws) == 3


def test_third_switch_shows_raw_view_and_is_remembered(qtbot, repo, make_item):
    _fill_raws(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    view.right_switch[2].click()
    assert view.right_stack.currentWidget() is view.raw_view
    assert load_json(view.state_path)["right"] == "raw"
    again = _view(qtbot, _ctx(repo, "u2"))
    assert again.right_stack.currentWidget() is again.raw_view
    assert again.right_switch[2].isChecked()


def test_tree_scopes_raw_view(qtbot, repo, make_item):
    _fill_raws(repo, make_item)
    view = _view(qtbot, _ctx(repo))
    assert len(view.raw_view.model.rows) == 3
    view.tree_view.tree.setCurrentIndex(_child(view, "a"))
    assert view.raw_view.scope() == "a"
    assert len(view.raw_view.model.rows) == 2
    view.raw_view.show_all.click()
    assert view.raw_view.scope() is None
    assert not view.tree_view.tree.currentIndex().isValid()


def test_leave_guard_counts_raw_picks(qtbot, repo, make_item, monkeypatch):
    _fill_raws(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    view.raw_view.toggle(view.data.raws[0].key)
    view.raw_view.toggle(view.data.raws[1].key)
    asked = []

    def answer(parent, title, text, *rest):
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    assert ctx.can_leave()
    assert asked == ["You have 2 unapplied choices. Discard them?"]
    assert view.raw_view.picks() == set()


def test_convert_starts_raw_to_jpeg_job(qtbot, repo, make_item, drive, monkeypatch):
    _fill_raws(repo, make_item)
    ctx = _ctx(repo)
    ctx.jobs = _Jobs()
    view = _view(qtbot, ctx)
    view.right_switch[2].click()
    cand = view.data.raws[0]
    view.raw_view.toggle(cand.key)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes
    )
    view.raw_view.convert_button.click()
    (spec,) = ctx.jobs.specs
    assert spec.title == "Convert to JPEG"
    argv = spec.argvs[0]
    assert argv[:3] == ("raw-to-jpeg", str(drive.resolve()), "--list")
    got = [e.relpath for e in read_list(Path(argv[3]))]
    assert got == [cand.raw.relpath]


def test_gallery_change_clears_picks(qtbot, repo, make_item, tmp_path):
    _fill_raws(repo, make_item)
    ctx = _ctx(repo)
    view = _view(qtbot, ctx)
    view.raw_view.toggle(view.data.raws[0].key)
    other = tmp_path / "other"
    other.mkdir()
    with GalleryRepository.create(other) as r2:
        path = r2.db_path
    view.data = None
    ctx.set_gallery(Gallery("u2", str(path), "other"))
    _loaded(qtbot, view)
    assert view.raw_view.picks() == set()
