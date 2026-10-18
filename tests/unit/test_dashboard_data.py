import math
import subprocess
import sys

import pandas as pd

from smart_gallery.dashboard import data
from smart_gallery.dashboard.data import (
    APERTURE,
    FOCAL,
    ISO,
    OTHER,
    OTHER_COLOR,
    PALETTE,
    SHUTTER,
    Counts,
)


def test_file_type_counts_drops_zero():
    one = pd.DataFrame({"a": [1]})
    empty = pd.DataFrame({"a": []})
    assert data.file_type_counts(one, empty, one) == (("Images", 1), ("Others", 1))
    assert data.file_type_counts(empty, empty, empty) == ()


def test_top_counts_ranks_and_total():
    df = pd.DataFrame({"camera": ["A"] * 3 + ["B"] * 3 + ["C"] + [None, None]})
    c = data.top_counts(df, "camera")
    assert c.items == (("A", 3), ("B", 3), ("C", 1))
    assert c.total == 9
    many = pd.DataFrame({"camera": [f"cam{i:02d}" for i in range(11)]})
    assert len(data.top_counts(many, "camera").items) == 10


def test_stop_buckets_nearest_on_log_scale():
    got = data.stop_buckets(pd.Series([1.4, 1.5, 2.0, 2.8, 2.9]), APERTURE)
    assert got == (("f/1.4", 2), ("f/1.8", 0), ("f/2", 1), ("f/2.8", 2))


def test_stop_buckets_tie_goes_lower():
    v = 10 ** ((math.log10(2.8) + math.log10(3.5)) / 2)
    assert data.stop_buckets(pd.Series([v]), APERTURE) == (("f/2.8", 1),)


def test_stop_buckets_clamps_and_drops():
    got = data.stop_buckets(pd.Series([0.7, 40, 0, -1, float("nan"), "abc"]), APERTURE)
    assert got[0] == ("f/1.0", 1)
    assert got[-1] == ("f/22", 1)
    assert sum(n for _, n in got) == 2
    assert data.stop_buckets(pd.Series([0, -1, float("nan"), "abc"]), APERTURE) == ()


def test_shutter_labels_and_bulb():
    fi = pd.DataFrame({"shutter_speed": ["1/250s", "2", "Bulb"]})
    got = data.settings_buckets(fi)["Shutter Speed"]
    assert got[0] == ("1/250", 1)
    assert got[-1] == ("2s", 1)
    assert sum(n for _, n in got) == 2


def test_iso_and_focal_labels():
    assert data.stop_buckets(pd.Series([400]), ISO) == (("ISO 400", 1),)
    assert data.stop_buckets(pd.Series([37]), FOCAL) == (("35mm", 1),)
    assert data.stop_buckets(pd.Series([1 / 250]), SHUTTER) == (("1/250", 1),)


def test_single_stop_gives_one_bucket():
    fi = pd.DataFrame({"aperture": [2.8, 2.8, 2.8]})
    assert data.settings_buckets(fi) == {"Aperture": (("f/2.8", 3),)}


def _photos(rows):
    return pd.DataFrame(rows, columns=["date_taken", "camera"]).assign(
        date_taken=lambda d: pd.to_datetime(d["date_taken"])
    )


def test_monthly_by_camera_zero_fill_and_other():
    rows = [("2025-11-05", "A"), ("2026-02-10", "A"), ("2026-02-11", "B")]
    rows += [(None, "A"), ("2026-01-01", None)]
    ms = data.monthly_by_camera(_photos(rows))
    assert ms.months == ("2025-11", "2025-12", "2026-01", "2026-02")
    assert ms.series["A"] == (1.0, 0.0, 0.0, 1.0)
    assert ms.series["B"] == (0.0, 0.0, 0.0, 1.0)

    many = [("2026-01-01", f"cam{i:02d}") for i in range(11)]
    many += [("2026-01-02", "cam00")]  # cam01..cam10 tie at 1; cam10 ranks last
    ms = data.monthly_by_camera(_photos(many))
    assert list(ms.series)[-1] == OTHER
    assert len(ms.series) == 11
    assert "cam10" not in ms.series
    assert ms.series[OTHER] == (1.0,)


def test_video_minutes_by_month():
    fv = pd.DataFrame(
        {
            "date_taken": pd.to_datetime(["2026-01-10", "2026-03-02"]),
            "duration_ms": [90_000, 30_000],
        }
    )
    ms = data.video_minutes_by_month(fv)
    assert ms.months == ("2026-01", "2026-02", "2026-03")
    assert ms.series == {"Minutes": (1.5, 0.0, 0.5)}


def test_month_axis_labels():
    months = [str(p) for p in pd.period_range("2024-01", periods=24, freq="M")]
    assert len(data.month_axis_labels(months)) == 24
    assert data.month_label("2026-03") == "Mar 2026"
    long = [str(p) for p in pd.period_range("2016-03", periods=130, freq="M")]
    labels = data.month_axis_labels(long)
    assert labels[0] == (0, "Mar 2016")
    assert labels[1] == (10, "2017")
    assert len(labels) == 11
    assert data.month_axis_labels(["2026-03"]) == ((0, "Mar 2026"),)


def test_color_for():
    assert data.color_for(OTHER, 3) == OTHER_COLOR
    assert data.color_for("x", 0) == PALETTE[0]
    assert data.color_for("x", len(PALETTE) + 1) == PALETTE[1]


def test_no_qt_or_plotly_import():
    code = (
        "import sys\n"
        "sys.modules['PySide6'] = None\n"
        "sys.modules['plotly'] = None\n"
        "import smart_gallery.dashboard.data\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_empty_inputs():
    empty = pd.DataFrame()
    assert data.file_type_counts(empty, empty, empty) == ()
    assert data.top_counts(empty, "camera") == Counts((), 0)
    assert data.stop_buckets(pd.Series([], dtype=float), APERTURE) == ()
    assert data.settings_buckets(empty) == {}
    assert data.monthly_by_camera(empty) is None
    assert data.video_minutes_by_month(empty) is None
    assert data.month_axis_labels([]) == ()
    assert data.geotagged(empty).empty
