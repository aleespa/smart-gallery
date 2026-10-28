"""The Library header's Mirror button: menu by pairing state, and its jobs."""

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QMessageBox

from smart_gallery.db import GalleryRepository
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.history.store import JobRecord
from smart_gallery.gui.mirror import menu as menu_module
from smart_gallery.gui.mirror.dialogs import FreeUpDialog, SyncPreviewDialog
from smart_gallery.gui.mirror.menu import MirrorButton
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.services import init_drive, pair_galleries

PLAN_CLASSES = (
    "copy_new",
    "overwrite",
    "trash",
    "moves",
    "only_on_mirror",
    "changed_on_mirror",
    "conflicts",
    "adopt",
    "offloaded_changed",
    "offloaded_missing",
    "offloaded_conflict",
)


class FakeJobs(QObject):
    started = Signal(object)
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.specs = []
        self.running = False

    def start(self, spec):
        self.specs.append(spec)
        return True

    def finish(self, index=-1, status="ok", tail=()):
        spec = self.specs[index]
        self.finished.emit(
            JobRecord(
                title=spec.title,
                argvs=[list(a) for a in spec.argvs],
                started_at="",
                ended_at="",
                status=status,
                exit_code=0 if status == "ok" else 1,
                summary=tail[-1] if tail else "",
                log_path=None,
                output_tail=list(tail),
            )
        )


class FakeGalleries:
    def __init__(self, galleries):
        self.galleries = list(galleries)

    def all(self):
        return list(self.galleries)


def _gallery(root: Path, label: str) -> Gallery:
    with GalleryRepository.open(root, read_only=True) as repo:
        return Gallery(repo.get_meta("db_uuid"), str(repo.db_path), label)


def _drives(tmp_path, *names):
    out = []
    for name in names:
        root = tmp_path / name
        root.mkdir()
        init_drive(root)
        out.append(_gallery(root, f"Drive {name}"))
    return out


def _pair(working: Gallery, mirror: Gallery):
    with GalleryRepository.open(working.db_path) as w:
        with GalleryRepository.open(mirror.db_path) as m:
            pair_galleries(w, m)


def _button(qtbot, current, galleries, jobs=None):
    ctx = AppContext()
    ctx.galleries = FakeGalleries(galleries)
    ctx.jobs = jobs if jobs is not None else FakeJobs()
    ctx.set_gallery(current)
    button = MirrorButton(ctx)
    qtbot.addWidget(button)
    return ctx, button


def _entries(button):
    return [a for a in button.menu().actions() if not a.isSeparator()]


def _texts(button):
    return [a.text() for a in _entries(button)]


def _action(button, text):
    (action,) = [a for a in _entries(button) if a.text() == text]
    return action


def _plan(**overrides):
    plan = {name: [] for name in PLAN_CLASSES}
    plan.update(copy_bytes=0, mirror_free_bytes=10**9, mirror_trash_bytes=0)
    plan["large_delete"] = False
    plan.update(overrides)
    plan["counts"] = {name: len(plan[name]) for name in PLAN_CLASSES}
    return plan


def test_button_hidden_when_unpaired_and_alone(qtbot, tmp_path):
    (e,) = _drives(tmp_path, "E")
    _ctx, button = _button(qtbot, e, [e])
    assert button.isHidden()


def test_unpaired_offers_pairing(qtbot, tmp_path, monkeypatch):
    e, d = _drives(tmp_path, "E", "D")
    ctx, button = _button(qtbot, e, [e, d])
    assert not button.isHidden()
    assert _texts(button) == ["Pair with another gallery…"]

    class Picker:
        def __init__(self, galleries, parent=None):
            assert [g.db_uuid for g in galleries] == [d.db_uuid]

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected(self):
            return d

    monkeypatch.setattr(menu_module, "PairDialog", Picker)
    _action(button, "Pair with another gallery…").trigger()

    (spec,) = ctx.jobs.specs
    assert spec.argvs == (("pair", e.db_path, d.db_path),)


def test_working_side_menu_entries(qtbot, tmp_path):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    _ctx, button = _button(qtbot, e, [e, d])
    assert _texts(button) == [
        "Sync to Drive D…",
        "Free up space…",
        "Bring back…",
        "Pair with another gallery…",
    ]


