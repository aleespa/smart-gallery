"""Face-crop grid and the bar of corrections for the selected faces.

``FaceGrid`` shows ``FaceTile`` crops from ``ctx.face_crops`` (asked for only
for the tiles in view, after the grid settles), selects them with checkboxes
and an accent outline, and marks the faces the user confirmed with a small
tick (``mark_confirmed`` adds it in place). ``FaceActionBar`` turns a button press into a
``LabelEntry``; neither widget starts a job.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QWidget,
)

from smart_gallery.gui import theme
from smart_gallery.gui.people.model import FaceTile, PeopleList, PersonRow
from smart_gallery.services.label_faces import LabelEntry

TILE_EDGE = 96
GRID_SIZE = QSize(TILE_EDGE + 10, TILE_EDGE + 10)
TICK_PX = 18
CHECK_PX = 20
CROP_DEBOUNCE_MS = 120
MENU_GROUPS = 20  # unnamed groups offered in "Move to…", largest first
TILE_ROLE = Qt.ItemDataRole.UserRole + 1

SELECTED = "{n:,} faces selected"
SELECTED_ONE = "1 face selected"
CONFIRM_PERSON = "✓ {name}"
CONFIRM_GROUP = "✓ This group"
CONFIRM_TIP = "These faces are {who}: lock them there and use them to recognise {who}"
NOT_PERSON = "Not {name}"
NOT_GROUP = "Not this group"
MOVE_TO = "Move to… ▾"
IGNORE = "Ignore"
NEW_PERSON = "New person…"
NEW_PERSON_TITLE = "New person"
NEW_PERSON_LABEL = "Name:"
UNNAMED = "Unnamed"
GROUP_ITEM = "Unnamed · {n:,} photos"
GROUP_ITEM_ONE = "Unnamed · 1 photo"

_placeholder: QPixmap | None = None


def _placeholder_tile() -> QPixmap:
    """The tile shown until a crop arrives, and when there is none."""
    global _placeholder
    if _placeholder is None:
        pixmap = QPixmap(TILE_EDGE, TILE_EDGE)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(theme.TILE_BACK))
        p.drawRoundedRect(QRectF(2, 2, TILE_EDGE - 4, TILE_EDGE - 4), 8, 8)
        p.setBrush(theme.qcolor(theme.TILE_GLYPH))
        edge = TILE_EDGE
        p.drawEllipse(QRectF(edge * 0.36, edge * 0.2, edge * 0.28, edge * 0.28))
        p.drawPie(QRectF(edge * 0.2, edge * 0.55, edge * 0.6, edge * 0.6), 0, 180 * 16)
        p.end()
        _placeholder = pixmap
    return _placeholder


class _FaceModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tiles: list[FaceTile] = []
        self._pixmaps: dict[int, QPixmap] = {}  # face id -> scaled crop
        self._missing: set[int] = set()  # no crop: keep the placeholder

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self.tiles)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.tiles):
            return None
        tile = self.tiles[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            return self._pixmaps.get(tile.face_id) or _placeholder_tile()
        if role == TILE_ROLE:
            return tile
        if role == Qt.ItemDataRole.ToolTipRole:
            if tile.score is not None:
                return f"{tile.relpath} · {tile.score:.2f}"
            return tile.relpath
        return None

    def set_tiles(self, tiles: Sequence[FaceTile]) -> None:
        self.beginResetModel()
        self.tiles = list(tiles)
        self.endResetModel()

    def remove_ids(self, gone: set[int]) -> None:
        """Drop these faces row by row, so scroll and selection stay put."""
        row = len(self.tiles) - 1
        while row >= 0:
            if self.tiles[row].face_id not in gone:
                row -= 1
                continue
            first = row
            while first > 0 and self.tiles[first - 1].face_id in gone:
                first -= 1
            self.beginRemoveRows(QModelIndex(), first, row)
            del self.tiles[first : row + 1]
            self.endRemoveRows()
            row = first - 1

    def mark_confirmed(self, ids: set[int]) -> None:
        """Show these faces as confirmed (the tick) without a reload."""
        for row, tile in enumerate(self.tiles):
            if tile.face_id in ids and tile.state != "user":
                self.tiles[row] = replace(tile, state="user")
                index = self.index(row)
                self.dataChanged.emit(index, index)

    def forget_crops(self) -> None:
        self._pixmaps.clear()
        self._missing.clear()

    def needs_crop(self, tile: FaceTile) -> bool:
        return tile.face_id not in self._pixmaps and tile.face_id not in self._missing

    def set_crop(self, face_id: int, path: str) -> None:
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self._missing.add(face_id)
            return
        self._pixmaps[face_id] = pixmap.scaled(
            TILE_EDGE,
            TILE_EDGE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        for i, tile in enumerate(self.tiles):
            if tile.face_id == face_id:
                index = self.index(i)
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])


def _draw_tick(painter: QPainter, box: QRectF, color) -> None:
    painter.setPen(QPen(color, 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    tick = QPainterPath(QPointF(box.left() + box.width() * 0.25, box.center().y()))
    tick.lineTo(QPointF(box.center().x() - 1, box.bottom() - box.height() * 0.28))
    tick.lineTo(
        QPointF(box.right() - box.width() * 0.22, box.top() + box.height() * 0.28)
    )
    painter.drawPath(tick)


def checkbox_rect(rect) -> QRectF:
    """Where a tile's selection checkbox sits: its top-left corner."""
    return QRectF(rect.left() + 8, rect.top() + 8, CHECK_PX, CHECK_PX)


