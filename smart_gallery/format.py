"""One way to write sizes, counts and durations, shared by every screen."""

_UNITS = ("B", "KB", "MB", "GB", "TB")


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
    if round(value, decimals) >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024  # 1023.9 KB rounds up to 1024: show 1.00 MB instead
        unit += 1
        decimals = 2
    return f"{value:.{decimals}f} {_UNITS[unit]}"


def format_count(n: int, noun: str, plural: str | None = None) -> str:
    """``1 file`` / ``84,355 files``; pass ``plural`` for irregular nouns."""
    word = noun if n == 1 else (plural or f"{noun}s")
    return f"{n:,} {word}"


def format_duration(seconds: float) -> str:
    """``12s``, ``4m 3s`` or ``15h 54m 59s``."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
