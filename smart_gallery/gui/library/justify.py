"""Qt-free justified-row layout for the Library grid."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from smart_gallery.models import display_size

GAP = 4
MIN_ASPECT = 0.4
MAX_ASPECT = 3.0
ROW_HEIGHT_MIN = 110
ROW_HEIGHT_MAX = 340
ROW_HEIGHT_DEFAULT = 190
ROW_HEIGHT_STEP = 20


@dataclass(frozen=True)
class JustifiedRow:
    start: int  # index of the row's first item
    height: int
    widths: tuple[int, ...]
    full: bool  # False for the leftover last row, which is not stretched


def tile_aspect(
    width: Optional[int],
    height: Optional[int],
    orientation: Optional[int],
    media_type: str,
) -> float:
    """Upright width/height ratio, clamped; a fallback when a side is unknown."""
    w, h = display_size(width, height, orientation)
    if not w or not h or w <= 0 or h <= 0:
        fallback = {"image": 1.5, "video": 16 / 9}.get(media_type, 1.0)
        return min(MAX_ASPECT, max(MIN_ASPECT, fallback))
    return min(MAX_ASPECT, max(MIN_ASPECT, w / h))


def justify(
    aspects: Sequence[float], width: int, target: int, gap: int = GAP
) -> list[JustifiedRow]:
    width = max(1, width)
    rows: list[JustifiedRow] = []
    start = 0
    total = 0.0
    for i, a in enumerate(aspects):
        total += a
        n = i - start + 1
        if total * target + gap * (n - 1) >= width:
            exact = (width - gap * (n - 1)) / total
            widths = [math.floor(x * exact) for x in aspects[start:i]]
            widths.append(width - gap * (n - 1) - sum(widths))
            rows.append(JustifiedRow(start, round(exact), tuple(widths), True))
            start, total = i + 1, 0.0
    if start < len(aspects):
        widths = tuple(round(a * target) for a in aspects[start:])
        rows.append(JustifiedRow(start, target, widths, False))
    return rows
