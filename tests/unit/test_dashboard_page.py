import os

import pandas as pd
import plotly.graph_objects as go

from smart_gallery.dashboard import figures

from smart_gallery.gui.dashboard.model import Section, build_sections, write_dashboard


def _fig():
    return go.Figure(go.Bar(x=[1, 2], y=[3, 4]))


def test_write_dashboard_inlines_one_div_per_section(tmp_path):
    sections = [Section("One", [_fig(), _fig()], ("A", "B")), Section("Two", [_fig()])]
    index = write_dashboard(tmp_path, [("Total Files", "2")], sections)
    html = index.read_text(encoding="utf-8")
    assert index == tmp_path / "index.html"
    assert html.count("plotly-graph-div") == 3
    assert html.count('<script src="plotly.min.js">') == 1
    assert "Total Files" in html and "One" in html
    assert (tmp_path / "plotly.min.js").stat().st_size > 100_000


def test_plotly_js_written_once(tmp_path):
    write_dashboard(tmp_path, [], [Section("One", [_fig()])])
    js = tmp_path / "plotly.min.js"
    os.utime(js, ns=(1_000_000_000, 1_000_000_000))
    write_dashboard(tmp_path, [], [Section("One", [_fig()])])
    assert js.stat().st_mtime_ns == 1_000_000_000
    assert not list(tmp_path.glob("*.tmp"))


def test_build_sections_drops_empty_figures():
    df = pd.DataFrame({"media_type": ["other"], "size (MB)": [1.0]})
    titles = [sec.title for sec in build_sections(df)]
    assert titles == ["File Types, Cameras, Lenses"]


def test_donut_section_keeps_top_donut_legends():
    df = pd.DataFrame(
        {
            "media_type": ["image"] * 3,
            "camera": ["A", "A", "B"],
            "lens": ["L", "L", "M"],
            "size (MB)": [1.0] * 3,
        }
    )
    first = build_sections(df)[0]
    assert first.labels == ("File Types", "Cameras", "Lenses")
    for fig, col in zip(first.figures[1:], ("camera", "lens")):
        ref = figures.top_donut(df, col)
        assert fig.layout.showlegend is True
        assert fig.layout.legend.to_plotly_json() == ref.layout.legend.to_plotly_json()


def test_photo_map_caption_counts_geotagged(tmp_path):
    df = pd.DataFrame(
        {
            "media_type": ["image"] * 3,
            "latitude": [1.0, 2.0, None],
            "longitude": [1.0, 2.0, None],
            "camera": ["A"] * 3,
            "date_taken": pd.to_datetime(["2026-01-01"] * 3),
            "name": ["a", "b", "c"],
        }
    )
    pm = [s for s in build_sections(df) if s.title == "Photo Map"][0]
    assert pm.caption == "Displaying 2 geotagged photos"
