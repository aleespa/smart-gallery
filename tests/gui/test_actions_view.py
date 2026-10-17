from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from smart_gallery.forms import default_values
from smart_gallery.gui.actions import model
from smart_gallery.gui.actions.fields import make_editor
from smart_gallery.gui.actions.view import ActionsView
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.jobs import JobSpec

ALL_KINDS = {
    "text",
    "list",
    "int",
    "int_list",
    "float",
    "date",
    "path",
    "path_list",
    "choice",
    "mode",
    "multi",
    "ordered",
    "switch",
}


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


def _view(qtbot, jobs=True, root=None):
    ctx = AppContext()
    ctx.jobs = FakeJobs() if jobs else None
    if root is not None:
        ctx.current = SimpleNamespace(root=root)
    view = ActionsView(ctx)
    qtbot.addWidget(view)
    return view, ctx


def test_every_field_kind_has_an_editor(qtbot):
    kinds = set()
    for name, form in model.gui_forms().items():
        values = default_values(form)
        for key, f in form.fields.items():
            kinds.add(f.kind)
            editor = make_editor(f, command=name)
            qtbot.addWidget(editor)
            editor.set_value(values[key])
            assert editor.value() == values[key], (name, key)
    assert kinds == ALL_KINDS


def test_editors_round_trip_non_default_values(qtbot):
    forms = model.gui_forms()
    order = make_editor(forms["export"].fields["structure"])
    order.set_value(("Model", "Year"))
    assert order.value() == ("Model", "Year")
    multi = make_editor(forms["export"].fields["file_types"])
    multi.set_value(("video", "image"))
    assert multi.value() == ("image", "video")  # a plain set keeps choice order
    mode = make_editor(forms["cluster-faces"].fields["mode"])
    mode.set_value("--incremental")
    assert mode.value() == "--incremental"
    mode.set_value(None)
    assert mode.value() is None


def test_ordered_editor_reorders_with_buttons(qtbot):
    editor = make_editor(model.gui_forms()["export"].fields["structure"])
    qtbot.addWidget(editor)
    editor.set_value(("Year", "Month"))
    seen = []
    editor.changed.connect(lambda: seen.append(editor.value()))
    editor.list.setCurrentRow(1)  # Month
    editor.up.click()
    assert editor.value() == ("Month", "Year")
    editor.down.click()
    assert editor.value() == ("Year", "Month")
    assert seen == [("Month", "Year"), ("Year", "Month")]


def test_browse_buttons_use_the_right_dialog(qtbot, monkeypatch):
    forms = model.gui_forms()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *a, **k: "D:/folder one"
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: ("D:/out.xlsx", "")
    )
    folder = make_editor(forms["sync"].fields["drive"], command="sync")
    qtbot.addWidget(folder)
    folder.browse.click()
    assert folder.value().replace("\\", "/") == "D:/folder one"
    report = make_editor(forms["report"].fields["output"], command="report")
    qtbot.addWidget(report)
    report.browse.click()
    assert report.value().replace("\\", "/") == "D:/out.xlsx"
    sources = make_editor(forms["import"].fields["sources"], command="import")
    qtbot.addWidget(sources)
    sources.set_value("F:/a")
    sources.browse.click()
    assert sources.value().replace("\\", "/") == 'F:/a "D:/folder one"'


def test_run_starts_job_with_built_argv(qtbot):
    view, ctx = _view(qtbot)
    view.select_command("sync")
    view.editor("drive").set_value("E:/")
    view.editor("dry_run").set_value(True)
    assert view.run_button.isEnabled()
    view.run_button.click()
    (spec,) = ctx.jobs.calls
    assert isinstance(spec, JobSpec)
    assert spec.title == "sync"
    assert spec.argvs == (("sync", "E:/", "--dry-run"),)
    assert spec.writes_catalog is True


def test_read_only_commands_do_not_write_the_catalog(qtbot):
    view, ctx = _view(qtbot)
    view.select_command("report")
    view.editor("drive").set_value("E:/")
    view.editor("output").set_value("D:/r.xlsx")
    view.run_button.click()
    assert ctx.jobs.calls[0].writes_catalog is False


def test_run_with_missing_field_shows_error_and_does_not_start(qtbot):
    view, ctx = _view(qtbot)
    view.select_command("sync")
    assert view.error_text("drive") == ""  # not nagging before the first Run
    view.run_button.click()
    assert ctx.jobs.calls == []
    assert view.error_text("drive") == "Required."


