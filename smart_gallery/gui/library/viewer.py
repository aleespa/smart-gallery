"""Single-item viewer: a large preview, the catalog metadata, and stepping
through the Library's current rows.

The window follows each photo's shape: the photo area takes the photo's
aspect ratio beside the fixed info card. The first photo sets the area's
height (most of the screen, never larger than the photo); later photos keep
that height, or the one the owner resized to, and change only the width.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QRect, QSize, Qt, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.config import to_abspath
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.library.model import LibraryRow
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.gui.thumbs.cache import load_preview
from smart_gallery.models import MediaItem
from smart_gallery.util import format_shutter

PREVIEW_EDGE = 2048
EMPTY = "—"
FIELDS = (
    "Path",
    "Taken",
    "Camera",
    "Lens",
    "Focal length",
    "Aperture",
    "ISO",
    "Shutter",
    "Dimensions",
    "Duration",
    "Size",
)
LOADING = "Loading…"
NO_PREVIEW = "No preview for this file."
DISCONNECTED = "Drive not connected."
FIT_SCREEN_HEIGHT = 0.8  # of the screen, for the first photo's window
FIT_SCREEN_MAX = 0.95  # the window never takes more of the screen than this
MIN_AREA = QSize(200, 200)  # the photo area's smallest size


def format_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return EMPTY
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            break
        size /= 1024
    else:
        unit = "TB"
    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"


def format_duration(ms: float | None) -> str:
    if ms is None:
        return ""
    seconds = int(round(ms / 1000))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _number(value, fmt: str = "{:g}") -> str:
    return EMPTY if value is None else fmt.format(value)


def _metadata(item: MediaItem) -> dict[str, str]:
    taken = " ".join(t for t in (item.date_taken, item.time_taken) if t)
    shutter = item.shutter_speed_text or format_shutter(item.shutter_speed_sec)
    dims = f"{item.width} × {item.height}" if item.width and item.height else ""
    return {
        "Path": item.relpath,
        "Taken": taken,
        "Camera": item.camera or "",
        "Lens": item.lens or "",
        "Focal length": _number(item.focal_length, "{:g} mm"),
        "Aperture": _number(item.aperture, "f/{:g}"),
        "ISO": _number(item.iso),
        "Shutter": shutter or "",
        "Dimensions": dims,
        "Duration": format_duration(item.duration_ms),
        "Size": format_size(item.size_bytes),
    }


def fitted_size(image: QSize, area_height: int, chrome: QSize, screen: QSize) -> QSize:
    """Window size whose photo area has ``image``'s shape at ``area_height``.

    ``chrome`` is everything around the photo area (info card, buttons,
    margins). The window stays within ``FIT_SCREEN_MAX`` of ``screen``: a
    photo too wide or too tall for it gets a smaller area of the same shape.
    """
    aspect = image.width() / image.height()
    max_w = int(screen.width() * FIT_SCREEN_MAX) - chrome.width()
    max_h = int(screen.height() * FIT_SCREEN_MAX) - chrome.height()
    height = max(MIN_AREA.height(), area_height)
    width = round(height * aspect)
    if width > max_w:
        width, height = max_w, round(max_w / aspect)
    if height > max_h:
        height, width = max_h, round(max_h * aspect)
    width = max(MIN_AREA.width(), width)
    height = max(MIN_AREA.height(), height)
    return QSize(width + chrome.width(), height + chrome.height())


def _to_qimage(image) -> QImage:
    """QImage copy of an RGB Pillow image; safe to build on a worker thread."""
    w, h = image.size
    data = image.tobytes("raw", "RGB")
    return QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def show_in_explorer(abspath: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{abspath}"])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(abspath).parent)))


class MediaViewer(QDialog):
    def __init__(
        self,
        ctx: AppContext,
        root: Path,
        rows: list[LibraryRow],
        index: int,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.root = Path(root)
        self.rows = rows
        self._index = index
        self._pixmap = QPixmap()
        self._text = ""
        # The photo area's height to keep from one photo to the next; set by
        # the first photo, then by any resize the owner makes.
        self._area_height: int | None = None
        self._fitted_size: QSize | None = None  # the last size we asked for
        self._query = RepoQuery(ctx, self)
        self.resize(1100, 750)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(MIN_AREA)
        # Ignored, so a large pixmap never forces the dialog to grow.
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

        self._values: dict[str, QLabel] = {}
        side = QWidget()
        side.setFixedWidth(320)
        form = QFormLayout(side)
        for name in FIELDS:
            value = QLabel(EMPTY)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._values[name] = value
            form.addRow(name, value)

        self.prev_btn = QPushButton("Previous")
        self.next_btn = QPushButton("Next")
        self.explorer_btn = QPushButton("Show in Explorer")
        close_btn = QPushButton("Close")
        buttons = QHBoxLayout()
        buttons.addWidget(self.prev_btn)
        buttons.addWidget(self.next_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.explorer_btn)
        buttons.addWidget(close_btn)

        body = QHBoxLayout()
        body.addWidget(self.image, 1)
        body.addWidget(side)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(buttons)

        self.prev_btn.clicked.connect(self.show_previous)
        self.next_btn.clicked.connect(self.show_next)
        self.explorer_btn.clicked.connect(lambda: show_in_explorer(self.abspath()))
        close_btn.clicked.connect(self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self.show_previous)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self.show_next)
        self._query.finished.connect(self._on_loaded)
        self._query.failed.connect(self._on_failed)
        # The rows belong to the gallery that was open; another one has other ids.
        ctx.gallery_changed.connect(self.close)

        self.show_index(index)

    # ── navigation ──────────────────────────────────────────────────────────
    def row(self) -> LibraryRow:
        return self.rows[self._index]

    def abspath(self) -> Path:
        return to_abspath(self.row().relpath, self.root)

    def show_previous(self) -> None:
        if self._index > 0:
            self.show_index(self._index - 1)

    def show_next(self) -> None:
        if self._index < len(self.rows) - 1:
            self.show_index(self._index + 1)

    def show_index(self, index: int) -> None:
        self._index = index
        row = self.row()
        self.setWindowTitle(PurePosixPath(row.relpath).name)
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < len(self.rows) - 1)
        self._set_fields({"Path": row.relpath})
        self._set_pixmap(QPixmap(), LOADING)
        abspath = self.abspath()

        def load(repo):
            item = repo.get_by_id(row.id)
            image = None
            if row.media_type == "image":
                preview = load_preview(abspath, row.ext, PREVIEW_EDGE)
                image = _to_qimage(preview) if preview is not None else None
            return row.id, item, image

        self._query.run(load)

    # ── results ─────────────────────────────────────────────────────────────
    def _on_loaded(self, result) -> None:
        media_id, item, image = result
        if media_id != self.row().id:
            return
        if item is not None:
            self._set_fields(_metadata(item))
        if image is None:
            self._set_pixmap(QPixmap(), NO_PREVIEW)
        else:
            self._fit_to(image.size())
            self._set_pixmap(QPixmap.fromImage(image), "")

    def _on_failed(self, error) -> None:
        text = DISCONNECTED if isinstance(error, FileNotFoundError) else str(error)
        self._set_pixmap(QPixmap(), text)

    def _set_fields(self, values: dict[str, str]) -> None:
        for name, label in self._values.items():
            label.setText(values.get(name) or EMPTY)

    def _set_pixmap(self, pixmap: QPixmap, text: str) -> None:
        self._pixmap = pixmap
        self._text = text
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap.isNull():
            self.image.setText(self._text)  # also clears any pixmap
            return
        self.image.setPixmap(
            self._pixmap.scaled(
                self.image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _fit_to(self, image: QSize) -> None:
        """Resize the window around its centre so the photo area takes the
        shape of ``image``, keeping it on screen."""
        if not self.isVisible() or image.isEmpty():
            return
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        chrome = self.size() - self.image.size()
        if self._area_height is None:
            fill = int(screen.height() * FIT_SCREEN_HEIGHT) - chrome.height()
            self._area_height = min(fill, image.height())  # never enlarge
        size = fitted_size(image, self._area_height, chrome, screen.size())
        geometry = QRect(self.geometry().topLeft(), size)
        geometry.moveCenter(self.geometry().center())
        geometry.moveLeft(
            max(screen.left(), min(geometry.left(), screen.right() - size.width()))
        )
        geometry.moveTop(
            max(screen.top(), min(geometry.top(), screen.bottom() - size.height()))
        )
        self._fitted_size = size
        self.setGeometry(geometry)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._area_height is not None and event.size() != self._fitted_size:
            self._area_height = self.image.height()  # the owner resized it
        self._rescale()

    # ── for tests and callers ───────────────────────────────────────────────
    def field(self, name: str) -> str:
        return self._values[name].text()

    def has_preview(self) -> bool:
        return not self._pixmap.isNull()
