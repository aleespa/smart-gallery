"""The main window's Acrylic backdrop on Windows 11, toned down by a tint.

Windows draws system backdrops only behind windows with a real frame, so a
window using one keeps its frame and hides the system title bar itself
(``native_event`` answers WM_NCCALCSIZE with "no frame"); the app's own title
bar stays. Dropping WS_SYSMENU stops Windows drawing its own caption buttons
over the app's. The settings must be applied before the window is first
shown, and again whenever Qt rebuilds the native window, or the window
flashes fully transparent.

Windows has no strength setting for Acrylic: ``tint`` is painted over it
(``colors.BACKDROP_TINT_ALPHA``) so text stays easy to read.

Elsewhere (Windows 10, the offscreen test platform, ``SG_NO_BACKDROP=1``)
``supported`` is False and the window stays frameless as before.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter

from smart_gallery import colors

MIN_BUILD = 22621  # Windows 11 22H2: DWMWA_SYSTEMBACKDROP_TYPE
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic
WM_NCCALCSIZE = 0x0083
GWL_STYLE = -16
WS_SYSMENU = 0x00080000
SM_CXSIZEFRAME = 32
SM_CXPADDEDBORDER = 92
# NOMOVE | NOSIZE | NOZORDER | FRAMECHANGED: recalculate the frame only.
SWP_FRAMECHANGED_ONLY = 0x0002 | 0x0001 | 0x0004 | 0x0020


def supported(platform_name: str | None = None, build: int | None = None) -> bool:
    """True on Windows 11 22H2 or later, on the real windowing platform."""
    if os.environ.get("SG_NO_BACKDROP"):
        return False
    if platform_name is None:
        platform_name = QGuiApplication.platformName()
    if platform_name != "windows":
        return False
    if build is None:
        build = sys.getwindowsversion().build if sys.platform == "win32" else 0
    return build >= MIN_BUILD


def tint(window_color: QColor, alpha: int = colors.BACKDROP_TINT_ALPHA) -> QColor:
    """The layer painted over the backdrop: the window colour, mostly opaque."""
    out = QColor(window_color)
    out.setAlpha(alpha)
    return out


def frame_inset(maximized: bool, border: int) -> int:
    """How far to pull the client area in on each side. A maximized window
    reaches past the screen by its frame's width; without this its edges
    would be cut off."""
    return border if maximized else 0


def caption_style(style: int, own_title_bar: bool) -> int:
    """The window style to set: without WS_SYSMENU when the window draws its
    own title bar (else Windows draws its caption buttons over the app's);
    unchanged for a window that keeps the Windows title bar and its buttons."""
    return style & ~WS_SYSMENU if own_title_bar else style


def apply(window, keep_caption: bool = False) -> bool:
    """Give ``window`` the Acrylic backdrop; True when Windows reports it on.
    ``keep_caption``: the window keeps the Windows title bar and buttons."""
    import ctypes
    from ctypes import byref, c_int, wintypes

    class MARGINS(ctypes.Structure):
        _fields_ = [("l", c_int), ("r", c_int), ("t", c_int), ("b", c_int)]

    try:
        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        hwnd = wintypes.HWND(int(window.winId()))
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        new_style = caption_style(style, own_title_bar=not keep_caption)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, ctypes.c_ssize_t(new_style))
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED_ONLY)
        dwm.DwmExtendFrameIntoClientArea(hwnd, byref(MARGINS(-1, -1, -1, -1)))
        dark = window.palette().window().color().lightness() < 128
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(c_int(int(dark))), 4
        )
        kind = c_int(DWMSBT_TRANSIENTWINDOW)
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, byref(kind), 4)
        got = c_int()
        dwm.DwmGetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, byref(got), 4)
        return got.value == DWMSBT_TRANSIENTWINDOW
    except (AttributeError, OSError):
        return False


def native_event(message) -> tuple[bool, int] | None:
    """Handle WM_NCCALCSIZE so the window has no system title bar; None for
    every other message."""
    import ctypes
    from ctypes import wintypes

    msg = wintypes.MSG.from_address(int(message))
    if msg.message != WM_NCCALCSIZE or not msg.wParam:
        return None
    user32 = ctypes.windll.user32
    inset = frame_inset(
        bool(user32.IsZoomed(msg.hWnd)),
        user32.GetSystemMetrics(SM_CXSIZEFRAME)
        + user32.GetSystemMetrics(SM_CXPADDEDBORDER),
    )
    if inset:
        # NCCALCSIZE_PARAMS starts with the proposed client RECT.
        rect = wintypes.RECT.from_address(msg.lParam)
        rect.left += inset
        rect.top += inset
        rect.right -= inset
        rect.bottom -= inset
    return True, 0


class BackdropMixin:
    """Acrylic for a top-level window. Put it first in the bases; call
    ``init_backdrop`` early in ``__init__`` and ``finish_backdrop`` once the
    window is built (before it is shown). ``own_title_bar``: the window draws
    its own title bar and buttons, so the Windows one is hidden."""

    backdrop = False
    _own_title_bar = False

    def init_backdrop(self, *, own_title_bar: bool) -> bool:
        self._own_title_bar = own_title_bar
        # Looked up on the module so tests can stand in for the system.
        self.backdrop = _module().supported()
        if self.backdrop:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        return self.backdrop

    _apply_on_show = False

    def finish_backdrop(self, *, before_show: bool = True) -> None:
        """Set the backdrop up now (``before_show``: the native window is
        made at once so it never shows transparent), or as the window is
        first shown. A window parented inside another must wait for its
        show: creating its translucent native window early painted a grey
        patch over the main window's top-left corner."""
        if not self.backdrop:
            return
        if before_show:
            self.winId()  # the native window now, so the backdrop is set
            self._apply_backdrop()  # before it is first shown (no flash)
        else:
            self._apply_on_show = True

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._apply_on_show:
            # Created, not yet on screen: no transparent flash either.
            self._apply_on_show = False
            self._apply_backdrop()
        super().showEvent(event)

    def _apply_backdrop(self) -> None:
        _module().apply(self, keep_caption=not self._own_title_bar)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WinIdChange and self.backdrop:
            # A rebuilt native window (Qt makes one when a web view first
            # needs it) has none of the backdrop settings.
            QTimer.singleShot(0, self._apply_backdrop)
        return super().event(event)

    def nativeEvent(self, event_type, message):  # noqa: N802 (Qt API)
        if self.backdrop and self._own_title_bar:
            handled = _module().native_event(message)
            if handled is not None:
                return handled
        return super().nativeEvent(event_type, message)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self.backdrop:  # tone the Acrylic down where nothing else paints
            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), tint(self.palette().window().color()))
            painter.end()
        super().paintEvent(event)


def _module():
    return sys.modules[__name__]
