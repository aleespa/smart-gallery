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

from PySide6.QtGui import QColor, QGuiApplication

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


def apply(window) -> bool:
    """Give ``window`` the Acrylic backdrop; True when Windows reports it on."""
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
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, ctypes.c_ssize_t(style & ~WS_SYSMENU))
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
