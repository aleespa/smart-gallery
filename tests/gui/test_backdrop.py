"""The main window's Acrylic backdrop (Windows 11), toned down by a tint."""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from smart_gallery import colors
from smart_gallery.gui import backdrop


def test_supported_only_on_windows_11(monkeypatch):
    monkeypatch.delenv("SG_NO_BACKDROP", raising=False)
    assert backdrop.supported("windows", 22621)
    assert backdrop.supported("windows", 26300)
    assert not backdrop.supported("windows", 19045)  # Windows 10
    assert not backdrop.supported("offscreen", 26300)  # tests, no compositor


def test_supported_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SG_NO_BACKDROP", "1")
    assert not backdrop.supported("windows", 26300)


def test_tint_keeps_the_colour_and_sets_the_strength():
    tint = backdrop.tint(QColor("#202020"))
    assert (tint.red(), tint.green(), tint.blue()) == (0x20, 0x20, 0x20)
    assert tint.alpha() == colors.BACKDROP_TINT_ALPHA


def test_tint_strength_tones_acrylic_down():
    # Mostly opaque: the blur shows through, busy wallpapers do not.
    assert 150 <= colors.BACKDROP_TINT_ALPHA < 255


def test_frame_inset_only_when_maximized():
    assert backdrop.frame_inset(maximized=True, border=8) == 8
    assert backdrop.frame_inset(maximized=False, border=8) == 0


@pytest.fixture
def fake_backdrop(monkeypatch):
    """Pretend the system supports Acrylic; record each apply."""

    class Calls(list):
        pass

    calls = Calls()
    monkeypatch.setattr(backdrop, "supported", lambda *a, **k: True)

    def record(window, keep_caption=False):
        calls.append(window.isVisible())
        captions.append(keep_caption)
        return True

    captions = []
    calls.captions = captions  # type: ignore[attr-defined]

    monkeypatch.setattr(backdrop, "apply", record)
    return calls


def test_main_window_without_backdrop_is_unchanged(qtbot):
    from smart_gallery.gui.app import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    assert win.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not win.backdrop


def test_main_window_with_backdrop(qtbot, fake_backdrop):
    from smart_gallery.gui.app import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    assert win.backdrop
    # A real frame (DWM draws backdrops only on framed windows); its system
    # title bar is hidden in nativeEvent.
    assert not win.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert fake_backdrop == [False]  # applied before the window is shown


def test_backdrop_reapplied_when_the_native_window_is_rebuilt(qtbot, fake_backdrop):
    from smart_gallery.gui.app import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    qtbot.wait(20)  # creating the native window already queued one re-apply
    before = len(fake_backdrop)
    QApplication.sendEvent(win, QEvent(QEvent.Type.WinIdChange))
    qtbot.waitUntil(lambda: len(fake_backdrop) == before + 1)


def test_tint_painted_where_nothing_else_paints(qtbot, fake_backdrop):
    from smart_gallery.gui.app import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.resize(900, 600)
    win.show()
    qtbot.waitExposed(win)
    corner = win.grab().toImage().pixelColor(1, 1)  # inside the resize margin
    assert corner.alpha() == colors.BACKDROP_TINT_ALPHA


def test_caption_style_drops_the_system_menu_only_for_own_title_bars():
    style = 0x00CF0000  # WS_OVERLAPPEDWINDOW, which includes WS_SYSMENU
    assert not backdrop.caption_style(style, own_title_bar=True) & backdrop.WS_SYSMENU
    assert backdrop.caption_style(style, own_title_bar=False) == style


def test_main_window_hides_windows_caption_buttons(qtbot, fake_backdrop):
    from smart_gallery.gui.app import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    assert fake_backdrop.captions == [False]  # its own buttons: no system ones


def _viewer(qtbot, repo, make_item, tmp_path, monkeypatch):
    from PIL import Image

    from smart_gallery.gui.context import AppContext
    from smart_gallery.gui.galleries.store import Gallery
    from smart_gallery.gui.library.model import LibraryRow
    from smart_gallery.gui.viewer.window import MediaViewer

    monkeypatch.setenv("SG_APP_DATA", str(tmp_path / "appdata"))
    path = repo.drive_root / "a.jpg"
    Image.new("RGB", (900, 600), (200, 30, 30)).save(path, "JPEG")
    repo.upsert_many([make_item("a.jpg", width=900, height=600)])
    r = repo.conn.execute(
        "SELECT id, relpath, media_type, ext, mtime_ns FROM media_items"
    ).fetchone()
    ctx = AppContext()
    ctx.set_gallery(Gallery("u", str(repo.db_path), "t"))
    viewer = MediaViewer(ctx, repo.drive_root, [LibraryRow(*r)], 0)
    qtbot.addWidget(viewer)
    return viewer


def test_viewer_without_backdrop_is_unchanged(
    qtbot, repo, make_item, tmp_path, monkeypatch
):
    viewer = _viewer(qtbot, repo, make_item, tmp_path, monkeypatch)
    assert not viewer.backdrop
    assert not viewer.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_viewer_with_backdrop_keeps_its_windows_title_bar(
    qtbot, repo, make_item, tmp_path, monkeypatch, fake_backdrop
):
    viewer = _viewer(qtbot, repo, make_item, tmp_path, monkeypatch)
    assert viewer.backdrop
    assert viewer.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not viewer.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert fake_backdrop == [False]  # set before the window is shown
    assert fake_backdrop.captions == [True]  # the system close button stays


def test_viewer_backdrop_behind_the_chrome_not_the_photo(
    qtbot, repo, make_item, tmp_path, monkeypatch, fake_backdrop
):
    from PySide6.QtCore import QPoint

    viewer = _viewer(qtbot, repo, make_item, tmp_path, monkeypatch)
    viewer.resize(1000, 700)
    viewer.show()
    qtbot.waitExposed(viewer)
    qtbot.waitUntil(viewer.has_preview)
    image = viewer.grab().toImage()
    bar = viewer.top_bar.mapTo(viewer, QPoint(viewer.top_bar.width() // 2, 4))
    assert image.pixelColor(bar).alpha() == colors.BACKDROP_TINT_ALPHA
    panel = viewer.info_panel.mapTo(viewer, QPoint(5, viewer.info_panel.height() - 5))
    assert image.pixelColor(panel).alpha() == colors.BACKDROP_TINT_ALPHA
    stage = viewer.stage.mapTo(viewer, QPoint(3, 3))
    assert image.pixelColor(stage) == QColor(colors.VIEWER_STAGE)  # solid
