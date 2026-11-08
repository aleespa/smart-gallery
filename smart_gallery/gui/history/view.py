"""History area: the jobs the app has run, with their logs and a way to rerun."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.format import format_duration as _format_seconds
from smart_gallery.gui import theme
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.history.store import JobRecord
from smart_gallery.gui.imports.model import profile_name_from_title
from smart_gallery.gui.jobs import JobSpec
from smart_gallery.gui.ui.widgets import (
    ActionBar,
    EmptyState,
    pill_style,
    status_pill,
)

COLUMNS = ("Job", "Started", "Duration", "Status", "Summary")
MAX_LOG_BYTES = 1_000_000  # a detail log can hold a line per file
JOB_RUNNING = "A job is already running."


def format_start(started_at: str, now: datetime | None = None) -> str:
    """``Today 17:01``, ``Yesterday 09:12`` or ``2026-11-01 09:12`` (local time)."""
    try:
        when = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return str(started_at or "")
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    today = (now or datetime.now()).date()
    days = (today - when.date()).days
    if days == 0:
        return f"Today {when:%H:%M}"
    if days == 1:
        return f"Yesterday {when:%H:%M}"
    return when.strftime("%Y-%m-%d %H:%M")


class _StatusDelegate(QStyledItemDelegate):
    """Paints the status column as a pill; the item keeps the raw text."""

    def paint(self, painter, option, index) -> None:
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        rect = QRectF(option.rect).adjusted(4, 4, -4, -4)
        status_pill(painter, rect, index.data() or "")

    def sizeHint(self, option, index) -> QSize:
        label, _ = pill_style(index.data() or "")
        width = option.fontMetrics.horizontalAdvance(label) + 32
        return QSize(width, option.fontMetrics.height() + 12)


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
    return _format_seconds(seconds)


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
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        for column in range(len(COLUMNS) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.setItemDelegateForColumn(3, _StatusDelegate(self.table))

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, monospace;")
        self._empty = EmptyState()
        self._empty.show_text("Select a job to see its log")
        self.detail = QStackedWidget()
        self.detail.addWidget(self._empty)
        self.detail.addWidget(self.log)

        self.run_button = QPushButton("Run again")
        theme.primary(self.run_button)
        self.run_button.setEnabled(False)
        self.footer = ActionBar()
        self.footer.add_right(self.run_button)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split, 1)
        layout.addWidget(self.footer)

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
            self.log.clear()
            self.detail.setCurrentWidget(self._empty)
        else:
            text = read_log(record.log_path) if record.log_path else None
            self.log.setPlainText(
                text if text is not None else "\n".join(record.output_tail)
            )
            self.detail.setCurrentWidget(self.log)
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
        self.footer.set_note("")
        if all(argv and argv[0] == "import" for argv in record.argvs):
            # The drive letters in an import may belong to another card or
            # drive by now, so the Library's Import window asks for them again.
            name = profile_name_from_title(record.title)
            if name is not None or len(record.argvs) > 1:
                self.ctx.navigate.emit("library")
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
            self.footer.set_note(JOB_RUNNING)
