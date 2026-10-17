"""Dashboard page builder. No Qt: figures in, a static HTML page out.

The page loads plotly.js from a file next to it because ``QWebEngineView.setHtml``
is capped at 2 MB and plotly.js alone is bigger.
"""

from __future__ import annotations

import html
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

from smart_gallery.dashboard import figures

PLOTLY_JS = "plotly.min.js"
INDEX_HTML = "index.html"

_STYLE = """
body { font-family: Segoe UI, sans-serif; margin: 16px; color: #222; background: #fff; }
@media (prefers-color-scheme: dark) { body { color: #ddd; background: #1e1e1e; } }
.metrics { display: flex; flex-wrap: wrap; gap: 24px; margin-bottom: 8px; }
.metric .label { font-size: 12px; opacity: .7; }
.metric .value { font-size: 24px; font-weight: 600; }
h2 { margin-top: 28px; }
"""


def build_sections(df: pd.DataFrame) -> list[tuple[str, go.Figure]]:
    """The dashboard's titled figures, in display order; empty ones are dropped."""
    fi, fv, fo = figures.split_media(df)
    sections = [
        ("File Types, Cameras, Lenses", _donut_row(fi, fv, fo)),
        ("Camera Settings Distribution", figures.settings_histograms(fi)),
        ("Photography Timeline", figures.timeline_by_camera(fi)),
        ("Video Recording Summary", figures.video_minutes(fv)),
        ("Photo Map", figures.photo_map(fi)),
    ]
    return [(title, fig) for title, fig in sections if fig is not None]


def _donut_row(fi, fv, fo) -> go.Figure | None:
    """File types, cameras and lenses side by side as one figure."""
    parts = [
        ("File Types", figures.file_type_donut(fi, fv, fo)),
        ("Cameras", figures.top_donut(fi, "camera")),
        ("Lenses", figures.top_donut(fi, "lens")),
    ]
    parts = [(title, fig) for title, fig in parts if fig is not None]
    if not parts:
        return None
    row = make_subplots(
        rows=1,
        cols=len(parts),
        specs=[[{"type": "domain"}] * len(parts)],
        subplot_titles=[title for title, _ in parts],
    )
    for col, (_, fig) in enumerate(parts, start=1):
        for trace in fig.data:
            row.add_trace(trace, row=1, col=col)
    row.update_traces(hole=0.4)
    row.update_layout(height=340, margin=dict(l=10, r=10, t=50, b=10), showlegend=False)
    return row


def write_dashboard(out_dir: Path, metrics, sections) -> Path:
    """Write the page (and plotly.js, once) into ``out_dir``; return index.html."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / PLOTLY_JS
    if not js.exists():
        js.write_text(get_plotlyjs(), encoding="utf-8")

    cells = "".join(
        f'<div class="metric"><div class="label">{html.escape(str(label))}</div>'
        f'<div class="value">{html.escape(str(value))}</div></div>'
        for label, value in metrics
    )
    body = "".join(
        f"<h2>{html.escape(title)}</h2>"
        + fig.to_html(full_html=False, include_plotlyjs=False)
        for title, fig in sections
    )
    page = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_STYLE}</style>"
        f'<script src="{PLOTLY_JS}"></script></head>'
        f'<body><div class="metrics">{cells}</div>{body}</body></html>'
    )
    index = out_dir / INDEX_HTML
    tmp = out_dir / (INDEX_HTML + ".tmp")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, index)
    return index
