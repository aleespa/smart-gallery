"""Every colour Smart Gallery uses, in one place.

The desktop app (``gui/theme.py``), the Streamlit dashboard and the report
figures all read their colours from here, so this module imports neither Qt
nor a plotting library. To retheme the app, edit the values in this file:

- ``SUNSET`` is the brand: the title-bar name runs from its first stop to its
  last, and its first stop is the ``ACCENT`` (selections, checked boxes, the
  open tab, primary buttons).
- ``categorical()`` is the chart colourmap. It is generated from the accent:
  every colour keeps the accent's lightness and colourfulness (in OKLCH, so
  they look equally bright) and only the hue turns, by the golden angle, so
  neighbouring ranks are far apart on the wheel. The largest category is
  always the accent itself.
- ``sequential()`` is the scale for densities (the photo map): from clear to
  the last Sunset stop to the accent.

Colours are ``#RRGGBB`` strings; the few translucent ones are ``(r, g, b, a)``
tuples with 0-255 channels.
"""

from __future__ import annotations

import math
from functools import lru_cache

# ── brand ───────────────────────────────────────────────────────────────────
# Sunset: coral to orange.
SUNSET: tuple[str, ...] = ("#FF5F6D", "#FFA552")
ACCENT = SUNSET[0]
# Text on the accent: dark, since white on coral is too faint to read.
ON_ACCENT = "#1A1A1A"
# The brand name's capital letters, over the gradient's lower-case ones.
BRAND_CAPS = "#FFFFFF"

# ── status ──────────────────────────────────────────────────────────────────
WARNING = "#F0C040"  # banners: "the ExifTool binary was not found", ...
ON_WARNING = "#1A1A1A"
ERROR = "#D64545"  # error text and the border of an invalid field
KEEP = "#2E8B57"  # a shot kept in a burst
CLOSE_HOVER = "#C42B1C"  # the window close button, as the system draws it

# ── neutrals ────────────────────────────────────────────────────────────────
NEUTRAL = "#9E9E9E"  # the "Other" category in charts
TILE_BACK = (128, 128, 128, 50)  # library placeholder tile
TILE_GLYPH = (128, 128, 128, 170)  # the photo/play glyph on it
BADGE_BACK = (0, 0, 0, 150)  # video badge over a thumbnail
BADGE_TEXT = (255, 255, 255, 235)
REPORT_HEADER = "#1A1A1A"  # Excel header row
ON_REPORT_HEADER = "#FFFFFF"
BAR_EDGE = "#1A1A1A"  # outlines of histogram bars in report figures
MAP_LAND = "#FFFFFF"  # base map in report figures
MAP_BORDER = "#D3D3D3"

# ── chart colourmap ─────────────────────────────────────────────────────────
CATEGORY_COUNT = 12  # how many distinct colours charts cycle through
GOLDEN_ANGLE = 137.50776  # degrees of hue between consecutive ranks
# After this many ranks the hues start to repeat closely, so each further
# round is a step darker to stay distinguishable.
HUES_PER_ROUND = 6
ROUND_DARKEN = 0.12  # OKLCH lightness taken off per round


def categorical(n: int = CATEGORY_COUNT, base: str = ACCENT) -> tuple[str, ...]:
    """``n`` distinct colours for categories, rank 0 being ``base``."""
    return _categorical(n, base.upper())


@lru_cache(maxsize=None)
def _categorical(n: int, base: str) -> tuple[str, ...]:
    lightness, chroma, hue = _oklch(base)
    colors = [base]
    for rank in range(1, n):
        dark = rank // HUES_PER_ROUND * ROUND_DARKEN
        colors.append(_from_oklch(lightness - dark, chroma, hue + rank * GOLDEN_ANGLE))
    return tuple(colors)


def sequential() -> list[tuple[float, str]]:
    """A density scale as (position, colour) stops, transparent at zero.
    It is Plotly's colorscale format, and easy to turn into a matplotlib one."""
    r, g, b = _rgb(SUNSET[-1])
    return [(0.0, f"rgba({r},{g},{b},0)"), (0.35, SUNSET[-1]), (1.0, ACCENT)]


# ── OKLCH ───────────────────────────────────────────────────────────────────
# Björn Ottosson's OKLab, in polar form. Lightness 0..1, chroma ~0..0.37.


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _oklch(color: str) -> tuple[float, float, float]:
    r, g, b = (_to_linear(v / 255) for v in _rgb(color))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return lightness, math.hypot(a, b2), math.degrees(math.atan2(b2, a)) % 360


def _linear_rgb(lightness: float, chroma: float, hue: float):
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def _from_oklch(lightness: float, chroma: float, hue: float) -> str:
    """``#RRGGBB`` for the colour, losing chroma until it fits in sRGB."""
    hue %= 360
    while True:
        rgb = _linear_rgb(lightness, chroma, hue)
        if all(-1e-4 <= c <= 1 + 1e-4 for c in rgb) or chroma <= 0:
            break
        chroma -= 0.005
    channels = (round(255 * _from_linear(min(max(c, 0.0), 1.0))) for c in rgb)
    return "#" + "".join(f"{c:02X}" for c in channels)


# ── chart series ────────────────────────────────────────────────────────────
# What each fixed series is drawn in. File types and camera settings take the
# first ranks of the colourmap, so the biggest one is the accent.
FILE_TYPE_COLORS: dict[str, str] = dict(
    zip(("Images", "Videos", "Others"), categorical(3))
)
VIDEO = FILE_TYPE_COLORS["Videos"]  # video minutes: the same as in file types
SETTINGS_COLORS: dict[str, str] = dict(
    zip(("Aperture", "Shutter Speed", "ISO", "Focal Length"), categorical(4))
)
# Photo calendar (Stats): days with photos, from fewest to most. Empty days
# are drawn from the palette's text colour so they follow light and dark.
CALENDAR_SHADES: tuple[str, ...] = ("#FFD2A8", SUNSET[1], "#FF825F", ACCENT)
