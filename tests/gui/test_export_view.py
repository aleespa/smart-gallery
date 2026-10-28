import re

from PySide6.QtCore import QObject, Signal

import smart_gallery.gui.exports.view as export_view
from smart_gallery.cli import build_filter_query, parse_args
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.exports.view import ExportView
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.imports.page import TransferPage
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.state_store import load_json
from smart_gallery.organize.filters import FilterOptions


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


def _ctx(repo, make_item):
    repo.upsert_many(
        [
            make_item("a/IMG_1.CR3", camera="Canon EOS R8", size_bytes=30),
            make_item("a/IMG_1.JPG", camera="Canon EOS R8", size_bytes=10),
            make_item("a/IMG_2.CR3", camera="Canon EOS R8", size_bytes=30),
            make_item("b/c.jpg", camera="Sony A7", size_bytes=10),
            make_item("b/clip.mp4", media_type="video", size_bytes=100),
        ]
    )
    ctx = AppContext()
    ctx.set_gallery(Gallery("uuid", str(repo.db_path), "E"))
    ctx.jobs = FakeJobs()
    return ctx


def _view(qtbot, repo, make_item, monkeypatch, exiftool=True):
    monkeypatch.setattr(export_view, "exiftool_available", lambda: exiftool)
    ctx = _ctx(repo, make_item)
    view = ExportView(ctx)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitUntil(lambda: view._summary is not None)
    return view, ctx


def _argv(view):
    (argv,) = view.ctx.jobs.calls[-1].argvs
    return parse_args(list(argv))


def test_summary_counts_what_the_filters_select(qtbot, repo, make_item, monkeypatch):
    view, _ = _view(qtbot, repo, make_item, monkeypatch)
    assert view.summary.text() == "5 files match (180 B)."
    view.jpeg_only.setChecked(True)
    text = view.summary.text()
    assert "3 JPEGs and videos copied, 1 RAW files converted" in text
    assert "1 other files left out" in text
    view.filters.cameras.set_selected(("Sony A7",))
    view.filters.apply()
    qtbot.waitUntil(lambda: view._summary is not None and view._summary.matched == 1)


def test_run_builds_the_export_job(qtbot, repo, make_item, monkeypatch, tmp_path):
    view, ctx = _view(qtbot, repo, make_item, monkeypatch)
    assert not view.run_button.isEnabled()  # no destination yet
    view.dest.setText(str(tmp_path / "share"))
    view.levels[1].setCurrentIndex(view.levels[1].findData("Model"))
    view.by_media_type.setChecked(False)
    view.jpeg_only.setChecked(True)
    view.filters.cameras.set_selected(("Canon EOS R8",))
    assert view.run_button.isEnabled()
    view.run_button.click()  # applies the filter edit still waiting to apply

    args = _argv(view)
    assert args.drive == repo.drive_root
    assert args.dest.parent == tmp_path / "share"
    assert re.fullmatch(r"Export \d{8} \d{4}", args.dest.name)
    assert args.structure == ["Year", "Model"] and not args.by_media_type
    assert args.jpeg_only and not args.mirror and not args.dry_run
    assert build_filter_query(args) == FilterOptions(cameras=["Canon EOS R8"])
    assert ctx.jobs.calls[-1].title == "Export · E"
    assert ctx.filters == FilterOptions()  # the Library's filters are untouched

    saved = load_json(app_data_dir() / "export.json")
    assert saved["jpeg_only"] is True and "dry_run" not in saved


def test_settings_are_remembered(qtbot, repo, make_item, monkeypatch, tmp_path):
    view, _ = _view(qtbot, repo, make_item, monkeypatch)
    view.dest.setText(str(tmp_path / "share"))
    view.keep_radio.setChecked(True)
    view.dry_run.setChecked(True)
    view.run_button.click()
    args = _argv(view)
    assert args.mirror and args.dry_run

    again = ExportView(view.ctx)
    qtbot.addWidget(again)
    assert again.dest.text() == str(tmp_path / "share")
    assert again.keep_radio.isChecked() and not again.dry_run.isChecked()
    assert not again.levels[0].isEnabled()


def test_folder_name_can_be_chosen(qtbot, repo, make_item, monkeypatch, tmp_path):
    view, _ = _view(qtbot, repo, make_item, monkeypatch)
    view.dest.setText(str(tmp_path))
    assert view.folder.text() == ""
    assert re.fullmatch(r"Export \d{8} \d{4}", view.folder.placeholderText())
    assert view.example.text().startswith(
        f"For example: {view.folder.placeholderText()}/"
    )
    view.folder.setText("Rome: day 1")
    assert not view.run_button.isEnabled()
    assert "folder name" in view.problem.text()
    view.folder.setText("Rome day 1")
    view.run_button.click()
    assert _argv(view).dest == tmp_path / "Rome day 1"

    again = ExportView(view.ctx)  # a name is for one export, not remembered
    qtbot.addWidget(again)
    assert again.folder.text() == ""
    assert "folder" not in load_json(app_data_dir() / "export.json")


def test_library_filters_are_copied_not_shared(qtbot, repo, make_item, monkeypatch):
    view, ctx = _view(qtbot, repo, make_item, monkeypatch)
    ctx.set_filters(FilterOptions(cameras=["Sony A7"]))
    assert view.filters.options == FilterOptions()  # not followed
    view.use_library.click()
    assert view.filters.options == FilterOptions(cameras=["Sony A7"])
    view.filters.clear()
    assert ctx.filters == FilterOptions(cameras=["Sony A7"])


def test_bad_filter_blocks_the_run(qtbot, repo, make_item, monkeypatch, tmp_path):
    view, _ = _view(qtbot, repo, make_item, monkeypatch)
    view.dest.setText(str(tmp_path))
    view.filters.texts["min_iso"].setText("lots")
    view.filters.apply()
    assert not view.run_button.isEnabled()
    assert view.summary.text() == export_view.FIX_FILTERS


def test_command_is_collapsed_until_asked_for(
    qtbot, repo, make_item, monkeypatch, tmp_path
):
    view, _ = _view(qtbot, repo, make_item, monkeypatch)
    assert not view.preview.isVisible()
    assert view.problem.isVisible()  # no destination: said without the command
    assert view.problem.text() == "Choose a destination folder."
    view.dest.setText(str(tmp_path))
    assert not view.problem.isVisible()
    view.command_toggle.click()
    assert view.preview.isVisible()
    assert view.preview.toPlainText().startswith("smart-gallery export")
    view.command_toggle.click()
    assert not view.preview.isVisible()


def test_jpeg_only_needs_exiftool(qtbot, repo, make_item, monkeypatch):
    view, _ = _view(qtbot, repo, make_item, monkeypatch, exiftool=False)
    assert not view.jpeg_only.isEnabled()
    assert view.jpeg_hint.text() == export_view.NO_EXIFTOOL


def test_page_switches_between_import_and_export(qtbot, repo, make_item, monkeypatch):
    monkeypatch.setattr(export_view, "exiftool_available", lambda: True)
    ctx = _ctx(repo, make_item)
    page = TransferPage(ctx)
    qtbot.addWidget(page)
    assert page.stack.currentWidget() is page.import_view
    assert page.header_controls is not None
    page.export_button.click()
    assert page.stack.currentWidget() is page.export_view
    ctx.open_in_import.emit("Canon SD card")
    assert page.stack.currentWidget() is page.import_view
