"""Typefaces and the accent colour for the desktop app.

Segoe UI Variable Text is the body face for every control; Georgia is kept for
headings (the brand, page titles, section titles). Each family lists fallbacks
for systems that lack it.

The accent is the first Sunset stop, the colour the brand name starts from.
It replaces the system accent in the palette, so selections, checked boxes,
toggled buttons and the open tab's underline all share it.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

SANS = ["Segoe UI Variable Text", "Segoe UI", "sans-serif"]
SERIF = ["Georgia", "Times New Roman", "serif"]

BRAND_PT = 17
TAB_PT = 11
TITLE_PT = 22
SECTION_PT = 10.5

# Sunset: coral to orange. The brand's letters run along it.
SUNSET = ("#FF5F6D", "#FFA552")
ACCENT = SUNSET[0]
# Text on the accent: dark, since white on coral is too faint to read.
ON_ACCENT = "#1A1A1A"


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
