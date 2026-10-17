"""Run catalog queries off the GUI thread.

SQLite connections belong to the thread that made them, so each query opens
its own read-only repository on the worker (``ctx.open_repo()``) and closes it
there. Only the result crosses back to the GUI thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from smart_gallery.db import GalleryRepository

logger = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(int, object, object)  # generation, result, error


class _Task(QRunnable):
    def __init__(self, ctx, fn, generation: int, signals: _Signals):
        super().__init__()
        self._ctx = ctx
        self._fn = fn
        self._generation = generation
        self._signals = signals

    def run(self) -> None:
        result = error = None
        try:
            with self._ctx.open_repo() as repo:
                result = self._fn(repo)
        except Exception as exc:  # reported to the GUI thread, never raised here
            error = exc
        self._signals.done.emit(self._generation, result, error)


class RepoQuery(QObject):
    """Runs ``fn(repo)`` on a private one-thread pool.

    ``finished(result)`` or ``failed(exception)`` is emitted on the GUI thread
    for the latest ``run`` only: a result from a run that a newer ``run`` or a
    ``cancel`` has replaced is dropped.
    """

    finished = Signal(object)
    failed = Signal(object)

    def __init__(self, ctx, parent: QObject | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self._generation = 0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        # Unparented on purpose: a running task keeps it alive by reference.
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)

    def run(self, fn: Callable[[GalleryRepository], object]) -> None:
        self._generation += 1
        self._pool.clear()  # a query still waiting to start is outdated now
        self._pool.start(_Task(self._ctx, fn, self._generation, self._signals))

    def cancel(self) -> None:
        self._generation += 1
        self._pool.clear()

    def wait(self, msecs: int = -1) -> bool:
        return self._pool.waitForDone(msecs)

    def _on_done(self, generation: int, result, error) -> None:
        if generation != self._generation:
            return
        if error is None:
            self.finished.emit(result)
            return
        if not isinstance(error, FileNotFoundError):
            logger.warning("Catalog query failed: %s", error)
        self.failed.emit(error)