def test_one_sync_entry_per_mirror(qtbot, tmp_path):
    e, d1, d2 = _drives(tmp_path, "E", "D1", "D2")
    _pair(e, d1)
    _pair(e, d2)
    # D2 is not a known gallery: its label falls back to its drive root.
    _ctx, button = _button(qtbot, e, [e, d1])
    syncs = [t for t in _texts(button) if t.startswith("Sync to ")]
    assert syncs == ["Sync to Drive D1…", f"Sync to {tmp_path / 'D2'}…"]


def test_mirror_side_shows_disabled_entry(qtbot, tmp_path):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    _ctx, button = _button(qtbot, d, [e, d])
    (entry,) = _entries(button)
    assert entry.text() == "Open Drive E to sync"
    assert not entry.isEnabled()


def test_menu_refreshes_on_catalog_update(qtbot, tmp_path):
    e, d = _drives(tmp_path, "E", "D")
    ctx, button = _button(qtbot, e, [e, d])
    _pair(e, d)
    ctx.catalog_updated.emit()
    assert _texts(button)[0] == "Sync to Drive D…"


def test_sync_runs_dry_run_then_apply_with_dialog_flags(qtbot, tmp_path, monkeypatch):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    ctx, button = _button(qtbot, e, [e, d])
    shown = []

    class Preview:
        def __init__(self, plan, parent=None, mirror_label=""):
            shown.append((plan, mirror_label))

        def exec(self):
            return QDialog.DialogCode.Accepted

        def flags(self):
            return ["--only-on-mirror", "copy-back", "--confirm-large-delete"]

    monkeypatch.setattr(menu_module, "SyncPreviewDialog", Preview)
    _action(button, "Sync to Drive D…").trigger()

    (spec,) = ctx.jobs.specs
    (argv,) = spec.argvs
    assert argv[:6] == (
        "sync-mirror",
        e.db_path,
        "--mirror",
        d.db_path,
        "--dry-run",
        "--plan-out",
    )
    plan_path = Path(argv[6])
    assert plan_path.parent == app_data_dir() / "mirror"
    assert plan_path.parent.is_dir()  # --plan-out does not create it
    plan = _plan(copy_new=["a.jpg"], copy_bytes=5)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    ctx.jobs.finish()
    qtbot.waitUntil(lambda: len(ctx.jobs.specs) == 2)

    assert shown == [(plan, "Drive D")]
    apply = ctx.jobs.specs[1]
    assert apply.argvs == (
        (
            "sync-mirror",
            e.db_path,
            "--mirror",
            d.db_path,
            "--only-on-mirror",
            "copy-back",
            "--confirm-large-delete",
        ),
    )


def test_sync_not_applied_when_dialog_cancelled(qtbot, tmp_path, monkeypatch):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    ctx, button = _button(qtbot, e, [e, d])
    shown = []

    class Preview:
        def __init__(self, plan, parent=None, mirror_label=""):
            shown.append(plan)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(menu_module, "SyncPreviewDialog", Preview)
    _action(button, "Sync to Drive D…").trigger()
    Path(ctx.jobs.specs[0].argvs[0][6]).write_text(json.dumps(_plan()), "utf-8")
    ctx.jobs.finish()
    qtbot.waitUntil(lambda: len(shown) == 1)
    assert len(ctx.jobs.specs) == 1


def _recording_preview(monkeypatch, result=QDialog.DialogCode.Accepted):
    shown = []

    class Preview:
        def __init__(self, plan, parent=None, mirror_label=""):
            shown.append(plan)

        def exec(self):
            return result

        def flags(self):
            return ["--only-on-mirror", "keep-on-mirror"]

    monkeypatch.setattr(menu_module, "SyncPreviewDialog", Preview)
    return shown


def _start_sync(ctx, button, d, plan=None):
    """Click "Sync to D…" and write the plan the dry run would leave."""
    before = len(ctx.jobs.specs)
    _action(button, "Sync to Drive D…").trigger()
    assert len(ctx.jobs.specs) == before + 1
    path = Path(ctx.jobs.specs[-1].argvs[0][6])
    path.write_text(json.dumps(plan or _plan()), encoding="utf-8")
    return len(ctx.jobs.specs) - 1


