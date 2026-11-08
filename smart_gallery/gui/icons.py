"""Shared line icons (viewer, Library and form buttons), painted with QPainter (no files)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _chevron(p: QPainter, s: float, left: bool) -> None:
    x0, x1 = (0.62, 0.38) if left else (0.38, 0.62)
    path = QPainterPath(QPointF(s * x0, s * 0.2))
    path.lineTo(s * x1, s * 0.5)
    path.lineTo(s * x0, s * 0.8)
    p.drawPath(path)


def _zoom(p: QPainter, s: float) -> None:
    p.drawEllipse(QRectF(s * 0.15, s * 0.15, s * 0.5, s * 0.5))
    p.drawLine(QPointF(s * 0.58, s * 0.58), QPointF(s * 0.85, s * 0.85))


def _faces(p: QPainter, s: float) -> None:
    p.drawRoundedRect(
        QRectF(s * 0.12, s * 0.12, s * 0.76, s * 0.76), s * 0.12, s * 0.12
    )
    p.drawEllipse(QRectF(s * 0.36, s * 0.28, s * 0.28, s * 0.28))
    p.drawArc(QRectF(s * 0.26, s * 0.58, s * 0.48, s * 0.4), 0, 180 * 16)


def _folder(p: QPainter, s: float) -> None:
    path = QPainterPath(QPointF(s * 0.1, s * 0.25))
    for x, y in ((0.4, 0.25), (0.48, 0.33), (0.9, 0.33), (0.9, 0.8), (0.1, 0.8)):
        path.lineTo(s * x, s * y)
    path.closeSubpath()
    p.drawPath(path)


def _full(p: QPainter, s: float) -> None:
    a, b, c = s * 0.15, s * 0.38, s * 0.85
    for x, y, dx, dy in ((a, a, 1, 1), (c, a, -1, 1), (a, c, 1, -1), (c, c, -1, -1)):
        p.drawLine(QPointF(x, y), QPointF(x + dx * (b - a), y))
        p.drawLine(QPointF(x, y), QPointF(x, y + dy * (b - a)))


def _info(p: QPainter, s: float) -> None:
    p.drawEllipse(QRectF(s * 0.12, s * 0.12, s * 0.76, s * 0.76))
    p.drawLine(QPointF(s * 0.5, s * 0.45), QPointF(s * 0.5, s * 0.7))
    p.drawPoint(QPointF(s * 0.5, s * 0.32))


def _close(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.25, s * 0.25), QPointF(s * 0.75, s * 0.75))
    p.drawLine(QPointF(s * 0.75, s * 0.25), QPointF(s * 0.25, s * 0.75))


def _play(p: QPainter, s: float) -> None:
    path = QPainterPath(QPointF(s * 0.32, s * 0.2))
    path.lineTo(s * 0.8, s * 0.5)
    path.lineTo(s * 0.32, s * 0.8)
    path.closeSubpath()
    p.setBrush(p.pen().color())
    p.drawPath(path)


def _open(p: QPainter, s: float) -> None:
    p.drawRoundedRect(QRectF(s * 0.15, s * 0.25, s * 0.6, s * 0.6), s * 0.08, s * 0.08)
    p.drawLine(QPointF(s * 0.45, s * 0.55), QPointF(s * 0.85, s * 0.15))
    p.drawLine(QPointF(s * 0.6, s * 0.15), QPointF(s * 0.85, s * 0.15))
    p.drawLine(QPointF(s * 0.85, s * 0.15), QPointF(s * 0.85, s * 0.4))


def _sort(p: QPainter, s: float, up: bool) -> None:
    """An arrow with a stem; the head points up for ascending, down for descending."""
    tip, tail = (0.18, 0.82) if up else (0.82, 0.18)
    head = 0.2 if up else -0.2
    p.drawLine(QPointF(s * 0.5, s * tip), QPointF(s * 0.5, s * tail))
    path = QPainterPath(QPointF(s * 0.3, s * (tip + head)))
    path.lineTo(s * 0.5, s * tip)
    path.lineTo(s * 0.7, s * (tip + head))
    p.drawPath(path)


def _refresh(p: QPainter, s: float) -> None:
    rect = QRectF(s * 0.17, s * 0.17, s * 0.66, s * 0.66)
    p.drawArc(rect, 40 * 16, 280 * 16)
    # The arrow head sits at the arc's open end (upper right).
    path = QPainterPath(QPointF(s * 0.8, s * 0.12))
    path.lineTo(s * 0.8, s * 0.34)
    path.lineTo(s * 0.58, s * 0.34)
    p.drawPath(path)


def _undo(p: QPainter, s: float) -> None:
    path = QPainterPath(QPointF(s * 0.38, s * 0.2))
    path.lineTo(s * 0.18, s * 0.4)
    path.lineTo(s * 0.38, s * 0.6)
    p.drawPath(path)
    curve = QPainterPath(QPointF(s * 0.18, s * 0.4))
    curve.lineTo(s * 0.55, s * 0.4)
    curve.cubicTo(
        QPointF(s * 0.85, s * 0.4),
        QPointF(s * 0.85, s * 0.8),
        QPointF(s * 0.55, s * 0.8),
    )
    curve.lineTo(s * 0.35, s * 0.8)
    p.drawPath(curve)


def _browse(p: QPainter, s: float) -> None:
    """A folder with three dots: pick a location."""
    path = QPainterPath(QPointF(s * 0.1, s * 0.2))
    for x, y in ((0.4, 0.2), (0.48, 0.28), (0.9, 0.28), (0.9, 0.8), (0.1, 0.8)):
        path.lineTo(s * x, s * y)
    path.closeSubpath()
    p.drawPath(path)
    for x in (0.3, 0.5, 0.7):
        p.drawPoint(QPointF(s * x, s * 0.54))


_PAINTERS = {
    "prev": lambda p, s: _chevron(p, s, True),
    "next": lambda p, s: _chevron(p, s, False),
    "zoom": _zoom,
    "faces": _faces,
    "folder": _folder,
    "full": _full,
    "info": _info,
    "close": _close,
    "play": _play,
    "open": _open,
    "sort-up": lambda p, s: _sort(p, s, True),
    "sort-down": lambda p, s: _sort(p, s, False),
    "refresh": _refresh,
    "undo": _undo,
    "browse": _browse,
}
NAMES = tuple(_PAINTERS)


def icon(name: str, color: QColor, size: int = 18) -> QIcon:
    """A line icon in ``color``; raises KeyError for an unknown name."""
    draw = _PAINTERS[name]
    ratio = 2  # sharp on high-DPI screens
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), max(1.5, size / 10))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    draw(p, float(size))
    p.end()
    return QIcon(pixmap)


def spinner_angle(step: int, dots: int = 8) -> float:
    """The angle in degrees of the leading dot at animation ``step``."""
    return (step % dots) * 360.0 / dots


def dot_position(centre: QPointF, radius: float, angle: float) -> QPointF:
    rad = math.radians(angle)
    return QPointF(
        centre.x() + radius * math.cos(rad), centre.y() + radius * math.sin(rad)
    )
