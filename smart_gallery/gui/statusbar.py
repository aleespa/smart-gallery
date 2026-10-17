"""Strip along the bottom of the window showing the running job."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)

from smart_gallery.gui.jobs import JobRunner
from smart_gallery.progress import ProgressEvent

LAST_JOB_MS = 10_000
STOPPING = "Stopping after the current file… (click again to force)"


def format_outcomes(outcomes: tuple[tuple[str, int], ...]) -> str:
    """``copied 10 · skipped 2 · failed 0``"""
    return " · ".join(f"{name} {count}" for name, count in outcomes)


class JobStatusBar(QWidget):
    def __init__(self, runner: JobRunner, parent: QWidget | None = None):
        super().__init__(parent)
        self.runner = runner
        self.title = QLabel()
        self.steps = QLabel()
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(220)
        self.detail = QLabel()
        self.cancel_btn = QPushButton("Cancel")
        self.last_job = QLabel()
        self._stopping = False
        self._running = [self.title, self.steps, self.bar, self.detail, self.cancel_btn]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        for widget in self._running:
            layout.addWidget(widget)
        layout.addStretch(1)
        layout.addWidget(self.last_job)

        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(LAST_JOB_MS)
        self._clear_timer.timeout.connect(self.last_job.clear)

        self.cancel_btn.clicked.connect(self._cancel)
        runner.started.connect(self._on_started)
        runner.step.connect(self._on_step)
        runner.progress.connect(self._on_progress)
        runner.finished.connect(self._on_finished)
        self._show_running(False)

    def _show_running(self, running: bool) -> None:
        for widget in self._running:
            widget.setVisible(running)

    def _cancel(self) -> None:
        if self._stopping:  # "Force stop"
            self.cancel_btn.setEnabled(False)
        else:
            self._stopping = True
            self.detail.setText(STOPPING)
            self.cancel_btn.setText("Force stop")
        self.runner.cancel()

    def _on_started(self, spec) -> None:
        self._clear_timer.stop()
        self.last_job.clear()
        self.title.setText(spec.title)
        self.steps.clear()
        self.detail.clear()
        self.bar.setRange(0, 0)
        self._stopping = False
        self.cancel_btn.setText("Cancel")
        self.cancel_btn.setEnabled(True)
        self._show_running(True)
        self.steps.setVisible(False)

    def _on_step(self, index: int, count: int) -> None:
        self.steps.setText(f"step {index} of {count}")
        self.steps.setVisible(count > 1)
        self.bar.setRange(0, 0)

    def _on_progress(self, event: ProgressEvent) -> None:
        if event.total:
            self.bar.setRange(0, event.total)
            self.bar.setValue(min(event.done, event.total))
            counts = f"{event.done} / {event.total} {event.unit}"
        else:
            self.bar.setRange(0, 0)  # total not known yet: busy
            counts = f"{event.done} {event.unit}"
        if self._stopping:
            return  # keep the stopping note until the job ends
        parts = [event.step, counts]
        if event.outcomes:
            parts.append(format_outcomes(event.outcomes))
        self.detail.setText(" · ".join(parts))

    def _on_finished(self, record) -> None:
        self._show_running(False)
        self.last_job.setText(f"Last job: {record.status}")
        self._clear_timer.start()
