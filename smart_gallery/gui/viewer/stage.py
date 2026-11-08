"""The photo viewer's stage: the photo fitted on a dark backdrop, fixed
previous/next arrows, zoom (fit, 100 %, Ctrl + wheel), panning, face boxes,
messages and a loading spinner.

Nothing here depends on the photo's shape except where the photo is painted:
the arrows and the filmstrip tab are placed from the stage's own size only.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QToolButton, QWidget

from smart_gallery import colors
from smart_gallery.gui import theme
from smart_gallery.gui import icons

MARGIN = 24  # around the fitted photo
ARROW_SIZE = QSize(40, 64)
ARROW_INSET = 12  # from the stage's left/right edges
MAX_ZOOM = 2.0  # of the image's pixels
ZOOM_STEP = 1.25  # per wheel notch with Ctrl
SPINNER_DELAY_MS = 150  # a load quicker than this shows no spinner
SPINNER_TICK_MS = 80
SHADOW_RINGS = 4
BOX_PEN = 3.0
STRIP_TAB = "▴ Filmstrip"


def fit_rect(image: QSize, area: QSize, margin: int = MARGIN) -> QRect:
    """The largest rectangle of ``image``'s shape inside ``area`` less
    ``margin`` on every side, centred, never larger than the image."""
    iw, ih = image.width(), image.height()
    aw, ah = area.width() - 2 * margin, area.height() - 2 * margin
    if iw <= 0 or ih <= 0 or aw <= 0 or ah <= 0:
        return QRect()
    if iw <= aw and ih <= ah:
        w, h = iw, ih
    elif aw * ih <= ah * iw:  # the width limits
        w, h = aw, ih * aw // iw
    else:
        w, h = iw * ah // ih, ah
    return QRect((area.width() - w) // 2, (area.height() - h) // 2, w, h)


def face_rect(face, image: QSize) -> QRectF | None:
    """Where a face (anything with ``bbox`` and ``img_size``) is on a preview
    of its photo ``image`` big, or None when that cannot be told: the scan
    did not record the size of the image it decoded, or the preview is turned
    from it (a RAW whose embedded preview is stored sideways)."""
    if not face.img_size or not face.img_size[0] or not face.img_size[1]:
        return None
    img_w, img_h = face.img_size
    if image.isEmpty() or (img_w > img_h) != (image.width() > image.height()):
        return None
    scale = image.width() / img_w
    x, y, w, h = (v * scale for v in face.bbox)
    rect = QRectF(x, y, w, h)
    if not QRectF(0, 0, image.width(), image.height()).contains(rect.center()):
        return None
    return rect


@dataclass(frozen=True)
class Box:
    """A face outline, in the shown image's pixels, with an optional name."""

    rect: QRectF
    label: str = ""


def _rgba(color: tuple[int, int, int, int]) -> str:
    return "rgba({}, {}, {}, {})".format(*color)


