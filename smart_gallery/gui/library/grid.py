"""Photo grid that lays its tiles out in justified rows, with a heading line
above each group of rows; clicking a heading collapses or expands its group.

``QListView`` cannot break its flow for a full-width heading or give each
tile its own width, so this view lays the tiles out itself. Each open group
is cut into rows by ``justify`` (the tiles of a row share one height and a
full row spans the width), and the result is kept in flat tables: each
visual row's top, height and first and last model rows, and each tile's x
and width. Painting, hit testing and selection look rows up by bisect, so
they only touch the rows in view.

While a group scrolls under the top edge, its heading stays pinned there
until the next heading pushes it out.

A new width or row height keeps the user's place: the tile at the top of the
view (or the current tile, while it is in view) stays where it was. Width
changes are laid out at most once per ``RELAYOUT_DELAY_MS`` (a window drag
sends many); until then the old tables are painted, and any geometry query
lays out first, so callers always see the current width.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

from PySide6.QtCore import (
    QItemSelection,
    QModelIndex,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QTimer,
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
from smart_gallery.gui.library.justify import (
    GAP,
    MAX_ASPECT,
    MIN_ASPECT,
    ROW_HEIGHT_DEFAULT,
    ROW_HEIGHT_MAX,
    ROW_HEIGHT_MIN,
    ROW_HEIGHT_STEP,
    justify,
)
from smart_gallery.gui.library.model import RowGroup

# Width / height of an index's tile; the grid reads it, the model provides it.
# Read only on modelReset, rowsInserted, rowsRemoved and layoutChanged: a
# dataChanged on this role does not lay the grid out again.
ASPECT_ROLE = Qt.ItemDataRole.UserRole + 20
FALLBACK_ASPECT = 1.5  # an index without a usable aspect: a 3:2 photo
HEADER_HEIGHT = 40
HEADER_PT = theme.HEADER_PT
HEADER_PAD = 6  # px left of the chevron
CHEVRON = 10  # px across the collapse chevron
CHEVRON_GAP = 8  # px between the chevron and the heading text
SIDE_PAD = 12  # px between the tiles and the grid's left and right edges
# Rows are never laid out narrower than this, so a tiny viewport cannot
# squeeze tiles down to nothing (they run off its right edge instead).
MIN_LAYOUT_WIDTH = ROW_HEIGHT_MIN
SCROLL_STEP = 40
WHEEL_NOTCH = 120  # angleDelta of one wheel click
RELAYOUT_DELAY_MS = 16  # width changes within this are laid out once


def _aspect(value) -> float:
    try:
        aspect = float(value)
    except (TypeError, ValueError):
        return FALLBACK_ASPECT
    if not math.isfinite(aspect) or aspect <= 0:
        return FALLBACK_ASPECT
    return min(MAX_ASPECT, max(MIN_ASPECT, aspect))


class GroupedGrid(QAbstractItemView):
    # A group was collapsed or expanded, so other tiles may now be in view.
    groups_toggled = Signal()
    row_height_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._target = ROW_HEIGHT_DEFAULT  # row height asked for
        self._groups: list[RowGroup] = []
        # Collapsed headings by label, so they stay collapsed across reloads.
        self._collapsed: set[str] = set()
        self._layout: list[RowGroup] = []  # groups that match the model
        self._starts: list[int] = []  # first model row of each group
        self._tops: list[int] = []  # content y of each group's heading
        self._header = HEADER_HEIGHT
        self._aspects: list[float] | None = None  # per model row, read once
        # One entry per visual row (open groups only), in order:
        self._row_tops: list[int] = []
        self._row_heights: list[int] = []
        self._row_starts: list[int] = []  # first model row in it
        self._row_ends: list[int] = []  # one past its last model row
        # One entry per model row (zero while its group is collapsed):
        self._xs: list[int] = []
        self._widths: list[int] = []
        self._laid_width = -1  # viewport width of the current tables
        self._height = 0
        # The width changed since the tables were made; they still match the
        # scroll offset, so the next layout can keep the user's place.
        self._stale = False
        self._painting = False
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(RELAYOUT_DELAY_MS)
        self._relayout_timer.timeout.connect(self._ensure_layout)
        self._wheel_rest = 0  # angleDelta not yet turned into a size step
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    # ── configuration ───────────────────────────────────────────────────────
    def row_height(self) -> int:
        return self._target

    def set_row_height(self, px: int) -> None:
        """The height rows aim for; full rows end up a little lower."""
        px = min(ROW_HEIGHT_MAX, max(ROW_HEIGHT_MIN, int(px)))
        if px == self._target:
            return
        self._target = px
        self._relayout(keep_place=True)
        self.row_height_changed.emit(px)

    def set_groups(self, groups: list[RowGroup]) -> None:
        """Headings for the model's rows; ignored until they cover them all."""
        self._groups = list(groups)
        self._relayout()

    def groups(self) -> list[RowGroup]:
        """The groups as laid out (one unnamed group if none match)."""
        self._ensure_layout()
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
                signal.disconnect(self._model_changed)
        super().setModel(model)
        if model is not None:
            for signal in self._layout_signals(model):
                signal.connect(self._model_changed)
        self._model_changed()

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

    def _model_changed(self, *_args) -> None:
        self._aspects = None
        self._relayout()

    def _read_aspects(self, n: int) -> list[float]:
        """Each row's aspect. Read only when the rows change: a resize or a
        new row height lays out the same shapes again."""
        if self._aspects is None or len(self._aspects) != n:
            model, root = self.model(), self.rootIndex()
            self._aspects = [
                _aspect(model.data(model.index(r, 0, root), ASPECT_ROLE))
                for r in range(n)
            ]
        return self._aspects

    def _ensure_layout(self) -> None:
        """Lay out now if a width change is waiting (not while painting:
        a paint shows the old tables until the timer runs)."""
        if self._stale and not self._painting:
            self._relayout(keep_place=True)

    def _place(self) -> tuple[int, int, int, int, bool] | None:
        """Where the user is, from the current tables: (model row, its
        viewport y, px into its visual row at that y, that row's height,
        whether it is the current tile).

        The current tile while any of it is below the pinned band, else the
        first row at the band's lower edge."""
        if not self._row_tops:
            return None
        offset = self.verticalOffset()
        page_top = offset + self._header
        current = self.currentIndex()
        if (
            current.isValid()
            and current.row() < len(self._xs)
            and not self._hidden_row(current.row())
        ):
            v = self._visual_row(current.row())
            top, height = self._row_tops[v], self._row_heights[v]
            if top + height > page_top and top < offset + self.viewport().height():
                return current.row(), top - offset, 0, height, True
        v = max(0, bisect_right(self._row_tops, page_top) - 1)
        if self._row_tops[v] + self._row_heights[v] <= page_top:
            v = min(v + 1, len(self._row_tops) - 1)  # in the gap below it
        top, height = self._row_tops[v], self._row_heights[v]
        return self._row_starts[v], self._header, page_top - top, height, False

    def _restore(self, place) -> None:
        """Scroll so ``place``'s row is back at its viewport y (the px into
        it scaled to the row's new height); the current tile stays in view."""
        row, view_y, into, old_height, is_current = place
        if row >= len(self._xs) or self._hidden_row(row):
            return
        v = self._visual_row(row)
        if into > 0:  # inside the row: the same share of it
            into = round(into * self._row_heights[v] / max(1, old_height))
        self.verticalScrollBar().setValue(self._row_tops[v] + into - view_y)
        if is_current:
            self.scrollTo(self.currentIndex())

    def _relayout(self, *_args, keep_place: bool = False) -> None:
        """Lay every tile out again; with ``keep_place`` (a new width or row
        height over the same rows) the tile in view stays in view."""
        place = self._place() if keep_place else None
        self._stale = False
        self._relayout_timer.stop()
        n = self._row_count()
        if sum(g.count for g in self._groups) == n:
            self._layout, self._header = list(self._groups), HEADER_HEIGHT
        else:  # stale or no headings: one block without a heading
            self._layout = [RowGroup(0, n, "")] if n else []
            self._header = 0
        aspects = self._read_aspects(n)
        self._laid_width = self.viewport().width()
        width = max(MIN_LAYOUT_WIDTH, self._laid_width - 2 * SIDE_PAD)
        self._starts, self._tops = [], []
        row_tops, row_heights, row_starts, row_ends = [], [], [], []
        xs, widths = [0] * n, [0] * n
        y = 0
        for g in self._layout:
            self._starts.append(g.start)
            self._tops.append(y)
            y += self._header
            if self._hidden(g):
                continue
            for row in justify(
                aspects[g.start : g.start + g.count], width, self._target
            ):
                first = g.start + row.start
                x = SIDE_PAD
                for i, w in enumerate(row.widths, first):
                    w = max(1, w)
                    xs[i] = x
                    widths[i] = w
                    x += w + GAP
                row_tops.append(y)
                row_heights.append(row.height)
                row_starts.append(first)
                row_ends.append(first + len(row.widths))
                y += row.height + GAP
        self._row_tops, self._row_heights = row_tops, row_heights
        self._row_starts, self._row_ends = row_starts, row_ends
        self._xs, self._widths = xs, widths
        self._height = y
        self.updateGeometries()
        if place is not None:
            self._restore(place)
        self.viewport().update()

    def _hidden(self, g: RowGroup) -> bool:
        return bool(self._header) and g.label in self._collapsed

    def _hidden_row(self, row: int) -> bool:
        gi = self._group_of(row)
        return gi >= 0 and self._hidden(self._layout[gi])

    def _group_of(self, row: int) -> int:
        return bisect_right(self._starts, row) - 1

    def _visual_row(self, row: int) -> int:
        """The visual row holding model ``row`` (which must be shown)."""
        return bisect_right(self._row_starts, row) - 1

    def _cell_rect(self, row: int) -> QRect:
        """Content-space tile of ``row`` (not scrolled); null if hidden."""
        gi = self._group_of(row)
        if gi < 0 or row >= len(self._xs) or self._hidden(self._layout[gi]):
            return QRect()
        v = self._visual_row(row)
        return QRect(
            self._xs[row], self._row_tops[v], self._widths[row], self._row_heights[v]
        )

    def _rows_in(self, rect: QRect):
        """Rows whose tiles intersect ``rect`` (content space), in order."""
        top, bottom = rect.top(), rect.bottom()
        left, right = rect.left(), rect.right()
        first = max(0, bisect_right(self._row_tops, top) - 1)
        for v in range(first, len(self._row_tops)):
            row_top = self._row_tops[v]
            if row_top > bottom:
                break
            if row_top + self._row_heights[v] <= top:
                continue  # the row above the rect's top edge
            for row in range(self._row_starts[v], self._row_ends[v]):
                x = self._xs[row]
                if x > right:
                    break
                if x + self._widths[row] > left:
                    yield row

    def _content_viewport(self) -> QRect:
        return self.viewport().rect().translated(0, self.verticalOffset())

    def visible_rows(self) -> list[int]:
        """Rows with a tile at least partly in view, in order. Not a range: a
        collapsed group between two open ones has rows but no tiles."""
        self._ensure_layout()
        return list(self._rows_in(self._content_viewport()))

    def _pinned(self) -> tuple[int, int]:
        """The group whose heading is pinned at the top and the heading's
        viewport y (0, or less while the next heading pushes it up); -1 when
        no heading has scrolled under the top edge."""
        if not self._header:
            return -1, 0
        offset = self.verticalOffset()
        gi = bisect_right(self._tops, offset) - 1
        if gi < 0 or self._tops[gi] >= offset:
            return -1, 0
        y = 0
        if gi + 1 < len(self._tops):
            y = min(0, self._tops[gi + 1] - offset - self._header)
        return gi, y

    def _in_pinned(self, point: QPoint) -> int:
        """The pinned group if viewport ``point`` is on its heading, else -1."""
        gi, top = self._pinned()
        if gi >= 0 and top <= point.y() < top + self._header:
            return gi
        return -1

    def heading_at(self, point: QPoint) -> int:
        """Index of the group whose heading is at viewport ``point``, or -1."""
        self._ensure_layout()
        if not self._header:
            return -1
        pinned = self._in_pinned(point)
        if pinned >= 0:
            return pinned
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
        if self.viewport().width() == self._laid_width:
            self._stale = False  # back to the width it was laid out for
            self._relayout_timer.stop()
            self.updateGeometries()  # only taller or shorter: same rows
        else:
            self._stale = True
            if not self._relayout_timer.isActive():
                self._relayout_timer.start()

    def wheelEvent(self, event) -> None:
        """Ctrl + wheel changes the row height (up: bigger) instead of
        scrolling; small touchpad deltas add up to one step per notch."""
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._wheel_rest = 0
            super().wheelEvent(event)
            return
        dy = event.angleDelta().y()
        if dy and (dy > 0) != (self._wheel_rest > 0):
            self._wheel_rest = 0  # turned the other way
        self._wheel_rest += dy
        steps = int(self._wheel_rest / WHEEL_NOTCH)
        if steps:
            self._wheel_rest -= steps * WHEEL_NOTCH
            self.set_row_height(self._target + steps * ROW_HEIGHT_STEP)
        event.accept()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if self._header:
            # The scroll moved the pinned heading's pixels with the tiles;
            # repaint the strip it was in and the one it is in now.
            band = QRect(0, 0, self.viewport().width(), self._header + abs(dy))
            self.viewport().update(band)

    # ── QAbstractItemView ───────────────────────────────────────────────────
    def visualRect(self, index: QModelIndex) -> QRect:
        self._ensure_layout()
        if not index.isValid() or index.row() >= self._row_count():
            return QRect()
        rect = self._cell_rect(index.row())
        if rect.isNull():
            return QRect()
        return rect.translated(0, -self.verticalOffset())

    def indexAt(self, point: QPoint) -> QModelIndex:
        self._ensure_layout()
        model = self.model()
        if model is None or self._in_pinned(point) >= 0:
            return QModelIndex()  # the pinned heading covers the tiles
        x, y = point.x(), point.y() + self.verticalOffset()
        v = bisect_right(self._row_tops, y) - 1
        if v < 0 or y >= self._row_tops[v] + self._row_heights[v]:
            return QModelIndex()
        lo, hi = self._row_starts[v], self._row_ends[v]
        row = bisect_right(self._xs, x, lo, hi) - 1
        if row < lo or x >= self._xs[row] + self._widths[row]:
            return QModelIndex()  # side padding or a gap
        return model.index(row, 0, self.rootIndex())

    def scrollTo(
        self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible
    ) -> None:
        self._ensure_layout()
        if not index.isValid() or self.isIndexHidden(index):
            return
        rect = self._cell_rect(index.row())
        if rect.isNull():
            return
        bar = self.verticalScrollBar()
        page = self.viewport().height()
        # Clear of the pinned heading; on a group's first row, that is the
        # group's own heading coming along.
        top = rect.top() - self._header
        if hint == QAbstractItemView.ScrollHint.PositionAtTop:
            bar.setValue(top)
        elif hint == QAbstractItemView.ScrollHint.PositionAtBottom:
            bar.setValue(rect.bottom() - page + 1)
        elif hint == QAbstractItemView.ScrollHint.PositionAtCenter:
            bar.setValue(rect.center().y() - page // 2)
        elif top < bar.value():
            bar.setValue(top)
        elif rect.bottom() > bar.value() + page:
            # A tile taller than the room under the band keeps its top in view.
            bar.setValue(min(top, rect.bottom() - page + 1))

    def horizontalOffset(self) -> int:
        return 0

    def verticalOffset(self) -> int:
        return self.verticalScrollBar().value()

    def isIndexHidden(self, index: QModelIndex) -> bool:
        self._ensure_layout()
        return index.isValid() and self._hidden_row(index.row())

    def moveCursor(self, action, modifiers) -> QModelIndex:
        self._ensure_layout()
        if not self._row_tops:  # no rows, or every group collapsed
            return QModelIndex()
        first, last = self._row_starts[0], self._row_ends[-1] - 1
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
            target = self._row_step(row, -1)
        elif action == A.MoveDown:
            target = self._row_step(row, 1)
        elif action == A.MovePageUp:
            target = self._page_step(row, -1)
        elif action == A.MovePageDown:
            target = self._page_step(row, 1)
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

    def _nearest(self, v: int, centre: float) -> int:
        """The tile in visual row ``v`` whose centre is nearest ``centre``."""
        return min(
            range(self._row_starts[v], self._row_ends[v]),
            key=lambda r: abs(self._xs[r] + self._widths[r] / 2 - centre),
        )

    def _centre(self, row: int) -> float:
        return self._xs[row] + self._widths[row] / 2

    def _row_step(self, row: int, direction: int) -> int:
        """The tile below (1) or above (-1) ``row``, across groups."""
        v = self._visual_row(row) + direction
        if not 0 <= v < len(self._row_tops):
            return row
        return self._nearest(v, self._centre(row))

    def _page_step(self, row: int, direction: int) -> int:
        """The tile about one viewport height below (1) or above (-1)."""
        v = self._visual_row(row)
        page = max(1, self.viewport().height())
        last = len(self._row_tops) - 1
        if direction > 0:
            target = bisect_right(self._row_tops, self._row_tops[v] + page) - 1
            target = min(last, max(v + 1, target))
        else:
            target = bisect_left(self._row_tops, self._row_tops[v] - page)
            target = max(0, min(v - 1, target))
        return self._nearest(target, self._centre(row))

    def setSelection(self, rect: QRect, flags) -> None:
        model = self.model()
        if model is None:
            return
        self._ensure_layout()
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
            pinned = self._pinned()[0] == gi
            label = self._layout[gi].label
            self.set_collapsed(label, not self.is_collapsed(label))
            if pinned:
                # Its rows above the top edge changed; keep its heading at
                # the top instead of landing inside a later group.
                self.verticalScrollBar().setValue(self._tops[gi])
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
        # A width change still waiting is painted with the old tables: laying
        # out here would undo the coalescing (every resize step is painted).
        self._painting = True
        try:
            painter = QPainter(self.viewport())
            offset = self.verticalOffset()
            exposed = event.rect().translated(0, offset)
            model = self.model()
            if model is not None:
                self._paint_tiles(painter, exposed, model)
            if self._header:
                self._paint_headings(painter, exposed, offset)
        finally:
            self._painting = False

    def _paint_tiles(self, painter: QPainter, exposed: QRect, model) -> None:
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
        for gi in range(len(self._layout)):
            top = self._tops[gi]
            if top > exposed.bottom():
                break
            if top + self._header < exposed.top():
                continue
            self._paint_heading(painter, gi, top - offset)
        # The heading of the group under the top edge, over its tiles.
        gi, y = self._pinned()
        if gi >= 0 and y + self._header > exposed.top() - offset:
            viewport = self.viewport()
            band = QRect(0, y, viewport.width(), self._header)
            # The grid's own background, so the band reads as the page.
            back = viewport.palette().color(viewport.backgroundRole())
            painter.fillRect(band, back)
            self._paint_heading(painter, gi, y)

    def _paint_heading(self, painter: QPainter, gi: int, y: int) -> None:
        """Group ``gi``'s chevron, title and count with the top at viewport y."""
        g = self._layout[gi]
        width = self.viewport().width()
        text = self.palette().color(QPalette.ColorRole.Text)
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText)
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        left = HEADER_PAD + CHEVRON + CHEVRON_GAP
        self._paint_chevron(painter, y, self._hidden(g), muted)
        box = QRect(left, y, width - left - HEADER_PAD, self._header)
        painter.setFont(theme.serif_font(HEADER_PT))
        painter.setPen(text)
        used = painter.boundingRect(box, align, g.label)
        painter.drawText(box, align, g.label)
        painter.setFont(QFont(self.font()))
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
