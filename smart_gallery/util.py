"""Small shared helpers with no project dependencies."""

import re
from typing import Optional

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F-\x9F]")


def clean_text(value):
    """Strip control characters that are unsafe for Excel / noisy in metadata."""
    if isinstance(value, str):
        cleaned = _CONTROL_CHARS_RE.sub("", value).strip()
        return cleaned or None
    return value


def to_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def format_shutter(seconds: Optional[float]) -> Optional[str]:
    """Render a shutter time in seconds as a display string, e.g. 0.004 -> '1/250s'."""
    if seconds is None:
        return None
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    if seconds < 1:
        return f"1/{round(1 / seconds)}s"
    return f"{seconds:g}s"


def parse_shutter(value) -> Optional[float]:
    """Parse a shutter string ('1/250s', '1/250', '2s', '0.5') into seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower().replace("s", "")
    if not s:
        return None
    try:
        if "/" in s:
            num, den = s.split("/")
            return float(num) / float(den)
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None
