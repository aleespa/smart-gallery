from PySide6.QtCore import QPoint, QSize, Qt

from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.titlebar import (
    GLYPHS,
    RESIZE_MARGIN,
    TitleBar,
    edges_at,
)

SIZE = QSize(400, 300)


def test_edges_at_corners_sides_and_inside():
    E = Qt.Edge
    assert edges_at(QPoint(200, 150), SIZE) == E(0)
    assert edges_at(QPoint(0, 150), SIZE) == E.LeftEdge
    assert edges_at(QPoint(399, 150), SIZE) == E.RightEdge
    assert edges_at(QPoint(200, 0), SIZE) == E.TopEdge
    assert edges_at(QPoint(200, 299), SIZE) == E.BottomEdge
    assert edges_at(QPoint(1, 1), SIZE) == E.LeftEdge | E.TopEdge
    assert edges_at(QPoint(398, 298), SIZE) == E.RightEdge | E.BottomEdge
    assert edges_at(QPoint(RESIZE_MARGIN, 150), SIZE) == E(0)


def _window(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    return win


def test_window_has_no_system_frame_and_its_own_buttons(qtbot):
    win = _window(qtbot)
    assert win.windowFlags() & Qt.WindowType.FramelessWindowHint
    buttons = win.window_buttons
    assert buttons.minimize_btn.text() == GLYPHS["minimize"]
    assert buttons.close_btn.accessibleName() == "Close"
    assert isinstance(win.brand.parent(), TitleBar)


def test_maximize_button_toggles_and_drops_the_resize_border(qtbot):
    win = _window(qtbot)
    win.show()
    qtbot.waitExposed(win)
    assert win.frame.layout().contentsMargins().left() == RESIZE_MARGIN
    win.window_buttons.maximize_btn.click()
    qtbot.waitUntil(win.isMaximized)
    assert win.window_buttons.maximize_btn.text() == GLYPHS["restore"]
    assert win.frame.layout().contentsMargins().left() == 0
    win.window_buttons.maximize_btn.click()
    qtbot.waitUntil(lambda: not win.isMaximized())
    assert win.window_buttons.maximize_btn.text() == GLYPHS["maximize"]
    assert win.frame.layout().contentsMargins().left() == RESIZE_MARGIN


def test_minimize_and_close_buttons_act_on_the_window(qtbot):
    win = _window(qtbot)
    win.show()
    qtbot.waitExposed(win)
    win.window_buttons.minimize_btn.click()
    qtbot.waitUntil(win.isMinimized)
    win.showNormal()
    qtbot.waitUntil(lambda: not win.isMinimized())
    win.window_buttons.close_btn.click()
    qtbot.waitUntil(lambda: not win.isVisible())


def test_resize_cursor_stays_on_the_border(qtbot):
    """Crossing the resize border into the window used to leave every widget
    inside showing the resize arrow, since they inherit the frame's cursor."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    from smart_gallery.gui.titlebar import ResizeFrame

    content = QWidget()
    label = QLabel("inside")
    QVBoxLayout(content).addWidget(label)
    frame = ResizeFrame(content)
    qtbot.addWidget(frame)
    frame.resize(400, 300)
    frame.show()
    qtbot.waitExposed(frame)

    on_border = QPointF(1, 150)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        on_border,
        frame.mapToGlobal(on_border.toPoint()).toPointF(),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(frame, move)
    assert frame.cursor().shape() == Qt.CursorShape.SizeHorCursor
    # The pointer then moves on into the window, where nothing resizes.
    assert label.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert content.cursor().shape() == Qt.CursorShape.ArrowCursor