def test_other_jobs_finishing_are_ignored(qtbot, tmp_path, monkeypatch):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    ctx, button = _button(qtbot, e, [e, d])
    shown = _recording_preview(monkeypatch)
    plan = _plan(copy_new=["a.jpg"])
    dry = _start_sync(ctx, button, d, plan)

    # An unrelated job ends first: it must not be taken for the dry run.
    ctx.jobs.specs.append(menu_module.JobSpec("Other", (("sync", e.db_path),)))
    ctx.jobs.finish()
    qtbot.wait(50)
    assert shown == []
    assert len(ctx.jobs.specs) == 2  # the dry run and the other job only

    ctx.jobs.finish(index=dry)
    qtbot.waitUntil(lambda: len(ctx.jobs.specs) == 3)
    assert shown == [plan]
    assert ctx.jobs.specs[2].argvs == (
        (
            "sync-mirror",
            e.db_path,
            "--mirror",
            d.db_path,
            "--only-on-mirror",
            "keep-on-mirror",
        ),
    )


def _dry_run_ending_without_preview(qtbot, tmp_path, monkeypatch, status):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    ctx, button = _button(qtbot, e, [e, d])
    shown = _recording_preview(monkeypatch)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    dry = _start_sync(ctx, button, d)

    ctx.jobs.finish(index=dry, status=status, tail=["boom"])
    qtbot.wait(50)
    assert shown == [] and len(ctx.jobs.specs) == 1
    assert button._pending == {}
    assert len(warned) == (1 if status == "failed" else 0)

    # A later click starts afresh and its dry run still leads to a preview.
    again = _start_sync(ctx, button, d)
    ctx.jobs.finish(index=again)
    qtbot.waitUntil(lambda: len(ctx.jobs.specs) == 3)
    assert len(shown) == 1


def test_failed_dry_run_does_not_preview_or_apply(qtbot, tmp_path, monkeypatch):
    _dry_run_ending_without_preview(qtbot, tmp_path, monkeypatch, "failed")


def test_cancelled_dry_run_does_not_preview_or_apply(qtbot, tmp_path, monkeypatch):
    _dry_run_ending_without_preview(qtbot, tmp_path, monkeypatch, "cancelled")


def test_running_job_refuses_sync(qtbot, tmp_path, monkeypatch):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    ctx, button = _button(qtbot, e, [e, d])
    ctx.jobs.running = True
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    _action(button, "Sync to Drive D…").trigger()
    assert ctx.jobs.specs == [] and told == [menu_module.JOB_RUNNING]


def test_large_delete_checkbox_adds_confirm_flag(qtbot):
    dialog = SyncPreviewDialog(
        _plan(trash=["a", "b"], large_delete=True), mirror_label="Drive D"
    )
    qtbot.addWidget(dialog)
    assert not dialog.confirm.isHidden()
    assert not dialog.ok_button.isEnabled()
    assert "--confirm-large-delete" not in dialog.flags()

    dialog.confirm.setChecked(True)

    assert dialog.ok_button.isEnabled()
    assert dialog.flags()[-1] == "--confirm-large-delete"


def test_preview_defaults_and_warnings(qtbot):
    dialog = SyncPreviewDialog(
        _plan(trash=["a", "b", "c"], offloaded_missing=["gone.jpg"]),
        mirror_label="Drive D",
    )
    qtbot.addWidget(dialog)
    assert dialog.confirm.isHidden()
    assert dialog.ok_button.isEnabled()
    assert dialog.only_on.currentText() == "Keep on Drive D only"
    assert dialog.flags() == [
        "--only-on-mirror",
        "keep-on-mirror",
        "--changed-on-mirror",
        "skip",
        "--conflicts",
        "skip",
    ]
    assert "Move 3 files on Drive D to its trash" in dialog.summary_text()
    assert not dialog.missing_warning.isHidden()


def test_free_up_dialog_argv(qtbot):
    dialog = FreeUpDialog(mirrors=[("Drive D1", "d1.db"), ("Drive D2", "d2.db")])
    qtbot.addWidget(dialog)
    dialog.types["image"].setChecked(False)
    dialog.types["video"].setChecked(True)
    dialog.folder.setText("2019")
    dialog.mirror.setCurrentIndex(1)
    assert dialog.argv() == [
        "--mirror",
        "d2.db",
        "--file-types",
        "video",
        "--folder",
        "2019",
    ]


