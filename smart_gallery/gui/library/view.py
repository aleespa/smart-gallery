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
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QPainter,
    QPalette,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui import icons, theme
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.collapsible_filters import CollapsibleFilters
from smart_gallery.gui.filters_panel import FilterPanel
from smart_gallery.gui.ui.metrics import FILTER_WIDTH, SIDEBAR_WIDTH
from smart_gallery.gui.ui.widgets import EmptyState, PanelFooter
from smart_gallery.gui.library.grid import ASPECT_ROLE, GroupedGrid
from smart_gallery.gui.library.photo_size import PhotoSizeSlider
from smart_gallery.gui.library.model import (
    FolderNode,
    LibraryQuery,
    LibraryRow,
    folder_tree,
    group_rows,
    library_rows,
    library_stats,
)
from smart_gallery.format import format_count, format_date
from smart_gallery.gui.library.tiles import (
    DURATION_ROLE,
    OFFLOADED_OPACITY,
    OFFLOADED_ROLE,
    PLACEHOLDER_ROLE,
    PhotoTileDelegate,
)
from smart_gallery.gui.library.viewer import MediaViewer, format_duration, format_size
from smart_gallery.gui.mirror.menu import MirrorButton, peer_label
from smart_gallery.mirror.types import key as relpath_key
from smart_gallery.gui.thumbs.placeholder import placeholder_for
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.organize.filters import is_query_empty

THUMB_DEBOUNCE_MS = 120
# Thumbnails kept, by their pixels (512 x 341 is 0.7 MB): enough for a full
# screen of the smallest tiles and a little scrolling either way.
PIXMAP_CACHE_BYTES = 200 * 1024 * 1024
TREE_INDENT = theme.TREE_INDENT
# (label, sort, descending by default): newest, A to Z, largest first.
SORTS = (("Date taken", "date", True), ("Name", "name", False), ("Size", "size", True))
# What the order is now, keyed by sort: (descending, ascending).
DIRECTION_TIPS = {
    "date": ("Newest first", "Oldest first"),
    "name": ("Z to A", "A to Z"),
    "size": ("Largest first", "Smallest first"),
}
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
IMPORT_TEXT = "Import…"
IMPORT_TIP = "Copy new photos and videos in from a card, phone or folder"
EXPORT_TEXT = "Export…"
EXPORT_TIP = "Copy the files these filters select to a folder"
EXPORT_SIZE = (720, 720)
IMPORT_SIZE = (860, 760)
STATE_FILE = "library.json"  # {"filters_shown": bool}

NO_GALLERY = "Open or create a gallery to browse it here."
DISCONNECTED = "Drive not connected. Plug in {label} to browse it."
NOTHING = "Nothing matches this folder and the current filters."
READ_ERROR = "Could not read the catalog: {error}"
OFFLOADED_TIP = "On {label} only"
UNKNOWN_HOLDER = "the mirror"  # an offloaded file whose mirror is not known
NO_DATE = "No date"


_faded: dict[int, QPixmap] = {}


def _faded_placeholder(row: LibraryRow) -> QPixmap:
    """The row's placeholder glyph drawn faint: the file is kept on a mirror
    only. The tile delegate draws the backing under it."""
    tile = placeholder_for(row, back=False)
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


