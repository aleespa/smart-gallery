"""The People page's right side: one person, several people, the hidden pile.

Every page reads off the GUI thread (``RepoQuery``) and never writes: face
corrections leave through the ``labels`` signal as ``LabelEntry`` lists, and
naming or showing a group again leave as signals too; ``PeopleView`` turns
them into CLI jobs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PySide6.QtCore import QEvent, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui import theme
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.library.grid import GroupedGrid
from smart_gallery.gui.library.model import LibraryQuery, group_rows, library_rows
from smart_gallery.gui.library.view import (
    GRID_SIZE,
    ICON_EDGE,
    THUMB_DEBOUNCE_MS,
    LibraryGridModel,
)
from smart_gallery.gui.library.viewer import MediaViewer
from smart_gallery.gui.people import model
from smart_gallery.gui.people.faces_grid import FaceActionBar, FaceGrid
from smart_gallery.gui.people.list_view import _round
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.gui.segment import segment as _segment
from smart_gallery.services.label_faces import LabelEntry

NAME_PT = 16
COVER_PX = 48

PHOTOS = "Photos"
FACES = "Faces"
RENAME = "Rename"
SHOW_IN_LIBRARY = "Show in Library"
ADD_NAME = "Add a name"
UNNAMED = "Unnamed"
PHOTO_COUNT = "{n:,} photos"
PHOTO_COUNT_ONE = "1 photo"
SUGGEST = "Is this {name}? {n:,} suggestions"
SUGGEST_ONE = "Is this {name}? 1 suggestion"
SOMEONE = "this person"
REVIEW = "Review"
REVIEW_TITLE = "Is this {name}?"
FIND_MORE = "Find more"
MORE_TITLE = "Could this be {name}?"
SEARCHING = "Searching…"
MORE_FAILED = "Couldn't search for more faces: {error}"
NO_MORE = "No more faces that look like {name}."
DONE = "Done"
CONFIRM = "✓"
REJECT = "✕"
CONFIRM_ALL = "✓ all"
REJECT_ALL = "✕ all"
SORTS = (("Least sure first", "least_sure"), ("Newest", "newest"))
SORT_LABEL = "Sort:"
ALL = "All of them"
ANY = "Any of them"
HIDDEN_TITLE = "Hidden faces"
HIDDEN_NOTE = (
    "Tiny, low-score, one-off and ignored faces. Move one to a person to keep it."
)
HIDDEN_GROUPS = "Hidden groups"
SHOW_AGAIN = "Show again"


def _photos(n: int) -> str:
    return PHOTO_COUNT_ONE if n == 1 else PHOTO_COUNT.format(n=n)


def _years(first: int | None, last: int | None) -> str:
    if first is None or last is None:
        return ""
    return f"{first}" if first == last else f"{first}–{last}"


def _heading(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setFont(theme.serif_font(NAME_PT))
    return label


class PhotoGrid(QWidget):
    """The Library's grouped grid and thumbnails for a list of photos;
    double-click opens the Library viewer."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.root: Path | None = None
        self.viewer: MediaViewer | None = None
        self.grid = GroupedGrid()
        self.grid.setIconSize(QSize(ICON_EDGE, ICON_EDGE))
        self.grid.setGridSize(GRID_SIZE)
        self.grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.model = LibraryGridModel(self)
        self.grid.setModel(self.model)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.grid)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(THUMB_DEBOUNCE_MS)
        self._timer.timeout.connect(self._request_visible)
        self.grid.doubleClicked.connect(self.open_viewer)
        self.model.rowsInserted.connect(self._schedule)
        self.grid.groups_toggled.connect(self._schedule)
        self.grid.verticalScrollBar().valueChanged.connect(self._schedule)
        self.grid.viewport().installEventFilter(self)
        if ctx.thumbs is not None:
            ctx.thumbs.ready.connect(self._on_thumb)

    def set_rows(self, root: Path, rows: list) -> None:
        if self.root is not None and root != self.root:
            self.model.forget_thumbnails()
        self.root = root
        self.grid.set_groups(group_rows(rows, "date"))
        self.model.set_rows(rows)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        if watched is self.grid.viewport() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self._schedule()
        return super().eventFilter(watched, event)

    def _schedule(self, *_args) -> None:
        self._timer.start()

    def _request_visible(self) -> None:
        thumbs = self.ctx.thumbs
        if thumbs is None or self.root is None or not self.grid.isVisible():
            return
        visible = self.grid.visible_rows()
        if not visible:
            return
        thumbs.cancel_pending()
        for i in visible:
            row = self.model.rows[i]
            if self.model.needs_thumbnail(row):
                thumbs.request(
                    self.root,
                    row.id,
                    row.mtime_ns,
                    row.relpath,
                    row.ext,
                    row.media_type,
                )

    def _on_thumb(self, root: str, media_id: int, path: str) -> None:
        if self.root is not None and root == str(self.root):
            self.model.set_thumbnail(media_id, path)

    def open_viewer(self, index: QModelIndex) -> None:
        if not index.isValid() or self.root is None:
            return
        if self.viewer is not None:
            self.viewer.close()
            self.viewer.deleteLater()
        self.viewer = MediaViewer(
            self.ctx, self.root, list(self.model.rows), index.row(), self
        )
        self.viewer.show()


