"""Step through one burst's shots at full size and mark the ones to remove.

Previews are decoded off the GUI thread (``load_preview``) and only the
finished ``QImage`` crosses back. Marks live in the Bursts list; this view
only calls ``toggle`` on it.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QPoint,
    QRect,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from smart_gallery import colors
from smart_gallery.config import to_abspath
from smart_gallery.gui.library.viewer import _to_qimage
from smart_gallery.gui.storage.bursts_view import KEEP_ONE, KEEPING, BurstsView
from smart_gallery.gui.thumbs.cache import load_preview
from smart_gallery.storage.bursts import Burst, Shot, ShotKey

HEADER = "Burst {i:,} of {n:,} · {shots:,} shots · {span} s · {folder}"
PREV = "◀ Previous group"
NEXT = "Next group ▶"
BACK = "Back to list"
KEEP_BUTTON = "K Keep"
REMOVE_BUTTON = "X Remove"
KEYS = (
    "← → step · K keep · X remove · Space zoom 100% · "
    "Enter next group · Esc back to list"
)
LOADING = "Loading…"
NO_PREVIEW = "No preview"
STRIP_THUMB = 60
STRIP_GAP = 6
CACHE_SIZE = 8
NOTICE_MS = 1500


PreviewKey = tuple[int, int | None]  # first file's (id, mtime_ns)


class _Signals(QObject):
    loaded = Signal(object, object, object)  # generation, preview key, QImage|None


class _Load(QRunnable):
    def __init__(
        self, generation: int, key: PreviewKey, abspath: Path, ext: str, signals
    ):
        super().__init__()
        self._args = (generation, key, abspath, ext)
        self._signals = signals

    def run(self) -> None:
        generation, key, abspath, ext = self._args
        image = load_preview(abspath, ext)
        self._signals.loaded.emit(generation, key, _to_qimage(image) if image else None)


class _Film(QWidget):
    """The row of small thumbnails; clicking one jumps to that shot."""

    picked = Signal(int)

    def __init__(self, review: BurstReview) -> None:
        super().__init__()
        self._review = review
        self.setFixedHeight(STRIP_THUMB + 2 * STRIP_GAP)

    def _rect(self, i: int) -> QRect:
        x = STRIP_GAP + i * (STRIP_THUMB + STRIP_GAP)
        return QRect(x, STRIP_GAP, STRIP_THUMB, STRIP_THUMB)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        for i in range(len(self._review.shots())):
            if self._rect(i).contains(event.position().toPoint()):
                self.picked.emit(i)
                return

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        review = self._review
        painter = QPainter(self)
        marks = review.bursts_view.marks()
        for i, shot in enumerate(review.shots()):
            box = self._rect(i)
            marked = shot.key in marks
            painter.setOpacity(0.35 if marked else 1.0)
            pixmap = review.bursts_view._thumb(shot.files[0])
            if pixmap is None:
                painter.fillRect(box, QColor(*colors.TILE_BACK))
            else:
                size = pixmap.size().scaled(
                    box.size(), Qt.AspectRatioMode.KeepAspectRatio
                )
                target = QRect(0, 0, size.width(), size.height())
                target.moveCenter(box.center())
                painter.drawPixmap(target, pixmap)
            painter.setOpacity(1.0)
            if i == review.index():
                painter.setPen(self.palette().highlight().color())
                painter.drawRect(box.adjusted(-2, -2, 1, 1))
            painter.setBrush(QColor(colors.ERROR if marked else colors.KEEP))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(box.bottomRight() - QPoint(9, 9), 4, 4)


class BurstReview(QWidget):
    closed = Signal(object)  # burst key to expand in the list

    def __init__(self, bursts_view: BurstsView, root: Path, parent=None) -> None:
        super().__init__(parent)
        self.bursts_view = bursts_view
        self._root = Path(root)
        self._burst: Burst | None = None
        self._index = 0
        self.fit = True
        self._cache: OrderedDict[PreviewKey, QImage | None] = OrderedDict()
        self._pending: set[PreviewKey] = set()
        self._generation = 0  # bumped by a root change: older loads are stale
        self._drag: QPoint | None = None
        # A child timer dies with the widget; a QTimer.singleShot would not.
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_MS)
        self._notice_timer.timeout.connect(self._end_notice)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.header = QLabel("")
        self.keeping = QLabel("")
        self.prev_button = QPushButton(PREV)
        self.next_button = QPushButton(NEXT)
        self.back_button = QPushButton(BACK)
        self.prev_button.clicked.connect(self.previous_group)
        self.next_button.clicked.connect(self.next_group)
        self.back_button.clicked.connect(self._close)
        head = QHBoxLayout()
        head.addWidget(self.header)
        head.addWidget(self.keeping)
        head.addStretch(1)
        for button in (self.prev_button, self.next_button, self.back_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            head.addWidget(button)

        self.image_label = QLabel(LOADING)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.image_label)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.image_label.installEventFilter(self)

        self.details = QLabel("")
        self.keep_button = QPushButton(KEEP_BUTTON)
        self.remove_button = QPushButton(REMOVE_BUTTON)
        for button in (self.keep_button, self.remove_button):
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keep_button.clicked.connect(lambda: self._set_marked(False))
        self.remove_button.clicked.connect(lambda: self._set_marked(True))
        info = QHBoxLayout()
        info.addWidget(self.details, 1)
        info.addWidget(self.keep_button)
        info.addWidget(self.remove_button)

        self.film = _Film(self)
        self.film.picked.connect(self._go)
        hint = QLabel(KEYS)
        hint.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(info)
        layout.addWidget(self.film)
        layout.addWidget(hint)

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        # Unparented on purpose: a running load keeps it alive by reference.
        self._signals = _Signals()
        self._signals.loaded.connect(self._on_loaded)

    # -- API ---------------------------------------------------------------
    def wait(self, msecs: int = 5000) -> bool:
        return self._pool.waitForDone(msecs)

    def set_root(self, root: Path) -> None:
        """Read previews under ``root``; a new root forgets the old previews."""
        root = Path(root)
        if root != self._root:
            self._root = root
            self._cache.clear()
            self._pending.clear()
            self._generation += 1

    def shots(self) -> list[Shot]:
        return list(self._burst.shots) if self._burst else []

    def index(self) -> int:
        return self._index

    def open_at(self, burst: ShotKey, shot: ShotKey) -> None:
        found = next(
            (b for b in self.bursts_view.bursts_in_scope() if b.key == burst), None
        )
        if found is None:
            self.closed.emit(None)
            return
        self._burst = found
        self._index = next((i for i, s in enumerate(found.shots) if s.key == shot), 0)
        self.fit = True
        self.setFocus()
        self._show()

    def current(self) -> tuple[ShotKey, ShotKey] | None:
        if self._burst is None:
            return None
        return self._burst.key, self._shot().key

    def step(self, delta: int) -> None:
        if self._burst is not None:
            self._go(self._index + delta)

    def next_group(self) -> None:
        self._move_group(1)

    def previous_group(self) -> None:
        self._move_group(-1)

    def refresh(self) -> None:
        if self._burst is None:
            return
        found = next(
            (b for b in self.bursts_view.bursts_in_scope() if b.key == self._burst.key),
            None,
        )
        if found is None:
            self.closed.emit(None)
            return
        shot = self._shot().key
        self._burst = found
        self._index = next((i for i, s in enumerate(found.shots) if s.key == shot), 0)
        self._show()

    # -- internals ---------------------------------------------------------
    def _move_group(self, delta: int) -> None:
        if self._burst is None:
            return
        bursts = self.bursts_view.bursts_in_scope()
        pos = next((i for i, b in enumerate(bursts) if b.key == self._burst.key), None)
        if pos is None:
            return
        new = max(0, min(len(bursts) - 1, pos + delta))
        if new != pos:
            self._burst = bursts[new]
            self._index = 0
            self._show()

    def _go(self, i: int) -> None:
        if self._burst is None:
            return
        self._index = max(0, min(len(self._burst.shots) - 1, i))
        self._show()

    def _close(self) -> None:
        self.closed.emit(self._burst.key if self._burst else None)

    def _shot(self) -> Shot:
        return self._burst.shots[self._index]

    def _set_marked(self, marked: bool) -> None:
        if self._burst is None:
            return
        shot = self._shot().key
        if (shot in self.bursts_view.marks()) != marked:
            if not self.bursts_view.toggle(shot):
                self._update_state()
                self.details.setText(KEEP_ONE)
                self._notice_timer.start()  # restarts a pending one
                return
        self._update_state()

    def _end_notice(self) -> None:
        if self._burst is not None:
            self._update_details()

    def _show(self) -> None:
        burst = self._burst
        bursts = self.bursts_view.bursts_in_scope()
        pos = next((i for i, b in enumerate(bursts) if b.key == burst.key), 0)
        self.header.setText(
            HEADER.format(
                i=pos + 1,
                n=len(bursts),
                shots=len(burst.shots),
                span=burst.span,
                folder=burst.folder or self.bursts_view.root_name(),
            )
        )
        self.prev_button.setEnabled(pos > 0)
        self.next_button.setEnabled(pos < len(bursts) - 1)
        self._update_state()
        self._load(self._shot())
        self._draw_image()

    def _update_state(self) -> None:
        burst = self._burst
        marks = self.bursts_view.marks()
        marked = sum(1 for s in burst.shots if s.key in marks)
        self.keeping.setText(
            KEEPING.format(k=len(burst.shots) - marked, n=len(burst.shots))
        )
        is_marked = self._shot().key in marks
        self.keep_button.setChecked(not is_marked)
        self.remove_button.setChecked(is_marked)
        self._update_details()
        self.film.update()

    def _update_details(self) -> None:
        shot = self._shot()
        parts = [shot.stem, f"+{shot.taken_ts - self._burst.shots[0].taken_ts} s"]
        if shot.shutter:
            parts.append(shot.shutter)
        if shot.aperture is not None:
            parts.append(f"f/{shot.aperture:g}")
        if shot.iso is not None:
            parts.append(f"ISO {shot.iso}")
        self.details.setText(" · ".join(parts))

    @staticmethod
    def _preview_key(shot: Shot) -> PreviewKey:
        return shot.files[0].id, shot.files[0].mtime_ns

    def _load(self, shot: Shot) -> None:
        key = self._preview_key(shot)
        if key in self._cache:
            self._cache.move_to_end(key)
            return
        if key in self._pending:
            return
        self._pending.add(key)
        ref = shot.files[0]
        abspath = to_abspath(ref.relpath, self._root)
        self._pool.start(_Load(self._generation, key, abspath, ref.ext, self._signals))

    def _on_loaded(
        self, generation: int, key: PreviewKey, image: QImage | None
    ) -> None:
        if generation != self._generation:
            return  # read under a root that is no longer the gallery's
        self._pending.discard(key)
        self._cache[key] = image
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        if self._burst is not None and self._preview_key(self._shot()) == key:
            self._draw_image()

    def _draw_image(self) -> None:
        image = self._cache.get(self._preview_key(self._shot()), False)
        if image is False or image is None or image.isNull():
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(LOADING if image is False else NO_PREVIEW)
            self.image_label.adjustSize()
            return
        pixmap = QPixmap.fromImage(image)
        if self.fit:
            pixmap = pixmap.scaled(
                self.scroll.viewport().size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.image_label.setPixmap(pixmap)
        self.image_label.resize(pixmap.size())

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        if self._burst is not None and self.fit:
            self._draw_image()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        if obj is self.image_label and not self.fit:
            kind = event.type()
            if kind == event.Type.MouseButtonPress:
                self._drag = event.globalPosition().toPoint()
                return True
            if kind == event.Type.MouseMove and self._drag is not None:
                now = event.globalPosition().toPoint()
                delta = now - self._drag
                self._drag = now
                bar_h, bar_v = (
                    self.scroll.horizontalScrollBar(),
                    self.scroll.verticalScrollBar(),
                )
                bar_h.setValue(bar_h.value() - delta.x())
                bar_v.setValue(bar_v.value() - delta.y())
                return True
            if kind == event.Type.MouseButtonRelease:
                self._drag = None
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        key = event.key()
        if self._burst is None:
            super().keyPressEvent(event)
        elif key == Qt.Key.Key_Left:
            self.step(-1)
        elif key == Qt.Key.Key_Right:
            self.step(1)
        elif key == Qt.Key.Key_K:
            self._set_marked(False)
        elif key == Qt.Key.Key_X:
            self._set_marked(True)
        elif key == Qt.Key.Key_Space:
            self.fit = not self.fit
            self._draw_image()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_PageDown):
            self.next_group()
        elif key == Qt.Key.Key_PageUp:
            self.previous_group()
        elif key == Qt.Key.Key_Escape:
            self._close()
        else:
            super().keyPressEvent(event)
