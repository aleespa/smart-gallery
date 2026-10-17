from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery, GalleryStore
from smart_gallery.gui.imports.editor import ProfileEditor
from smart_gallery.gui.imports.view import ImportView
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.import_profiles import (
    ImportProfile,
    ImportRule,
    expand,
    load_profiles,
    starter_profiles,
)


class FakeJobs(QObject):
    started = Signal(object)
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.running = False
        self.calls = []

    def start(self, spec):
        self.calls.append(spec)
        return True


def _store(tmp_path, connected=("a",), missing=()):
    store = GalleryStore(tmp_path / "galleries.json")
    galleries = []
    for name in (*connected, *missing):
        db = tmp_path / name / ".smart_gallery" / "gallery.db"
        if name in connected:
            db.parent.mkdir(parents=True)
            db.touch()
        galleries.append(Gallery(name, str(db), name))
    store.all = lambda: galleries
    return store, galleries


def _view(qtbot, tmp_path, connected=("a",), missing=()):
    ctx = AppContext()
    ctx.galleries, galleries = _store(tmp_path, connected, missing)
    ctx.jobs = FakeJobs()
    view = ImportView(ctx)
    qtbot.addWidget(view)
    view.show()
    return view, ctx, galleries


def _tick(view, index=0):
    item = view.gallery_list.item(index)
    item.setCheckState(Qt.CheckState.Checked)


def test_preview_lists_commands(qtbot, tmp_path):
    view, ctx, galleries = _view(qtbot, tmp_path)
    view.set_source(str(tmp_path / "card"))
    _tick(view)
    text = view.preview.toPlainText()
    assert text.count("smart-gallery import") == 2  # image + video rule
    assert "--file-types image" in text
    assert view.run_button.isEnabled()


def test_disconnected_gallery_checkbox_disabled(qtbot, tmp_path):
    view, ctx, galleries = _view(qtbot, tmp_path, missing=("gone",))
    item = view.gallery_list.item(1)
    assert "(not connected)" in item.text()
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled


def test_preview_error_disables_run(qtbot, tmp_path):
    view, ctx, galleries = _view(qtbot, tmp_path)
    view.set_source(str(tmp_path / "card"))  # no gallery ticked
    assert not view.run_button.isEnabled()
    assert view.preview.toPlainText()


def test_run_disabled_while_job_running(qtbot, tmp_path):
    view, ctx, galleries = _view(qtbot, tmp_path)
    view.set_source("card")
    _tick(view)
    assert view.run_button.isEnabled()
    ctx.jobs.running = True
    ctx.jobs.started.emit(None)
    assert not view.run_button.isEnabled()
    ctx.jobs.running = False
    ctx.jobs.finished.emit(None)
    assert view.run_button.isEnabled()


def test_canon_profile_runs_expected_argvs(qtbot, tmp_path):
    view, ctx, galleries = _view(qtbot, tmp_path, connected=("a", "b"))
    view.set_source(str(tmp_path / "card"))
    view.profile_combo.setCurrentIndex(view.profile_combo.findText("Canon SD card"))
    _tick(view, 0)
    _tick(view, 1)
    view.run_button.click()
    (spec,) = ctx.jobs.calls
    canon = starter_profiles()[0]
    expected = expand(canon, str(tmp_path / "card"), [str(g.root) for g in galleries])
    assert [list(a) for a in spec.argvs] == expected


def test_starter_profiles_seeded_on_first_show(qtbot, tmp_path):
    _view(qtbot, tmp_path)
    names = [p.name for p in load_profiles(app_data_dir() / "profiles")]
    assert "Canon SD card" in names


def test_gallery_list_refreshes_on_gallery_changed(qtbot, tmp_path):
    view, ctx, galleries = _view(qtbot, tmp_path)
    assert view.gallery_list.count() == 1
    more = Gallery("b", str(tmp_path / "b" / ".smart_gallery" / "gallery.db"), "b")
    ctx.galleries.all = lambda: [*galleries, more]
    ctx.gallery_changed.emit(None)
    assert view.gallery_list.count() == 2


def test_editor_stores_only_non_default_options(qtbot):
    profile = ImportProfile(
        "P", [ImportRule("image", [".jpg"], "Photos", {"on_exist": "skip"})]
    )
    editor = ProfileEditor(profile)
    qtbot.addWidget(editor)
    result = editor.profile()
    assert result.name == "P"
    assert result.rules[0].options == {"on_exist": "skip"}
    assert result.rules[0].extensions == [".jpg"]
    editor.rule_editors[0].option_editors["on_exist"].set_value("rename")
    assert editor.profile().rules[0].options == {}


def test_delete_profile_asks_confirmation(qtbot, tmp_path, monkeypatch):
    view, ctx, galleries = _view(qtbot, tmp_path)
    before = view.profile_combo.count()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    view.delete_button.click()
    assert view.profile_combo.count() == before
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    view.delete_button.click()
    assert view.profile_combo.count() == before - 1


def test_dry_run_box_changes_job(qtbot, tmp_path):
    view, ctx, galleries = _view(qtbot, tmp_path)
    view.set_source("card")
    _tick(view)
    view.dry_run_box.setChecked(True)
    assert "--dry-run" in view.preview.toPlainText()
    view.run_button.click()
    (spec,) = ctx.jobs.calls
    assert spec.title.endswith("(dry run)") and not spec.writes_catalog
    assert all(a[-1] == "--dry-run" for a in spec.argvs)


class _FakeEditor:
    result = None

    def __init__(self, *a, **k):
        pass

    def exec(self):
        return True

    def profile(self):
        return _FakeEditor.result


def test_rename_keeps_profile_if_save_fails(qtbot, tmp_path, monkeypatch):
    import smart_gallery.gui.imports.view as v

    view, ctx, galleries = _view(qtbot, tmp_path)
    old = view._profile()
    _FakeEditor.result = ImportProfile("Renamed", old.rules)
    monkeypatch.setattr(v, "ProfileEditor", _FakeEditor)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(v, "save_profile", boom)
    view._edit(old)
    names = [p.name for p in load_profiles(app_data_dir() / "profiles")]
    assert old.name in names and "Renamed" not in names


def test_new_profile_with_existing_name_refused(qtbot, tmp_path, monkeypatch):
    import smart_gallery.gui.imports.view as v

    view, ctx, galleries = _view(qtbot, tmp_path)
    existing = view._profile()
    _FakeEditor.result = ImportProfile(existing.name, [])
    monkeypatch.setattr(v, "ProfileEditor", _FakeEditor)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    view._edit(None)
    assert warned
    after = {p.name: p for p in load_profiles(app_data_dir() / "profiles")}
    assert after[existing.name].rules == existing.rules


def test_select_profile_preselects_and_asks_to_pick_again(qtbot, tmp_path):
    from smart_gallery.gui.imports.view import PICK_AGAIN

    view, ctx, galleries = _view(qtbot, tmp_path)
    assert view.profile_combo.currentText() == "Canon SD card"
    assert view.note.text() == ""
    ctx.open_in_import.emit("Pixel dump")
    assert view.profile_combo.currentText() == "Pixel dump"
    assert view.note.text() == PICK_AGAIN

    ctx.open_in_import.emit("Deleted since")  # unknown: keep the selection
    assert view.profile_combo.currentText() == "Pixel dump"
    assert view.note.text() == PICK_AGAIN
