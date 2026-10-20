import pytest
import shiboken6

from smart_gallery import colors
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery

pytest.importorskip("PySide6.QtWebEngineWidgets")

from PySide6.QtCharts import QChartView  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from smart_gallery.gui.dashboard.view import (  # noqa: E402
    FILES_PANEL,
    MAP_PANEL,
    PANELS,
    SETTINGS_PANEL,
    TIMELINE_PANEL,
    VIDEO_PANEL,
    DashboardView,
)

METRIC_LABELS = [
    "Total Files",
    "Images (filtered)",
    "Videos",
    "Total Size",
    "Video Length",
]


def _view(qtbot, db_path):
    ctx = AppContext()
    ctx.set_gallery(Gallery("uuid", str(db_path), "test"))
    view = DashboardView(ctx)
    qtbot.addWidget(view)
    return view, ctx


def _loaded(qtbot, view):
    qtbot.waitUntil(lambda: view.data is not None)
    return view.data


def _shown(view, title) -> bool:
    return view.panels[title].isVisibleTo(view)


def _charts(view, title) -> list[QChartView]:
    body = view.panels[title].body
    if isinstance(body, QChartView):
        return [body]
    return body.findChildren(QChartView)


def _video(make_item, relpath, date="2026-07-01"):
    return make_item(
        relpath,
        media_type="video",
        ext=".mp4",
        duration_ms=60000,
        date_taken=date,
        camera=None,
        lens=None,
        aperture=None,
        iso=None,
        shutter_speed_sec=None,
        shutter_speed_text=None,
    )


def test_dashboard_native_panels(qtbot, repo, make_item):
    repo.upsert_many(
        [
            make_item("a/1.jpg", focal_length=50, latitude=40.4, longitude=-3.7),
            make_item("a/2.jpg", camera="X1", lens="L2", focal_length=35),
            _video(make_item, "b/v.mp4"),
        ]
    )
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)

    assert view.stack.currentIndex() == 0
    assert [tile.caption.text() for tile in view.tiles] == METRIC_LABELS
    assert view.tiles[0].value.text() == "3"
    assert view.tables["Images"].model().rowCount() == 2
    assert view.tables["Videos"].model().rowCount() == 1

    assert all(_shown(view, title) for title in PANELS)
    assert len(_charts(view, FILES_PANEL)) == 3
    assert len(_charts(view, SETTINGS_PANEL)) == 4
    assert len(_charts(view, TIMELINE_PANEL)) == 1
    assert len(_charts(view, VIDEO_PANEL)) == 1
    webs = view.panels[MAP_PANEL].findChildren(QWebEngineView)
    assert webs == [view.map_view]
    assert view.map_caption.text() == "Displaying 1 geotagged photos"
    page = view.data.map_page.as_posix()
    qtbot.waitUntil(lambda: view.map_view.url().toLocalFile() == page)


def test_dashboard_videos_only_hides_image_panels(qtbot, repo, make_item):
    repo.upsert_many(
        [_video(make_item, "v/1.mp4"), _video(make_item, "v/2.mp4", "2026-09-01")]
    )
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)

    assert view.stack.currentIndex() == 0
    assert _shown(view, FILES_PANEL)
    assert len(_charts(view, FILES_PANEL)) == 1  # the donut; no cameras or lenses
    for title in (SETTINGS_PANEL, TIMELINE_PANEL, MAP_PANEL):
        assert not _shown(view, title)
    assert _shown(view, VIDEO_PANEL)
    assert len(_charts(view, VIDEO_PANEL)) == 1


def test_dashboard_hides_empty_settings_charts(qtbot, repo, make_item):
    # No focal length and no shutter: only the aperture and ISO charts remain.
    repo.upsert_many(
        [make_item("a/1.jpg", shutter_speed_sec=None, shutter_speed_text=None)]
    )
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)
    assert _shown(view, SETTINGS_PANEL)
    assert len(_charts(view, SETTINGS_PANEL)) == 2
    titles = [
        label.text() for label in view.panels[SETTINGS_PANEL].body.findChildren(QLabel)
    ]
    assert titles == ["Aperture", "ISO"]


def test_dashboard_empty_catalog(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)
    assert view.stack.currentIndex() == 0
    assert [tile.caption.text() for tile in view.tiles] == METRIC_LABELS
    assert view.tiles[0].value.text() == "0"
    assert not any(_shown(view, title) for title in PANELS)


def test_dashboard_file_type_colors_without_images(qtbot, repo, make_item):
    repo.upsert_many(
        [_video(make_item, "b/v.mp4"), make_item("c/notes.txt", media_type="other")]
    )
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)
    donut = _charts(view, FILES_PANEL)[0]
    (series,) = donut.chart().series()
    # Videos stays green, as in the Streamlit dashboard.
    assert [s.property("sg_name") for s in series.slices()] == ["Videos", "Others"]
    assert [s.color() for s in series.slices()] == [
        QColor(colors.FILE_TYPE_COLORS["Videos"]),
        QColor(colors.FILE_TYPE_COLORS["Others"]),
    ]


def test_dashboard_rebuild_deletes_old_chart_views(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1.jpg"), _video(make_item, "b/v.mp4")])
    view, _ = _view(qtbot, repo.db_path)
    first = _loaded(qtbot, view)
    old = _charts(view, FILES_PANEL) + _charts(view, VIDEO_PANEL)
    assert old

    view.reload()
    qtbot.waitUntil(lambda: view.data is not first)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not any(shiboken6.isValid(v) for v in old)
    assert all(shiboken6.isValid(v) for v in _charts(view, FILES_PANEL))
    view.theme_watcher.reapply()  # skips the deleted views


def test_dashboard_files_row_reflows_with_width(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1.jpg", focal_length=50)])
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)
    files = view.panels[FILES_PANEL].body
    settings = view.panels[SETTINGS_PANEL].body
    view.show()
    qtbot.waitExposed(view)

    view.charts_area.resize(1400, 800)
    assert (files.columns, settings.columns) == (3, 2)
    view.charts_area.resize(1000, 800)
    assert (files.columns, settings.columns) == (1, 2)
    view.charts_area.resize(600, 800)
    assert (files.columns, settings.columns) == (1, 1)


def test_dashboard_disconnected_drive_message(qtbot, tmp_path):
    view, _ = _view(qtbot, tmp_path / "gone" / "gallery.db")
    qtbot.waitUntil(lambda: "not connected" in view.message.text())
    assert view.stack.currentWidget() is view.message


def test_map_page_may_fetch_its_tiles_and_plotly(qtbot, repo):
    from PySide6.QtWebEngineCore import QWebEngineSettings

    view, _ = _view(qtbot, repo.db_path)
    settings = view.map_view.settings()
    attrs = QWebEngineSettings.WebAttribute
    # The map is a local page whose tiles come from the internet, and which
    # loads plotly.js from a file beside it. Turning on the first quietly
    # turns off the second, so both must hold.
    assert settings.testAttribute(attrs.LocalContentCanAccessRemoteUrls)
    assert settings.testAttribute(attrs.LocalContentCanAccessFileUrls)
