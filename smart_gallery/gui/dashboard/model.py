"""Stats data for the desktop app. No Qt: everything here runs on the worker.

The charts are drawn natively from these numbers. Only the photo map is still
a Plotly figure, written as a page of its own that loads plotly.js from a file
next to it, because ``QWebEngineView.setHtml`` is capped at 2 MB and plotly.js
alone is bigger.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

from smart_gallery.dashboard import data, figures
from smart_gallery.dashboard.data import Counts, MonthSeries

PLOTLY_JS = "plotly.min.js"
MAP_HTML = "map.html"

_MAP_STYLE = "html, body { height: 100%; } body { margin: 0; }"


@dataclass(frozen=True)
class DashboardData:
    """Everything the Stats page draws, computed off the GUI thread."""

    metrics: list[tuple[str, str]]
    cameras: Counts
    lenses: Counts
    settings: dict  # spec name -> stop buckets, non-empty specs only
    timeline: MonthSeries | None
    video: MonthSeries | None
    geotag_count: int
    map_page: Path | None  # None when no photo has a geotag


def write_map_page(out_dir: Path, fig) -> Path:
    """Write the map-only page (and plotly.js, once) into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / PLOTLY_JS
    if not js.exists():
        js.write_text(get_plotlyjs(), encoding="utf-8")

    # Fill the web view instead of the figure's fixed height; the caption is
    # a native label, so no top margin either.
    fig = go.Figure(fig).update_layout(height=None, margin=dict(t=0))
    div = fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        default_height="100%",
        config={"displayModeBar": False, "responsive": True},
    )
    page = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_MAP_STYLE}</style>"
        f'<script src="{PLOTLY_JS}"></script></head>'
        f"<body>{div}</body></html>"
    )
    path = out_dir / MAP_HTML
    tmp = out_dir / (MAP_HTML + ".tmp")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_dashboard(db_path, filters, out_dir: Path) -> DashboardData:
    """Load the (filtered) catalog and compute every chart's numbers."""
    df = figures.load_catalog_df(db_path, filters)
    fi, fv, fo = figures.split_media(df)
    geotag_count = len(data.geotagged(fi))
    map_page = write_map_page(out_dir, figures.photo_map(fi)) if geotag_count else None
    return DashboardData(
        metrics=figures.metrics(fi, fv, fo),
        cameras=data.top_counts(fi, "camera"),
        lenses=data.top_counts(fi, "lens"),
        settings=data.settings_buckets(fi),
        timeline=data.monthly_by_camera(fi),
        video=data.video_minutes_by_month(fv),
        geotag_count=geotag_count,
        map_page=map_page,
    )
