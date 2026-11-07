"""What the photo viewer remembers between openings: its window, and whether
the info panel and the filmstrip are open."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QRect

from smart_gallery.gui.state_store import load_json, save_json

STATE_FILE = "viewer.json"
FIRST_OPEN = 0.8  # of the screen, centred, the first time


@dataclass
class ViewerState:
    geometry: tuple[int, int, int, int] | None = None  # x, y, width, height
    info_open: bool = True
    strip_open: bool = False


def load_state(path: Path) -> ViewerState:
    """The saved state; defaults for anything missing or of the wrong type."""
    data = load_json(path)
    state = ViewerState()
    geo = data.get("geometry")
    if (
        isinstance(geo, list)
        and len(geo) == 4
        and all(isinstance(v, int) and not isinstance(v, bool) for v in geo)
    ):
        state.geometry = tuple(geo)
    for name in ("info_open", "strip_open"):
        if isinstance(data.get(name), bool):
            setattr(state, name, data[name])
    return state


def save_state(path: Path, state: ViewerState) -> None:
    try:
        save_json(path, asdict(state))
    except OSError:
        pass  # remembering is a nicety


def place(saved: tuple[int, int, int, int] | None, screen: QRect) -> QRect:
    """Where the window opens: the saved rectangle moved (and shrunk if
    needed) fully onto ``screen``; the first time, 80 % of it, centred."""
    if saved is None:
        width = int(screen.width() * FIRST_OPEN)
        height = int(screen.height() * FIRST_OPEN)
        rect = QRect(0, 0, width, height)
        rect.moveCenter(screen.center())
        return rect
    x, y, width, height = saved
    width, height = min(width, screen.width()), min(height, screen.height())
    x = max(screen.left(), min(x, screen.left() + screen.width() - width))
    y = max(screen.top(), min(y, screen.top() + screen.height() - height))
    return QRect(x, y, width, height)
