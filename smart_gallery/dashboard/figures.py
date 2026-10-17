"""Plotly figures and summary numbers shared by the Streamlit dashboard and the
desktop app. No Streamlit import here: every builder takes DataFrames and
returns a ``go.Figure`` (or ``None`` when there is nothing to draw)."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from smart_gallery.db import GalleryRepository
from smart_gallery.organize.filters import FilterOptions

STACKED_COLORS = [
    "#e60000",
    "#3d6ba6",
    "#65880f",
    "#4e4e94",
    "#a70000",
    "#2b4e72",
    "#5f720f",
    "#313178",
    "#ff5252",
    "#5a8cc2",
    "#8eb027",
    "#7474b0",
]


def load_catalog_df(db_path, filters: Optional[FilterOptions] = None) -> pd.DataFrame:
    """Load the catalog (optionally filtered in SQL) as a DataFrame."""
    with GalleryRepository.open(db_path, read_only=True) as repo:
        df = repo.query_df(filters)
    if "date_taken" in df.columns:
        df["date_taken"] = pd.to_datetime(df["date_taken"], errors="coerce")
    return df


def split_media(df: pd.DataFrame):
    """Split a catalog frame into (images, videos, others)."""
    return tuple(
        df[df["media_type"] == kind].reset_index(drop=True)
        for kind in ("image", "video", "other")
    )


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


def metrics(fi, fv, fo) -> list[tuple[str, str]]:
    """The headline numbers as (label, formatted value) pairs."""
    total_size = sum(
        d["size (MB)"].sum() for d in (fi, fv, fo) if "size (MB)" in d.columns
    )
    total_duration_sec = (
        fv["duration_ms"].sum(skipna=True) / 1000.0
        if "duration_ms" in fv.columns
        else 0.0
    )
    h = int(total_duration_sec // 3600)
    m = int((total_duration_sec % 3600) // 60)
    s = int(total_duration_sec % 60)
    return [
        ("Total Files", f"{len(fi) + len(fv) + len(fo):,}"),
        ("Images (filtered)", f"{len(fi):,}"),
        ("Videos", f"{len(fv):,}"),
        ("Total Size", f"{total_size:,.0f} MB"),
        ("Video Length", f"{h}h {m}m {s}s"),
    ]


def file_type_donut(fi, fv, fo) -> Optional[go.Figure]:
    counts = {
        k: v
        for k, v in {"Images": len(fi), "Videos": len(fv), "Others": len(fo)}.items()
        if v > 0
    }
    if not counts:
        return None
    fig = px.pie(
        names=list(counts),
        values=list(counts.values()),
        color_discrete_sequence=["#66b3ff", "#99ff99", "#ff9999"],
        hole=0.4,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(showlegend=False, height=300, margin=dict(l=20, r=20, t=20, b=20))
    return fig


def top_donut(df, column: str) -> Optional[go.Figure]:
    """Top-10 donut for a categorical column such as ``camera`` or ``lens``."""
    if column not in df.columns or not df[column].dropna().any():
        return None
    counts = df[column].value_counts().nlargest(10).reset_index()
    counts.columns = [column, "count"]
    fig = px.pie(
        counts,
        names=column,
        values="count",
        color_discrete_sequence=STACKED_COLORS,
        hole=0.4,
    )
    fig.update_traces(textposition="inside", textinfo="percent")
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="h", y=-0.5),
        height=300,
        margin=dict(l=0, r=0, t=0, b=0),
    )
    return fig


def settings_histograms(fi) -> Optional[go.Figure]:
    if fi.empty:
        return None
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=["Aperture", "Shutter Speed", "ISO", "Focal Length"],
    )
    STD_APERTURES = [1.2, 1.8, 2.2, 2.8, 3.5, 4.0, 5.6, 8, 11, 16, 22]
    STD_SHUTTERS = [
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
    ]
    STD_ISOS = [100, 200, 400, 800, 1600, 3200, 6400, 12800]
    STD_FOCALS = [14, 18, 24, 28, 35, 50, 70, 85, 105, 135, 200, 300, 400]

    def _log_hist(series, row, col, color, tickvals, ticktext):
        data = pd.to_numeric(series, errors="coerce").dropna()
        data = data[
            (data > data.quantile(0.01)) & (data <= data.quantile(0.99)) & (data > 0)
        ]
        if data.empty:
            return
        log_data = np.log10(data)
        fig.add_trace(
            go.Histogram(
                x=log_data, nbinsx=30, marker_color=color, opacity=0.7, showlegend=False
            ),
            row=row,
            col=col,
        )
        valid = [
            (v, t)
            for v, t in zip(tickvals, ticktext)
            if log_data.min() * 0.9 <= np.log10(v) <= log_data.max() * 1.1
        ]
        if valid:
            vt, tt = zip(*valid)
            fig.update_xaxes(
                tickvals=[np.log10(v) for v in vt],
                ticktext=list(tt),
                row=row,
                col=col,
                tickangle=45,
            )

    _log_hist(
        fi.get("aperture", pd.Series()),
        1,
        1,
        "coral",
        STD_APERTURES,
        [f"f{v:g}" for v in STD_APERTURES],
    )
    ss = (
        fi["shutter_speed"].apply(parse_shutter)
        if "shutter_speed" in fi.columns
        else pd.Series()
    )
    _log_hist(
        ss,
        1,
        2,
        "orchid",
        STD_SHUTTERS,
        [f"1/{int(round(1/v))}" if v < 1 else f"{v:g}s" for v in STD_SHUTTERS],
    )
    _log_hist(
        fi.get("iso", pd.Series()), 2, 1, "gold", STD_ISOS, [str(v) for v in STD_ISOS]
    )
    _log_hist(
        fi.get("focal_length", pd.Series()),
        2,
        2,
        "teal",
        STD_FOCALS,
        [f"{v}mm" for v in STD_FOCALS],
    )
    fig.update_layout(height=600, margin=dict(t=50, b=50))
    return fig


def timeline_by_camera(fi) -> Optional[go.Figure]:
    if "date_taken" not in fi.columns or "camera" not in fi.columns or fi.empty:
        return None
    tmp = fi.dropna(subset=["date_taken", "camera"]).copy()
    if tmp.empty:
        return None
    tmp["period"] = tmp["date_taken"].dt.to_period("M").astype(str)
    top = tmp["camera"].value_counts().nlargest(10).index
    tmp["camera"] = tmp["camera"].where(tmp["camera"].isin(top), other="Other")
    pivot = tmp.groupby(["period", "camera"]).size().reset_index(name="count")
    fig = px.bar(
        pivot,
        x="period",
        y="count",
        color="camera",
        barmode="stack",
        color_discrete_sequence=STACKED_COLORS,
        title="Monthly Photo Count by Camera",
    )
    fig.update_layout(xaxis_tickangle=45)
    return fig


def video_minutes(fv) -> Optional[go.Figure]:
    if fv.empty or "date_taken" not in fv.columns or "duration_ms" not in fv.columns:
        return None
    tmp = fv.dropna(subset=["date_taken"]).copy()
    if tmp.empty:
        return None
    tmp["month"] = tmp["date_taken"].dt.to_period("M").astype(str)
    aggs = tmp.groupby("month")["duration_ms"].sum().reset_index()
    aggs["minutes"] = aggs["duration_ms"] / 60000.0
    fig = px.bar(
        aggs,
        x="month",
        y="minutes",
        title="Minutes Recorded per Month",
        color_discrete_sequence=["crimson"],
    )
    fig.update_layout(xaxis_tickangle=45, yaxis_title="Duration (Minutes)")
    return fig


def geotagged(fi) -> pd.DataFrame:
    """Rows of ``fi`` with a usable latitude/longitude (empty frame if none)."""
    if fi.empty or "latitude" not in fi.columns or "longitude" not in fi.columns:
        return pd.DataFrame()
    df_loc = fi[["latitude", "longitude", "camera", "date_taken", "name"]].copy()
    df_loc["latitude"] = pd.to_numeric(df_loc["latitude"], errors="coerce")
    df_loc["longitude"] = pd.to_numeric(df_loc["longitude"], errors="coerce")
    return df_loc.dropna(subset=["latitude", "longitude"])


def photo_map(fi) -> Optional[go.Figure]:
    df_loc = geotagged(fi)
    if df_loc.empty:
        return None
    fig = px.density_map(
        df_loc,
        lat="latitude",
        lon="longitude",
        radius=10,
        zoom=2,
        map_style="open-street-map",
        color_continuous_scale="Viridis",
        opacity=0.75,
    )
    fig.update_layout(
        height=600, margin=dict(l=0, r=0, t=30, b=0), coloraxis_showscale=False
    )
    return fig
