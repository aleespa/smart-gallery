import sys
from pathlib import Path

from PySide6.QtCore import QProcess

from smart_gallery.gui.history.store import HistoryStore
from smart_gallery.gui.jobs import JobRunner, JobSpec
from smart_gallery.progress import ProgressEvent

FAKE_CLI = [sys.executable, str(Path(__file__).with_name("fake_cli.py"))]


def _runner(tmp_path, program=None, **kwargs):
    return JobRunner(
        HistoryStore(tmp_path / "history.jsonl"),
        workdir=tmp_path / "work",
        program=FAKE_CLI if program is None else program,
        **kwargs,
    )


def _run(qtbot, runner, spec, timeout=15000):
    with qtbot.waitSignal(runner.finished, timeout=timeout) as blocker:
        assert runner.start(spec)
    return blocker.args[0]


def test_runner_emits_progress_and_records_ok(qtbot, tmp_path):
    runner = _runner(tmp_path)
    events, steps, outputs, written = [], [], [], []
    runner.progress.connect(events.append)
    runner.step.connect(lambda i, n: steps.append((i, n)))
    runner.output.connect(outputs.append)
    runner.catalog_written.connect(lambda: written.append(True))

    record = _run(qtbot, runner, JobSpec("Copy", (("progress", "3"),)))

    assert record.status == "ok" and record.exit_code == 0
    assert [e.done for e in events] == [1, 2, 3]
    assert isinstance(events[0], ProgressEvent) and events[-1].total == 3
    assert steps == [(1, 1)]
    assert outputs and all("@sg-progress" not in line for line in outputs)
    assert record.log_path == str(Path("logs") / "progress.log")
    assert record.summary == ""  # the only non-progress line is "Details:"
    assert len(record.output_tail) == 1
    assert record.output_tail[0].endswith(f" - Details: {record.log_path}")
    assert written == [True]
    assert not runner.running
    assert HistoryStore(tmp_path / "history.jsonl").load() == [record]
    assert list((tmp_path / "work" / "jobs").glob("*.stop")) == []


def test_summary_is_last_line_before_details(qtbot, tmp_path):
    runner = _runner(tmp_path)
    record = _run(qtbot, runner, JobSpec("Echo", (("echo", "all", "done"),)))
    assert record.summary == "all done"


def test_catalog_written_only_when_spec_writes_catalog(qtbot, tmp_path):
    runner = _runner(tmp_path)
    written = []
    runner.catalog_written.connect(lambda: written.append(True))
    _run(qtbot, runner, JobSpec("Echo", (("echo", "x"),), writes_catalog=False))
    assert written == []


def test_sequence_stops_at_first_failure(qtbot, tmp_path):
    runner = _runner(tmp_path)
    steps = []
    runner.step.connect(lambda i, n: steps.append((i, n)))
    spec = JobSpec("Seq", (("progress", "2"), ("fail",), ("progress", "2")))
    record = _run(qtbot, runner, spec)
    assert steps == [(1, 3), (2, 3)]
    assert record.status == "failed"
    assert record.exit_code == 1
    # loguru prefix and ANSI colors are gone from the summary; log_path is parsed
    assert record.summary == "something broke"
    assert record.log_path == str(Path("logs") / "fail.log")
    assert "|" in record.output_tail[0]  # the full line is kept in the tail


def test_cancel_cooperative(qtbot, tmp_path):
    runner = _runner(tmp_path)
    steps = []
    runner.step.connect(lambda i, n: steps.append(i))
    with qtbot.waitSignal(runner.output, timeout=10000):  # the child is running
        assert runner.start(JobSpec("Wait", (("wait",), ("progress", "2"))))
    with qtbot.waitSignal(runner.finished, timeout=5000) as blocker:
        runner.cancel()
    record = blocker.args[0]
    assert record.status == "cancelled"
    assert record.exit_code == 130
    assert steps == [1]  # the second argv never started
    assert list((tmp_path / "work" / "jobs").glob("*.stop")) == []


def test_cancel_kills_after_grace(qtbot, tmp_path):
    runner = _runner(tmp_path, grace_ms=500)
    with qtbot.waitSignal(runner.output, timeout=10000):
        assert runner.start(JobSpec("Hang", (("hang",),)))
    with qtbot.waitSignal(runner.finished, timeout=10000) as blocker:
        runner.cancel()
    assert blocker.args[0].status == "cancelled"
    assert not runner.running


def test_second_start_rejected_while_running(qtbot, tmp_path):
    runner = _runner(tmp_path)
    assert runner.start(JobSpec("Wait", (("wait",),)))
    assert runner.running
    assert not runner.start(JobSpec("Echo", (("echo", "x"),)))
    with qtbot.waitSignal(runner.finished, timeout=5000):
        runner.cancel()


def test_unicode_argv_reaches_cli(qtbot, tmp_path):
    runner = _runner(tmp_path)
    outputs = []
    runner.output.connect(outputs.append)
    _run(qtbot, runner, JobSpec("Echo", (("echo", "Mamá"),)))
    assert "Mamá" in outputs


def test_failure_to_start_is_a_failed_record(qtbot, tmp_path):
    runner = _runner(tmp_path, program=[str(tmp_path / "no-such-program.exe")])
    record = _run(qtbot, runner, JobSpec("Nope", (("x",),)))
    assert record.status == "failed"
    assert record.exit_code is None
    assert record.summary
    assert not runner.running


def test_ansi_codes_are_stripped_from_output(qtbot, tmp_path):
    code = "print('\\x1b[31mred\\x1b[0m')"
    runner = _runner(tmp_path, program=[sys.executable, "-c", code])
    outputs = []
    runner.output.connect(outputs.append)
    record = _run(qtbot, runner, JobSpec("Ansi", ((),)))
    assert outputs == ["red"]
    assert record.output_tail == ["red"]


def test_cancel_falls_back_to_kill_when_taskkill_cannot_start(
    qtbot, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        QProcess, "startDetached", staticmethod(lambda *args: (False, -1))
    )
    runner = _runner(tmp_path, grace_ms=300)
    with qtbot.waitSignal(runner.output, timeout=10000):
        assert runner.start(JobSpec("Hang", (("hang",),)))
    with qtbot.waitSignal(runner.finished, timeout=10000) as blocker:
        runner.cancel()
    assert blocker.args[0].status == "cancelled"


def test_cancel_kills_when_taskkill_did_not_stop_the_process(
    qtbot, tmp_path, monkeypatch
):
    launched = []
    monkeypatch.setattr(
        QProcess,
        "startDetached",
        staticmethod(lambda *args: (launched.append(args) or True, 1)),
    )
    runner = _runner(tmp_path, grace_ms=300)
    with qtbot.waitSignal(runner.output, timeout=10000):
        assert runner.start(JobSpec("Hang", (("hang",),)))
    with qtbot.waitSignal(runner.finished, timeout=10000) as blocker:
        runner.cancel()
    assert blocker.args[0].status == "cancelled"
    assert len(launched) == (1 if sys.platform == "win32" else 0)


def test_second_cancel_kills_at_once(qtbot, tmp_path):
    runner = _runner(tmp_path)  # the default grace is far longer than this test
    with qtbot.waitSignal(runner.output, timeout=10000):
        assert runner.start(JobSpec("Hang", (("hang",),)))
    runner.cancel()
    with qtbot.waitSignal(runner.finished, timeout=5000) as blocker:
        runner.cancel()  # "Force stop"
    assert blocker.args[0].status == "cancelled"
    assert not runner.running
