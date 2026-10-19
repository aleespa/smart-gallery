"""The app's name in the top bar, each letter filled with its own gradient.

Every letter fades from a base colour at the bottom to white at the top. The
base colours run along the Sunset map (coral to orange, ``theme.SUNSET``)
from the first letter to the last. The name is drawn once as one outline, so kerning is kept, and
each letter's slice of it gets its own fill.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from smart_gallery.gui import theme

APP_NAME = "Smart Gallery"
# The base colours run from the first stop to the last.
GRADIENT = tuple(QColor(c) for c in theme.SUNSET)
TOP = QColor("#FFFFFF")
# Where the fade sits in each letter's height, from the bottom.
BASE_AT = 0.15
WHITE_AT = 0.95


def letter_colours(count: int) -> list[QColor]:
    """``count`` base colours spread evenly along the brand's colour map."""
    start, end = GRADIENT
    if count <= 1:
        return [QColor(start)] * count
    colours = []
    for i in range(count):
        t = i / (count - 1)
        colours.append(
            QColor.fromRgbF(
                start.redF() + (end.redF() - start.redF()) * t,
                start.greenF() + (end.greenF() - start.greenF()) * t,
                start.blueF() + (end.blueF() - start.blueF()) * t,
            )
        )
    return colours


class BrandLabel(QWidget):
    def __init__(self, text: str = APP_NAME, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self.setFont(theme.serif_font(theme.BRAND_PT))
        self.setAccessibleName(text)

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        metrics = QFontMetricsF(self.font())
        return QSize(
            int(metrics.horizontalAdvance(self._text)) + 2, int(metrics.height()) + 2
        )

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        metrics = QFontMetricsF(self.font())
        top = (self.height() - metrics.height()) / 2
        baseline = top + metrics.ascent()
        bottom = top + metrics.height()
        path = QPainterPath()
        path.addText(QPointF(0, baseline), self.font(), self._text)

        letters = [i for i, ch in enumerate(self._text) if not ch.isspace()]
        colours = dict(zip(letters, letter_colours(len(letters))))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(0, 0, 0, 0))
        for i in letters:
            # Each letter's slice runs from where it starts to where the next
            # one starts, so the fills meet without gaps or overlaps.
            left = metrics.horizontalAdvance(self._text[:i])
            right = metrics.horizontalAdvance(self._text[: i + 1])
            if i == letters[-1]:
                right = self.width()  # keep any overhang of the last letter
            fade = QLinearGradient(0, bottom, 0, top)
            fade.setColorAt(BASE_AT, colours[i])
            fade.setColorAt(WHITE_AT, TOP)
            painter.setClipRect(QRectF(left, 0, right - left, self.height()))
            painter.fillPath(path, fade)
        painter.end()
