"""Check RAW + JPEG pairs off the GUI thread.

``check_pairs`` runs in one ``QRunnable`` on a private one-thread pool. Results
cross back to the GUI thread through signals tagged with a generation, as in
``RepoQuery``: a run that a newer ``start`` or a ``cancel`` replaced is dropped.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from smart_gallery.storage.check_cache import CheckCache
from smart_gallery.storage.compare import check_pairs
from smart_gallery.storage.pairs import Pair

logger = logging.getLogger(__name__)


class _Signals(QObject):
    result = Signal(int, object, str)  # generation, PairKey, verdict
    done = Signal(int)  # generation


class _Task(QRunnable):
    def __init__(self, pairs, root, cache_path, generation, stop, signals):
        super().__init__()
        self._pairs = pairs
        self._root = root
        self._cache_path = cache_path
        self._generation = generation
        self._stop = stop
        self._signals = signals

    def run(self) -> None:
        gen, signals = self._generation, self._signals
        try:
            if not self._stop.is_set():
                check_pairs(
                    self._pairs,
                    self._root,
                    CheckCache(self._cache_path),
                    lambda key, verdict: signals.result.emit(gen, key, verdict),
                    cancelled=self._stop.is_set,
                )
        except Exception as exc:  # logged, never raised on the worker
            logger.warning("Picture check failed: %s", exc)
        signals.done.emit(gen)


class PairChecker(QObject):
    checked = Signal(object, str)  # PairKey, verdict
    progress = Signal(int, int)  # done, total
    finished = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._generation = 0
        self._stop = threading.Event()
        self._done = 0
        self._total = 0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        # Unparented on purpose: a running task keeps it alive by reference.
        self._signals = _Signals()
        self._signals.result.connect(self._on_result)
        self._signals.done.connect(self._on_done)

    def start(self, pairs: list[Pair], root: Path, cache_path: Path) -> None:
        self.cancel()
        self._stop = threading.Event()
        self._done, self._total = 0, len(pairs)
        self._pool.start(
            _Task(
                list(pairs),
                Path(root),
                Path(cache_path),
                self._generation,
                self._stop,
                self._signals,
            )
        )
        self.progress.emit(0, self._total)

    def cancel(self) -> None:
        self._generation += 1
        self._stop.set()
        self._pool.clear()

    def wait(self, msecs: int = -1) -> bool:
        return self._pool.waitForDone(msecs)

    def _on_result(self, generation: int, key, verdict: str) -> None:
        if generation != self._generation:
            return
        self._done += 1
        self.checked.emit(key, verdict)
        self.progress.emit(self._done, self._total)

    def _on_done(self, generation: int) -> None:
        if generation == self._generation:
            self.finished.emit()
