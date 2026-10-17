import os

import pandas as pd
import plotly.graph_objects as go

from smart_gallery.gui.dashboard.model import build_sections, write_dashboard


def _fig():
    return go.Figure(go.Bar(x=[1, 2], y=[3, 4]))


def test_write_dashboard_inlines_one_div_per_section(tmp_path):
    sections = [("One", _fig()), ("Two", _fig())]
    index = write_dashboard(tmp_path, [("Total Files", "2")], sections)
    html = index.read_text(encoding="utf-8")
    assert index == tmp_path / "index.html"
    assert html.count("plotly-graph-div") == len(sections)
    assert html.count('<script src="plotly.min.js">') == 1
    assert "Total Files" in html and "One" in html
    assert (tmp_path / "plotly.min.js").stat().st_size > 100_000


def test_plotly_js_written_once(tmp_path):
    write_dashboard(tmp_path, [], [("One", _fig())])
    js = tmp_path / "plotly.min.js"
    os.utime(js, ns=(1_000_000_000, 1_000_000_000))
    write_dashboard(tmp_path, [], [("One", _fig())])
    assert js.stat().st_mtime_ns == 1_000_000_000
    assert not list(tmp_path.glob("*.tmp"))


def test_build_sections_drops_empty_figures():
    df = pd.DataFrame({"media_type": ["other"], "size (MB)": [1.0]})
    titles = [t for t, _ in build_sections(df)]
    assert titles == ["File Types, Cameras, Lenses"]
