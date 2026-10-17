"""The app's areas, in rail order. Views are imported lazily by their factories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from smart_gallery.gui.context import AppContext


@dataclass(frozen=True)
class Area:
    id: str
    label: str
    icon: str
    factory: Callable[[AppContext], QWidget]


def placeholder(label: str) -> Callable[[AppContext], QWidget]:
    """Factory for an area that is not built yet."""

    def build(ctx: AppContext) -> QWidget:
        page = QWidget()
        text = QLabel(f"{label} is coming soon.")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        QVBoxLayout(page).addWidget(text)
        return page

    return build


def _people(ctx: AppContext) -> QWidget:
    from smart_gallery.gui.people.view import PeopleView

    return PeopleView(ctx)


def _actions(ctx: AppContext) -> QWidget:
    from smart_gallery.gui.actions.view import ActionsView

    return ActionsView(ctx)


def _history(ctx: AppContext) -> QWidget:
    from smart_gallery.gui.history.view import HistoryView

    return HistoryView(ctx)


AREAS: list[Area] = [
    Area("library", "Library", "image", placeholder("Library")),
    Area("import", "Import", "download", placeholder("Import")),
    Area("dashboard", "Dashboard", "chart", placeholder("Dashboard")),
    Area("people", "People", "people", _people),
    Area("actions", "Actions", "play", _actions),
    Area("history", "History", "history", _history),
]
