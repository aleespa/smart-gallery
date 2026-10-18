"""Plotly figures and summary numbers shared by the Streamlit dashboard and the
desktop app. No Streamlit import here: every builder takes DataFrames and
returns a ``go.Figure`` (or ``None`` when there is nothing to draw)."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from smart_gallery.dashboard import data
from smart_gallery.dashboard.data import geotagged, parse_shutter  # noqa: F401
from smart_gallery.db import GalleryRepository
from smart_gallery.organize.filters import FilterOptions

STACKED_COLORS = data.PALETTE


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
    items = data.file_type_counts(fi, fv, fo)
    if not items:
        return None
    fig = go.Figure(
        go.Pie(
            labels=[k for k, _ in items],
            values=[v for _, v in items],
            marker=dict(colors=data.file_type_colors(items)),
            hole=0.4,
            sort=False,
        )
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(showlegend=False, height=300, margin=dict(l=20, r=20, t=20, b=20))
    return fig


def top_donut(df, column: str) -> Optional[go.Figure]:
    """Top-10 donut for a categorical column such as ``camera`` or ``lens``."""
    items = data.top_counts(df, column).items
    if not items:
        return None
    fig = go.Figure(
        go.Pie(
            labels=[k for k, _ in items],
            values=[v for _, v in items],
            marker=dict(
                colors=[data.color_for(k, i) for i, (k, _) in enumerate(items)]
            ),
            hole=0.4,
            sort=False,
        )
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
    """Camera settings as one bar per photographic stop (2x2 grid)."""
    buckets = data.settings_buckets(fi)
    if not buckets:
        return None
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=[spec.name for spec in data.SETTINGS],
    )
    for index, spec in enumerate(data.SETTINGS):
        items = buckets.get(spec.name)
        if not items:
            continue
        fig.add_trace(
            go.Bar(
                x=[k for k, _ in items],
                y=[v for _, v in items],
                marker_color=data.SETTINGS_COLORS[spec.name],
                showlegend=False,
            ),
            row=index // 2 + 1,
            col=index % 2 + 1,
        )
        fig.update_xaxes(
            type="category", tickangle=45, row=index // 2 + 1, col=index % 2 + 1
        )
    fig.update_layout(height=600, margin=dict(t=50, b=50))
    return fig


def timeline_by_camera(fi) -> Optional[go.Figure]:
    ms = data.monthly_by_camera(fi)
    if ms is None:
        return None
    fig = go.Figure(
        [
            go.Bar(
                name=name,
                x=list(ms.months),
                y=list(values),
                marker_color=data.color_for(name, rank),
            )
            for rank, (name, values) in enumerate(ms.series.items())
        ]
    )
    fig.update_layout(
        barmode="stack",
        title="Monthly Photo Count by Camera",
        xaxis_tickangle=45,
        legend_title_text="camera",
    )
    return fig


def video_minutes(fv) -> Optional[go.Figure]:
    ms = data.video_minutes_by_month(fv)
    if ms is None:
        return None
    fig = go.Figure(
        go.Bar(
            x=list(ms.months),
            y=list(ms.series["Minutes"]),
            marker_color=data.VIDEO_COLOR,
        )
    )
    fig.update_layout(
        title="Minutes Recorded per Month",
        xaxis_tickangle=45,
        yaxis_title="Duration (Minutes)",
    )
    return fig


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