def _photo_rows(repo, person_ids: list[int], match: str) -> list:
    """Photos with any (or all) of ``person_ids``, newest first."""
    filters = model.photos_filter(person_ids, match)
    q = LibraryQuery(filters=filters, sort="date", descending=True)
    return library_rows(repo, q)


class PersonPage(QWidget):
    """One person: header, suggestions strip, and Photos | Faces."""

    labels = Signal(list)  # list[LabelEntry]
    rename_requested = Signal(int)
    name_entered = Signal(int, str)  # person id, name typed for a group

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.person_id: int | None = None
        self.summary: model.PersonSummary | None = None
        self.root: Path | None = None
        self.loaded = False
        self.faces_loaded = False
        self._faces_for: tuple[int, str] | None = None  # (id, sort) shown
        self._cover_id: int | None = None
        self._query = RepoQuery(ctx, self)
        self._faces_query = RepoQuery(ctx, self)
        self._more_query = RepoQuery(ctx, self)
        self._suggestions: list[model.FaceTile] = []
        self.review_mode: Literal["suggestions", "more"] = "suggestions"

        # Header.
        self.cover = QLabel()
        self.cover.setFixedSize(COVER_PX, COVER_PX)
        self.name_label = _heading()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(ADD_NAME)
        self.name_edit.setFont(theme.serif_font(NAME_PT))
        self.subtitle = QLabel()
        self.rename_button = QPushButton(RENAME)
        self.library_button = QPushButton(SHOW_IN_LIBRARY)
        self.find_more_button = QPushButton(FIND_MORE)
        switch, self._mode, (self.photos_button, self.faces_button) = _segment(
            PHOTOS, FACES
        )
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.name_label)
        titles.addWidget(self.name_edit)
        titles.addWidget(self.subtitle)
        header = QHBoxLayout()
        header.addWidget(self.cover)
        header.addLayout(titles)
        header.addStretch(1)
        header.addWidget(self.rename_button)
        header.addWidget(self.library_button)
        header.addWidget(self.find_more_button)
        header.addSpacing(12)
        header.addWidget(switch)

        # Suggestions strip.
        self.suggest_label = QLabel()
        self.review_button = QPushButton(REVIEW)
        self.suggest_strip = QWidget()
        strip = QHBoxLayout(self.suggest_strip)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.addWidget(self.suggest_label)
        strip.addWidget(self.review_button)
        strip.addStretch(1)
        self.suggest_strip.hide()

        # Photos.
        self.photos = PhotoGrid(ctx)

        # Faces.
        self.sort = QComboBox()
        for label, value in SORTS:
            self.sort.addItem(label, value)
        self.faces_grid = FaceGrid(ctx)
        self.action_bar = FaceActionBar()
        self.faces_page = QWidget()
        faces = QVBoxLayout(self.faces_page)
        faces.setContentsMargins(0, 0, 0, 0)
        sort_row = QHBoxLayout()
        sort_row.addStretch(1)
        sort_row.addWidget(QLabel(SORT_LABEL))
        sort_row.addWidget(self.sort)
        faces.addLayout(sort_row)
        faces.addWidget(self.faces_grid, 1)
        faces.addWidget(self.action_bar)

        # Review of the suggestions.
        self.review_title = _heading()
        self.review_grid = FaceGrid(ctx)
        self.confirm_button = QPushButton(CONFIRM)
        self.reject_button = QPushButton(REJECT)
        self.confirm_all_button = QPushButton(CONFIRM_ALL)
        self.reject_all_button = QPushButton(REJECT_ALL)
        self.done_button = QPushButton(DONE)
        self.review_page = QWidget()
        review = QVBoxLayout(self.review_page)
        review.setContentsMargins(0, 0, 0, 0)
        review.addWidget(self.review_title)
        review.addWidget(self.review_grid, 1)
        buttons = QHBoxLayout()
        for button in (
            self.confirm_button,
            self.reject_button,
            self.confirm_all_button,
            self.reject_all_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.done_button)
        review.addLayout(buttons)

        self.body = QStackedWidget()
        self.body.addWidget(self.photos)
        self.body.addWidget(self.faces_page)
        self.body.addWidget(self.review_page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.suggest_strip)
        layout.addWidget(self.body, 1)

        self._mode.buttonClicked.connect(self._show_mode)
        self.sort.currentIndexChanged.connect(self._load_faces)
        self.rename_button.clicked.connect(self._rename)
        self.library_button.clicked.connect(self._show_in_library)
        self.name_edit.returnPressed.connect(self._name_entered)
        self.review_button.clicked.connect(self._review)
        self.find_more_button.clicked.connect(self.find_more)
        self.done_button.clicked.connect(self._show_mode)
        self.faces_grid.faces_selected.connect(self.action_bar.set_faces)
        self.action_bar.action.connect(lambda e: self.labels.emit([e]))
        self.confirm_button.clicked.connect(lambda: self._decide("confirm", False))
        self.reject_button.clicked.connect(lambda: self._decide("reject", False))
        self.confirm_all_button.clicked.connect(lambda: self._decide("confirm", True))
        self.reject_all_button.clicked.connect(lambda: self._decide("reject", True))
        self._query.finished.connect(self._on_data)
        self._faces_query.finished.connect(self._on_faces)
        self._more_query.finished.connect(self._on_more)
        self._more_query.failed.connect(self._on_more_failed)
        crops = getattr(ctx, "face_crops", None)
        if crops is not None:
            crops.ready.connect(self._on_crop)

    # ── loading ─────────────────────────────────────────────────────────────
    def set_people(self, people: model.PeopleList | None) -> None:
        """The people offered by **Move to…**."""
        self.action_bar.set_people(people)

    def show_person(self, person_id: int) -> None:
        if person_id != self.person_id:
            self.person_id = person_id
            self.summary = None
            self._suggestions = []
            self._faces_for = None
            self.review_mode = "suggestions"
            if self.body.currentWidget() is self.review_page:
                self._show_mode()
        self.loaded = False

        def load(repo):
            return (
                repo.drive_root,
                model.person_summary(repo, person_id),
                _photo_rows(repo, [person_id], "any"),
                model.suggestions_for(repo, person_id),
            )

        self._query.run(load)
        if self.body.currentWidget() is self.faces_page:
            self._load_faces()

    def _on_data(self, result) -> None:
        root, summary, rows, suggestions = result
        self._suggestions = suggestions
        self.root = Path(root)
        self.summary = summary
        self.photos.set_rows(self.root, rows)
        if self.review_mode == "suggestions":
            self.review_grid.set_tiles(suggestions, self.root)
        self._show_header()
        self.loaded = True
        if self.review_mode == "more" and self.body.currentWidget() is self.review_page:
            self.find_more()

    def _load_faces(self, *_args) -> None:
        pid, sort = self.person_id, self.sort.currentData()
        if pid is None:
            return
        self.faces_loaded = False

        def load(repo):
            return repo.drive_root, model.person_faces(repo, pid, sort)

        self._faces_for = (pid, sort)
        self._faces_query.run(load)

    def _on_faces(self, result) -> None:
        root, tiles = result
        self.faces_grid.set_tiles(tiles, Path(root))
        self.faces_loaded = True

    # ── header ──────────────────────────────────────────────────────────────
    def _show_header(self) -> None:
        summary = self.summary
        row = summary.row if summary is not None else None
        self.action_bar.set_person(row)
        named = bool(row and row.name)
        self.name_label.setText(row.name if named else "")
        self.name_label.setHidden(not named)
        self.name_edit.setHidden(named or row is None)
        if not named:
            self.name_edit.clear()
        self.rename_button.setHidden(not named)
        self.library_button.setHidden(not named)
        if row is None:
            self.subtitle.setText("")
            self.suggest_strip.hide()
            return
        years = _years(summary.first_year, summary.last_year)
        self.subtitle.setText(_photos(row.photos) + (f" · {years}" if years else ""))
        name = row.name or SOMEONE
        n = row.suggestions
        text = SUGGEST_ONE if n == 1 else SUGGEST
        self.suggest_label.setText(text.format(name=name, n=n))
        self.suggest_strip.setVisible(n > 0)
        if self.review_mode == "suggestions":
            self.review_title.setText(REVIEW_TITLE.format(name=name))
        self._ask_cover(row.cover)

    def _ask_cover(self, tile: model.FaceTile | None) -> None:
        blank = QPixmap(COVER_PX, COVER_PX)
        blank.fill(theme.qcolor(theme.TILE_BACK))
        self.cover.setPixmap(_round(blank, COVER_PX))
        self._cover_id = tile.face_id if tile is not None else None
        crops = getattr(self.ctx, "face_crops", None)
        if tile is None or crops is None or self.root is None:
            return
        crops.request(
            self.root, tile.face_id, tile.relpath, tile.ext, tile.bbox, tile.img_size
        )

    def _on_crop(self, root: str, face_id: int, path: str) -> None:
        if self.root is None or root != str(self.root):
            return
        if face_id != self._cover_id:
            return
        pixmap = QPixmap(path) if path else QPixmap()
        if not pixmap.isNull():
            self.cover.setPixmap(_round(pixmap, COVER_PX))

    # ── modes ───────────────────────────────────────────────────────────────
    def _show_mode(self, *_args) -> None:
        if self.faces_button.isChecked():
            self.body.setCurrentWidget(self.faces_page)
            if self._faces_for != (self.person_id, self.sort.currentData()):
                self._load_faces()
        else:
            self.body.setCurrentWidget(self.photos)

    def _name(self) -> str:
        row = self.summary.row if self.summary is not None else None
        return (row.name if row is not None else None) or SOMEONE

    def _review(self) -> None:
        self.review_mode = "suggestions"
        self.review_title.setText(REVIEW_TITLE.format(name=self._name()))
        if self.root is not None:
            self.review_grid.set_tiles(self._suggestions, self.root)
        self.body.setCurrentWidget(self.review_page)

    def find_more(self) -> None:
        """Faces nobody has classified that look like this person."""
        pid = self.person_id
        if pid is None:
            return
        self.review_mode = "more"
        self.review_title.setText(SEARCHING)
        self.body.setCurrentWidget(self.review_page)

        def load(repo):
            return repo.drive_root, pid, model.find_more(repo, pid)

        self._more_query.run(load)

    def _on_more(self, result) -> None:
        root, pid, tiles = result
        if pid != self.person_id or self.review_mode != "more":
            return
        self.review_grid.set_tiles(tiles, Path(root))
        name = self._name()
        self.review_title.setText(
            MORE_TITLE.format(name=name) if tiles else NO_MORE.format(name=name)
        )

    def _on_more_failed(self, error) -> None:
        if self.review_mode != "more":
            return
        self.review_grid.set_tiles([], self.root)
        self.review_title.setText(MORE_FAILED.format(error=error))

    def _decide(self, action: str, everything: bool) -> None:
        if self.person_id is None:
            return
        grid = self.review_grid
        faces = (
            [t.face_id for t in grid.tiles()]
            if everything
            else grid.selected_face_ids()
        )
        if faces:
            entry = LabelEntry(action, tuple(faces), person_id=self.person_id)
            self.labels.emit([entry])

    # ── naming and the Library ──────────────────────────────────────────────
    def _rename(self) -> None:
        if self.person_id is not None:
            self.rename_requested.emit(self.person_id)

    def _name_entered(self) -> None:
        name = self.name_edit.text().strip()
        if name and self.person_id is not None:
            self.name_entered.emit(self.person_id, name)

    def _show_in_library(self) -> None:
        row = self.summary.row if self.summary is not None else None
        if row is None or not row.name:
            return
        self.ctx.set_filters(model.library_filter(self.ctx.filters, [row.name], "any"))
        self.ctx.navigate.emit("library")


