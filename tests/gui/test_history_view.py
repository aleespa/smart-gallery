import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QHeaderView, QMessageBox

from smart_gallery.colors import KEEP, NEUTRAL

from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.actions.view import ActionsView
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.history.store import HistoryStore, JobRecord
from smart_gallery.gui.history.view import HistoryView, _StatusDelegate, format_start
from smart_gallery.gui.jobs import JobRunner, JobSpec
from smart_gallery.gui.ui.widgets import ActionBar, EmptyState, status_pill

FAKE_CLI = [sys.executable, str(Path(__file__).with_name("fake_cli.py"))]


class FakeJobs(QObject):
    started = Signal(object)
    finished = Signal(object)

    def __init__(self, store):
        super().__init__()
        self.history = store
        self.running = False
        self.calls = []
        self.accept = True

    def start(self, spec):
        self.calls.append(spec)
        return self.accept


def _record(title, started="2026-10-17T10:00:00+00:00", argvs=None, **kwargs):
    fields = dict(
        title=title,
        argvs=argvs or [["sync", "E:/"]],
        started_at=started,
        ended_at=started[:14] + "01:05+00:00",  # 1m 5s after a :00:00 start
        status="ok",
        exit_code=0,
        summary=f"{title} summary",
        log_path=None,
        output_tail=["tail line"],
    )
    fields.update(kwargs)
    return JobRecord(**fields)


def _view(qtbot, tmp_path, *records):
    store = HistoryStore(tmp_path / "history.jsonl")
    for record in records:
        store.append(record)
    ctx = AppContext()
    ctx.jobs = FakeJobs(store)
    view = HistoryView(ctx)
    qtbot.addWidget(view)
    return view, ctx, store


def _cell(view, row, column):
    return view.table.item(row, column).text()


def test_history_lists_newest_first(qtbot, tmp_path):
    view, ctx, store = _view(
        qtbot, tmp_path, _record("Old"), _record("New", "2026-10-18T10:00:00+00:00")
    )
    assert [_cell(view, r, 0) for r in range(2)] == ["New", "Old"]
    assert _cell(view, 0, 2) == "1m 5s"
    assert _cell(view, 0, 3) == "ok"
    assert _cell(view, 0, 4) == "New summary"

    store.append(_record("Newest", "2026-10-19T10:00:00+00:00"))
    ctx.jobs.finished.emit(None)
    assert [_cell(view, r, 0) for r in range(3)] == ["Newest", "New", "Old"]


def test_run_again_single_opens_actions(qtbot, tmp_path):
    view, ctx, _ = _view(qtbot, tmp_path, _record("Sync", argvs=[["sync", "E:/"]]))
    events = []
    ctx.navigate.connect(lambda area: events.append(("navigate", area)))
    ctx.open_in_actions.connect(lambda argv: events.append(("open", argv)))
    assert not view.run_button.isEnabled()
    view.table.selectRow(0)
    view.run_button.click()
    assert events == [("navigate", "actions"), ("open", ["sync", "E:/"])]
    assert ctx.jobs.calls == []


def test_run_again_single_fills_actions_on_first_visit(qtbot, tmp_path):
    win = MainWindow()
    qtbot.addWidget(win)
    win.ctx.jobs.history.append(_record("Sync", argvs=[["sync", "E:/Photos"]]))
    win.ctx.navigate.emit("history")
    assert "actions" not in win._pages  # Actions has never been visited
    view = win._pages["history"]
    view.refresh()
    view.table.selectRow(0)
    view.run_button.click()
    actions = win._pages["actions"]
    assert isinstance(actions, ActionsView)
    assert win.stack.currentWidget() is actions
    argv = actions.page().result.argv  # the path field normalizes separators
    assert argv[0] == "sync" and Path(argv[1]) == Path("E:/Photos")


def test_run_again_sequence_confirms_then_starts(qtbot, tmp_path, monkeypatch):
    argvs = [["sync", "E:/"], ["scan-faces", "E:/"]]
    view, ctx, _ = _view(qtbot, tmp_path, _record("Two steps", argvs=argvs))
    answers = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
    asked = []

    def question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return next(answers)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    opened = []
    ctx.open_in_actions.connect(opened.append)
    view.table.selectRow(0)

    view.run_button.click()
    assert ctx.jobs.calls == [] and len(asked) == 1

    view.run_button.click()
    assert ctx.jobs.calls == [
        JobSpec("Two steps", (("sync", "E:/"), ("scan-faces", "E:/")), True)
    ]
    assert opened == []


def _no_question(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("must not ask")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fail))