class _TickDelegate(QStyledItemDelegate):
    """The crop, a selection checkbox in the top-left corner, an accent
    outline on selected faces, and a small tick on faces the user confirmed."""

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        plain = QStyleOptionViewItem(option)
        # The accent outline and checkbox show the selection, not a tint.
        plain.state &= ~QStyle.StateFlag.State_Selected
        plain.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, plain, index)
        accent = option.palette.highlight().color()
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            painter.setPen(QPen(accent, 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect).adjusted(2.5, 2.5, -2.5, -2.5), 8, 8)
        box = checkbox_rect(rect)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(box, 4, 4)
            _draw_tick(painter, box, option.palette.highlightedText().color())
        else:
            painter.setPen(QPen(Qt.GlobalColor.white, 1.5))
            painter.setBrush(QColor(0, 0, 0, 90))
            painter.drawRoundedRect(box, 4, 4)
        tile = index.data(TILE_ROLE)
        if tile is not None and tile.state == "user":
            mark = QRectF(rect.right() - TICK_PX - 6, rect.top() + 6, TICK_PX, TICK_PX)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(mark)
            _draw_tick(painter, mark, option.palette.highlightedText().color())
        painter.restore()


class FaceGrid(QListView):
    """Face crops in icon mode with a checkbox each: a click ticks or unticks
    one face and keeps the others; Shift+click ticks a range."""

    faces_selected = Signal(list)  # face ids, in grid order

    def __init__(self, ctx, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.root: Path | None = None
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(TILE_EDGE, TILE_EDGE))
        self.setGridSize(GRID_SIZE)
        self.setItemDelegate(_TickDelegate(self))
        self._model = _FaceModel(self)
        self.setModel(self._model)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(CROP_DEBOUNCE_MS)
        self._timer.timeout.connect(self._request_visible)
        self.verticalScrollBar().valueChanged.connect(self._schedule)
        self.viewport().installEventFilter(self)
        self.selectionModel().selectionChanged.connect(self._on_selection)
        crops = getattr(ctx, "face_crops", None)
        if crops is not None:
            crops.ready.connect(self._on_crop)

    def selectionCommand(self, index, event=None):  # noqa: N802 (Qt API)
        """A plain click toggles one face, like ticking its checkbox, instead
        of replacing the selection; Shift keeps the usual range select."""
        flags = QItemSelectionModel.SelectionFlag
        if (
            index.isValid()
            and event is not None
            and event.type() == QEvent.Type.MouseButtonPress
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            return flags.Toggle | flags.Rows
        return super().selectionCommand(index, event)

    # ── tiles ───────────────────────────────────────────────────────────────
    def set_tiles(self, tiles: list[FaceTile], root: Path | None = None) -> None:
        if root is not None and root != self.root:
            self._model.forget_crops()
            self.root = root
        self._model.set_tiles(tiles)
        self._on_selection()
        self._schedule()

    def tile(self, row: int) -> FaceTile:
        return self._model.tiles[row]

    def tiles(self) -> list[FaceTile]:
        return list(self._model.tiles)

    def selected_face_ids(self) -> list[int]:
        rows = sorted(i.row() for i in self.selectionModel().selectedIndexes())
        return [self._model.tiles[r].face_id for r in rows]

    def remove_faces(self, ids: Iterable[int]) -> None:
        self._model.remove_ids(set(ids))
        self._on_selection()

    def mark_confirmed(self, ids: Iterable[int]) -> None:
        self._model.mark_confirmed(set(ids))

    def _on_selection(self, *_args) -> None:
        self.faces_selected.emit(self.selected_face_ids())

    # ── crops ───────────────────────────────────────────────────────────────
    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        if watched is self.viewport() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self._schedule()
        return super().eventFilter(watched, event)

    def _schedule(self, *_args) -> None:
        self._timer.start()

    def _request_visible(self) -> None:
        crops = getattr(self.ctx, "face_crops", None)
        if crops is None or self.root is None or not self.isVisible():
            return
        area = self.viewport().rect()
        for i, tile in enumerate(self._model.tiles):
            if not self._model.needs_crop(tile):
                continue
            if self.visualRect(self._model.index(i)).intersects(area):
                crops.request(
                    self.root,
                    tile.face_id,
                    tile.relpath,
                    tile.ext,
                    tile.bbox,
                    tile.img_size,
                )

    def _on_crop(self, root: str, face_id: int, path: str) -> None:
        if self.root is None or root != str(self.root):
            return
        self._model.set_crop(face_id, path)


class FaceActionBar(QWidget):
    """'N faces selected' and the corrections for them.

    ``confirm``, ``reject`` and ``ignore`` False leave only **Move to…** (the
    hidden pile). Confirm and Not show only while the faces have a person.
    """

    action = Signal(object)  # LabelEntry

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        confirm: bool = True,
        reject: bool = True,
        ignore: bool = True,
    ):
        super().__init__(parent)
        self._faces: list[int] = []
        self._person: PersonRow | None = None
        self._people: list[PersonRow] = []

        self._confirm = confirm
        self._reject_shown = reject

        self.count_label = QLabel()
        self.confirm_button = QPushButton(CONFIRM_GROUP)
        self.not_button = QPushButton(NOT_GROUP)
        self.move_menu = QMenu(self)
        self.move_button = QToolButton()
        self.move_button.setText(MOVE_TO)
        self.move_button.setMenu(self.move_menu)
        self.move_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self.ignore_button = QPushButton(IGNORE)
        self.ignore_button.setHidden(not ignore)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.count_label)
        layout.addStretch(1)
        layout.addWidget(self.confirm_button)
        layout.addWidget(self.not_button)
        layout.addWidget(self.move_button)
        layout.addWidget(self.ignore_button)

        self.confirm_button.clicked.connect(self._confirm_faces)
        self.not_button.clicked.connect(self._reject)
        self.ignore_button.clicked.connect(self._ignore)
        self.set_faces([])
        self.set_person(None)

    def set_person(self, person: PersonRow | None) -> None:
        """The person the faces are filed under now (None: no one)."""
        self._person = person
        name = person.name if person is not None else None
        self.not_button.setText(NOT_PERSON.format(name=name) if name else NOT_GROUP)
        self.confirm_button.setText(
            CONFIRM_PERSON.format(name=name) if name else CONFIRM_GROUP
        )
        who = name or "this group"
        self.confirm_button.setToolTip(CONFIRM_TIP.format(who=who))
        self.confirm_button.setHidden(not self._confirm or person is None)
        self.not_button.setHidden(not self._reject_shown or person is None)
        self._rebuild_menu()

    def set_people(self, people: PeopleList | None) -> None:
        """The move targets: named people, then the largest unnamed groups."""
        if people is None:
            self._people = []
        else:
            self._people = [*people.named, *people.groups[:MENU_GROUPS]]
        self._rebuild_menu()

    def set_faces(self, ids: Sequence[int]) -> None:
        self._faces = list(ids)
        n = len(self._faces)
        self.count_label.setText(SELECTED_ONE if n == 1 else SELECTED.format(n=n))
        buttons = (
            self.confirm_button,
            self.not_button,
            self.move_button,
            self.ignore_button,
        )
        for button in buttons:
            button.setEnabled(n > 0)

    def _rebuild_menu(self) -> None:
        self.move_menu.clear()
        current = self._person.id if self._person is not None else None
        for row in self._people:
            if row.id == current:
                continue
            if row.name:
                text = row.name
            else:
                one = row.photos == 1
                text = GROUP_ITEM_ONE if one else GROUP_ITEM.format(n=row.photos)
            self.move_menu.addAction(text, lambda pid=row.id: self._move(pid))
        if not self.move_menu.isEmpty():
            self.move_menu.addSeparator()
        self.move_menu.addAction(NEW_PERSON, self._new_person)

    def _emit(self, entry: LabelEntry) -> None:
        if self._faces:
            self.action.emit(entry)

    def _confirm_faces(self) -> None:
        if self._person is not None:
            faces = tuple(self._faces)
            self._emit(LabelEntry("confirm", faces, person_id=self._person.id))

    def _reject(self) -> None:
        if self._person is not None:
            faces = tuple(self._faces)
            self._emit(LabelEntry("reject", faces, person_id=self._person.id))

    def _ignore(self) -> None:
        self._emit(LabelEntry("ignore", tuple(self._faces)))

    def _move(self, person_id: int) -> None:
        self._emit(LabelEntry("move", tuple(self._faces), person_id=person_id))

    def _new_person(self) -> None:
        if not self._faces:
            return
        text, ok = QInputDialog.getText(self, NEW_PERSON_TITLE, NEW_PERSON_LABEL)
        name = text.strip() if ok else ""
        if name:
            self._emit(LabelEntry("new_person", tuple(self._faces), name=name))