class SeveralPeoplePage(QWidget):
    """Photos with all (or any) of the selected people."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list[model.PersonRow] = []
        self.loaded = False
        self._query = RepoQuery(ctx, self)

        self.title = _heading()
        self.title.setWordWrap(True)
        switch, self._match, (self.all_button, self.any_button) = _segment(ALL, ANY)
        self.count = QLabel()
        self.library_button = QPushButton(SHOW_IN_LIBRARY)
        self.photos = PhotoGrid(ctx)
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(switch)
        header.addSpacing(12)
        header.addWidget(self.count)
        header.addSpacing(12)
        header.addWidget(self.library_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.photos, 1)

        self._match.buttonClicked.connect(self._load)
        self.library_button.clicked.connect(self._show_in_library)
        self._query.finished.connect(self._on_data)

    @property
    def match(self) -> str:
        return "any" if self.any_button.isChecked() else "all"

    def show_people(self, rows: list[model.PersonRow]) -> None:
        self.rows = list(rows)
        self.title.setText(", ".join(r.name or UNNAMED for r in self.rows))
        self.library_button.setEnabled(bool(rows) and all(r.name for r in rows))
        self._load()

    def _load(self, *_args) -> None:
        ids, match = [r.id for r in self.rows], self.match
        self.loaded = False
        self._query.run(lambda repo: (repo.drive_root, _photo_rows(repo, ids, match)))

    def _on_data(self, result) -> None:
        root, rows = result
        self.photos.set_rows(Path(root), rows)
        self.count.setText(_photos(len(rows)))
        self.loaded = True

    def _show_in_library(self) -> None:
        names = [r.name for r in self.rows]
        if not names or not all(names):
            return
        self.ctx.set_filters(model.library_filter(self.ctx.filters, names, self.match))
        self.ctx.navigate.emit("library")


class HiddenFacesPage(QWidget):
    """The hidden pile (rescue a face with **Move to…**) and hidden groups."""

    labels = Signal(list)  # list[LabelEntry]
    unhide_requested = Signal(int)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.loaded = False
        self.unhide_buttons: dict[int, QPushButton] = {}
        self._query = RepoQuery(ctx, self)

        note = QLabel(HIDDEN_NOTE)
        note.setWordWrap(True)
        self.faces_grid = FaceGrid(ctx)
        self.action_bar = FaceActionBar(confirm=False, reject=False, ignore=False)
        self.groups = QListWidget()
        self.groups.setMaximumHeight(160)
        self.groups_title = QLabel(HIDDEN_GROUPS)
        self.groups_title.setFont(theme.serif_font(theme.SECTION_PT))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_heading(HIDDEN_TITLE))
        layout.addWidget(note)
        layout.addWidget(self.faces_grid, 1)
        layout.addWidget(self.action_bar)
        layout.addWidget(self.groups_title)
        layout.addWidget(self.groups)

        self.faces_grid.faces_selected.connect(self.action_bar.set_faces)
        self.action_bar.action.connect(lambda e: self.labels.emit([e]))
        self._query.finished.connect(self._on_data)

    def set_people(self, people: model.PeopleList | None) -> None:
        self.action_bar.set_people(people)

    def show_hidden(self, groups: list[model.PersonRow]) -> None:
        self._set_groups(groups)
        self.loaded = False
        self._query.run(lambda repo: (repo.drive_root, model.hidden_faces(repo)))

    def _on_data(self, result) -> None:
        root, tiles = result
        self.faces_grid.set_tiles(tiles, Path(root))
        self.loaded = True

    def _set_groups(self, groups: list[model.PersonRow]) -> None:
        self.groups.clear()
        self.unhide_buttons = {}
        for row in groups:
            line = QWidget()
            box = QHBoxLayout(line)
            box.setContentsMargins(4, 2, 4, 2)
            box.addWidget(QLabel(f"{row.name or UNNAMED} · {_photos(row.photos)}"))
            box.addStretch(1)
            button = QPushButton(SHOW_AGAIN)
            button.clicked.connect(
                lambda _=False, pid=row.id: self.unhide_requested.emit(pid)
            )
            box.addWidget(button)
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setSizeHint(line.sizeHint())
            self.groups.addItem(item)
            self.groups.setItemWidget(item, line)
            self.unhide_buttons[row.id] = button
        self.groups_title.setHidden(not groups)
        self.groups.setHidden(not groups)
