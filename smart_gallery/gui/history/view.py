"""History area: the jobs the app has run, with their logs and a way to rerun."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.history.store import JobRecord
from smart_gallery.gui.imports.model import profile_name_from_title
from smart_gallery.gui.jobs import JobSpec

COLUMNS = ("Job", "Started", "Duration", "Status", "Summary")
MAX_LOG_BYTES = 1_000_000  # a detail log can hold a line per file
JOB_RUNNING = "A job is already running."


def format_start(started_at: str) -> str:
    try:
        return datetime.fromisoformat(started_at).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return started_at


def format_duration(started_at: str, ended_at: str) -> str:
    """``1h 2m 5s``, or empty when either time cannot be read."""
    try:
        seconds = int(
            (
                datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
            ).total_seconds()
        )
    except ValueError:
        return ""
    if seconds < 0:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def read_log(path: str) -> str | None:
    """The end of the log file, or None when it cannot be read."""
    try:
        with Path(path).open("rb") as fh:
            size = fh.seek(0, 2)
            fh.seek(max(0, size - MAX_LOG_BYTES))
            data = fh.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    if size > MAX_LOG_BYTES:
        text = "... (earlier lines not shown)\n" + text
    return text


class HistoryView(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._records: list[JobRecord] = []

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setStyleSheet("font-family: Consolas, monospace;")
        self.run_button = QPushButton("Run again")
        self.run_button.setEnabled(False)
        self.note = QLabel()

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.note, 1)
        buttons.addWidget(self.run_button)
        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addLayout(buttons)

        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.run_button.clicked.connect(self._run_again)
        if ctx.jobs is not None:
            ctx.jobs.started.connect(
                lambda _spec: self._update_run_button(self._selected())
            )
            ctx.jobs.finished.connect(lambda _record: self.refresh())
        self.refresh()

    # ── list ─────────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Reload the list, keeping the selected job selected."""
        selected = self._selected()
        key = (selected.started_at, selected.title) if selected else None
        self._records = self.ctx.jobs.history.load() if self.ctx.jobs else []
        self.table.setRowCount(len(self._records))
        row_to_select = -1
        for row, record in enumerate(self._records):
            cells = (
                record.title,
                format_start(record.started_at),
                format_duration(record.started_at, record.ended_at),
                record.status,
                record.summary,
            )
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))
            if (record.started_at, record.title) == key:
                row_to_select = row
        self.table.clearSelection()
        if row_to_select >= 0:
            self.table.selectRow(row_to_select)
        self._selection_changed()

    def _selected(self) -> JobRecord | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._records):
            return None
        return self._records[rows[0].row()]

    def _selection_changed(self) -> None:
        record = self._selected()
        if record is None:
            self.detail.clear()
        else:
            text = read_log(record.log_path) if record.log_path else None
            self.detail.setPlainText(
                text if text is not None else "\n".join(record.output_tail)
            )
        self._update_run_button(record)

    def _update_run_button(self, record: JobRecord | None) -> None:
        busy = bool(self.ctx.jobs is not None and self.ctx.jobs.running)
        self.run_button.setEnabled(bool(record and record.argvs) and not busy)
        self.run_button.setToolTip(JOB_RUNNING if busy else "")

    # ── run again ────────────────────────────────────────────────────────────
    def _run_again(self) -> None:
        record = self._selected()
        if record is None or not record.argvs:
            return
        self.note.clear()
        if all(argv and argv[0] == "import" for argv in record.argvs):
            # The drive letters in an import may belong to another card or
            # drive by now, so the Import area asks for them again.
            name = profile_name_from_title(record.title)
            if name is not None or len(record.argvs) > 1:
                self.ctx.navigate.emit("import")
                self.ctx.open_in_import.emit(name)
                return
        if len(record.argvs) == 1:
            # Actions is built on first visit, so it must exist before it is told
            # what to show.
            self.ctx.navigate.emit("actions")
            self.ctx.open_in_actions.emit(list(record.argvs[0]))
            return
        answer = QMessageBox.question(
            self,
            "Run again",
            f'Run all {len(record.argvs)} steps of "{record.title}" again?',
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        # Records do not keep writes_catalog, so assume the job changes it.
        spec = JobSpec(record.title, tuple(tuple(argv) for argv in record.argvs), True)
        if not self.ctx.jobs.start(spec):
            self.note.setText(JOB_RUNNING)
