"""Typefaces, colours and shared style sheets for the desktop app.

Segoe UI Variable Text is the body face for every control; Georgia is kept for
headings (the brand, page titles, section titles). Each family lists fallbacks
for systems that lack it.

The colours themselves live in ``smart_gallery/colors.py``, shared with the
Streamlit dashboard and the report figures; edit them there. The accent is the
first Sunset stop, the colour the brand name starts from. It replaces the
system accent in the palette, so selections, checked boxes, toggled buttons
and the open tab's underline all share it. Everything else should use a
palette role or one of the names re-exported here, never a colour literal.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from smart_gallery.colors import (  # noqa: F401  (re-exported for the GUI)
    ACCENT,
    BADGE_BACK,
    BADGE_TEXT,
    BRAND_CAPS,
    CLOSE_HOVER,
    ERROR,
    ON_ACCENT,
    ON_WARNING,
    SUNSET,
    TILE_BACK,
    TILE_GLYPH,
    WARNING,
)

SANS = ["Segoe UI Variable Text", "Segoe UI", "sans-serif"]
SERIF = ["Georgia", "Times New Roman", "serif"]

BRAND_PT = 17
TAB_PT = 11
TITLE_PT = 22
HEADER_PT = 13  # a heading inside a page: Library groups, Stats panels
SECTION_PT = 10.5
TREE_INDENT = 12  # px per tree level (Qt's default is about 20)

WARNING_BANNER_STYLE = f"background: {WARNING}; color: {ON_WARNING}; padding: 6px;"
ERROR_TEXT_STYLE = f"color: {ERROR};"
ERROR_FIELD_STYLE = f"border: 1px solid {ERROR};"


def qcolor(color: str | tuple[int, ...]) -> QColor:
    """A ``colors`` value as a QColor: ``#RRGGBB`` or an ``(r, g, b, a)`` tuple."""
    return QColor(*color) if isinstance(color, tuple) else QColor(color)


def apply_theme(app: QApplication) -> None:
    """Make the body face the default (keeping the size) and the Sunset
    accent the palette's highlight."""
    font = QFont(app.font())
    font.setFamilies(SANS)
    app.setFont(font)
    app.setPalette(accent_palette(app.palette()))


def accent_palette(palette: QPalette) -> QPalette:
    """``palette`` with the accent in every role the styles highlight with."""
    palette = QPalette(palette)
    accent, on_accent = QColor(ACCENT), QColor(ON_ACCENT)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.Highlight, accent)
        palette.setColor(group, QPalette.ColorRole.Accent, accent)
        palette.setColor(group, QPalette.ColorRole.HighlightedText, on_accent)
    return palette


def serif_font(point_size: float) -> QFont:
    font = QFont()
    font.setFamilies(SERIF)
    font.setPointSizeF(point_size)
    return font