def _pixmap_bytes(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * 4


class LibraryGridModel(QAbstractListModel):
    """The grid's rows; tiles show a cached thumbnail pixmap or a placeholder.

    Pixmaps are the thumbnails as loaded (``PhotoTileDelegate`` scales them to
    the tile), keyed by (id, mtime_ns), so a reload keeps the ones whose file
    did not change; the most recently shown are kept, up to
    ``PIXMAP_CACHE_BYTES``.
    """

    # Every pixmap was dropped (another catalog): scaled copies are stale too.
    thumbnails_forgotten = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[LibraryRow] = []
        self._row_of: dict[int, int] = {}
        self._pixmaps: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._pixmap_bytes = 0
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
        if role == Qt.ItemDataRole.DisplayRole:  # not painted; for screen readers
            return PurePosixPath(row.relpath).name
        if role == ASPECT_ROLE:
            return row.aspect
        if role == DURATION_ROLE:  # None: no chip; "": a play chip alone
            if row.media_type != "video":
                return None
            if row.duration_ms is None:
                return ""
            return format_duration(row.duration_ms)
        if role == OFFLOADED_ROLE:
            return self._holder(row) if row.offloaded else None
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row)
        if role == PLACEHOLDER_ROLE:
            if row.offloaded:
                return _faded_placeholder(row)
            if self._key(row) in self._pixmaps:
                return None
            return placeholder_for(row, back=False)
        if role == Qt.ItemDataRole.DecorationRole:
            if row.offloaded:
                return _faded_placeholder(row)
            key = self._key(row)
            pixmap = self._pixmaps.get(key)
            if pixmap is None:
                return placeholder_for(row)
            self._pixmaps.move_to_end(key)
            return pixmap
        return None

    def _holder(self, row: LibraryRow) -> str:
        return self.holders.get(row.id) or UNKNOWN_HOLDER

    def _tooltip(self, row: LibraryRow) -> str:
        """File name, then date taken and size, then where an offloaded file is."""
        details = format_date(row.date_taken) or NO_DATE
        if row.size_bytes is not None:
            details = f"{details} · {format_size(row.size_bytes)}"
        tip = f"{PurePosixPath(row.relpath).name}\n{details}"
        if row.offloaded:
            tip += "\n" + OFFLOADED_TIP.format(label=self._holder(row))
        return tip

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
        self._pixmap_bytes = 0
        self.thumbnails_forgotten.emit()
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
            old = self._pixmaps.pop(key, None)
            if old is not None:
                self._pixmap_bytes -= _pixmap_bytes(old)
            self._pixmaps[key] = pixmap
            self._pixmap_bytes += _pixmap_bytes(pixmap)
            while self._pixmap_bytes > PIXMAP_CACHE_BYTES and len(self._pixmaps) > 1:
                _key, dropped = self._pixmaps.popitem(last=False)
                self._pixmap_bytes -= _pixmap_bytes(dropped)
        index = self.index(i)
        self.dataChanged.emit(
            index, index, [Qt.ItemDataRole.DecorationRole, PLACEHOLDER_ROLE]
        )
        return True


def attach_tiles(
    grid: GroupedGrid,
    model: LibraryGridModel,
    delegate: PhotoTileDelegate | None = None,
) -> PhotoTileDelegate:
    """Paint ``grid``'s tiles with ``delegate`` (a new ``PhotoTileDelegate``
    by default), whose scaled copies go when ``model`` forgets its pixmaps."""
    delegate = delegate or PhotoTileDelegate(grid)
    grid.setItemDelegate(delegate)
    model.thumbnails_forgotten.connect(delegate.clear_cache)
    return delegate


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


