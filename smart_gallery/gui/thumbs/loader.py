"""Background thumbnail loader: a small private thread pool over the cache."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from smart_gallery.config import to_abspath
from smart_gallery.gui.thumbs.cache import ensure_thumbnail

MAX_THREADS = 4


class _Task(QRunnable):
    def __init__(self, loader: ThumbnailLoader, generation: int, job: tuple):
        super().__init__()
        self._loader = loader
        self._generation = generation
        self._job = job  # (root, media_id, mtime_ns, abspath, ext, media_type)

    def run(self) -> None:
        loader = self._loader
        media_id = self._job[1]
        if not loader._start(media_id, self._generation):
            return  # cancelled while queued
        path = ensure_thumbnail(*self._job)
        loader._finished.emit(media_id, str(path) if path else "")


class ThumbnailLoader(QObject):
    """Loads thumbnails off the GUI thread.

    ``ready(media_id, path)`` is emitted on the GUI thread; ``path`` is ""
    when the item has no thumbnail.
    """

    ready = Signal(int, str)
    _finished = Signal(int, str)  # worker threads -> GUI thread

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_THREADS)
        self._inflight: set[int] = set()  # GUI thread only
        self._lock = threading.Lock()
        self._queued: set[int] = set()  # guarded by _lock
        self._generation = 0  # guarded by _lock
        # The loader lives in the GUI thread, so a signal emitted from a pool
        # thread reaches this slot through a queued connection.
        self._finished.connect(self._on_finished)

    def request(
        self,
        root: Path,
        media_id: int,
        mtime_ns: int | None,
        relpath: str,
        ext: str,
        media_type: str,
    ) -> None:
        if media_id in self._inflight:
            return
        self._inflight.add(media_id)
        job = (root, media_id, mtime_ns, to_abspath(relpath, root), ext, media_type)
        with self._lock:
            self._queued.add(media_id)
            generation = self._generation
        self._pool.start(_Task(self, generation, job))

    def cancel_pending(self) -> None:
        """Drop requests that have not started; running ones still finish."""
        with self._lock:
            self._generation += 1
            dropped, self._queued = self._queued, set()
        self._inflight -= dropped
        self._pool.clear()

    def _start(self, media_id: int, generation: int) -> bool:
        with self._lock:
            if generation != self._generation:
                return False
            self._queued.discard(media_id)
            return True

    def _on_finished(self, media_id: int, path: str) -> None:
        self._inflight.discard(media_id)
        self.ready.emit(media_id, path)
