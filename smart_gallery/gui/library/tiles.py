"""How a Library tile looks: the thumbnail fills the tile, no file name.

``PhotoTileDelegate`` paints the thumbnail as a centre crop scaled to the tile
(its own small cache keeps the scaled copies, so scrolling does not rescale),
then the state on top: a hovered tile is a little brighter with an empty
check circle top-left; a selected one shrinks to 90% inside an accent ring
with a filled check circle. A video carries a play chip with its length
top-right, and a file kept only on a mirror shows a faded placeholder with an
"On D:" chip bottom-left. The model tells it what to show through the roles
below and ``Qt.DecorationRole`` (the thumbnail as loaded).
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from smart_gallery.gui import theme

# The video's length as the chip shows it ("0:42"), or "" for anything else.
DURATION_ROLE = Qt.ItemDataRole.UserRole + 21
# The label of the mirror that keeps the file's only copy, or None if on disk.
OFFLOADED_ROLE = Qt.ItemDataRole.UserRole + 22
# While a tile has no thumbnail: the glyph to centre on its plain backing
# (already faded for an offloaded file); None once the thumbnail is in.
PLACEHOLDER_ROLE = Qt.ItemDataRole.UserRole + 23

OFFLOADED_OPACITY = 0.35  # the placeholder of a file kept on a mirror only
SCALED_CACHE_BYTES = 64 * 1024 * 1024  # scaled thumbnails kept for repaint
SELECTED_SCALE = 0.9  # a selected tile's image, of the tile's size
RING = 2  # px, the selected tile's accent ring
CHECK = 20  # px across the check circle
CHECK_INSET = 6  # px from the tile's top-left corner to the circle
CHIP_MARGIN = 6  # px from the image's edges to a chip
CHIP_PAD = 6  # px of chip on either side of its content
CHIP_PT = 9
GLYPH_EDGE = 150  # px, the most a placeholder glyph is drawn across

_BACK = theme.qcolor(theme.TILE_BACK)
_HOVER = theme.qcolor(theme.TILE_HOVER)
_CHECK_BACK = theme.qcolor(theme.TILE_CHECK_BACK)
_BADGE_BACK = theme.qcolor(theme.BADGE_BACK)
_BADGE_TEXT = theme.qcolor(theme.BADGE_TEXT)
_ON_ACCENT = theme.qcolor(theme.ON_ACCENT)
_ACCENT = theme.qcolor(theme.ACCENT)


class PhotoTileDelegate(QStyledItemDelegate):
    """Paints Library tiles: see the module docstring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._cache_bytes = 0

    def sizeHint(self, option, index) -> QSize:  # the grid lays tiles out itself
        return QSize(150, 100)

    # ── scaled thumbnails ───────────────────────────────────────────────────
    def _scaled(self, pixmap: QPixmap, size: QSize, dpr: float) -> QPixmap:
        """``pixmap`` scaled to cover ``size`` (logical px at ``dpr``) and
        cropped to it about the centre; kept until the cache is full."""
        key = (pixmap.cacheKey(), size.width(), size.height(), dpr)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        w = max(1, round(size.width() * dpr))
        h = max(1, round(size.height() * dpr))
        scaled = pixmap.scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if (scaled.width(), scaled.height()) != (w, h):
            x = (scaled.width() - w) // 2
            y = (scaled.height() - h) // 2
            scaled = scaled.copy(x, y, w, h)
        scaled.setDevicePixelRatio(dpr)
        self._cache[key] = scaled
        self._cache_bytes += w * h * 4
        while self._cache_bytes > SCALED_CACHE_BYTES and len(self._cache) > 1:
            _key, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.width() * old.height() * 4
        return scaled

    # ── painting ────────────────────────────────────────────────────────────
    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        rect = QRect(option.rect)
        if rect.isEmpty():
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        image = _inset(rect, SELECTED_SCALE) if selected else rect
        holder = index.data(OFFLOADED_ROLE)
        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        glyph = index.data(PLACEHOLDER_ROLE)
        thumb = (
            None if glyph is not None else index.data(Qt.ItemDataRole.DecorationRole)
        )
        if isinstance(thumb, QPixmap) and not thumb.isNull():
            dpr = painter.device().devicePixelRatioF()
            painter.drawPixmap(image.topLeft(), self._scaled(thumb, image.size(), dpr))
        else:
            self._paint_placeholder(painter, image, glyph, holder is not None)
        if hovered and not selected:
            painter.fillRect(image, _HOVER)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        duration = index.data(DURATION_ROLE)
        if duration:
            self._paint_chip(painter, image, duration, top_right=True, play=True)
        if holder is not None:
            self._paint_chip(painter, image, f"On {holder}", top_right=False)
        if selected:
            self._paint_ring(painter, rect)
            self._paint_check(painter, rect, option.palette, filled=True)
        elif hovered:
            self._paint_check(painter, rect, option.palette, filled=False)
        painter.restore()

    def _paint_placeholder(
        self, painter: QPainter, rect: QRect, glyph, faded: bool
    ) -> None:
        """The plain backing with the file's glyph in the middle."""
        painter.save()
        if faded:
            painter.setOpacity(OFFLOADED_OPACITY)
        painter.fillRect(rect, _BACK)
        painter.restore()
        if isinstance(glyph, QPixmap) and not glyph.isNull():
            edge = min(rect.width(), rect.height(), GLYPH_EDGE)
            box = QRect(0, 0, edge, edge)
            box.moveCenter(rect.center())
            painter.drawPixmap(box, glyph)

    def _paint_ring(self, painter: QPainter, rect: QRect) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_ACCENT)
        # Four bands inside the tile, so the ring stays crisp at any scale.
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        for band in (
            QRect(x, y, w, RING),
            QRect(x, y + h - RING, w, RING),
            QRect(x, y, RING, h),
            QRect(x + w - RING, y, RING, h),
        ):
            painter.drawRect(band)
        painter.restore()

    def _paint_check(
        self, painter: QPainter, rect: QRect, palette: QPalette, filled: bool
    ) -> None:
        """The check circle top-left: empty and white on hover; filled with
        the highlight colour and ticked in white when selected."""
        circle = QRectF(rect.x() + CHECK_INSET, rect.y() + CHECK_INSET, CHECK, CHECK)
        painter.save()
        if filled:
            painter.setPen(QPen(_ON_ACCENT, 1.5))
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
        else:
            painter.setPen(QPen(_BADGE_TEXT, 2))
            painter.setBrush(_CHECK_BACK)
        painter.drawEllipse(circle.adjusted(1, 1, -1, -1))
        if filled:
            pen = QPen(_ON_ACCENT, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x, y, s = circle.x(), circle.y(), CHECK
            tick = [(0.28, 0.52), (0.44, 0.67), (0.73, 0.36)]
            painter.drawPolyline(
                QPolygonF([QPointF(x + s * px, y + s * py) for px, py in tick])
            )
        painter.restore()

    def _paint_chip(
        self,
        painter: QPainter,
        image: QRect,
        text: str,
        top_right: bool,
        play: bool = False,
    ) -> None:
        """A dark rounded chip with ``text`` (and a play sign before it) in
        the image's top-right or bottom-left corner."""
        painter.save()
        font = QFont(painter.font())
        font.setPointSizeF(CHIP_PT)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        height = metrics.height() + 4
        glyph = round(metrics.ascent() * 0.7) if play else 0
        gap = 4 if play else 0
        room = image.width() - 2 * CHIP_MARGIN - 2 * CHIP_PAD - glyph - gap
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, room))
        width = CHIP_PAD * 2 + glyph + gap + metrics.horizontalAdvance(text)
        chip = QRectF(0, 0, width, height)
        if top_right:
            chip.moveTopRight(
                QPointF(image.right() + 1 - CHIP_MARGIN, image.top() + CHIP_MARGIN)
            )
        else:
            chip.moveBottomLeft(
                QPointF(image.left() + CHIP_MARGIN, image.bottom() + 1 - CHIP_MARGIN)
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_BADGE_BACK)
        painter.drawRoundedRect(chip, 4, 4)
        x = chip.x() + CHIP_PAD
        if play:
            cy = chip.center().y()
            painter.setBrush(_BADGE_TEXT)
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(x, cy - glyph / 2),
                        QPointF(x, cy + glyph / 2),
                        QPointF(x + glyph * 0.85, cy),
                    ]
                )
            )
            x += glyph + gap
        painter.setPen(_BADGE_TEXT)
        box = QRectF(x, chip.y(), chip.right() - CHIP_PAD - x, height)
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self._draw_text(painter, box, align, text)
        painter.restore()

    def _draw_text(self, painter: QPainter, rect: QRectF, flags, text: str) -> None:
        """Every word a tile shows goes through here (tests count them)."""
        painter.drawText(rect, flags, text)


def _inset(rect: QRect, scale: float) -> QRect:
    """``rect`` shrunk to ``scale`` of its size about its centre."""
    w = max(1, round(rect.width() * scale))
    h = max(1, round(rect.height() * scale))
    inner = QRect(0, 0, w, h)
    inner.moveCenter(rect.center())
    return inner