def test_free_up_runs_dry_run_then_asks(qtbot, tmp_path, monkeypatch):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    ctx, button = _button(qtbot, e, [e, d])

    class Picker:
        def __init__(self, parent=None, mirrors=(), **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def argv(self):
            return ["--file-types", "video"]

    asked = []

    def question(_parent, _title, text, *args):
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(menu_module, "FreeUpDialog", Picker)
    monkeypatch.setattr(QMessageBox, "question", question)
    _action(button, "Free up space…").trigger()
    (spec,) = ctx.jobs.specs
    assert spec.argvs == (("free-up", e.db_path, "--file-types", "video", "--dry-run"),)

    ctx.jobs.finish(tail=["x - SUCCESS - Would free 3 file(s), 2.0 MB.", "Skipped: a"])
    qtbot.waitUntil(lambda: len(ctx.jobs.specs) == 2)

    assert "Would free 3 file(s), 2.0 MB." in asked[0]
    assert ctx.jobs.specs[1].argvs == (("free-up", e.db_path, "--file-types", "video"),)


def test_button_hidden_when_drive_not_connected(qtbot, tmp_path):
    (e,) = _drives(tmp_path, "E")
    gone = Gallery("gone", str(tmp_path / "none" / "gallery.db"), "Gone")
    _ctx, button = _button(qtbot, gone, [gone, e])
    assert button.isHidden()


def test_library_greys_offloaded_items(qtbot, tmp_path, make_item):
    from PySide6.QtCore import Qt

    from smart_gallery.gui.library.view import LibraryView

    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    with GalleryRepository.open(e.db_path) as w:
        w.upsert_many([make_item("a.jpg"), make_item("b.jpg")])
        w.add_offloaded(["a.jpg"], d.db_uuid)
    ctx = AppContext()
    ctx.galleries = FakeGalleries([e, d])
    ctx.set_gallery(e)
    view = LibraryView(ctx)
    qtbot.addWidget(view)
    model = view.grid_model
    qtbot.waitUntil(lambda: len(model.rows) == 2)

    tips = {
        r.relpath: model.data(model.index(i), Qt.ItemDataRole.ToolTipRole)
        for i, r in enumerate(model.rows)
    }
    assert tips == {"a.jpg": "On Drive D only", "b.jpg": "b.jpg"}
    offloaded = next(r for r in model.rows if r.offloaded)
    assert not model.needs_thumbnail(offloaded)
    assert view.mirror_btn.parent() is view.header_controls
    assert not view.mirror_btn.isHidden()


def test_free_up_unparseable_output_does_not_delete(qtbot, tmp_path, monkeypatch):
    e, d = _drives(tmp_path, "E", "D")
    _pair(e, d)
    ctx, button = _button(qtbot, e, [e, d])

    class Picker:
        def __init__(self, parent=None, mirrors=(), **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def argv(self):
            return ["--file-types", "video"]

    told, asked = [], []
    monkeypatch.setattr(menu_module, "FreeUpDialog", Picker)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: asked.append(a) or QMessageBox.Yes
    )
    _action(button, "Free up space…").trigger()
    assert len(ctx.jobs.specs) == 1

    ctx.jobs.finish(tail=["x - INFO - Syncing catalogs", "Skipped: a.mp4"])
    qtbot.waitUntil(lambda: bool(told))

    assert told == [menu_module.NOTHING_TO_FREE]
    assert asked == []
    assert len(ctx.jobs.specs) == 1
    assert button._pending == {}


def test_preview_warns_about_files_at_freed_up_paths(qtbot):
    dialog = SyncPreviewDialog(
        _plan(offloaded_conflict=["a.jpg"]), mirror_label="Drive D"
    )
    qtbot.addWidget(dialog)
    assert not dialog.conflict_warning.isHidden()
    assert "a.jpg" in dialog.conflict_warning.text()
    plain = SyncPreviewDialog(_plan(), mirror_label="Drive D")
    qtbot.addWidget(plain)
    assert plain.conflict_warning.isHidden()
