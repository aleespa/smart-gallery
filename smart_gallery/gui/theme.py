"""Typefaces, colours and shared style sheets for the desktop app.

Segoe UI Variable Text is the body face for every control; Georgia is kept for
headings (the brand, page titles, section titles). Each family lists fallbacks
for systems that lack it.

The colours themselves live in ``smart_gallery/colors.py``, shared with the
Streamlit dashboard and the report figures; edit them there. The accent is the
first Sunset stop, the colour the brand name starts from; text and thin lines
on the dark background use it. Surfaces that carry text use the deeper
``ACCENT_FILL`` with white ``ON_ACCENT`` text instead. The fill replaces the
system accent in the palette (Highlight/Accent), so selections, checked boxes
and toggled buttons all share it and stay readable. Everything else should use a
palette role or one of the names re-exported here, never a colour literal.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QPushButton

from smart_gallery.colors import (  # noqa: F401  (re-exported for the GUI)
    ACCENT,
    ACCENT_FILL,
    BADGE_BACK,
    BADGE_TEXT,
    BRAND_CAPS,
    DARK_DISABLED_TEXT,
    DARK_PALETTE,
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

BRAND_PT = 21
TAB_PT = 11
TITLE_PT = 22
HEADER_PT = 13  # a heading inside a page: Library groups, Stats panels
SECTION_PT = 10.5
TREE_INDENT = 20  # px per tree level; less and the arrow hits the selection bar

WARNING_BANNER_STYLE = f"background: {WARNING}; color: {ON_WARNING}; padding: 6px;"
ERROR_TEXT_STYLE = f"color: {ERROR};"
ERROR_FIELD_STYLE = f"border: 1px solid {ERROR};"

# A page's main action (Run, Export…, Import…, Find new faces…): white bold
# text on the accent fill. Disabled, it falls back to a plain grey button so
# a coral button always means "you can press this".
_PRIMARY_HOVER = QColor(ACCENT_FILL).lighter(110).name()
_PRIMARY_PRESSED = QColor(ACCENT_FILL).darker(112).name()
PRIMARY_STYLE = f"""
QPushButton#primaryButton {{
    background: {ACCENT_FILL};
    color: {ON_ACCENT};
    border: none;
    border-radius: 4px;
    padding: 7px 20px;
    font-weight: bold;
}}
QPushButton#primaryButton:hover {{ background: {_PRIMARY_HOVER}; }}
QPushButton#primaryButton:pressed {{ background: {_PRIMARY_PRESSED}; }}
QPushButton#primaryButton:disabled {{
    background: {DARK_PALETTE["Button"]};
    color: {DARK_DISABLED_TEXT};
}}
"""


def primary(button: QPushButton) -> QPushButton:
    """Style ``button`` as its page's main action. The sheet sits on the
    button itself, so no parent's style sheet can undo it."""
    button.setObjectName("primaryButton")
    button.setStyleSheet(PRIMARY_STYLE)
    return button


def qcolor(color: str | tuple[int, ...]) -> QColor:
    """A ``colors`` value as a QColor: ``#RRGGBB`` or an ``(r, g, b, a)`` tuple."""
    return QColor(*color) if isinstance(color, tuple) else QColor(color)


def apply_theme(app: QApplication) -> None:
    """Make the body face the default (keeping the size), the app always dark
    whatever the system is set to, and the Sunset accent the palette's
    highlight."""
    font = QFont(app.font())
    font.setFamilies(SANS)
    app.setFont(font)
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    if app.palette().color(QPalette.ColorRole.Window).lightness() >= 128:
        # The platform did not follow (offscreen, older Windows styles):
        # Fusion with the same dark colours.
        app.setStyle("Fusion")
        app.setPalette(dark_palette())
    app.setPalette(accent_palette(app.palette()))


def dark_palette() -> QPalette:
    """The dark palette Qt's windows11 style uses, from ``colors``."""
    palette = QPalette()
    for name, color in DARK_PALETTE.items():
        palette.setColor(getattr(QPalette.ColorRole, name), qcolor(color))
    disabled = qcolor(DARK_DISABLED_TEXT)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def accent_palette(palette: QPalette) -> QPalette:
    """``palette`` with the accent fill in every role the styles highlight
    with, and white text on it."""
    palette = QPalette(palette)
    accent, on_accent = QColor(ACCENT_FILL), QColor(ON_ACCENT)
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