def test_invalid_value_shows_error_live(qtbot):
    view, _ = _view(qtbot)
    view.select_command("export")
    view.editor("min_iso").set_value("abc")
    assert view.error_text("min_iso") == "Enter a whole number."


def test_extra_options_are_appended_and_validated(qtbot):
    view, _ = _view(qtbot)
    view.select_command("sync")
    view.editor("drive").set_value("E:/")
    view.extra_edit.setText("--dry-run")
    assert view.preview.text() == "smart-gallery sync E:/ --dry-run"
    view.extra_edit.setText('"oops')
    assert view.error_text("extra") == "Close the quotation mark."


def test_run_disabled_while_a_job_runs(qtbot):
    view, ctx = _view(qtbot)
    view.select_command("sync")
    assert view.run_button.isEnabled()
    ctx.jobs.running = True
    ctx.jobs.started.emit(JobSpec("x", ()))
    assert not view.run_button.isEnabled()
    ctx.jobs.running = False
    ctx.jobs.finished.emit(object())
    assert view.run_button.isEnabled()


def test_run_disabled_without_a_job_runner(qtbot):
    view, _ = _view(qtbot, jobs=False)
    view.select_command("sync")
    assert not view.run_button.isEnabled()


def test_face_commands_show_banner_and_disable_run(qtbot, monkeypatch):
    monkeypatch.setattr(model, "faces_available", lambda: False)
    view, _ = _view(qtbot)
    view.select_command("scan-faces")
    assert not view.banner.isHidden()
    assert view.banner.text() == "Face features aren't installed in this build"
    assert not view.run_button.isEnabled()
    view.select_command("sync")
    assert view.banner.isHidden()
    assert view.run_button.isEnabled()


def test_commands_listed_in_cli_order(qtbot):
    view, _ = _view(qtbot)
    names = [view.commands.item(i).text() for i in range(view.commands.count())]
    assert names == list(model.gui_forms())


def test_collapsed_sections_start_folded_and_unfold(qtbot):
    view, _ = _view(qtbot)
    view.select_command("export")
    required = view.section("Required")
    filters = view.section("Filters")
    assert not required.is_folded()
    assert filters.is_folded()
    assert filters.content.isHidden()
    filters.setChecked(True)
    assert not filters.is_folded()
    assert not filters.content.isHidden()


def test_copy_button_puts_command_on_clipboard(qtbot):
    view, _ = _view(qtbot)
    view.select_command("sync")
    view.editor("drive").set_value("E:/My Photos")
    view.copy_button.click()
    assert QApplication.clipboard().text() == 'smart-gallery sync "E:/My Photos"'


def test_gallery_changes_prefill_drive_unless_edited(qtbot, tmp_path):
    view, ctx = _view(qtbot, root=tmp_path)
    view.select_command("sync")
    assert view.editor("drive").value() == str(tmp_path)
    other = tmp_path / "other"
    ctx.set_gallery(SimpleNamespace(root=other))
    assert view.editor("drive").value() == str(other)
    view.editor("drive").set_value("Z:/")  # the user types
    ctx.set_gallery(SimpleNamespace(root=tmp_path))
    assert view.editor("drive").value() == "Z:/"
    view.select_command("export")  # a form built later gets the current root
    assert view.editor("drive").value() == str(tmp_path)


def test_open_in_actions_selects_command_and_fills_values(qtbot):
    view, ctx = _view(qtbot)
    ctx.open_in_actions.emit(
        ["export", "--from", "E:/", "--to", "D:/out", "--min-iso", "400"]
    )
    assert view.commands.currentItem().text() == "export"
    assert view.editor("min_iso").value() == "400"
    assert view.editor("dest").value().replace("\\", "/") == "D:/out"
    assert view.preview.text().startswith("smart-gallery export")


def test_open_in_actions_ignores_unusable_argv(qtbot):
    view, ctx = _view(qtbot)
    view.select_command("sync")
    ctx.open_in_actions.emit(["nonsense"])
    ctx.open_in_actions.emit([])
    assert view.commands.currentItem().text() == "sync"


def test_actions_area_builds_the_real_view(qtbot):
    from smart_gallery.gui.areas import AREAS

    area = next(a for a in AREAS if a.id == "actions")
    page = area.factory(AppContext())
    qtbot.addWidget(page)
    assert isinstance(page, ActionsView)
