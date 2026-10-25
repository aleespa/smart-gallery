"""Check RAW + JPEG pairs off the GUI thread.

Two kinds of run share one private one-thread pool. ``load_cached`` only reads
the verdict cache and reports what it remembers (cheap: no ExifTool, no image
files). ``start`` checks the given pairs for real, reporting cached verdicts
first. Results cross back to the GUI thread through signals tagged with a
generation, as in ``RepoQuery``: a run that a newer call or a cancel replaced
is dropped. A check that raises reports ``failed(reason)`` instead of
``finished``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from smart_gallery.storage.check_cache import CheckCache
from smart_gallery.storage.compare import check_pairs, report_cached
from smart_gallery.storage.pairs import Pair

logger = logging.getLogger(__name__)


class _Signals(QObject):
    result = Signal(int, object, str)  # generation, PairKey, verdict
    done = Signal(int)  # generation
    failed = Signal(int, str)  # generation, reason


class _Task(QRunnable):
    def __init__(self, pairs, root, cache_path, generation, stop, signals, full):
        super().__init__()
        self._pairs = pairs
        self._root = root
        self._cache_path = cache_path
        self._generation = generation
        self._stop = stop
        self._signals = signals
        self._full = full  # False: only report cached verdicts

    def run(self) -> None:
        gen, signals = self._generation, self._signals

        def on_result(key, verdict) -> None:
            signals.result.emit(gen, key, verdict)

        try:
            if self._stop.is_set():
                pass
            elif self._full:
                check_pairs(
                    self._pairs,
                    self._root,
                    CheckCache(self._cache_path),
                    on_result,
                    cancelled=self._stop.is_set,
                )
            else:
                report_cached(self._pairs, CheckCache(self._cache_path), on_result)
        except Exception as exc:  # reported, never raised on the worker
            logger.warning("Picture check failed: %s", exc)
            signals.failed.emit(gen, str(exc) or type(exc).__name__)
        else:
            signals.done.emit(gen)


class PairChecker(QObject):
    checked = Signal(object, str)  # PairKey, verdict (from either kind of run)
    progress = Signal(int, int)  # done, total (of the check)
    finished = Signal()  # the check finished
    failed = Signal(str)  # reason; sent instead of finished

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._generation = 0
        self._cache_generation = 0
        self._stop = threading.Event()
        self._cache_stop = threading.Event()
        self._done = 0
        self._total = 0
        self._running = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        # Unparented on purpose: a running task keeps them alive by reference.
        self._signals = _Signals()
        self._signals.result.connect(self._on_result)
        self._signals.done.connect(self._on_done)
        self._signals.failed.connect(self._on_failed)
        self._cache_signals = _Signals()
        self._cache_signals.result.connect(self._on_cached)

    @property
    def running(self) -> bool:
        """A check (not a cache read) is under way."""
        return self._running

    def load_cached(self, pairs: list[Pair], cache_path: Path) -> None:
        """Report the remembered verdicts of ``pairs``; checks nothing."""
        self._cancel_cached()
        self._cache_stop = threading.Event()
        self._pool.start(
            _Task(
                list(pairs),
                Path(),
                Path(cache_path),
                self._cache_generation,
                self._cache_stop,
                self._cache_signals,
                full=False,
            )
        )

    def start(self, pairs: list[Pair], root: Path, cache_path: Path) -> None:
        self.cancel()
        self._stop = threading.Event()
        self._done, self._total = 0, len(pairs)
        self._running = True
        self._pool.start(
            _Task(
                list(pairs),
                Path(root),
                Path(cache_path),
                self._generation,
                self._stop,
                self._signals,
                full=True,
            )
        )
        self.progress.emit(0, self._total)

    def cancel(self) -> None:
        """Stop the check. A queued cache read still runs."""
        self._generation += 1
        self._stop.set()  # a queued check then skips itself
        self._running = False

    def cancel_all(self) -> None:
        self.cancel()
        self._cancel_cached()
        self._pool.clear()

    def _cancel_cached(self) -> None:
        self._cache_generation += 1
        self._cache_stop.set()

    def wait(self, msecs: int = -1) -> bool:
        return self._pool.waitForDone(msecs)

    def _on_cached(self, generation: int, key, verdict: str) -> None:
        if generation == self._cache_generation:
            self.checked.emit(key, verdict)

    def _on_result(self, generation: int, key, verdict: str) -> None:
        if generation != self._generation:
            return
        self._done += 1
        self.checked.emit(key, verdict)
        self.progress.emit(self._done, self._total)

    def _on_done(self, generation: int) -> None:
        if generation == self._generation:
            self._running = False
            self.finished.emit()

    def _on_failed(self, generation: int, reason: str) -> None:
        if generation == self._generation:
            self._running = False
            self.failed.emit(reason)