def test_run_again_import_chain_opens_the_import_area(qtbot, tmp_path, monkeypatch):
    argvs = [
        ["import", "E:/", "--target", "F:/", "--file-types", "image", "--dry-run"],
        ["import", "E:/", "--target", "F:/", "--file-types", "video", "--dry-run"],
    ]
    record = _record("Import · Canon SD card (dry run)", argvs=argvs)
    view, ctx, _ = _view(qtbot, tmp_path, record)
    _no_question(monkeypatch)
    events = []
    ctx.navigate.connect(lambda area: events.append(("navigate", area)))
    ctx.open_in_import.connect(lambda name: events.append(("import", name)))
    ctx.open_in_actions.connect(lambda argv: events.append(("actions", argv)))
    view.table.selectRow(0)
    view.run_button.click()
    assert events == [("navigate", "library"), ("import", "Canon SD card")]
    assert ctx.jobs.calls == []


def test_run_again_single_import_from_a_profile_opens_the_import_area(
    qtbot, tmp_path, monkeypatch
):
    record = _record("Import · Pixel dump", argvs=[["import", "E:/"]])
    view, ctx, _ = _view(qtbot, tmp_path, record)
    events = []
    ctx.navigate.connect(lambda area: events.append(("navigate", area)))
    ctx.open_in_import.connect(lambda name: events.append(("import", name)))
    view.table.selectRow(0)
    view.run_button.click()
    assert events == [("navigate", "library"), ("import", "Pixel dump")]


def test_run_again_import_opens_the_library_import_window(qtbot, tmp_path):
    from smart_gallery.gui.imports.view import PICK_AGAIN, ImportView

    win = MainWindow()
    qtbot.addWidget(win)
    argvs = [["import", "E:/", "--file-types", "image"]] * 2
    win.ctx.jobs.history.append(_record("Import · Pixel dump", argvs=argvs))
    win.ctx.navigate.emit("history")
    view = win._pages["history"]
    view.refresh()
    view.table.selectRow(0)
    view.run_button.click()
    library = win._pages["library"]
    assert win.stack.currentWidget() is library
    window = library.import_window
    assert window.isVisible() and isinstance(window.form, ImportView)
    assert window.form.profile_combo.currentText() == "Pixel dump"
    assert window.form.note.text() == PICK_AGAIN


def test_run_again_disabled_while_a_job_runs(qtbot, tmp_path):
    view, ctx, _ = _view(qtbot, tmp_path, _record("Sync"))
    view.table.selectRow(0)
    assert view.run_button.isEnabled()
    ctx.jobs.running = True
    ctx.jobs.finished.emit(None)
    view.table.selectRow(0)
    assert not view.run_button.isEnabled()


def test_log_file_is_shown_when_present(qtbot, tmp_path):
    log = tmp_path / "job.log"
    log.write_text("line one\nline two\n", encoding="utf-8")
    view, _, _ = _view(qtbot, tmp_path, _record("Sync", log_path=str(log)))
    view.table.selectRow(0)
    assert view.log.toPlainText() == "line one\nline two\n"


def test_missing_log_falls_back_to_tail(qtbot, tmp_path):
    record = _record(
        "Sync", log_path=str(tmp_path / "gone.log"), output_tail=["a", "b"]
    )
    view, _, _ = _view(qtbot, tmp_path, record)
    view.table.selectRow(0)
    assert view.log.toPlainText() == "a\nb"


def test_bad_timestamps_do_not_break_the_list(qtbot, tmp_path):
    view, _, _ = _view(qtbot, tmp_path, _record("Odd", started="?", ended_at="?"))
    assert _cell(view, 0, 2) == ""


# ── closing the window while a job runs ─────────────────────────────────────


def _busy_window(qtbot, tmp_path):
    runner = JobRunner(
        HistoryStore(tmp_path / "history.jsonl"),
        workdir=tmp_path / "work",
        program=FAKE_CLI,
    )
    ctx = AppContext()
    ctx.jobs = runner
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    return win, runner


def test_close_without_job_closes(qtbot, tmp_path, monkeypatch):
    win, _ = _busy_window(qtbot, tmp_path)

    def fail(*args, **kwargs):
        raise AssertionError("must not ask")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fail))
    assert win.close()
    assert not win.isVisible()


def test_close_while_running_declined_keeps_job(qtbot, tmp_path, monkeypatch):
    win, runner = _busy_window(qtbot, tmp_path)
    asked = []

    def question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    assert runner.start(JobSpec("Wait", (("wait",),), writes_catalog=False))
    try:
        assert not win.close()
        assert asked == ["A job is still running. Cancel it and quit?"]
        assert win.isVisible() and runner.running
    finally:
        with qtbot.waitSignal(runner.finished, timeout=15000):
            runner.cancel()


def test_close_while_running_cancels_then_closes(qtbot, tmp_path, monkeypatch):
    win, runner = _busy_window(qtbot, tmp_path)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    assert runner.start(JobSpec("Wait", (("wait",),), writes_catalog=False))
    assert not win.close()  # the event is ignored; the window closes later
    assert win.isVisible()
    qtbot.waitUntil(lambda: not win.isVisible(), timeout=15000)
    assert not runner.running
    assert HistoryStore(tmp_path / "history.jsonl").load()[0].status == "cancelled"


