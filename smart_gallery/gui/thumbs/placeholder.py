"""Placeholder tiles shown until a thumbnail arrives."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPixmap, QPolygonF

from smart_gallery.gui import theme
from smart_gallery.gui.library.viewer import format_duration

_TILE_BG = theme.qcolor(theme.TILE_BACK)
_TILE_FG = theme.qcolor(theme.TILE_GLYPH)
_placeholders: dict[tuple[str, str, int], QPixmap] = {}


def _tile(kind: str, text: str, edge: int) -> QPixmap:
    """Tile shown until a thumbnail arrives: a photo glyph, a play sign with
    the video's duration, or the file extension."""
    key = (kind, text, edge)
    cached = _placeholders.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(edge, edge)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_TILE_BG)
    p.drawRoundedRect(QRectF(2, 2, edge - 4, edge - 4), 8, 8)
    p.setBrush(_TILE_FG)
    if kind == "photo":
        p.drawEllipse(QRectF(edge * 0.58, edge * 0.25, edge * 0.14, edge * 0.14))
        hills = [(0.2, 0.72), (0.42, 0.42), (0.58, 0.6), (0.68, 0.5), (0.82, 0.72)]
        p.drawPolygon(QPolygonF([QPointF(x * edge, y * edge) for x, y in hills]))
    elif kind == "video":
        tri = [(0.4, 0.32), (0.4, 0.68), (0.68, 0.5)]
        p.drawPolygon(QPolygonF([QPointF(x * edge, y * edge) for x, y in tri]))
    if text:
        font = QFont(p.font())
        font.setPointSize(11 if kind == "video" else 16)
        font.setBold(True)
        p.setFont(font)
        p.setPen(_TILE_FG)
        if kind == "video":
            box = QRectF(0, edge * 0.72, edge - 12, edge * 0.2)
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        else:
            box = QRectF(0, 0, edge, edge)
            align = Qt.AlignmentFlag.AlignCenter
        p.drawText(box, align, text)
    p.end()
    _placeholders[key] = pixmap
    return pixmap


_KINDS = {"image": "photo", "video": "video", "other": "file"}


def placeholder_pixmap(kind: str, size: int, text: str = "") -> QPixmap:
    """A ``size`` square tile for ``kind`` (``image``, ``video`` or ``other``)."""
    return _tile(_KINDS.get(kind, "file"), text, size)


def placeholder_for(row, size: int = 150) -> QPixmap:
    if row.media_type == "video":
        return placeholder_pixmap("video", size, format_duration(row.duration_ms))
    if row.media_type == "image":
        return placeholder_pixmap("image", size)
    return placeholder_pixmap("other", size, (row.ext or "").lstrip(".").upper()[:5])
