"""Library area: folder tree, filter panel, and a lazily thumbnailed grid
grouped under headings that follow the sort (month, folder or size band).

Catalog queries run on worker threads (``RepoQuery``); thumbnails are asked
for only for the rows in view, after the grid settles.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path, PurePosixPath

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QFont,
    QPainter,
    QPalette,
    QPixmap,
    QPolygonF,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui import theme
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.filters_panel import PANEL_WIDTH, FilterPanel
from smart_gallery.gui.library.grid import GroupedGrid
from smart_gallery.gui.library.model import (
    FolderNode,
    LibraryQuery,
    LibraryRow,
    folder_tree,
    group_rows,
    library_rows,
    library_stats,
)
from smart_gallery.gui.library.viewer import MediaViewer, format_duration, format_size
from smart_gallery.gui.mirror.menu import MirrorButton, peer_label
from smart_gallery.mirror.types import key as relpath_key
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.organize.filters import is_query_empty

ICON_EDGE = 150
GRID_SIZE = QSize(ICON_EDGE + 20, ICON_EDGE + 40)
THUMB_DEBOUNCE_MS = 120
PIXMAP_CACHE_SIZE = 600  # ~55 MB of tile pixmaps
TREE_INDENT = theme.TREE_INDENT
# (label, sort, descending by default): newest, A to Z, largest first.
SORTS = (("Date taken", "date", True), ("Name", "name", False), ("Size", "size", True))
ASCENDING_ARROW = "↑"
DESCENDING_ARROW = "↓"
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
HIDE_FILTERS_TEXT = "«"
HIDE_FILTERS_TIP = "Hide the filters"
SHOW_FILTERS_TIP = "Show the filters"
FILTERS_STRIP_TEXT = "Filters"
STRIP_WIDTH = 28

NO_GALLERY = "Open or create a gallery to browse it here."
DISCONNECTED = "Drive not connected. Plug in {label} to browse it."
NOTHING = "Nothing matches this folder and the current filters."
READ_ERROR = "Could not read the catalog: {error}"
OFFLOADED_TIP = "On {label} only"
OFFLOADED_OPACITY = 0.35

_TILE_BG = theme.qcolor(theme.TILE_BACK)
_TILE_FG = theme.qcolor(theme.TILE_GLYPH)
_placeholders: dict[tuple[str, str], QPixmap] = {}


def _placeholder(kind: str, text: str = "") -> QPixmap:
    """Tile shown until a thumbnail arrives: a photo glyph, a play sign with
    the video's duration, or the file extension."""
    key = (kind, text)
    cached = _placeholders.get(key)
    if cached is not None:
        return cached
    edge = ICON_EDGE
    pixmap = QPixmap(edge, edge)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_TILE_BG)
    p.drawRoundedRect(QRectF(2, 2, edge - 4, edge - 4), 8, 8)
    p.setBrush(_TILE_FG)
    if kind == "photo":
        p.drawEllipse(QRectF(edge * 0.58, edge * 0.25, edge * 0.14, edge * 0.14))
        hills = [(0.2, 0.72), (0.42, 0.42), (0.58, 0.6), (0.68, 0.5), (0.82, 0.72)]
        p.drawPolygon(QPolygonF([QPointF(x * edge, y * edge) for x, y in hills]))
    elif kind == "video":
        tri = [(0.4, 0.32), (0.4, 0.68), (0.68, 0.5)]
        p.drawPolygon(QPolygonF([QPointF(x * edge, y * edge) for x, y in tri]))
    if text:
        font = QFont(p.font())
        font.setPointSize(11 if kind == "video" else 16)
        font.setBold(True)
        p.setFont(font)
        p.setPen(_TILE_FG)
        if kind == "video":
            box = QRectF(0, edge * 0.72, edge - 12, edge * 0.2)
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        else:
            box = QRectF(0, 0, edge, edge)
            align = Qt.AlignmentFlag.AlignCenter
        p.drawText(box, align, text)
    p.end()
    _placeholders[key] = pixmap
    return pixmap


_BADGE_BG = theme.qcolor(theme.BADGE_BACK)
_BADGE_FG = theme.qcolor(theme.BADGE_TEXT)


