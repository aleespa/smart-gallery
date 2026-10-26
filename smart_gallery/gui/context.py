"""Shared state and signals that connect the app's areas."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from smart_gallery.db import GalleryRepository
from smart_gallery.organize.filters import FilterOptions


class AppContext(QObject):
    gallery_changed = Signal(object)  # Gallery | None
    catalog_updated = Signal()  # a job changed the open catalog
    filters_changed = Signal(object)  # FilterOptions
    navigate = Signal(str)  # area id
    open_in_actions = Signal(object)  # argv list for the Actions area
    open_in_import = Signal(object)  # profile name (or None) for the Import area

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        # Set by whoever builds the window: GalleryStore (Task 5), JobRunner
        # (Task 6), ThumbnailLoader (Task 9),
        # FaceCropLoader (face crops for the People page).
        self.galleries = None
        self.jobs = None
        self.thumbs = None
        self.face_crops = None
        self.current = None  # Gallery | None
        self.filters = FilterOptions()
        self._leave_guards: list[Callable[[], bool]] = []

    def set_gallery(self, gallery) -> None:
        self.current = gallery
        self.gallery_changed.emit(gallery)

    def add_leave_guard(self, guard: Callable[[], bool]) -> None:
        """Ask ``guard`` before the user closes the app or switches gallery.

        A guard returns True when leaving is fine (it may ask the user first).
        """
        self._leave_guards.append(guard)

    def remove_leave_guard(self, guard: Callable[[], bool]) -> None:
        if guard in self._leave_guards:
            self._leave_guards.remove(guard)

    def can_leave(self) -> bool:
        """True when every guard agrees; stops asking at the first refusal."""
        return all(guard() for guard in list(self._leave_guards))

    def set_filters(self, filters: FilterOptions) -> None:
        self.filters = filters
        self.filters_changed.emit(filters)

    @contextmanager
    def open_repo(self) -> Iterator[GalleryRepository]:
        """Read-only repository for the current gallery.

        Raises FileNotFoundError when no gallery is selected or its drive is
        not connected.
        """
        if self.current is None:
            raise FileNotFoundError("No gallery is open.")
        db_path = Path(self.current.db_path)
        if not db_path.exists():
            raise FileNotFoundError(f"Gallery is not connected: {db_path}")
        with GalleryRepository.open(db_path, read_only=True) as repo:
            yield repo
