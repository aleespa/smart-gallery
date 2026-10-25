"""Chart numbers shared by the Streamlit dashboard and the desktop app.

Every function takes DataFrames (see ``figures.load_catalog_df``) and returns
plain tuples and dataclasses. No Qt and no Plotly import here, so both front
ends draw identical values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from smart_gallery import colors

# Category rank i gets PALETTE[i]; the colours are set in ``colors``.
PALETTE: tuple[str, ...] = colors.categorical()
OTHER = "Other"
OTHER_COLOR = colors.NEUTRAL
FILE_TYPES: tuple[str, ...] = tuple(colors.FILE_TYPE_COLORS)
FILE_TYPE_COLORS: tuple[str, ...] = tuple(colors.FILE_TYPE_COLORS.values())
VIDEO_COLOR = colors.VIDEO

_TIE_TOLERANCE = 1e-12
_MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def color_for(name: str, rank: int) -> str:
    """Colour of a category: grey for "Other", else the palette by rank."""
    return OTHER_COLOR if name == OTHER else PALETTE[rank % len(PALETTE)]


@dataclass(frozen=True)
class Counts:
    items: tuple[tuple[str, int], ...]
    total: int


def file_type_counts(fi, fv, fo) -> tuple[tuple[str, int], ...]:
    pairs = zip(FILE_TYPES, (len(fi), len(fv), len(fo)))
    return tuple((name, n) for name, n in pairs if n > 0)


def file_type_colors(items) -> list[str]:
    """File-type colours by name, so Videos keeps its colour with no images."""
    by_name = dict(zip(FILE_TYPES, FILE_TYPE_COLORS))
    return [by_name.get(name, color_for(name, i)) for i, (name, _) in enumerate(items)]


def top_counts(df, column: str, n: int = 10) -> Counts:
    """The ``n`` most common values of ``column``; ``total`` is every row."""
    total = len(df)
    if column not in df.columns:
        return Counts((), total)
    counts = df[column].dropna().value_counts()
    if counts.empty:
        return Counts((), total)
    ranked = sorted(((str(k), int(v)) for k, v in counts.items()), key=_by_count)
    return Counts(tuple(ranked[:n]), total)


def _by_count(item: tuple[str, int]):
    return (-item[1], item[0])


@dataclass(frozen=True)
class StopSpec:
    name: str
    stops: tuple[float, ...]
    labels: tuple[str, ...]


def _shutter_label(v: float) -> str:
    return f"1/{round(1 / v)}" if v < 1 else f"{v:g}s"


_SHUTTER_STOPS = (
    1 / 4000,
    1 / 2000,
    1 / 1000,
    1 / 500,
    1 / 250,
    1 / 125,
    1 / 60,
    1 / 30,
    1 / 15,
    1 / 8,
    1 / 4,
    1 / 2,
    1,
    2,
    4,
    8,
    15,
    30,
)
_APERTURE_STOPS = (1.0, 1.2, 1.4, 1.8, 2, 2.8, 3.5, 4, 5.6, 8, 11, 16, 22)
_ISO_STOPS = (100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600)
_FOCAL_STOPS = (14, 18, 24, 28, 35, 50, 70, 85, 105, 135, 200, 300, 400)

APERTURE = StopSpec(
    "Aperture",
    _APERTURE_STOPS,
    tuple("f/1.0" if v == 1.0 else f"f/{v:g}" for v in _APERTURE_STOPS),
)
SHUTTER = StopSpec(
    "Shutter Speed", _SHUTTER_STOPS, tuple(_shutter_label(v) for v in _SHUTTER_STOPS)
)
ISO = StopSpec("ISO", _ISO_STOPS, tuple(f"ISO {v}" for v in _ISO_STOPS))
FOCAL = StopSpec("Focal Length", _FOCAL_STOPS, tuple(f"{v}mm" for v in _FOCAL_STOPS))
SETTINGS: tuple[StopSpec, ...] = (APERTURE, SHUTTER, ISO, FOCAL)

SETTINGS_COLORS: dict[str, str] = colors.SETTINGS_COLORS


def stop_buckets(values: pd.Series, spec: StopSpec) -> tuple[tuple[str, int], ...]:
    """Count values per nearest stop (log scale, ties to the lower stop).

    The result covers every stop from the lowest to the highest one used, so
    unused stops in between show as zero. Values that are not positive finite
    numbers are dropped."""
    nums = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    nums = nums[np.isfinite(nums) & (nums > 0)]
    if nums.size == 0:
        return ()
    log_stops = np.log10(np.asarray(spec.stops, dtype=float))
    dist = np.abs(np.log10(nums)[:, None] - log_stops[None, :])
    near = dist <= dist.min(axis=1, keepdims=True) + _TIE_TOLERANCE
    idx = near.argmax(axis=1)  # first True = lowest stop, stops are ascending
    hist = np.bincount(idx, minlength=len(spec.stops))
    lo, hi = int(idx.min()), int(idx.max())
    return tuple((spec.labels[i], int(hist[i])) for i in range(lo, hi + 1))


def parse_shutter(s) -> Optional[float]:
    if pd.isna(s):
        return None
    try:
        s_str = str(s).rstrip("s")
        if "/" in s_str:
            num, den = s_str.split("/")
            return float(num) / float(den)
        return float(s_str)
    except Exception:
        return None


def settings_buckets(fi) -> dict[str, tuple[tuple[str, int], ...]]:
    """Bucketed camera settings for the images, non-empty specs only."""
    out: dict[str, tuple[tuple[str, int], ...]] = {}
    if fi.empty:
        return out
    columns = {
        "Aperture": "aperture",
        "Shutter Speed": "shutter_speed",
        "ISO": "iso",
        "Focal Length": "focal_length",
    }
    for spec in SETTINGS:
        column = columns[spec.name]
        if column not in fi.columns:
            continue
        series = fi[column]
        if spec is SHUTTER:
            series = series.apply(parse_shutter)
        buckets = stop_buckets(series, spec)
        if buckets:
            out[spec.name] = buckets
    return out


@dataclass(frozen=True)
class MonthSeries:
    months: tuple[str, ...]
    series: dict[str, tuple[float, ...]]


def _month_range(periods: pd.Series) -> pd.PeriodIndex:
    return pd.period_range(periods.min(), periods.max(), freq="M")


# A camera whose clock was reset stamps its files near the Unix epoch, and a
# wrongly set clock can stamp them in the future. Either one stretches a
# timeline by years, so both are left out of the monthly charts.
_EPOCH_YEAR = 1970


def _drop_implausible_dates(df: pd.DataFrame) -> pd.DataFrame:
    dates = df["date_taken"]
    now = pd.Timestamp.now(tz=dates.dt.tz)
    return df[(dates.dt.year != _EPOCH_YEAR) & (dates <= now)]


def monthly_by_camera(fi, n: int = 10) -> Optional[MonthSeries]:
    """Photos per month for the ``n`` top cameras, the rest as "Other".

    The top cameras are ranked over all of ``fi``, undated photos included,
    exactly as ``top_counts`` ranks them for the Cameras chart, so a camera
    has the same colour on both. Only dated photos are counted per month, and
    photos dated in 1970 (a reset camera clock) or in the future are left out."""
    if fi.empty or "date_taken" not in fi.columns or "camera" not in fi.columns:
        return None
    tmp = _drop_implausible_dates(fi.dropna(subset=["date_taken", "camera"]))
    if tmp.empty:
        return None
    period = tmp["date_taken"].dt.to_period("M")
    top = [name for name, _ in top_counts(fi, "camera", n).items]
    camera = tmp["camera"].astype(str)
    camera = camera.where(camera.isin(top), other=OTHER)
    table = pd.crosstab(period, camera).reindex(_month_range(period), fill_value=0)
    names = [c for c in top if c in table.columns]
    if OTHER in table.columns:
        names.append(OTHER)
    return MonthSeries(
        tuple(str(p) for p in table.index),
        {name: tuple(float(v) for v in table[name]) for name in names},
    )


def video_minutes_by_month(fv) -> Optional[MonthSeries]:
    """Video minutes per month, leaving out 1970 and future dates."""
    if fv.empty or "date_taken" not in fv.columns or "duration_ms" not in fv.columns:
        return None
    tmp = _drop_implausible_dates(fv.dropna(subset=["date_taken"]))
    if tmp.empty:
        return None
    period = tmp["date_taken"].dt.to_period("M")
    minutes = tmp["duration_ms"].groupby(period).sum() / 60000.0
    minutes = minutes.reindex(_month_range(period), fill_value=0.0)
    return MonthSeries(
        tuple(str(p) for p in minutes.index),
        {"Minutes": tuple(float(v) for v in minutes)},
    )


def month_label(month: str) -> str:
    """``"2026-03"`` -> ``"Mar 2026"``."""
    year, mon = month.split("-")
    return f"{_MONTH_NAMES[int(mon) - 1]} {year}"


def month_axis_labels(months) -> tuple[tuple[int, str], ...]:
    """Which month indices get an axis label, and its text."""
    months = list(months)
    if len(months) <= 24:
        return tuple((i, month_label(m)) for i, m in enumerate(months))
    labels = [(0, month_label(months[0]))]
    for i, m in enumerate(months[1:], start=1):
        if m.endswith("-01"):
            labels.append((i, m[:4]))
    return tuple(labels)


def geotagged(fi) -> pd.DataFrame:
    """Rows of ``fi`` with a usable latitude/longitude (empty frame if none)."""
    if fi.empty or "latitude" not in fi.columns or "longitude" not in fi.columns:
        return pd.DataFrame()
    df_loc = fi[["latitude", "longitude", "camera", "date_taken", "name"]].copy()
    df_loc["latitude"] = pd.to_numeric(df_loc["latitude"], errors="coerce")
    df_loc["longitude"] = pd.to_numeric(df_loc["longitude"], errors="coerce")
    return df_loc.dropna(subset=["latitude", "longitude"])