def _with_video_badge(frame: QPixmap, duration: str) -> QPixmap:
    """``frame`` with a play sign in the middle and ``duration`` in the
    bottom-right corner, so a video tile still reads as a video."""
    tile = QPixmap(frame)
    w, h = tile.width(), tile.height()
    p = QPainter(tile)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    radius = min(w, h) * 0.16
    cx, cy = w / 2, h / 2
    p.setBrush(_BADGE_BG)
    p.drawEllipse(QPointF(cx, cy), radius, radius)
    p.setBrush(_BADGE_FG)
    side = radius * 0.9
    tri = [
        QPointF(cx - side * 0.38, cy - side * 0.55),
        QPointF(cx - side * 0.38, cy + side * 0.55),
        QPointF(cx + side * 0.62, cy),
    ]
    p.drawPolygon(QPolygonF(tri))
    if duration:
        font = QFont(p.font())
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        metrics = p.fontMetrics()
        pad = 4
        box = QRectF(
            0, 0, metrics.horizontalAdvance(duration) + pad * 2, metrics.height() + 2
        )
        box.moveBottomRight(QPointF(w - 6, h - 6))
        p.setBrush(_BADGE_BG)
        p.drawRoundedRect(box, 4, 4)
        p.setPen(_BADGE_FG)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, duration)
    p.end()
    return tile


_faded: dict[int, QPixmap] = {}


def _faded_placeholder(row: LibraryRow) -> QPixmap:
    """The row's placeholder drawn faint: the file is kept on a mirror only."""
    tile = _placeholder_for(row)
    cached = _faded.get(tile.cacheKey())
    if cached is not None:
        return cached
    faded = QPixmap(tile.size())
    faded.fill(Qt.GlobalColor.transparent)
    p = QPainter(faded)
    p.setOpacity(OFFLOADED_OPACITY)
    p.drawPixmap(0, 0, tile)
    p.end()
    _faded[tile.cacheKey()] = faded
    return faded


def _placeholder_for(row: LibraryRow) -> QPixmap:
    if row.media_type == "video":
        return _placeholder("video", format_duration(row.duration_ms))
    if row.media_type == "image":
        return _placeholder("photo")
    return _placeholder("file", (row.ext or "").lstrip(".").upper()[:5])


