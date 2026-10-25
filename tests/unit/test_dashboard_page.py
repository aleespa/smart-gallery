import os

from smart_gallery.dashboard import data, figures
from smart_gallery.gui.dashboard.model import (
    DashboardData,
    load_dashboard,
    write_map_page,
)


def _catalog(repo, make_item, geotags=True):
    coords = dict(latitude=40.4, longitude=-3.7) if geotags else {}
    repo.upsert_many(
        [
            make_item("a/1.jpg", focal_length=50, **coords),
            make_item("a/2.jpg", camera="Sony A7", lens="FE 35mm", focal_length=35),
            make_item(
                "b/v.mp4",
                media_type="video",
                ext=".mp4",
                duration_ms=120000,
                date_taken="2026-07-01",
            ),
            make_item("c/n.txt", media_type="other", ext=".txt"),
        ]
    )


def test_load_dashboard_computes_everything(repo, make_item, tmp_path):
    _catalog(repo, make_item)
    out = tmp_path / "dash"
    result = load_dashboard(repo.db_path, None, out)

    assert isinstance(result, DashboardData)
    fi, fv, fo = figures.split_media(figures.load_catalog_df(repo.db_path))
    assert (len(fi), len(fv), len(fo)) == (2, 1, 1)
    assert result.metrics == figures.metrics(fi, fv, fo)
    assert result.file_types == (("Images", 2), ("Videos", 1), ("Others", 1))
    assert result.cameras == data.top_counts(fi, "camera")
    assert result.cameras.total == 2
    assert result.lenses == data.top_counts(fi, "lens")
    assert set(result.settings) == {
        "Aperture",
        "Shutter Speed",
        "ISO",
        "Focal Length",
    }
    assert result.timeline == data.monthly_by_camera(fi)
    assert result.video.months == ("2026-07",)
    assert result.video.series["Minutes"] == (2.0,)
    assert result.geotag_count == 1

    assert result.map_page == out / "map.html"
    page = result.map_page.read_text(encoding="utf-8")
    assert page.count("plotly-graph-div") == 1
    assert page.count('<script src="plotly.min.js">') == 1
    assert "body { margin: 0" in page
    assert (out / "plotly.min.js").stat().st_size > 100_000
    assert not list(out.glob("*.tmp"))


def test_load_dashboard_no_geotags_writes_no_map(repo, make_item, tmp_path):
    _catalog(repo, make_item, geotags=False)
    out = tmp_path / "dash"
    result = load_dashboard(repo.db_path, None, out)
    assert result.geotag_count == 0
    assert result.map_page is None
    assert not (out / "map.html").exists()


def test_load_dashboard_empty_catalog(repo, tmp_path):
    result = load_dashboard(repo.db_path, None, tmp_path / "dash")
    assert result.file_types == ()
    assert result.cameras.items == () and result.lenses.items == ()
    assert result.settings == {}
    assert result.timeline is None and result.video is None
    assert result.map_page is None


def test_plotly_js_written_once(repo, make_item, tmp_path):
    _catalog(repo, make_item)
    load_dashboard(repo.db_path, None, tmp_path)
    images = figures.split_media(figures.load_catalog_df(repo.db_path))[0]
    fig = figures.photo_map(images)
    js = tmp_path / "plotly.min.js"
    os.utime(js, ns=(1_000_000_000, 1_000_000_000))
    assert write_map_page(tmp_path, fig) == tmp_path / "map.html"
    assert js.stat().st_mtime_ns == 1_000_000_000
    assert not list(tmp_path.glob("*.tmp"))
