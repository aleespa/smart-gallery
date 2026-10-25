"""A large view of one RAW + JPEG pair with the same three keep buttons.

Both images are decoded off the GUI thread (``load_preview``) and only the
finished ``QImage`` crosses back.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from smart_gallery.config import to_abspath
from smart_gallery.gui.library.viewer import _to_qimage
from smart_gallery.gui.thumbs.cache import load_preview
from smart_gallery.storage.pairs import KEEP_BOTH, KEEP_JPEG, KEEP_RAW, Pair
from smart_gallery.storage.tree import format_bytes, type_label

PILLS = {KEEP_RAW: "RAW", KEEP_JPEG: "JPEG", KEEP_BOTH: "Both"}
LOADING = "Loading…"
NO_PREVIEW = "No preview"
IMAGE_EDGE = 480


class _Signals(QObject):
    loaded = Signal(int, object)  # column, QImage or None


class _Load(QRunnable):
    def __init__(self, column: int, abspath: Path, ext: str, signals: _Signals):
        super().__init__()
        self._args = (column, abspath, ext)
        self._signals = signals

    def run(self) -> None:
        column, abspath, ext = self._args
        image = load_preview(abspath, ext)
        self._signals.loaded.emit(column, _to_qimage(image) if image else None)


class PairDialog(QDialog):
    def __init__(self, view, pair: Pair, root: Path, choice: str | None):
        super().__init__(view)
        self._view = view
        self._pair = pair
        self.setWindowTitle(pair.stem)

        grid = QGridLayout()
        self.images: list[QLabel] = []
        for column, ref in enumerate((pair.raw, pair.jpeg)):
            image = QLabel(LOADING)
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setMinimumSize(IMAGE_EDGE, IMAGE_EDGE * 2 // 3)
            caption = QLabel(f"{type_label(ref.ext)} {format_bytes(ref.size_bytes)}")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(image, 0, column)
            grid.addWidget(caption, 1, column)
            self.images.append(image)

        self.buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        row.addStretch(1)
        for key, label in PILLS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == choice)
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
            self.buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(grid, 1)
        layout.addLayout(row)

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        # Unparented on purpose: a running load keeps it alive by reference.
        self._signals = _Signals()
        self._signals.loaded.connect(self._on_loaded)
        for column, ref in enumerate((pair.raw, pair.jpeg)):
            abspath = to_abspath(ref.relpath, Path(root))
            self._pool.start(_Load(column, abspath, ref.ext, self._signals))

    def wait(self, msecs: int = 5000) -> bool:
        return self._pool.waitForDone(msecs)

    def _choose(self, key: str) -> None:
        self._view.choose(self._pair.key, key)
        current = self._view.choices().get(self._pair.key)
        for k, button in self.buttons.items():
            button.setChecked(k == current)

    def _on_loaded(self, column: int, image: QImage | None) -> None:
        label = self.images[column]
        if image is None or image.isNull():
            label.setText(NO_PREVIEW)
            return
        label.setPixmap(
            QPixmap.fromImage(image).scaled(
                label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
