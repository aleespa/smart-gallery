"""The app's own title bar: the top bar moves the window and carries the
minimize, maximize and close buttons, so the window has no system frame.

Moving and resizing go through ``QWindow.startSystemMove``/``startSystemResize``,
so the system still snaps the window to screen edges. A thin margin around
the window's content is the resize border.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

RESIZE_MARGIN = 5  # px of the window edge that resizes it
# Windows' own caption glyphs (Segoe Fluent Icons; MDL2 Assets on Windows 10).
ICON_FONTS = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
GLYPHS = {
    "minimize": "",
    "maximize": "",
    "restore": "",
    "close": "",
}
BUTTON_SIZE = QSize(46, 36)
CLOSE_HOVER = "#C42B1C"  # the system close button's red
BUTTON_STYLE = f"""
QToolButton#windowButton {{ border: none; background: transparent; }}
QToolButton#windowButton:hover {{ background: palette(midlight); }}
QToolButton#windowButton[role="close"]:hover {{
    background: {CLOSE_HOVER};
    color: white;
}}
"""

_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
    Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
}


def edges_at(pos: QPoint, size: QSize, margin: int = RESIZE_MARGIN) -> Qt.Edge:
    """The window edges ``pos`` (in window coordinates) is within ``margin``
    of; an empty value when it is inside."""
    edges = Qt.Edge(0)
    if pos.x() < margin:
        edges |= Qt.Edge.LeftEdge
    elif pos.x() >= size.width() - margin:
        edges |= Qt.Edge.RightEdge
    if pos.y() < margin:
        edges |= Qt.Edge.TopEdge
    elif pos.y() >= size.height() - margin:
        edges |= Qt.Edge.BottomEdge
    return edges


class WindowButtons(QWidget):
    """Minimize, maximize/restore and close for ``window``."""

    def __init__(self, window: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._window = window
        self.setStyleSheet(BUTTON_STYLE)
        icon_font = QFont()
        icon_font.setFamilies(ICON_FONTS)
        icon_font.setPointSizeF(7.5)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        def button(role: str, tip: str, slot) -> QToolButton:
            btn = QToolButton()
            btn.setObjectName("windowButton")
            btn.setProperty("role", role)
            btn.setFont(icon_font)
            btn.setText(GLYPHS[role])
            btn.setToolTip(tip)
            btn.setAccessibleName(tip)
            btn.setFixedSize(BUTTON_SIZE)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(slot)
            row.addWidget(btn)
            return btn

        self.minimize_btn = button("minimize", "Minimize", window.showMinimized)
        self.maximize_btn = button("maximize", "Maximize", self.toggle_maximized)
        self.close_btn = button("close", "Close", window.close)
        window.installEventFilter(self)
        self._sync()

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._window and event.type() == QEvent.Type.WindowStateChange:
            self._sync()
        return False

    def _sync(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize_btn.setText(GLYPHS["restore" if maximized else "maximize"])
        tip = "Restore" if maximized else "Maximize"
        self.maximize_btn.setToolTip(tip)
        self.maximize_btn.setAccessibleName(tip)


class TitleBar(QWidget):
    """A bar that moves its window when dragged by any empty part, and
    maximizes or restores it on a double-click."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window.isMaximized():
                window.showNormal()
            else:
                window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ResizeFrame(QWidget):
    """Holds the window's content inside a thin margin that resizes the
    window. The margin goes away while the window is maximized."""

    def __init__(self, content: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        layout = QHBoxLayout(self)
        layout.setSpacing(0)
        layout.addWidget(content)
        # The content's own arrow: otherwise everything inside inherits the
        # resize cursor set on this frame, and keeps it once the pointer has
        # crossed the border inwards.
        content.setCursor(Qt.CursorShape.ArrowCursor)
        self.set_margin(RESIZE_MARGIN)

    def set_margin(self, margin: int) -> None:
        self._margin = margin
        self.layout().setContentsMargins(margin, margin, margin, margin)

    def _edges(self, pos: QPoint) -> Qt.Edge:
        if not self._margin:
            return Qt.Edge(0)
        return edges_at(pos, self.size(), self._margin)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        cursor = _CURSORS.get(self._edges(event.position().toPoint()))
        if cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        edges = self._edges(event.position().toPoint())
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and edges and handle:
            handle.startSystemResize(edges)
            event.accept()
            return
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:
        self.unsetCursor()
        super().leaveEvent(event)


def round_corners(window: QWidget) -> None:
    """Ask Windows 11 to round the frameless window's corners like any other
    window. Does nothing elsewhere or when the call is not supported."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        corner_preference, round_ = 33, 2  # DWMWA_WINDOW_CORNER_PREFERENCE
        value = ctypes.c_int(round_)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()),
            corner_preference,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except (AttributeError, OSError):
        pass