def test_quit_while_already_stopping_does_not_force_stop(qtbot, tmp_path, monkeypatch):
    win, runner = _busy_window(qtbot, tmp_path)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    assert runner.start(JobSpec("Wait", (("wait",),), writes_catalog=False))
    forced = []
    monkeypatch.setattr(runner, "_kill", lambda: forced.append(True))
    runner.cancel()  # the user clicked Cancel before quitting
    assert not win.close()
    assert forced == []  # quitting waits for the safe stop
    qtbot.waitUntil(lambda: not win.isVisible(), timeout=15000)


def test_job_finishing_during_the_question_closes_without_stale_hook(
    qtbot, tmp_path, monkeypatch
):
    win, runner = _busy_window(qtbot, tmp_path)
    assert runner.start(JobSpec("Wait", (("wait",),), writes_catalog=False))

    def question(*args, **kwargs):
        with qtbot.waitSignal(runner.finished, timeout=15000):
            runner.cancel()  # the job ends while the dialog is still open
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    assert win.close()
    assert not win.isVisible()

    closes = []
    monkeypatch.setattr(win, "close", lambda: closes.append(True))
    with qtbot.waitSignal(runner.finished, timeout=15000):
        assert runner.start(JobSpec("Echo", (("echo", "x"),), writes_catalog=False))
    assert closes == []


def test_second_close_while_question_is_open_is_ignored(qtbot, tmp_path, monkeypatch):
    win, runner = _busy_window(qtbot, tmp_path)
    assert runner.start(JobSpec("Wait", (("wait",),), writes_catalog=False))
    asked = []

    def question(parent, title, text, buttons, default):
        asked.append(default)
        win.close()  # re-entrant request while the dialog is open
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    try:
        assert not win.close()
        assert asked == [QMessageBox.StandardButton.No]  # asked once, default No
    finally:
        with qtbot.waitSignal(runner.finished, timeout=15000):
            runner.cancel()


# Pills, start times, detail stack, action bar
NOW = datetime(2026, 11, 8, 12, 0)


def test_start_today_yesterday_else_date():
    assert format_start("2026-11-08T17:01:00", NOW) == "Today 17:01"
    assert format_start("2026-11-07T09:12:00", NOW) == "Yesterday 09:12"
    assert format_start("2026-11-01T09:12:00", NOW) == "2026-11-01 09:12"
    assert format_start("garbage", NOW) == "garbage"


def test_start_midnight_boundary():
    now = datetime(2026, 11, 8, 0, 1)
    assert format_start("2026-11-07T23:59:00", now) == "Yesterday 23:59"


def test_start_tz_aware_is_converted_to_local():
    aware = datetime(2026, 11, 8, 10, 0).astimezone()
    expected = f"Today {aware:%H:%M}"
    assert format_start(aware.isoformat(timespec="seconds"), NOW) == expected
    utc = aware.astimezone(timezone.utc).isoformat(timespec="seconds")
    assert format_start(utc, NOW) == expected


def _pill_pixel(status):
    img = QImage(80, 24, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    status_pill(painter, QRectF(0, 0, 80, 24), status)
    painter.end()
    return img.pixelColor(8, 12)


def _distance(a, b):
    b = QColor(b)
    return (
        abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
    )


def test_status_pill_paints_onto_an_image(qtbot):
    ok, weird = _pill_pixel("ok"), _pill_pixel("weird")
    assert ok.alpha() > 0 and weird.alpha() > 0
    assert _distance(ok, KEEP) < _distance(ok, NEUTRAL)
    assert _distance(weird, NEUTRAL) < _distance(weird, KEEP)


def test_status_column_uses_a_pill_delegate(qtbot, tmp_path):
    view = _view(qtbot, tmp_path, _record("A", status="weird"))[0]
    assert isinstance(view.table.itemDelegateForColumn(3), _StatusDelegate)
    assert _cell(view, 0, 3) == "weird"


def test_header_and_resize_modes(qtbot, tmp_path):
    view = _view(qtbot, tmp_path, _record("A"))[0]
    header = view.table.horizontalHeader()
    assert header.defaultAlignment() & Qt.AlignmentFlag.AlignLeft
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.ResizeToContents


def test_detail_empty_until_selected(qtbot, tmp_path):
    view = _view(qtbot, tmp_path, _record("A", output_tail=["hello"]))[0]
    assert isinstance(view.detail.currentWidget(), EmptyState)
    assert view.detail.currentWidget().text.text() == "Select a job to see its log"
    view.table.selectRow(0)
    assert view.detail.currentWidget() is view.log
    assert "hello" in view.log.toPlainText()
    assert isinstance(view.footer, ActionBar)
