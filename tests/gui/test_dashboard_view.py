import pytest

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery

pytest.importorskip("PySide6.QtWebEngineWidgets")

from smart_gallery.gui.dashboard.view import DashboardView  # noqa: E402


def _view(qtbot, db_path):
    ctx = AppContext()
    ctx.set_gallery(Gallery("uuid", str(db_path), "test"))
    view = DashboardView(ctx)
    qtbot.addWidget(view)
    return view, ctx


def test_dashboard_loads_for_repo(qtbot, repo, make_item):
    repo.upsert_many(
        [
            make_item("a/1.jpg", camera="X1"),
            make_item("a/2.jpg", camera="X1"),
            make_item("b/v.mp4", media_type="video", ext=".mp4", duration_ms=60000),
        ]
    )
    view, _ = _view(qtbot, repo.db_path)
    qtbot.waitUntil(lambda: view.tables["Images"].model().rowCount() == 2)
    assert view.tables["Videos"].model().rowCount() == 1
    assert view.tables["Others"].model().rowCount() == 0
    assert view.index_path.exists()
    assert "Total Files" in view.index_path.read_text(encoding="utf-8")


def test_dashboard_disconnected_drive_message(qtbot, tmp_path):
    view, _ = _view(qtbot, tmp_path / "gone" / "gallery.db")
    qtbot.waitUntil(lambda: "not connected" in view.message.text())
    assert view.stack.currentWidget() is view.message