class LibraryGridModel(QAbstractListModel):
    """The grid's rows; tiles show a cached thumbnail pixmap or a placeholder.

    Pixmaps are keyed by (id, mtime_ns), so a reload keeps the ones whose
    file did not change, and only the most recently shown are kept.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[LibraryRow] = []
        self._row_of: dict[int, int] = {}
        self._pixmaps: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._missing: set[tuple] = set()  # no thumbnail: keep the placeholder
        self.holders: dict[int, str] = {}  # offloaded row id -> mirror label

    @staticmethod
    def _key(row: LibraryRow) -> tuple:
        return row.id, row.mtime_ns

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return PurePosixPath(row.relpath).name
        if role == Qt.ItemDataRole.ToolTipRole:
            if row.offloaded:
                label = self.holders.get(row.id) or "the mirror"
                return OFFLOADED_TIP.format(label=label)
            return row.relpath
        if role == Qt.ItemDataRole.ForegroundRole and row.offloaded:
            return QApplication.palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
            )
        if role == Qt.ItemDataRole.DecorationRole:
            if row.offloaded:
                return _faded_placeholder(row)
            key = self._key(row)
            pixmap = self._pixmaps.get(key)
            if pixmap is None:
                return _placeholder_for(row)
            self._pixmaps.move_to_end(key)
            return pixmap
        return None

    def set_rows(
        self, rows: list[LibraryRow], holders: dict[int, str] | None = None
    ) -> None:
        self.beginResetModel()
        self.rows, self._row_of = [], {}
        self.holders = dict(holders or {})
        self.endResetModel()
        if rows:
            self.beginInsertRows(QModelIndex(), 0, len(rows) - 1)
            self.rows = list(rows)
            self._row_of = {r.id: i for i, r in enumerate(self.rows)}
            self.endInsertRows()

    def forget_thumbnails(self) -> None:
        """Drop every pixmap: ids from another catalog mean other files."""
        self._pixmaps.clear()
        self._missing.clear()

    def needs_thumbnail(self, row: LibraryRow) -> bool:
        key = self._key(row)
        return (
            row.media_type in ("image", "video")
            and not row.offloaded  # not on this drive: nothing to read
            and key not in self._pixmaps
            and key not in self._missing
        )

    def set_thumbnail(self, media_id: int, path: str) -> bool:
        """Store the thumbnail at ``path`` ("" = none); False if not a row here."""
        i = self._row_of.get(media_id)
        if i is None:
            return False
        row = self.rows[i]
        key = self._key(row)
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self._missing.add(key)
        else:
            pixmap = pixmap.scaled(
                ICON_EDGE,
                ICON_EDGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            if row.media_type == "video":
                pixmap = _with_video_badge(pixmap, format_duration(row.duration_ms))
            self._pixmaps[key] = pixmap
            self._pixmaps.move_to_end(key)
            while len(self._pixmaps) > PIXMAP_CACHE_SIZE:
                self._pixmaps.popitem(last=False)
        index = self.index(i)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
        return True


def _holders(repo) -> dict:
    """Offloaded relpath key -> the ``Peer`` that keeps the file. No Qt."""
    pairing = repo.get_pairing()
    if pairing is None:
        return {}
    return {
        relpath_key(rel): peer
        for peer in pairing.peers
        for rel in repo.offloaded_on(peer.uuid)
    }


def _tree_items(node: FolderNode, label: str) -> QStandardItem:
    item = QStandardItem(f"{label} ({node.count:,})")
    item.setData(node.path, PATH_ROLE)
    item.setEditable(False)
    for child in node.children:
        item.appendRow(_tree_items(child, child.name))
    return item


class CollapsedStrip(QAbstractButton):
    """A slim bar with its label running bottom to top; it stands in for a
    hidden panel and brings it back when clicked."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(STRIP_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(STRIP_WIDTH, self.fontMetrics().horizontalAdvance(self.text()))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        back = palette.color(QPalette.ColorRole.Button)
        if self.isDown():
            back = back.darker(115)
        elif self.underMouse():
            back = back.lighter(125)
        painter.setPen(palette.color(QPalette.ColorRole.Mid))
        painter.setBrush(back)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4
        )
        painter.setPen(palette.color(QPalette.ColorRole.ButtonText))
        painter.translate(0, self.height())
        painter.rotate(-90)
        # Rotated, x runs up the bar and y across it; the label starts at the top.
        width = self.fontMetrics().horizontalAdvance(self.text())
        top = QRectF(self.height() - width - 12, 0, width, self.width())
        painter.drawText(top, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class LibraryView(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.viewer: MediaViewer | None = None
        self._root: Path | None = None  # drive root of the rows in the grid
        self._folder = ""
        self._building_tree = False
        self._tree_query = RepoQuery(ctx, self)
        self._rows_query = RepoQuery(ctx, self)

        # Folder tree.
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(TREE_INDENT)
        self.tree_model = QStandardItemModel(self)
        self.tree.setModel(self.tree_model)
        self.subfolders = QCheckBox("Include subfolders")
        self.subfolders.setChecked(True)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.tree, 1)
        left_layout.addWidget(self.subfolders)

        self.filters = FilterPanel(ctx)

        # Header, grid and message.
        self.stats = QLabel()
        self.sort = QComboBox()
        for label, value, _descending in SORTS:
            self.sort.addItem(label, value)
        self.descending = QToolButton()
        self.descending.setCheckable(True)
        # Tool buttons keep a fixed, shorter height; let it fill the header row
        # so it lines up with the sort box.
        self.descending.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.descending.setChecked(SORTS[0][2])
        self._show_direction(SORTS[0][2])
        # The filter panel hides itself with « in its top row; while hidden, a
        # slim strip in its place brings it back.
        self.hide_filters_btn = QToolButton()
        self.hide_filters_btn.setText(HIDE_FILTERS_TEXT)
        self.hide_filters_btn.setToolTip(HIDE_FILTERS_TIP)
        self.hide_filters_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.filters.top_row.addWidget(self.hide_filters_btn)
        self.filters_strip = CollapsedStrip(FILTERS_STRIP_TEXT)
        self.filters_strip.setToolTip(SHOW_FILTERS_TIP)
        self.filters_strip.setVisible(False)
        # The main window moves these to the right of its page header; a view
        # shown on its own keeps them above the grid.
        self.header_controls = QWidget()
        header = QHBoxLayout(self.header_controls)
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.stats)
        header.addSpacing(16)
        header.addWidget(QLabel("Sort by"))
        header.addWidget(self.sort)
        header.addWidget(self.descending)
        header.addSpacing(16)
        self.mirror_btn = MirrorButton(ctx)
        header.addWidget(self.mirror_btn)

        self.grid = GroupedGrid()
        self.grid.setIconSize(QSize(ICON_EDGE, ICON_EDGE))
        self.grid.setGridSize(GRID_SIZE)
        self.grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.grid_model = LibraryGridModel(self)
        self.grid.setModel(self.grid_model)
        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.message)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.header_controls)
        right_layout.addWidget(self.stack, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.filters)
        splitter.addWidget(self.filters_strip)
        splitter.addWidget(right)
        splitter.setStretchFactor(3, 1)
        splitter.setCollapsible(2, False)  # else the strip shows at width 0
        splitter.setSizes([220, PANEL_WIDTH, 0, 800])
        QVBoxLayout(self).addWidget(splitter)

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(THUMB_DEBOUNCE_MS)
        self._thumb_timer.timeout.connect(self._request_visible)

        self.tree.selectionModel().currentChanged.connect(self._on_tree_current)
        self.subfolders.toggled.connect(self.reload_rows)
        self.sort.currentIndexChanged.connect(self._on_sort_changed)
        self.descending.toggled.connect(self._show_direction)
        self.descending.toggled.connect(self.reload_rows)
        self.hide_filters_btn.clicked.connect(lambda: self.set_filters_shown(False))
        self.filters_strip.clicked.connect(lambda: self.set_filters_shown(True))
        self.grid.doubleClicked.connect(self.open_viewer)
        self.grid_model.rowsInserted.connect(self._schedule_thumbs)
        self.grid.groups_toggled.connect(self._schedule_thumbs)
        self.grid.verticalScrollBar().valueChanged.connect(self._schedule_thumbs)
        self.grid.viewport().installEventFilter(self)
        self._tree_query.finished.connect(self._on_tree)
        self._tree_query.failed.connect(self._on_failed)
        self._rows_query.finished.connect(self._on_rows)
        self._rows_query.failed.connect(self._on_failed)
        ctx.gallery_changed.connect(self._on_gallery_changed)
        ctx.filters_changed.connect(self.reload_rows)
        ctx.catalog_updated.connect(self.reload)
        if ctx.thumbs is not None:
            ctx.thumbs.ready.connect(self._on_thumb_ready)

        self.reload()

    def set_filters_shown(self, shown: bool) -> None:
        """Show the filter panel, or hide it behind the slim Filters strip."""
        self.filters.setVisible(shown)
        self.filters_strip.setVisible(not shown)

    # ── loading ─────────────────────────────────────────────────────────────
    def reload(self, *_args) -> None:
        """Re-read the folder tree and the rows."""
        if self.ctx.current is None:
            self._tree_query.cancel()
            self._rows_query.cancel()
            self._show_message(NO_GALLERY)
            return
        self._tree_query.run(folder_tree)
        self.reload_rows()

    def reload_rows(self, *_args) -> None:
        if self.ctx.current is None:
            return
        filters = self.ctx.filters
        q = LibraryQuery(
            folder=self._folder,
            recursive=self.subfolders.isChecked(),
            filters=None if is_query_empty(filters) else filters,
            sort=self.sort.currentData(),
            descending=self.descending.isChecked(),
        )

        def load(repo):
            rows = library_rows(repo, q)
            holders = _holders(repo) if any(r.offloaded for r in rows) else {}
            return repo.drive_root, rows, library_stats(repo, q), q.sort, holders

        self._rows_query.run(load)

    def _on_sort_changed(self, index: int) -> None:
        """Switch to the new sort's natural direction, then reload once."""
        descending = SORTS[index][2]
        self.grid.expand_all()  # another sort's headings name other groups
        self.descending.blockSignals(True)
        self.descending.setChecked(descending)
        self.descending.blockSignals(False)
        self._show_direction(descending)
        self.reload_rows()

    def _show_direction(self, descending: bool) -> None:
        self.descending.setText(DESCENDING_ARROW if descending else ASCENDING_ARROW)
        direction = "Descending" if descending else "Ascending"
        self.descending.setToolTip(f"{direction}. Click to reverse.")

    def select_folder(self, path: str) -> None:
        """Show ``path`` (POSIX, relative to the root; "" for everything)."""
        item = self._find_folder(path)
        if item is not None:
            self.tree.setCurrentIndex(item.index())  # reloads via the tree signal
        if path != self._folder:
            self._folder = path
            self.reload_rows()

    def _find_folder(self, path: str) -> QStandardItem | None:
        matches = self.tree_model.match(
            self.tree_model.index(0, 0),
            PATH_ROLE,
            path,
            1,
            Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive,
        )
        return self.tree_model.itemFromIndex(matches[0]) if matches else None

    def _on_gallery_changed(self, _gallery) -> None:
        if self.ctx.thumbs is not None:
            self.ctx.thumbs.cancel_pending()
        self._root = None
        self._folder = ""
        self.grid_model.set_rows([])
        self.grid_model.forget_thumbnails()
        self.tree_model.clear()
        self.stats.clear()
        self.reload()

    def _on_tree(self, node: FolderNode) -> None:
        label = getattr(self.ctx.current, "label", "") or "All items"
        self._building_tree = True
        try:
            self.tree_model.clear()
            self.tree_model.appendRow(_tree_items(node, label))
            self.tree.expand(self.tree_model.index(0, 0))
            item = self._find_folder(self._folder)
            if item is None:  # the folder is gone: fall back to everything
                item = self.tree_model.item(0)
            self.tree.setCurrentIndex(item.index())
        finally:
            self._building_tree = False
        path = item.data(PATH_ROLE)
        if path != self._folder:
            self._folder = path
            self.reload_rows()

    def _on_tree_current(self, current: QModelIndex, _previous) -> None:
        if self._building_tree or not current.isValid():
            return
        path = current.data(PATH_ROLE) or ""
        if path != self._folder:
            self._folder = path
            self.reload_rows()

    def _on_rows(self, result) -> None:
        root, rows, (count, size), sort, holders = result
        if self._root is not None and Path(root) != self._root:
            self.grid_model.forget_thumbnails()
        self._root = Path(root)
        self.grid.set_groups(group_rows(rows, sort))
        self.grid_model.set_rows(rows, self._holder_labels(rows, holders))
        noun = "item" if count == 1 else "items"
        self.stats.setText(f"{count:,} {noun} · {format_size(size)}")
        if rows:
            self.stack.setCurrentWidget(self.grid)
        else:
            self._show_message(NOTHING, clear=False)

    def _holder_labels(self, rows: list[LibraryRow], holders: dict) -> dict:
        """Offloaded row id -> label of the mirror that keeps its only copy."""
        if not holders:
            return {}
        store = self.ctx.galleries
        galleries = store.all() if store is not None else []
        labels = {peer.uuid: peer_label(galleries, peer) for peer in holders.values()}
        return {
            r.id: labels[holders[relpath_key(r.relpath)].uuid]
            for r in rows
            if r.offloaded and relpath_key(r.relpath) in holders
        }

    def _on_failed(self, error) -> None:
        if self.ctx.current is None:
            text = NO_GALLERY
        elif isinstance(error, FileNotFoundError):
            label = getattr(self.ctx.current, "label", "") or str(self.ctx.current.root)
            text = DISCONNECTED.format(label=label)
        else:
            text = READ_ERROR.format(error=error)
        self._show_message(text)

    def _show_message(self, text: str, clear: bool = True) -> None:
        if clear:
            self.grid_model.set_rows([])
            self.tree_model.clear()
            self.stats.clear()
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)

    # ── thumbnails ──────────────────────────────────────────────────────────
    def eventFilter(self, watched, event) -> bool:
        if watched is self.grid.viewport() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self._schedule_thumbs()
        return super().eventFilter(watched, event)

    def _schedule_thumbs(self, *_args) -> None:
        self._thumb_timer.start()

    def _request_visible(self) -> None:
        thumbs = self.ctx.thumbs
        if thumbs is None or self._root is None or not self.grid.isVisible():
            return
        visible = self.grid.visible_rows()
        if not visible:
            return
        # Rows scrolled past are still queued; drop them so what is on screen
        # now loads first, not after them.
        thumbs.cancel_pending()
        for i in visible:
            row = self.grid_model.rows[i]
            if self.grid_model.needs_thumbnail(row):
                thumbs.request(
                    self._root,
                    row.id,
                    row.mtime_ns,
                    row.relpath,
                    row.ext,
                    row.media_type,
                )

    def _on_thumb_ready(self, root: str, media_id: int, path: str) -> None:
        if self._root is None or root != str(self._root):
            return  # another gallery's request
        self.grid_model.set_thumbnail(media_id, path)

    # ── viewer ──────────────────────────────────────────────────────────────
    def open_viewer(self, index: QModelIndex) -> None:
        if not index.isValid() or self._root is None:
            return
        if self.viewer is not None:
            self.viewer.close()
            self.viewer.deleteLater()
        self.viewer = MediaViewer(
            self.ctx, self._root, list(self.grid_model.rows), index.row(), self
        )
        self.viewer.show()
