"""Runs Smart Gallery CLI commands in the background, one job at a time.

Every write the app makes goes through here as a ``smart-gallery`` subprocess,
so a job is safe to cancel: the CLI is first asked to stop through a stop file,
and only killed (with its ExifTool children) if it ignores that for the grace
time, or when the user cancels a second time ("Force stop").
"""

from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from smart_gallery.gui.history.store import TAIL_LINES, HistoryStore, JobRecord
from smart_gallery.gui.jobspec import JobSpec
from smart_gallery.gui.paths import cli_command
from smart_gallery.progress import (
    CANCEL_ENV,
    CANCELLED_EXIT,
    PROGRESS_ENV,
    ProgressEvent,
)

__all__ = ["JobRunner", "JobSpec"]

logger = logging.getLogger(__name__)

# The CLI logs through loguru, so the Details line arrives as
# "2026-10-17 20:11:25.507 | INFO     | __main__:main:854 - Details: <path>".
_DETAILS = re.compile(r"(?:^|\s-\s)Details:\s*(.+)$")
_LOG_PREFIX = re.compile(r"^\S+ \S+ \| \w+\s*\| \S+ - ")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Long enough for the CLI to finish the file or ExifTool batch it is on.
GRACE_MS = 120_000
# After taskkill, how long to wait before falling back to QProcess.kill().
KILL_CHECK_MS = 5_000


class JobRunner(QObject):
    started = Signal(object)  # JobSpec
    step = Signal(int, int)  # 1-based index, count
    progress = Signal(object)  # ProgressEvent
    output = Signal(str)
    finished = Signal(object)  # JobRecord
    catalog_written = Signal()  # after ``finished`` when the spec wrote the catalog

    def __init__(
        self,
        history: HistoryStore,
        workdir: Path,
        program: list[str] | None = None,
        grace_ms: int = GRACE_MS,
    ):
        super().__init__()
        self._history = history
        self._workdir = Path(workdir)
        self._program = list(program) if program is not None else cli_command()
        self._grace_ms = grace_ms
        self._grace = QTimer(self)
        self._grace.setSingleShot(True)
        self._grace.timeout.connect(self._kill)
        self._spec: JobSpec | None = None
        self._process: QProcess | None = None
        self._index = 0
        self._stop_file: Path | None = None
        self._cancel_requested = False
        self._taskkill_sent = False
        self._buffer = b""
        self._tail: deque[str] = deque(maxlen=TAIL_LINES)
        self._summary = ""
        self._log_path: str | None = None
        self._started_at = ""

    @property
    def history(self) -> HistoryStore:
        return self._history

    @property
    def running(self) -> bool:
        return self._spec is not None

    @property
    def cancelling(self) -> bool:
        """A stop was requested and the job has not ended yet."""
        return self.running and self._cancel_requested

    def start(self, spec: JobSpec) -> bool:
        """Begin ``spec``; False when another job is still running.

        ``finished`` may be emitted before this returns (e.g. the program
        cannot start), so connect to it before calling.
        """
        if self.running:
            return False
        jobs_dir = self._workdir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        self._spec = spec
        self._index = 0
        self._stop_file = jobs_dir / f"{uuid.uuid4().hex}.stop"
        self._cancel_requested = False
        self._taskkill_sent = False
        self._tail.clear()
        self._summary = ""
        self._log_path = None
        self._started_at = _now()
        self.started.emit(spec)
        if not spec.argvs:
            self._end(ok=True, exit_code=0)
        else:
            self._launch()
        return True

    def cancel(self) -> None:
        """Ask the running job to stop; kill it if it has not within the grace
        time. Cancelling again while it is stopping kills it now."""
        if not self.running:
            return
        if self._cancel_requested:
            self._kill()
            return
        self._cancel_requested = True
        try:
            self._stop_file.touch()
        except OSError:
            logger.warning("Could not create stop file %s", self._stop_file)
        self._grace.start(self._grace_ms)

    def _launch(self) -> None:
        spec = self._spec
        self.step.emit(self._index + 1, len(spec.argvs))
        env = QProcessEnvironment()
        for key, value in {
            **os.environ,
            # The child writes to a pipe, which would use the locale code page.
            "PYTHONIOENCODING": "utf-8",
            # Without this, progress lines reach the pipe in bursts.
            "PYTHONUNBUFFERED": "1",
            PROGRESS_ENV: "json",
            CANCEL_ENV: str(self._stop_file),
        }.items():
            env.insert(key, value)
        process = QProcess(self)
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(self._workdir))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._on_process_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        self._buffer = b""
        process.start(self._program[0], [*self._program[1:], *spec.argvs[self._index]])

    def _read_output(self) -> None:
        if self._process is None:
            return
        self._buffer += bytes(self._process.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            self._handle_line(raw)

    def _handle_line(self, raw: bytes) -> None:
        text = _ANSI.sub("", raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        event = ProgressEvent.from_line(text)
        if event is not None:
            self.progress.emit(event)
            return
        if not text.strip():
            return
        self._tail.append(text)
        details = _DETAILS.search(text)
        if details:
            self._log_path = details.group(1).strip()
        else:
            self._summary = _LOG_PREFIX.sub("", text)
        self.output.emit(text)

    def _on_process_finished(self, exit_code: int, _status) -> None:
        self._read_output()
        if self._buffer:
            self._handle_line(self._buffer)
            self._buffer = b""
        self._retire_process()
        self._index += 1
        more = self._index < len(self._spec.argvs)
        if exit_code == 0 and not self._cancel_requested and more:
            self._launch()
        else:
            self._end(ok=exit_code == 0, exit_code=exit_code)

    def _on_error(self, error) -> None:
        if error != QProcess.ProcessError.FailedToStart or self._process is None:
            return  # other errors are followed by ``finished``
        message = self._process.errorString()
        self._retire_process()
        self._tail.append(message)
        self._summary = message
        self.output.emit(message)
        self._end(ok=False, exit_code=None)

    def _retire_process(self) -> None:
        self._grace.stop()
        if self._process is not None:
            self._process.deleteLater()
            self._process = None

    def _kill(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        pid = process.processId()
        if sys.platform == "win32" and pid and not self._taskkill_sent:
            # /T takes the whole tree, so ExifTool children die with the CLI.
            ok, _pid = QProcess.startDetached(
                "taskkill", ["/PID", str(pid), "/T", "/F"]
            )
            if ok:
                self._taskkill_sent = True
                # Still running a little later: kill() it directly.
                self._grace.start(min(self._grace_ms, KILL_CHECK_MS))
                return
        process.kill()

    def _end(self, ok: bool, exit_code: int | None) -> None:
        spec = self._spec
        if self._cancel_requested or exit_code == CANCELLED_EXIT:
            status = "cancelled"
        else:
            status = "ok" if ok else "failed"
        try:
            self._stop_file.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove stop file %s", self._stop_file)
        record = JobRecord(
            title=spec.title,
            argvs=[list(argv) for argv in spec.argvs],
            started_at=self._started_at,
            ended_at=_now(),
            status=status,
            exit_code=exit_code,
            summary=self._summary,
            log_path=self._log_path,
            output_tail=list(self._tail),
        )
        try:
            self._history.append(record)
        except OSError:
            logger.exception("Could not write job history")
        self._spec = None  # so a ``finished`` handler may start the next job
        self.finished.emit(record)
        if spec.writes_catalog:
            self.catalog_written.emit()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
