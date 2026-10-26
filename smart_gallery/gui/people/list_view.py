"""The People page's left column: named people, then unnamed groups.

Rows come from ``model.people_list``. Selection is reported in click order, a
row dropped onto another asks for a merge, and the context menu asks for the
person actions. This widget never starts a job itself: it emits a signal and
``PeopleView`` turns it into one.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui import paths, theme
from smart_gallery.gui.people import model

PERSON_ROLE = Qt.ItemDataRole.UserRole + 1  # model.PersonRow on a person row
HEADER_ROLE = Qt.ItemDataRole.UserRole + 2  # section title on a header row
COVER_ROLE = Qt.ItemDataRole.UserRole + 3  # round QPixmap of the cover crop

NAMED = "Named"
TO_NAME = "To name ({n})"
UNNAMED = "Unnamed"
SEARCH_HINT = "Search…"
SHOW_HIDDEN = "Show hidden faces"

COVER_PX = 32
ROW_PX = 44
HEADER_PX = 28
PAD = 6
BAR_PX = 3  # accent bar on the selected row's left edge
SELECTED_ALPHA = 56  # strength of the selected row's accent wash, 0-255


def _round(pixmap: QPixmap, size: int = COVER_PX) -> QPixmap:
    """``pixmap`` cropped to a centred square and clipped to a circle."""
    side = min(pixmap.width(), pixmap.height())
    square = pixmap.copy(
        (pixmap.width() - side) // 2, (pixmap.height() - side) // 2, side, side
    ).scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(0, 0, size, size)
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, square)
    painter.end()
    return out


class _RowDelegate(QStyledItemDelegate):
    """Header rows in the heading face; person rows as cover, name, badge and
    photo count."""

    def sizeHint(self, option, index: QModelIndex) -> QSize:  # noqa: N802
        height = HEADER_PX if index.data(HEADER_ROLE) is not None else ROW_PX
        return QSize(option.rect.width(), height)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        header = index.data(HEADER_ROLE)
        rect = option.rect
        painter.save()
        if header is not None:
            painter.setFont(theme.serif_font(theme.SECTION_PT))
            painter.setPen(
                option.palette.color(option.palette.ColorRole.PlaceholderText)
            )
            painter.drawText(
                rect.adjusted(PAD, 0, -PAD, 0),
                Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                header,
            )
            painter.restore()
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            # A soft accent wash and an accent bar, so the name keeps the
            # normal text colour instead of dark text on the bright accent.
            accent = option.palette.highlight().color()
            wash = QColor(accent)
            wash.setAlpha(SELECTED_ALPHA)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(wash)
            painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), 6, 6)
            painter.setBrush(accent)
            painter.drawRoundedRect(
                QRect(rect.left() + 2, rect.top() + 6, BAR_PX, rect.height() - 12),
                BAR_PX / 2,
                BAR_PX / 2,
            )
        else:
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            style = opt.widget.style() if opt.widget else None
            if style is not None:  # the usual hover panel
                style.drawPrimitive(
                    QStyle.PrimitiveElement.PE_PanelItemViewItem,
                    opt,
                    painter,
                    opt.widget,
                )
        text_color = option.palette.color(option.palette.ColorRole.Text)
        cover = QRect(
            rect.left() + PAD, rect.center().y() - COVER_PX // 2, COVER_PX, COVER_PX
        )
        pixmap = index.data(COVER_ROLE)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.drawPixmap(cover, pixmap)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(theme.qcolor(theme.TILE_BACK)))
            painter.drawEllipse(cover)

        row: model.PersonRow = index.data(PERSON_ROLE)
        right = rect.right() - PAD
        count = f"{row.photos:,}"
        metrics = option.fontMetrics
        count_w = metrics.horizontalAdvance(count)
        painter.setPen(text_color)
        painter.drawText(
            QRect(right - count_w, rect.top(), count_w, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            count,
        )
        right -= count_w + PAD
        if row.suggestions > 0:
            badge = f"{row.suggestions:,}"
            w = metrics.horizontalAdvance(badge) + 10
            h = metrics.height()
            box = QRect(right - w, rect.center().y() - h // 2, w, h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(theme.qcolor(theme.BADGE_BACK)))
            painter.drawRoundedRect(box, h / 2, h / 2)
            painter.setPen(QColor(theme.qcolor(theme.BADGE_TEXT)))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, badge)
            right -= w + PAD
        name_rect = QRect(
            cover.right() + PAD * 2,
            rect.top(),
            right - cover.right() - PAD * 2,
            rect.height(),
        )
        painter.setPen(text_color)
        name = metrics.elidedText(
            index.data(Qt.ItemDataRole.DisplayRole) or "",
            Qt.TextElideMode.ElideRight,
            name_rect.width(),
        )
        painter.drawText(
            name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name
        )
        painter.restore()


class _RowList(QListWidget):
    """A list whose rows are dropped onto each other rather than moved."""

    dropped = Signal(object)  # the QListWidgetItem dropped onto

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        # Copy, never move: a moved drag would take the source rows out.
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setUniformItemSizes(False)
        self.setItemDelegate(_RowDelegate(self))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _target(self, event) -> QListWidgetItem | None:
        item = self.itemAt(event.position().toPoint())
        if item is None or item.data(PERSON_ROLE) is None or item.isSelected():
            return None
        return item

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.source() is self and self._target(event) is not None:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt API)
        target = self._target(event) if event.source() is self else None
        if target is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.drop_onto(target)

    def drop_onto(self, item: QListWidgetItem) -> None:
        self.dropped.emit(item)


class PeopleList(QWidget):
    selection_changed = Signal(list)  # person ids, in click order
    merge_requested = Signal(int, list)  # dst, srcs
    rename_requested = Signal(int)
    split_requested = Signal(int)
    hide_requested = Signal(int, bool)  # id, hide (False: show again)
    delete_requested = Signal(int)
    show_hidden_requested = Signal()

    def __init__(self, ctx, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._order: list[int] = []
        self._root = ""
        self._covers: dict[int, QPixmap] = {}  # face id -> round crop
        self._waiting: dict[int, list[QListWidgetItem]] = {}  # face id -> rows

        self.search = QLineEdit()
        self.search.setPlaceholderText(SEARCH_HINT)
        self.search.setClearButtonEnabled(True)
        self.list = _RowList()
        self.show_hidden = QPushButton(SHOW_HIDDEN)
        self.show_hidden.setFlat(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.show_hidden)

        self.search.textChanged.connect(self._apply_search)
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.dropped.connect(self._on_drop)
        self.list.customContextMenuRequested.connect(self._on_menu)
        self.show_hidden.clicked.connect(self.show_hidden_requested)
        crops = getattr(ctx, "face_crops", None)
        if crops is not None:
            crops.ready.connect(self._on_crop)

    # ── rows ────────────────────────────────────────────────────────────────
    def set_people(
        self, people: model.PeopleList | None, root: Path | str | None = None
    ) -> None:
        """Show ``people`` (None clears), keeping the selected ids that remain."""
        root_str = str(root) if root is not None else ""
        if root_str != self._root:
            self._covers.clear()
            self._root = root_str
        self._waiting.clear()
        before = list(self._order)
        self.list.blockSignals(True)
        try:
            self.list.clear()
            by_id: dict[int, QListWidgetItem] = {}
            if people is not None:
                if people.named:
                    self._add_header(NAMED)
                    for row in people.named:
                        by_id[row.id] = self._add_row(row)
                if people.groups:
                    self._add_header(TO_NAME.format(n=len(people.groups)))
                    for row in people.groups:
                        by_id[row.id] = self._add_row(row)
            self._order = [pid for pid in before if pid in by_id]
            for pid in self._order:
                by_id[pid].setSelected(True)
        finally:
            self.list.blockSignals(False)
        self._apply_search()
        if self._order != before:
            self.selection_changed.emit(list(self._order))

    def _add_header(self, text: str) -> None:
        item = QListWidgetItem()
        item.setData(HEADER_ROLE, text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.list.addItem(item)

    def _add_row(self, row: model.PersonRow) -> QListWidgetItem:
        item = QListWidgetItem(row.name or UNNAMED)
        item.setData(PERSON_ROLE, row)
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        tip = f"{row.photos:,} photos"
        if row.suggestions:
            tip += f" · {row.suggestions:,} suggestions"
        item.setToolTip(tip)
        self.list.addItem(item)
        if row.cover is not None:
            self._ask_cover(item, row.cover)
        return item

    def _ask_cover(self, item: QListWidgetItem, tile: model.FaceTile) -> None:
        cached = self._covers.get(tile.face_id)
        if cached is not None:
            item.setData(COVER_ROLE, cached)
            return
        crops = getattr(self.ctx, "face_crops", None)
        if crops is None or not self._root:
            return
        self._waiting.setdefault(tile.face_id, []).append(item)
        crops.request(
            Path(self._root),
            tile.face_id,
            tile.relpath,
            tile.ext,
            tile.bbox,
            tile.img_size,
        )

    def _on_crop(self, root: str, face_id: int, path: str) -> None:
        if root != self._root or face_id not in self._waiting:
            return
        items = self._waiting.pop(face_id)
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            return
        cover = _round(pixmap)
        self._covers[face_id] = cover
        for item in items:
            item.setData(COVER_ROLE, cover)

    # ── search ──────────────────────────────────────────────────────────────
    def _apply_search(self, *_args) -> None:
        wanted = self.search.text().strip().lower()
        header_row = None
        shown_in_section = False
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(HEADER_ROLE) is not None:
                if header_row is not None:
                    self.list.setRowHidden(header_row, not shown_in_section)
                header_row, shown_in_section = i, False
                continue
            row = item.data(PERSON_ROLE)
            match = not wanted or wanted in (row.name or "").lower()
            self.list.setRowHidden(i, not match)
            shown_in_section = shown_in_section or match
        if header_row is not None:
            self.list.setRowHidden(header_row, not shown_in_section)

    # ── selection ───────────────────────────────────────────────────────────
    def selected_ids(self) -> list[int]:
        return list(self._order)

    def _on_selection(self) -> None:
        now = [
            item.data(PERSON_ROLE).id
            for item in self.list.selectedItems()
            if item.data(PERSON_ROLE) is not None
        ]
        kept = [pid for pid in self._order if pid in now]
        self._order = kept + [pid for pid in now if pid not in kept]
        self.selection_changed.emit(list(self._order))

    # ── merge by drop ───────────────────────────────────────────────────────
    def _on_drop(self, item: QListWidgetItem) -> None:
        row = item.data(PERSON_ROLE)
        if row is None:
            return
        srcs = [pid for pid in self._order if pid != row.id]
        if srcs:
            self.merge_requested.emit(row.id, srcs)

    # ── context menu ────────────────────────────────────────────────────────
    def menu_for(self, item: QListWidgetItem) -> QMenu:
        row: model.PersonRow = item.data(PERSON_ROLE)
        menu = QMenu(self)
        menu.addAction("Rename", lambda: self.rename_requested.emit(row.id))
        merge = menu.addAction("Merge", lambda: self._merge_selected(row.id))
        merge.setEnabled(len(self._order) >= 2)
        split = menu.addAction("Split", lambda: self.split_requested.emit(row.id))
        split.setEnabled(paths.faces_available())
        menu.addSeparator()
        if row.name:
            menu.addAction("Delete", lambda: self.delete_requested.emit(row.id))
        else:
            menu.addAction("Hide", lambda: self.hide_requested.emit(row.id, True))
        return menu

    def _merge_selected(self, clicked: int) -> None:
        if len(self._order) < 2:
            return
        dst = clicked if clicked in self._order else self._order[0]
        self.merge_requested.emit(dst, [pid for pid in self._order if pid != dst])

    def _on_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or item.data(PERSON_ROLE) is None:
            return
        menu = self.menu_for(item)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.exec(self.list.viewport().mapToGlobal(pos))
