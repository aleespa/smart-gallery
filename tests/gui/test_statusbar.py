from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal

from smart_gallery.gui.statusbar import JobStatusBar, format_outcomes
from smart_gallery.progress import ProgressEvent


class _Runner(QObject):
    started = Signal(object)
    step = Signal(int, int)
    progress = Signal(object)
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def _bar(qtbot):
    runner = _Runner()
    bar = JobStatusBar(runner)
    qtbot.addWidget(bar)
    bar.show()
    return runner, bar


def test_outcomes_format():
    assert (
        format_outcomes((("copied", 10), ("skipped", 2), ("failed", 0)))
        == "copied 10 · skipped 2 · failed 0"
    )


def test_status_bar_follows_a_job(qtbot):
    runner, bar = _bar(qtbot)
    assert not bar.cancel_btn.isVisible()

    runner.started.emit(SimpleNamespace(title="Import"))
    runner.step.emit(1, 2)
    assert bar.title.text() == "Import"
    assert bar.steps.isVisible() and bar.steps.text() == "step 1 of 2"
    assert bar.bar.maximum() == 0  # busy until a total is known

    outcomes = (("copied", 10), ("skipped", 2), ("failed", 0))
    runner.progress.emit(ProgressEvent("Copying files", 12, 40, outcomes))
    assert (bar.bar.maximum(), bar.bar.value()) == (40, 12)
    assert "copied 10 · skipped 2 · failed 0" in bar.detail.text()

    runner.progress.emit(ProgressEvent("Finding files", 5))
    assert bar.bar.maximum() == 0

    bar.cancel_btn.click()
    assert runner.cancelled

    runner.finished.emit(SimpleNamespace(status="cancelled"))
    assert not bar.cancel_btn.isVisible()
    assert bar.last_job.text() == "Last job: cancelled"


def test_single_step_job_hides_step_label(qtbot):
    runner, bar = _bar(qtbot)
    runner.started.emit(SimpleNamespace(title="Sync"))
    runner.step.emit(1, 1)
    assert not bar.steps.isVisible()
