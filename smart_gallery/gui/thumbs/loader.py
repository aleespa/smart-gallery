"""Background thumbnail loader: a small private thread pool over the cache."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from smart_gallery.config import to_abspath
from smart_gallery.gui.thumbs.cache import ensure_thumbnail

MAX_THREADS = 4


class _Task(QRunnable):
    def __init__(self, loader: _PoolLoader, generation: int, job: tuple):
        super().__init__()
        self._loader = loader
        self._generation = generation
        self._job = job  # job[0] is the root, job[1] the id

    def run(self) -> None:
        loader = self._loader
        root, item_id = str(self._job[0]), self._job[1]
        if not loader._start((root, item_id), self._generation):
            return  # cancelled while queued
        path = loader._work(self._job)
        loader._finished.emit(root, item_id, str(path) if path else "")


class _PoolLoader(QObject):
    """Shared plumbing: a private thread pool, per-key dedup and cancel."""

    ready = Signal(str, int, str)
    _finished = Signal(str, int, str)  # worker threads -> GUI thread

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_THREADS)
        self._inflight: set[tuple[str, int]] = set()  # GUI thread only
        self._lock = threading.Lock()
        self._queued: set[tuple[str, int]] = set()  # guarded by _lock
        self._generation = 0  # guarded by _lock
        # The loader lives in the GUI thread, so a signal emitted from a pool
        # thread reaches this slot through a queued connection.
        self._finished.connect(self._on_finished)

    def _submit(self, job: tuple) -> None:
        """Queue ``job`` (``(root, id, ...)``) unless that key is in flight."""
        key = (str(job[0]), job[1])
        if key in self._inflight:
            return
        self._inflight.add(key)
        with self._lock:
            self._queued.add(key)
            generation = self._generation
        self._pool.start(_Task(self, generation, job))

    def _work(self, job: tuple) -> Path | None:
        raise NotImplementedError

    def cancel_pending(self) -> None:
        """Drop requests that have not started; running ones still finish."""
        with self._lock:
            self._generation += 1
            dropped, self._queued = self._queued, set()
        self._inflight -= dropped
        self._pool.clear()

    def _start(self, key: tuple[str, int], generation: int) -> bool:
        with self._lock:
            if generation != self._generation:
                return False
            self._queued.discard(key)
            return True

    def _on_finished(self, root: str, item_id: int, path: str) -> None:
        self._inflight.discard((root, item_id))
        self.ready.emit(root, item_id, path)


class ThumbnailLoader(_PoolLoader):
    """Loads thumbnails off the GUI thread.

    ``ready(root, media_id, path)`` is emitted on the GUI thread; ``root`` is
    the drive root as given to ``request`` (as a str) and ``path`` is "" when
    the item has no thumbnail.
    """

    def request(
        self,
        root: Path,
        media_id: int,
        mtime_ns: int | None,
        relpath: str,
        ext: str,
        media_type: str,
    ) -> None:
        self._submit(
            (root, media_id, mtime_ns, to_abspath(relpath, root), ext, media_type)
        )

    def _work(self, job: tuple) -> Path | None:
        return ensure_thumbnail(*job)