class LibraryView(QWidget):
    summary_changed = Signal(str)  # "N files · S", or "" with nothing to count

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.viewer: MediaViewer | None = None
        self.import_window = None  # FormWindow, built on first use
        self.export_window = None
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
        footer = PanelFooter()
        footer.add(self.subfolders)
        left_layout.addWidget(footer)

        self.filters = FilterPanel(ctx)

        # Header, grid and message.
        self.summary_text = ""
        self.sort = QComboBox()
        for label, value, _descending in SORTS:
            self.sort.addItem(label, value)
        self.descending = QToolButton()
        self._descending = SORTS[0][2]
        # Tool buttons keep a fixed, shorter height; let it fill the header row
        # so it lines up with the sort box.
        self.descending.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self._show_direction()
        # The filter panel hides itself with « in its top row; while hidden, a
        # slim strip in its place brings it back.
        self.collapsible = CollapsibleFilters(self.filters, STATE_FILE)
        self.filters_strip = self.collapsible.strip
        self.export_btn = QPushButton(EXPORT_TEXT)
        self.export_btn.setToolTip(EXPORT_TIP)
        self.filters.bottom_row.addWidget(self.export_btn, 1)
        self.import_btn = QPushButton(IMPORT_TEXT)
        self.import_btn.setToolTip(IMPORT_TIP)
        theme.primary(self.import_btn)
        # The main window moves these to the right of its page header; a view
        # shown on its own keeps them above the grid.
        self.header_controls = QWidget()
        header = QHBoxLayout(self.header_controls)
        header.setContentsMargins(0, 0, 0, 0)
        self.size_slider = PhotoSizeSlider(ctx)
        header.addWidget(self.size_slider)
        header.addSpacing(16)
        header.addWidget(QLabel("Sort by"))
        header.addWidget(self.sort)
        header.addWidget(self.descending)
        header.addSpacing(16)
        self.mirror_btn = MirrorButton(ctx)
        header.addWidget(self.mirror_btn)
        header.addSpacing(16)
        header.addWidget(self.import_btn)
        # The primary Import button sets the row's height; the other controls
        # fill it so the row lines up.
        for widget in (self.sort, self.mirror_btn):
            widget.setSizePolicy(
                widget.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Preferred
            )

        self.grid = GroupedGrid()
        self.grid_model = LibraryGridModel(self)
        self.grid.setModel(self.grid_model)
        attach_tiles(self.grid, self.grid_model)
        self.size_slider.bind(self.grid)
        self.message = EmptyState()
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
        splitter.addWidget(self.collapsible.strip)
        splitter.addWidget(right)
        splitter.setStretchFactor(3, 1)
        splitter.setCollapsible(2, False)  # else the strip shows at width 0
        splitter.setSizes([SIDEBAR_WIDTH, FILTER_WIDTH, 0, 800])
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(THUMB_DEBOUNCE_MS)
        self._thumb_timer.timeout.connect(self._request_visible)

        self.tree.selectionModel().currentChanged.connect(self._on_tree_current)
        self.subfolders.toggled.connect(self.reload_rows)
        self.sort.currentIndexChanged.connect(self._on_sort_changed)
        self.descending.clicked.connect(self._flip_direction)
        self.export_btn.clicked.connect(self.open_export)
        self.import_btn.clicked.connect(self.open_import)
        ctx.open_in_import.connect(self._import_again)
        self.grid.doubleClicked.connect(self.open_viewer)
        self.grid_model.rowsInserted.connect(self._schedule_thumbs)
        self.grid.groups_toggled.connect(self._schedule_thumbs)
        self.grid.row_height_changed.connect(self._schedule_thumbs)
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

    # ── import and export ───────────────────────────────────────────────────
    def open_export(self) -> None:
        """Open the Export window for what the filters select now."""
        self.filters.apply()  # an edit still waiting out the pause counts
        if not self.filters.valid:
            return  # the panel already marks the bad box in red
        if self.export_window is None:
            from smart_gallery.gui.exports.view import ExportView
            from smart_gallery.gui.form_window import FormWindow

            self.export_window = FormWindow(
                "Export", ExportView(self.ctx), self, size=EXPORT_SIZE
            )
        self.export_window.present()

    def open_import(self) -> None:
        """Open the Import window."""
        if self.import_window is None:
            from smart_gallery.gui.form_window import FormWindow
            from smart_gallery.gui.imports.view import ImportView

            self.import_window = FormWindow(
                "Import", ImportView(self.ctx), self, size=IMPORT_SIZE
            )
        self.import_window.present()

    def _import_again(self, profile: str | None) -> None:
        """History's "Run again" on an import: open Import with its profile."""
        self.open_import()
        self.import_window.form.select_profile(profile)

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
            descending=self._descending,
        )

        def load(repo):
            rows = library_rows(repo, q)
            holders = _holders(repo) if any(r.offloaded for r in rows) else {}
            return repo.drive_root, rows, library_stats(repo, q), q.sort, holders

        self._rows_query.run(load)

    def _on_sort_changed(self, index: int) -> None:
        """Switch to the new sort's natural direction, then reload once."""
        self._descending = SORTS[index][2]
        self.grid.expand_all()  # another sort's headings name other groups
        self._show_direction()
        self.reload_rows()

    def _flip_direction(self) -> None:
        self._descending = not self._descending
        self._show_direction()
        self.reload_rows()

    def _show_direction(self) -> None:
        name = "sort-down" if self._descending else "sort-up"
        colour = self.palette().color(QPalette.ColorRole.ButtonText)
        self.descending.setIcon(icons.icon(name, colour))
        tips = DIRECTION_TIPS[self.sort.currentData()]
        self.descending.setToolTip(tips[0] if self._descending else tips[1])

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
        self._set_summary("")
        self.reload()

    def _on_tree(self, node: FolderNode) -> None:
        label = getattr(self.ctx.current, "label", "") or "All files"
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
        self._set_summary(f"{format_count(count, 'file')} · {format_size(size)}")
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

    def _set_summary(self, text: str) -> None:
        self.summary_text = text
        self.summary_changed.emit(text)

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
            self._set_summary("")
        self.message.show_text(text)
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
            self.ctx,
            self._root,
            list(self.grid_model.rows),
            index.row(),
            self,
            holder=lambda row: self.grid_model.holders.get(row.id),
        )
        self.viewer.show()
