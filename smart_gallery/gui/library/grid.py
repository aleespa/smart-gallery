"""Icon grid that shows a heading line above each group of rows; clicking a
heading collapses or expands its group.

``QListView`` cannot break its icon flow for a full-width heading, so this
view lays the tiles out itself. Every tile is the same size, so the layout
is arithmetic: a group's top plus its row and column, and painting, hit
testing and selection only touch the rows in view.
"""

from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import (
    QItemSelection,
    QModelIndex,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QCursor, QFont, QPainter, QPalette, QPolygonF, QRegion
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
    QStyle,
    QStyleOptionViewItem,
    QWidget,
)

from smart_gallery.gui import theme
from smart_gallery.gui.library.model import RowGroup

HEADER_HEIGHT = 40
HEADER_PT = 13
HEADER_PAD = 6  # px left of the chevron
CHEVRON = 10  # px across the collapse chevron
CHEVRON_GAP = 8  # px between the chevron and the heading text
TILE_INSET = 3  # px between a tile's selection box and its grid cell
SCROLL_STEP = 40


class GroupedGrid(QAbstractItemView):
    # A group was collapsed or expanded, so other tiles may now be in view.
    groups_toggled = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cell = QSize(100, 100)
        self._groups: list[RowGroup] = []
        # Collapsed headings by label, so they stay collapsed across reloads.
        self._collapsed: set[str] = set()
        self._layout: list[RowGroup] = []  # groups that match the model
        self._starts: list[int] = []
        self._tops: list[int] = []  # content y of each group's heading
        self._header = HEADER_HEIGHT
        self._cols = 1
        self._height = 0
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    # ── configuration ───────────────────────────────────────────────────────
    def setGridSize(self, size: QSize) -> None:
        self._cell = QSize(size)
        self._relayout()

    def gridSize(self) -> QSize:
        return QSize(self._cell)

    def set_groups(self, groups: list[RowGroup]) -> None:
        """Headings for the model's rows; ignored until they cover them all."""
        self._groups = list(groups)
        self._relayout()

    def groups(self) -> list[RowGroup]:
        """The groups as laid out (one unnamed group if none match)."""
        return list(self._layout)

    def is_collapsed(self, label: str) -> bool:
        return label in self._collapsed

    def set_collapsed(self, label: str, collapsed: bool) -> None:
        if collapsed:
            self._collapsed.add(label)
        else:
            self._collapsed.discard(label)
        self._toggled()

    def collapse_all(self) -> None:
        self._collapsed = {g.label for g in self._layout if g.label}
        self._toggled()

    def expand_all(self) -> None:
        self._collapsed.clear()
        self._toggled()

    def _toggled(self) -> None:
        self._relayout()
        self.groups_toggled.emit()

    def setModel(self, model) -> None:
        old = self.model()
        if old is not None:
            for signal in self._layout_signals(old):
                signal.disconnect(self._relayout)
        super().setModel(model)
        if model is not None:
            for signal in self._layout_signals(model):
                signal.connect(self._relayout)
        self._relayout()

    @staticmethod
    def _layout_signals(model):
        return (
            model.modelReset,
            model.rowsInserted,
            model.rowsRemoved,
            model.layoutChanged,
        )

    # ── layout ──────────────────────────────────────────────────────────────
    def _row_count(self) -> int:
        model = self.model()
        return 0 if model is None else model.rowCount(self.rootIndex())

    def _relayout(self, *_args) -> None:
        n = self._row_count()
        if sum(g.count for g in self._groups) == n:
            self._layout, self._header = list(self._groups), HEADER_HEIGHT
        else:  # stale or no headings: one block without a heading
            self._layout = [RowGroup(0, n, "")] if n else []
            self._header = 0
        self._cols = max(1, self.viewport().width() // max(1, self._cell.width()))
        self._starts, self._tops = [], []
        y = 0
        for g in self._layout:
            self._starts.append(g.start)
            self._tops.append(y)
            y += self._header + self._shown_rows(g) * self._cell.height()
        self._height = y
        self.updateGeometries()
        self.viewport().update()

    def _group_rows(self, g: RowGroup) -> int:
        return -(-g.count // self._cols)

    def _hidden(self, g: RowGroup) -> bool:
        return bool(self._header) and g.label in self._collapsed

    def _shown_rows(self, g: RowGroup) -> int:
        """Tile rows the group takes up: none while collapsed."""
        return 0 if self._hidden(g) else self._group_rows(g)

    def _group_of(self, row: int) -> int:
        return bisect_right(self._starts, row) - 1

    def _cell_rect(self, row: int) -> QRect:
        """Content-space grid cell of ``row`` (not scrolled); null if hidden."""
        gi = self._group_of(row)
        if gi < 0:
            return QRect()
        g = self._layout[gi]
        if self._hidden(g):
            return QRect()
        r, c = divmod(row - g.start, self._cols)
        w, h = self._cell.width(), self._cell.height()
        return QRect(c * w, self._tops[gi] + self._header + r * h, w, h)

    def _rows_in(self, rect: QRect):
        """Rows whose cells intersect ``rect`` (content space), in order."""
        h, w = self._cell.height(), self._cell.width()
        first_col = max(0, rect.left() // w)
        last_col = min(self._cols - 1, rect.right() // w)
        if last_col < first_col:
            return
        gi = max(0, bisect_right(self._tops, rect.top()) - 1)
        for gi in range(gi, len(self._layout)):
            top = self._tops[gi] + self._header
            if self._tops[gi] > rect.bottom():
                break
            g = self._layout[gi]
            first_row = max(0, (rect.top() - top) // h)
            last_row = min(self._shown_rows(g) - 1, (rect.bottom() - top) // h)
            for r in range(first_row, last_row + 1):
                for c in range(first_col, last_col + 1):
                    i = r * self._cols + c
                    if i < g.count:
                        yield g.start + i

    def _content_viewport(self) -> QRect:
        return self.viewport().rect().translated(0, self.verticalOffset())

    def visible_rows(self) -> list[int]:
        """Rows with a tile at least partly in view, in order. Not a range: a
        collapsed group between two open ones has rows but no tiles."""
        return list(self._rows_in(self._content_viewport()))

    def heading_at(self, point: QPoint) -> int:
        """Index of the group whose heading is at viewport ``point``, or -1."""
        if not self._header:
            return -1
        y = point.y() + self.verticalOffset()
        gi = bisect_right(self._tops, y) - 1
        if gi < 0 or y >= self._tops[gi] + self._header:
            return -1
        return gi

    def updateGeometries(self) -> None:
        bar = self.verticalScrollBar()
        page = self.viewport().height()
        bar.setRange(0, max(0, self._height - page))
        bar.setPageStep(page)
        bar.setSingleStep(SCROLL_STEP)
        super().updateGeometries()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    # ── QAbstractItemView ───────────────────────────────────────────────────
    def visualRect(self, index: QModelIndex) -> QRect:
        if not index.isValid() or index.row() >= self._row_count():
            return QRect()
        rect = self._cell_rect(index.row())
        if rect.isNull():
            return QRect()
        return rect.translated(0, -self.verticalOffset()).adjusted(
            TILE_INSET, TILE_INSET, -TILE_INSET, -TILE_INSET
        )

    def indexAt(self, point: QPoint) -> QModelIndex:
        x, y = point.x(), point.y() + self.verticalOffset()
        w, h = self._cell.width(), self._cell.height()
        gi = bisect_right(self._tops, y) - 1
        if gi < 0 or x < 0 or x // w >= self._cols:
            return QModelIndex()
        g = self._layout[gi]
        local = y - self._tops[gi] - self._header
        if local < 0:  # on the heading
            return QModelIndex()
        i = (local // h) * self._cols + x // w
        if i >= g.count or self._hidden(g):
            return QModelIndex()
        return self.model().index(g.start + i, 0, self.rootIndex())

    def scrollTo(
        self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible
    ) -> None:
        if not index.isValid() or self.isIndexHidden(index):
            return
        rect = self._cell_rect(index.row())
        bar = self.verticalScrollBar()
        page = self.viewport().height()
        top = rect.top()
        g = self._layout[self._group_of(index.row())]
        if index.row() - g.start < self._cols:
            top -= self._header  # bring the first row's heading along
        if hint == QAbstractItemView.ScrollHint.PositionAtTop:
            bar.setValue(top)
        elif hint == QAbstractItemView.ScrollHint.PositionAtBottom:
            bar.setValue(rect.bottom() - page + 1)
        elif hint == QAbstractItemView.ScrollHint.PositionAtCenter:
            bar.setValue(rect.center().y() - page // 2)
        elif top < bar.value():
            bar.setValue(top)
        elif rect.bottom() > bar.value() + page:
            bar.setValue(rect.bottom() - page + 1)

    def horizontalOffset(self) -> int:
        return 0

    def verticalOffset(self) -> int:
        return self.verticalScrollBar().value()

    def isIndexHidden(self, index: QModelIndex) -> bool:
        gi = self._group_of(index.row()) if index.isValid() else -1
        return gi >= 0 and self._hidden(self._layout[gi])

    def moveCursor(self, action, modifiers) -> QModelIndex:
        open_groups = [g for g in self._layout if not self._hidden(g)]
        if not open_groups:
            return QModelIndex()
        first = open_groups[0].start
        last = open_groups[-1].start + open_groups[-1].count - 1
        current = self.currentIndex()
        row = current.row()
        A = QAbstractItemView.CursorAction
        if not current.isValid() or self.isIndexHidden(current):
            target = first
        elif action in (A.MoveLeft, A.MovePrevious):
            target = self._step(row, -1)
        elif action in (A.MoveRight, A.MoveNext):
            target = self._step(row, 1)
        elif action == A.MoveUp:
            target = self._row_above(row)
        elif action == A.MoveDown:
            target = self._row_below(row)
        elif action in (A.MovePageUp, A.MovePageDown):
            step = self._row_above if action == A.MovePageUp else self._row_below
            target = row
            for _ in range(max(1, self.viewport().height() // self._cell.height())):
                target = step(target)
        elif action == A.MoveHome:
            target = first
        elif action == A.MoveEnd:
            target = last
        else:
            target = row
        return self.model().index(target, 0, self.rootIndex())

    def _open_neighbour(self, gi: int, direction: int) -> int:
        """The nearest open group before (-1) or after (1) group ``gi``, or -1."""
        gi += direction
        while 0 <= gi < len(self._layout):
            if not self._hidden(self._layout[gi]):
                return gi
            gi += direction
        return -1

    def _step(self, row: int, direction: int) -> int:
        """The next tile in reading order, skipping collapsed groups."""
        gi = self._group_of(row)
        g = self._layout[gi]
        if g.start <= row + direction < g.start + g.count:
            return row + direction
        other = self._open_neighbour(gi, direction)
        if other < 0:
            return row
        o = self._layout[other]
        return o.start if direction > 0 else o.start + o.count - 1

    def _row_above(self, row: int) -> int:
        gi = self._group_of(row)
        g = self._layout[gi]
        r, c = divmod(row - g.start, self._cols)
        if r > 0:
            return row - self._cols
        other = self._open_neighbour(gi, -1)
        if other < 0:
            return row
        prev = self._layout[other]
        last_row_start = (prev.count - 1) // self._cols * self._cols
        return prev.start + min(last_row_start + c, prev.count - 1)

    def _row_below(self, row: int) -> int:
        gi = self._group_of(row)
        g = self._layout[gi]
        r, c = divmod(row - g.start, self._cols)
        if r + 1 < self._group_rows(g):
            return g.start + min(row - g.start + self._cols, g.count - 1)
        other = self._open_neighbour(gi, 1)
        if other < 0:
            return row
        nxt = self._layout[other]
        return nxt.start + min(c, nxt.count - 1)

    def setSelection(self, rect: QRect, flags) -> None:
        model = self.model()
        if model is None:
            return
        content = rect.normalized().translated(0, self.verticalOffset())
        chosen = QItemSelection()
        for row in self._rows_in(content):
            index = model.index(row, 0, self.rootIndex())
            chosen.select(index, index)
        self.selectionModel().select(chosen, flags)

    def visualRegionForSelection(self, selection) -> QRegion:
        region = QRegion()
        for index in selection.indexes():
            region += self.visualRect(index)
        return region

    # ── headings ────────────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        gi = self.heading_at(event.position().toPoint())
        if gi >= 0 and event.button() == Qt.MouseButton.LeftButton:
            label = self._layout[gi].label
            self.set_collapsed(label, not self.is_collapsed(label))
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.heading_at(event.position().toPoint()) >= 0:
            # A quick second click on a heading toggles it back, as a press.
            self.mousePressEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.heading_at(event.position().toPoint()) >= 0:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self.heading_at(self.viewport().mapFrom(self, event.pos())) < 0:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        menu.addAction("Collapse all", self.collapse_all)
        menu.addAction("Expand all", self.expand_all)
        menu.exec(event.globalPos())

    # ── painting ────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        offset = self.verticalOffset()
        exposed = event.rect().translated(0, offset)
        if self._header:
            self._paint_headings(painter, exposed, offset)
        model = self.model()
        if model is None:
            return
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.decorationPosition = QStyleOptionViewItem.Position.Top
        option.decorationAlignment = Qt.AlignmentFlag.AlignCenter
        option.displayAlignment = Qt.AlignmentFlag.AlignCenter
        option.decorationSize = self.iconSize()
        option.showDecorationSelected = False
        option.textElideMode = self.textElideMode()
        selection = self.selectionModel()
        current = self.currentIndex()
        hover = QModelIndex()
        if self.viewport().underMouse():
            hover = self.indexAt(self.viewport().mapFromGlobal(QCursor.pos()))
        delegate = self.itemDelegate()
        base_state = option.state & ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )
        for row in self._rows_in(exposed):
            index = model.index(row, 0, self.rootIndex())
            option.rect = self.visualRect(index)
            state = base_state
            if selection is not None and selection.isSelected(index):
                state |= QStyle.StateFlag.State_Selected
            if index == hover:
                state |= QStyle.StateFlag.State_MouseOver
            if index == current and self.hasFocus():
                state |= QStyle.StateFlag.State_HasFocus
            option.state = state
            delegate.paint(painter, option, index)

    def _paint_headings(self, painter: QPainter, exposed: QRect, offset: int) -> None:
        width = self.viewport().width()
        title_font = theme.serif_font(HEADER_PT)
        count_font = QFont(self.font())
        text = self.palette().color(QPalette.ColorRole.Text)
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText)
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        left = HEADER_PAD + CHEVRON + CHEVRON_GAP
        for gi, g in enumerate(self._layout):
            top = self._tops[gi]
            if top > exposed.bottom():
                break
            if top + self._header < exposed.top():
                continue
            y = top - offset
            self._paint_chevron(painter, y, self._hidden(g), muted)
            box = QRect(left, y, width - left - HEADER_PAD, self._header)
            painter.setFont(title_font)
            painter.setPen(text)
            used = painter.boundingRect(box, align, g.label)
            painter.drawText(box, align, g.label)
            painter.setFont(count_font)
            painter.setPen(muted)
            count = box.adjusted(used.width() + 10, 0, 0, 0)
            painter.drawText(count, align, f"{g.count:,}")

    def _paint_chevron(self, painter: QPainter, y: int, collapsed: bool, color):
        """A small triangle: pointing right when collapsed, down when open."""
        x, cy, half = HEADER_PAD, y + self._header / 2, CHEVRON / 2
        if collapsed:
            points = [(x + 2, cy - half), (x + 2, cy + half), (x + CHEVRON - 1, cy)]
        else:
            points = [(x, cy - half / 2), (x + CHEVRON, cy - half / 2), (x + half, cy + half / 2 + 1)]  # fmt: skip
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(px, py) for px, py in points]))
        painter.restore()
