import pandas as pd
import plotly.graph_objects as go

from smart_gallery.dashboard import figures


def _images(n=2, **extra):
    data = {
        "media_type": ["image"] * n,
        "name": [f"a{i}.jpg" for i in range(n)],
        "camera": ["Canon R5"] * n,
        "lens": ["RF 24-70"] * n,
        "aperture": [2.8 + i for i in range(n)],
        "iso": [100 * (i + 1) for i in range(n)],
        "shutter_speed": ["1/250s"] * n,
        "focal_length": [24 + i for i in range(n)],
        "latitude": [40.0 + i for i in range(n)],
        "longitude": [-3.0 - i for i in range(n)],
        "date_taken": pd.to_datetime(["2024-01-05", "2024-03-09"] * n)[:n],
        "size (MB)": [1.0] * n,
    }
    data.update(extra)
    return pd.DataFrame(data)


def _videos():
    return pd.DataFrame(
        {
            "media_type": ["video"],
            "name": ["v.mp4"],
            "duration_ms": [90_000],
            "date_taken": pd.to_datetime(["2024-02-01"]),
            "size (MB)": [5.0],
        }
    )


def test_figures_none_on_empty():
    empty = pd.DataFrame()
    assert figures.file_type_donut(empty, empty, empty) is None
    assert figures.top_donut(empty, "camera") is None
    assert figures.settings_histograms(empty) is None
    assert figures.timeline_by_camera(empty) is None
    assert figures.video_minutes(empty) is None
    assert figures.photo_map(empty) is None


def test_metrics_labels_and_values():
    fi, fv, fo = _images(2), _videos(), pd.DataFrame()
    m = dict(figures.metrics(fi, fv, fo))
    assert list(m) == [
        "Total Files",
        "Images (filtered)",
        "Videos",
        "Total Size",
        "Video Length",
    ]
    assert m["Total Files"] == "3"
    assert m["Images (filtered)"] == "2"
    assert m["Total Size"] == "7 MB"
    assert m["Video Length"] == "0h 1m 30s"


def test_parse_shutter():
    assert figures.parse_shutter("1/250s") == 0.004
    assert figures.parse_shutter("2") == 2.0
    assert figures.parse_shutter("x") is None


def test_timeline_groups_top_cameras_into_other():
    n = 12
    df = pd.DataFrame(
        {
            "camera": [f"cam{i}" for i in range(n)],
            "date_taken": pd.to_datetime(["2024-01-01"] * n),
        }
    )
    fig = figures.timeline_by_camera(df)
    assert "Other" in {t.name for t in fig.data}


def test_split_media():
    df = pd.DataFrame({"media_type": ["image", "image", "video", "other"]})
    fi, fv, fo = figures.split_media(df)
    assert (len(fi), len(fv), len(fo)) == (2, 1, 1)


def test_every_figure_builds_from_realistic_data():
    fi, fv = _images(2), _videos()
    fo = pd.DataFrame({"media_type": ["other"], "size (MB)": [0.1]})
    figs = [
        figures.file_type_donut(fi, fv, fo),
        figures.top_donut(fi, "camera"),
        figures.top_donut(fi, "lens"),
        figures.settings_histograms(fi),
        figures.timeline_by_camera(fi),
        figures.video_minutes(fv),
        figures.photo_map(fi),
    ]
    assert all(isinstance(f, go.Figure) for f in figs)
