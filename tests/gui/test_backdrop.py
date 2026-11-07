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
    calls = []
    monkeypatch.setattr(backdrop, "supported", lambda *a, **k: True)

    def record(window):
        calls.append(window.isVisible())
        return True

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
