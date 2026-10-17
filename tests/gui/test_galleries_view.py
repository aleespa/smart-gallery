import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from smart_gallery.db import GalleryRepository
from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries import view as galleries_view
from smart_gallery.gui.galleries.store import GalleryStore
from smart_gallery.gui.galleries.view import GalleriesPanel
from smart_gallery.gui.history.store import HistoryStore
from smart_gallery.gui.jobs import JobRunner, JobSpec
from smart_gallery.gui.statusbar import JobStatusBar


def _catalog(root, label):
    root.mkdir()
    GalleryRepository.create(root, label=label).close()
    return root


def _panel(qtbot, tmp_path, *roots):
    store = GalleryStore(tmp_path / "galleries.json")
    for root in roots:
        store.add(root)
    ctx = AppContext()
    ctx.galleries = store
    panel = GalleriesPanel(ctx)
    qtbot.addWidget(panel)
    return panel, ctx, store


def test_panel_greys_out_disconnected(qtbot, tmp_path):
    here = _catalog(tmp_path / "here", "Here")
    gone = _catalog(tmp_path / "gone", "Gone")
    panel, ctx, store = _panel(qtbot, tmp_path, here, gone)
    shutil.rmtree(gone)
    panel.refresh()
    combo = panel.combo
    assert combo.itemText(0) == "Here"
    assert combo.itemText(1) == "Gone (not connected)"
    grey = combo.itemData(1, Qt.ItemDataRole.ForegroundRole)
    assert grey is not None
    assert combo.itemData(0, Qt.ItemDataRole.ForegroundRole) is None


def test_selecting_sets_gallery_and_remembers_it(qtbot, tmp_path):
    a = _catalog(tmp_path / "a", "A")
    b = _catalog(tmp_path / "b", "B")
    panel, ctx, store = _panel(qtbot, tmp_path, a, b)
    assert ctx.current.label == "A"
    with qtbot.waitSignal(ctx.gallery_changed):
        panel.combo.setCurrentIndex(1)
    assert ctx.current.label == "B"
    assert store.last() == ctx.current.db_uuid
    again = AppContext()
    again.galleries = store
    qtbot.addWidget(GalleriesPanel(again))
    assert again.current.label == "B"


def test_add_existing_registers_and_selects(qtbot, tmp_path, monkeypatch):
    panel, ctx, store = _panel(qtbot, tmp_path)
    assert ctx.current is None
    root = _catalog(tmp_path / "new", "New")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(root))
    panel.add_btn.click()
    assert [g.label for g in store.all()] == ["New"]
    assert ctx.current.label == "New"
    assert panel.combo.count() == 1


def test_add_existing_reports_a_folder_without_catalog(qtbot, tmp_path, monkeypatch):
    panel, ctx, store = _panel(qtbot, tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    shown = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(empty))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    panel.add_btn.click()
    assert len(shown) == 1
    assert store.all() == []


def test_disconnected_gallery_is_located_on_startup(qtbot, tmp_path, monkeypatch):
    old = _catalog(tmp_path / "old", "Old")
    store = GalleryStore(tmp_path / "galleries.json")
    g = store.add(old)
    # The same folder reappears under another "drive" and the old one vanishes.
    rel = Path(g.db_path).parent.parent.relative_to(Path(g.db_path).anchor)
    fake_drive = tmp_path / "fake_drive"
    remounted = fake_drive / rel
    shutil.copytree(old, remounted)
    shutil.rmtree(old)
    monkeypatch.setattr(galleries_view, "existing_drives", lambda: [str(fake_drive)])

    ctx = AppContext()
    ctx.galleries = store
    panel = GalleriesPanel(ctx)
    qtbot.addWidget(panel)
    assert [Path(x.db_path) for x in store.all()] == [
        (remounted / ".smart_gallery" / "gallery.db").resolve()
    ]
    assert panel.combo.itemText(0) == "Old"
    assert ctx.current.db_uuid == g.db_uuid


def test_create_button_is_disabled_until_jobs_exist(qtbot, tmp_path):
    panel, ctx, store = _panel(qtbot, tmp_path)
    assert not panel.create_btn.isEnabled()
    assert panel.create_btn.toolTip() == "Available once jobs are wired"


def _jobs_panel(qtbot, tmp_path, monkeypatch, folder):
    # "python -c pass init <folder>" exits 0 like a successful init.
    runner = JobRunner(
        HistoryStore(tmp_path / "history.jsonl"),
        workdir=tmp_path / "work",
        program=[sys.executable, "-c", "pass"],
        grace_ms=200,
    )
    store = GalleryStore(tmp_path / "galleries.json")
    ctx = AppContext()
    ctx.galleries = store
    ctx.jobs = runner
    panel = GalleriesPanel(ctx)
    qtbot.addWidget(panel)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder)
    )
    return panel, ctx, store, runner


def test_create_on_drive_runs_init_then_opens_the_gallery(qtbot, tmp_path, monkeypatch):
    root = _catalog(tmp_path / "new", "New")  # what a real init would leave behind
    panel, ctx, store, runner = _jobs_panel(qtbot, tmp_path, monkeypatch, root)
    assert panel.create_btn.isEnabled()
    seen = []
    runner.started.connect(seen.append)
    with qtbot.waitSignal(runner.finished, timeout=15000):
        panel.create_btn.click()
    assert seen[0].argvs == (("init", str(root)),)
    qtbot.waitUntil(lambda: ctx.current is not None, timeout=5000)
    assert ctx.current.label == "New"
    assert [g.label for g in store.all()] == ["New"]
    assert panel.combo.itemText(0) == "New"


def test_create_on_drive_ignores_failed_job(qtbot, tmp_path, monkeypatch):
    root = _catalog(tmp_path / "new", "New")
    panel, ctx, store, runner = _jobs_panel(qtbot, tmp_path, monkeypatch, root)
    runner._program = [sys.executable, "-c", "raise SystemExit(1)"]
    with qtbot.waitSignal(runner.finished, timeout=15000):
        panel.create_btn.click()
    assert store.all() == [] and ctx.current is None


def test_create_on_drive_tells_when_a_job_is_running(qtbot, tmp_path, monkeypatch):
    root = _catalog(tmp_path / "new", "New")
    panel, ctx, store, runner = _jobs_panel(qtbot, tmp_path, monkeypatch, root)
    runner._program = [sys.executable, "-c", "import time; time.sleep(30)"]
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text: shown.append(text)
    )
    assert runner.start(JobSpec("other", (("x",),)))
    panel.create_btn.click()
    assert shown == ["A job is already running."]
    with qtbot.waitSignal(runner.finished, timeout=15000):
        runner.cancel()


def test_main_window_builds_job_runner_and_forwards_catalog_updates(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    assert isinstance(win.ctx.jobs, JobRunner)
    assert isinstance(win.job_bar, JobStatusBar)
    assert win.galleries_panel.create_btn.isEnabled()
    with qtbot.waitSignal(win.ctx.catalog_updated):
        win.ctx.jobs.catalog_written.emit()


def test_main_window_mounts_panel_and_store(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    assert isinstance(win.ctx.galleries, GalleryStore)
    assert win.galleries_slot.itemAt(0).widget().__class__.__name__ == "GalleriesPanel"
