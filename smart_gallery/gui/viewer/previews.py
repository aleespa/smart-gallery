"""Previews for the photo viewer, decoded off the GUI thread and kept in a
small cache so stepping back and forth is instant. ``prefetch`` warms the
cache with the neighbours quietly."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage

from smart_gallery.gui.thumbs.cache import load_preview

PREVIEW_EDGE = 2048  # what the viewer shows at fit
ZOOM_EDGE = 8192  # asked for when zooming, so 100 % shows real detail
CACHE_SIZE = 6
THREADS = 2
_PREFETCH_PRIORITY = -1


@dataclass(frozen=True)
class PreviewResult:
    key: int
    edge: int
    image: QImage | None
    error: str  # "missing" when the file is not there, else ""


def to_qimage(image) -> QImage:
    """QImage copy of an RGB Pillow image; safe to build on a worker thread."""
    w, h = image.size
    data = image.tobytes("raw", "RGB")
    return QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class _Signals(QObject):
    done = Signal(object)  # PreviewResult, worker -> GUI thread


class _Task(QRunnable):
    def __init__(self, signals, key, abspath, ext, edge):
        super().__init__()
        self._signals = signals
        self._args = (key, Path(abspath), ext, edge)

    def run(self) -> None:
        key, abspath, ext, edge = self._args
        image, error = None, ""
        try:
            if not abspath.is_file():
                error = "missing"
            else:
                preview = load_preview(abspath, ext, edge, largest=edge > PREVIEW_EDGE)
                image = to_qimage(preview) if preview is not None else None
        except Exception:  # reported as "no preview", never raised here
            image = None
        self._signals.done.emit(PreviewResult(key, edge, image, error))


class PreviewLoader(QObject):
    """``request`` answers with ``ready(PreviewResult)`` on the GUI thread;
    ``prefetch`` only fills the cache."""

    ready = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(THREADS)
        self._cache: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self._inflight: dict[tuple[int, int], bool] = {}  # -> someone waits
        # Unparented: a running task keeps it alive by reference.
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)

    def cached(self, key: int, edge: int) -> QImage | None:
        return self._cache.get((key, edge))

    def request(
        self,
        key: int,
        abspath: Path,
        ext: str,
        media_type: str,
        edge: int = PREVIEW_EDGE,
    ) -> None:
        if media_type != "image":
            self._later(PreviewResult(key, edge, None, ""))
            return
        image = self._cache.get((key, edge))
        if image is not None:
            self._cache.move_to_end((key, edge))
            self._later(PreviewResult(key, edge, image, ""))
            return
        if (key, edge) in self._inflight:
            self._inflight[(key, edge)] = True  # a prefetch is now awaited
            return
        self._inflight[(key, edge)] = True
        self._pool.start(_Task(self._signals, key, abspath, ext, edge))

    def prefetch(self, items: list[tuple[int, Path, str, str]]) -> None:
        """Decode these ``(key, abspath, ext, media_type)`` quietly."""
        for key, abspath, ext, media_type in items:
            slot = (key, PREVIEW_EDGE)
            if media_type != "image" or slot in self._cache or slot in self._inflight:
                continue
            self._inflight[slot] = False
            task = _Task(self._signals, key, abspath, ext, PREVIEW_EDGE)
            self._pool.start(task, _PREFETCH_PRIORITY)

    def cancel_pending(self) -> None:
        """Drop decodes that have not started."""
        self._pool.clear()
        self._inflight.clear()

    def _later(self, result: PreviewResult) -> None:
        QTimer.singleShot(0, self, lambda: self.ready.emit(result))

    def _on_done(self, result: PreviewResult) -> None:
        slot = (result.key, result.edge)
        awaited = self._inflight.pop(slot, False)
        if result.image is not None:
            self._cache[slot] = result.image
            self._cache.move_to_end(slot)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        if awaited:
            self.ready.emit(result)
