"""Dashboard page builder. No Qt: figures in, a static HTML page out.

The page loads plotly.js from a file next to it because ``QWebEngineView.setHtml``
is capped at 2 MB and plotly.js alone is bigger.
"""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import NamedTuple

import pandas as pd
from plotly.offline import get_plotlyjs

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
.row { display: flex; flex-wrap: wrap; gap: 16px; }
.cell { flex: 1 1 300px; min-width: 0; }
.caption { opacity: .7; margin: 0 0 8px; }
"""


class Section(NamedTuple):
    """A titled block of figures drawn side by side, unmodified."""

    title: str
    figures: list
    labels: tuple = ()  # optional subheader per figure
    caption: str = ""


def build_sections(df: pd.DataFrame, split=None) -> list[Section]:
    """The dashboard's sections in display order; empty ones are dropped.

    ``split`` is ``split_media(df)`` when the caller already has it.
    """
    fi, fv, fo = split or figures.split_media(df)
    donuts = [
        ("File Types", figures.file_type_donut(fi, fv, fo)),
        ("Cameras", figures.top_donut(fi, "camera")),
        ("Lenses", figures.top_donut(fi, "lens")),
    ]
    donuts = [(label, fig) for label, fig in donuts if fig is not None]
    sections = [
        Section(
            "File Types, Cameras, Lenses",
            [fig for _, fig in donuts],
            tuple(label for label, _ in donuts),
        ),
        Section("Camera Settings Distribution", [figures.settings_histograms(fi)]),
        Section("Photography Timeline", [figures.timeline_by_camera(fi)]),
        Section("Video Recording Summary", [figures.video_minutes(fv)]),
        Section(
            "Photo Map",
            [figures.photo_map(fi)],
            caption=f"Displaying {len(figures.geotagged(fi)):,} geotagged photos",
        ),
    ]
    return [sec for sec in sections if sec.figures and None not in sec.figures]


def _section_html(sec: Section) -> str:
    parts = [f"<h2>{html.escape(sec.title)}</h2>"]
    if sec.caption:
        parts.append(f'<p class="caption">{html.escape(sec.caption)}</p>')
    cells = []
    for i, fig in enumerate(sec.figures):
        head = f"<h3>{html.escape(sec.labels[i])}</h3>" if i < len(sec.labels) else ""
        cells.append(
            f'<div class="cell">{head}'
            + fig.to_html(full_html=False, include_plotlyjs=False)
            + "</div>"
        )
    parts.append('<div class="row">' + "".join(cells) + "</div>")
    return "".join(parts)


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
    body = "".join(_section_html(sec) for sec in sections)
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