class PhotoStage(QWidget):
    previous = Signal()
    next = Signal()
    zoom_changed = Signal(bool)
    strip_toggle = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self._image: QImage | None = None
        self._sharp = False
        self._zoom: float | None = None  # None: fitted
        self._centre = QPointF()  # image point at the stage centre (zoomed)
        self._boxes: list[Box] = []
        self._message = ""
        self._play = False
        self._drag: QPointF | None = None
        self._wheel_rest = 0
        self._spinner_on = False
        self._spinner_step = 0
        self._spinner_delay = QTimer(self, singleShot=True, interval=SPINNER_DELAY_MS)
        self._spinner_delay.timeout.connect(self._start_spinner)
        self._spinner_tick = QTimer(self, interval=SPINNER_TICK_MS)
        self._spinner_tick.timeout.connect(self._tick)

        on_stage = QColor(colors.VIEWER_ON_STAGE)
        style = (
            f"QToolButton {{ background: {_rgba(colors.VIEWER_ARROW_BACK)};"
            " border: none; border-radius: 8px; }"
            " QToolButton:hover { background: palette(highlight); }"
        )
        self.prev_btn = self._arrow("prev", on_stage, style)
        self.next_btn = self._arrow("next", on_stage, style)
        self.prev_btn.clicked.connect(self.previous)
        self.next_btn.clicked.connect(self.next)
        self.strip_tab = QToolButton(self)
        self.strip_tab.setText(STRIP_TAB)
        self.strip_tab.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.strip_tab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.strip_tab.setStyleSheet(
            f"QToolButton {{ background: {_rgba(colors.VIEWER_ARROW_BACK)};"
            f" color: {colors.VIEWER_ON_STAGE}; border: none;"
            " border-top-left-radius: 8px; border-top-right-radius: 8px;"
            " padding: 3px 14px; }"
        )
        self.strip_tab.clicked.connect(self.strip_toggle)
        self.strip_tab.hide()

    def _arrow(self, name: str, color: QColor, style: str) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icons.icon(name, color, 24))
        button.setIconSize(QSize(24, 24))
        button.setFixedSize(ARROW_SIZE)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(style)
        return button

    # ── content ─────────────────────────────────────────────────────────────
    def set_image(self, image: QImage, *, sharp: bool, keep_view: bool = False) -> None:
        """Show ``image``. ``sharp`` False marks a placeholder (the
        thumbnail): boxes wait for the sharp image. ``keep_view`` swaps in a
        sharper copy of the same photo without moving the view."""
        old = self._image
        if keep_view and old is not None and self._zoom is not None and old.width():
            ratio = image.width() / old.width()
            self._zoom /= ratio
            self._centre = QPointF(self._centre.x() * ratio, self._centre.y() * ratio)
        elif not keep_view:
            self._set_zoom(None)
        self._image = image
        self._sharp = sharp
        self._message = ""
        self._stop_spinner()
        self._clamp()
        self.update()

    def set_message(self, text: str) -> None:
        """Show ``text`` in the centre instead of a photo."""
        self._image = None
        self._message = text
        self._set_zoom(None)
        self._stop_spinner()
        self.update()

    def message(self) -> str:
        return self._message

    def set_boxes(self, boxes: list[Box]) -> None:
        self._boxes = list(boxes)
        self.update()

    def set_play_glyph(self, on: bool) -> None:
        """A play sign over the picture (a video's thumbnail)."""
        self._play = on
        self.update()

    def set_nav(self, has_prev: bool, has_next: bool) -> None:
        self.prev_btn.setEnabled(has_prev)
        self.next_btn.setEnabled(has_next)

    def set_strip_handle(self, visible: bool) -> None:
        self.strip_tab.setVisible(visible)
        self._place_tab()

    def image_size(self) -> QSize:
        return self._image.size() if self._image is not None else QSize()

    def has_sharp_image(self) -> bool:
        return self._image is not None and self._sharp

    # ── loading ─────────────────────────────────────────────────────────────
    def set_loading(self, on: bool) -> None:
        if on:
            if not self._spinner_on:
                self._spinner_delay.start()
        else:
            self._stop_spinner()

    def spinner_visible(self) -> bool:
        return self._spinner_on

    def _start_spinner(self) -> None:
        self._spinner_on = True
        self._spinner_tick.start()
        self.update()

    def _stop_spinner(self) -> None:
        self._spinner_delay.stop()
        self._spinner_tick.stop()
        if self._spinner_on:
            self._spinner_on = False
            self.update()

    def _tick(self) -> None:
        self._spinner_step += 1
        self.update()

    # ── geometry and zoom ───────────────────────────────────────────────────
    def _fit(self) -> QRect:
        if self._image is None:
            return QRect()
        return fit_rect(self._image.size(), self.size())

    def _fit_scale(self) -> float:
        fit = self._fit()
        return fit.width() / self._image.width() if fit.width() else 1.0

    def image_rect(self) -> QRectF:
        """Where the image is painted now; empty when there is none."""
        if self._image is None:
            return QRectF()
        if self._zoom is None:
            return QRectF(self._fit())
        z = self._zoom
        w, h = self._image.width() * z, self._image.height() * z
        left = self.width() / 2 - self._centre.x() * z
        top = self.height() / 2 - self._centre.y() * z
        return QRectF(left, top, w, h)

    def is_zoomed(self) -> bool:
        return self._zoom is not None

    def reset_zoom(self) -> None:
        self._set_zoom(None)
        self.update()

    def toggle_zoom(self, anchor: QPointF | None = None) -> None:
        """Fit ↔ 100 %, keeping the image point under ``anchor`` (default:
        the stage centre) where it is."""
        if self._image is None:
            return
        if self._zoom is not None:
            self.reset_zoom()
            return
        self._zoom_to(1.0, anchor or QPointF(self.width() / 2, self.height() / 2))

    def zoom_by(self, steps: int, anchor: QPointF) -> None:
        """Zoom ``steps`` notches in (positive) or out, between fit and
        ``MAX_ZOOM``."""
        if self._image is None or not steps:
            return
        fit = self._fit_scale()
        current = self._zoom if self._zoom is not None else fit
        target = min(current * ZOOM_STEP**steps, MAX_ZOOM)
        if target <= fit + 1e-9:
            self.reset_zoom()
        else:
            self._zoom_to(target, anchor)

    def _zoom_to(self, z: float, anchor: QPointF) -> None:
        rect = self.image_rect()
        if rect.isEmpty():
            return
        iw, ih = self._image.width(), self._image.height()
        point = QPointF(
            (anchor.x() - rect.left()) / rect.width() * iw,
            (anchor.y() - rect.top()) / rect.height() * ih,
        )
        self._centre = QPointF(
            point.x() - (anchor.x() - self.width() / 2) / z,
            point.y() - (anchor.y() - self.height() / 2) / z,
        )
        self._set_zoom(z)
        self._clamp()
        self.update()

    def _set_zoom(self, z: float | None) -> None:
        was = self._zoom is not None
        self._zoom = z
        self._drag = None
        self.setCursor(
            Qt.CursorShape.OpenHandCursor
            if z is not None
            else Qt.CursorShape.ArrowCursor
        )
        if was != (z is not None):
            self.zoom_changed.emit(z is not None)

    def _clamp(self) -> None:
        """Keep a zoomed image covering the stage (or centred on an axis
        where it is smaller than the stage)."""
        if self._image is None or self._zoom is None:
            return
        z = self._zoom
        cx, cy = self._centre.x(), self._centre.y()
        for axis, size, stage in (
            (0, self._image.width(), self.width()),
            (1, self._image.height(), self.height()),
        ):
            shown = size * z
            if shown <= stage:
                value = size / 2
            else:
                half = stage / 2 / z
                value = min(max(cx if axis == 0 else cy, half), size - half)
            if axis == 0:
                cx = value
            else:
                cy = value
        self._centre = QPointF(cx, cy)

    # ── events ──────────────────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        y = (self.height() - ARROW_SIZE.height()) // 2
        self.prev_btn.move(ARROW_INSET, y)
        self.next_btn.move(self.width() - ARROW_INSET - ARROW_SIZE.width(), y)
        self._place_tab()
        self._clamp()

    def _place_tab(self) -> None:
        hint = self.strip_tab.sizeHint()
        self.strip_tab.setGeometry(
            (self.width() - hint.width()) // 2,
            self.height() - hint.height(),
            hint.width(),
            hint.height(),
        )

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._wheel_rest += event.angleDelta().y()
        steps = int(self._wheel_rest / 120)
        if not steps:
            return
        self._wheel_rest -= steps * 120
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(steps, event.position())
        else:
            signal = self.previous if steps > 0 else self.next
            for _ in range(abs(steps)):
                signal.emit()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_zoom(event.position())

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton and self._zoom is not None:
            self._drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._drag is None or self._zoom is None:
            return
        delta = event.position() - self._drag
        self._drag = event.position()
        self._centre -= QPointF(delta.x() / self._zoom, delta.y() / self._zoom)
        self._clamp()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ── painting ────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(colors.VIEWER_STAGE))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        on_stage = QColor(colors.VIEWER_ON_STAGE)
        if self._message:
            p.setPen(on_stage)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
        elif self._image is not None:
            rect = self.image_rect()
            if self._zoom is None:
                self._paint_shadow(p, rect)
            p.drawImage(rect, self._image)
            if self._sharp and self._boxes:
                self._paint_boxes(p, rect)
            if self._play:
                self._paint_play(p, rect, on_stage)
        if self._spinner_on:
            self._paint_spinner(p, on_stage)
        p.end()

    def _paint_shadow(self, p: QPainter, rect: QRectF) -> None:
        shade = QColor(*colors.VIEWER_SHADOW)
        p.setPen(Qt.PenStyle.NoPen)
        for ring in range(SHADOW_RINGS, 0, -1):
            shade.setAlpha(colors.VIEWER_SHADOW[3] // (ring + 1))
            p.setBrush(shade)
            p.drawRoundedRect(
                rect.adjusted(-ring, -ring + 2, ring, ring + 2), ring, ring
            )

    def _paint_boxes(self, p: QPainter, rect: QRectF) -> None:
        scale = rect.width() / self._image.width()
        accent = theme.qcolor(theme.ACCENT)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for box in self._boxes:
            r = QRectF(
                rect.left() + box.rect.x() * scale,
                rect.top() + box.rect.y() * scale,
                box.rect.width() * scale,
                box.rect.height() * scale,
            )
            p.setPen(QPen(accent, BOX_PEN))
            p.drawRoundedRect(r, 4, 4)
            if box.label:
                metrics = p.fontMetrics()
                w = metrics.horizontalAdvance(box.label) + 12
                h = metrics.height() + 4
                chip = QRectF(r.left(), r.top() - h - 4, w, h)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.qcolor(theme.ACCENT_FILL))
                p.drawRoundedRect(chip, h / 2, h / 2)
                p.setPen(theme.qcolor(theme.ON_ACCENT))
                p.drawText(chip, Qt.AlignmentFlag.AlignCenter, box.label)
                p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_play(self, p: QPainter, rect: QRectF, color: QColor) -> None:
        side = min(72.0, rect.width() / 3, rect.height() / 3)
        circle = QRectF(0, 0, side, side)
        circle.moveCenter(rect.center())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(*colors.VIEWER_ARROW_BACK))
        p.drawEllipse(circle)
        glyph = icons.icon("play", color, int(side / 2)).pixmap(int(side / 2))
        target = QRectF(0, 0, side / 2, side / 2)
        target.moveCenter(circle.center())
        p.drawPixmap(target.toRect(), glyph)

    def _paint_spinner(self, p: QPainter, color: QColor) -> None:
        centre = QPointF(self.width() / 2, self.height() / 2)
        lead = icons.spinner_angle(self._spinner_step)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(8):
            dot = QColor(color)
            dot.setAlphaF(0.15 + 0.85 * (i / 7))
            p.setBrush(dot)
            pos = icons.dot_position(centre, 14, lead - (7 - i) * 45)
            p.drawEllipse(pos, 3, 3)
