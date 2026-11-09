"""One way to write sizes, counts, durations and dates, shared by every screen."""

from datetime import date

_UNITS = ("B", "KB", "MB", "GB", "TB")
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


def format_size(num_bytes: int) -> str:
    """1024-based size with three significant figures (``45.6 GB``)."""
    if num_bytes < 1024:
        return f"{int(num_bytes)} B"
    value = float(num_bytes)
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    decimals = 2 if value < 10 else 1 if value < 100 else 0
    if decimals == 2 and round(value, 2) >= 10:
        decimals = 1
    if decimals == 1 and round(value, 1) >= 100:
        decimals = 0
    if round(value, decimals) >= 1000 and unit < len(_UNITS) - 1:
        value /= 1024  # 999.5 MB would print four digits: roll to 0.98 GB
        unit += 1
        decimals = 2
    return f"{value:.{decimals}f} {_UNITS[unit]}"


def format_count(n: int, noun: str, plural: str | None = None) -> str:
    """``1 file`` / ``84,355 files``; pass ``plural`` for irregular nouns."""
    word = noun if n == 1 else (plural or f"{noun}s")
    return f"{n:,} {word}"


def format_duration(seconds: float) -> str:
    """``12s``, ``4m 3s`` or ``15h 54m 59s``."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_date(date_taken: str | None) -> str:
    """``1 Mar 2026`` from a catalog date (``YYYY-MM-DD...``); "" if unknown."""
    try:
        day = date.fromisoformat((date_taken or "")[:10])
    except ValueError:
        return ""
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"
