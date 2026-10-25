"""The Storage page's left column: a size tree with share bars.

Rows are folders and file types (``Folder ▸ Type`` or ``Type ▸ Folder``), in the
order ``storage.tree`` already sorted them. A click reports the row's folder; a
double-click opens it in Explorer.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui.library.viewer import open_folder
from smart_gallery.storage.tree import TYPE, StorageNode, StorageTree, format_bytes

COLUMNS = ("Name", "Size", "Share of parent", "Files")
GROUPING_LABELS = {"folder": "Folder ▸ Type", "type": "Type ▸ Folder"}
NOT_FOUND = "Folder not found: {path}"
NODE_ROLE = Qt.ItemDataRole.UserRole + 1  # StorageNode, column 0
SHARE_ROLE = Qt.ItemDataRole.UserRole + 2  # float 0..1, column 2 (root 1.0)
SHARE_COLUMN = 2
SHARE_WIDTH = 150
_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_BAR_ALPHA = 150


class _ShareDelegate(QStyledItemDelegate):
    """Paints a bar for the share, then the percentage over it."""

    def __init__(self, view: "StorageTreeView") -> None:
        super().__init__(view.tree)
        self._view = view

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        share = index.data(SHARE_ROLE)
        node = index.siblingAtColumn(0).data(NODE_ROLE)
        if share is not None:
            color = option.palette.highlight().color()
            if node is not None and node.kind == TYPE:
                type_color = self._view.type_color(node)
                if type_color:
                    color = QColor(type_color)
            color.setAlpha(_BAR_ALPHA)
            r = option.rect.adjusted(4, 4, -4, -4)
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            width = max(1, round(r.width() * share))
            painter.drawRoundedRect(QRect(r.left(), r.top(), width, r.height()), 2, 2)
            painter.restore()
        super().paint(painter, option, index)


class StorageTreeView(QWidget):
    grouping_changed = Signal(str)
    folder_selected = Signal(object)  # str relpath ("" = root) or None
    message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: StorageTree | None = None
        self._root = Path()
        self._grouping = "folder"

        self.buttons: dict[str, QPushButton] = {}
        bar = QHBoxLayout()
        for key, label in GROUPING_LABELS.items():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setChecked(key == self._grouping)
            btn.clicked.connect(lambda _=False, k=key: self.set_grouping(k))
            self.buttons[key] = btn
            bar.addWidget(btn)
        bar.addStretch(1)

        self.tree = QTreeView()
        self.tree.setSortingEnabled(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setItemDelegateForColumn(SHARE_COLUMN, _ShareDelegate(self))
        self.tree.doubleClicked.connect(self.open_node)
        self.tree.customContextMenuRequested.connect(self._on_menu_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.tree, 1)

    # ── API ─────────────────────────────────────────────────────────────────
    def set_tree(self, tree: StorageTree, root: Path) -> None:
        self._data = tree
        self._root = Path(root)
        self._rebuild()

    def set_grouping(self, grouping: str) -> None:
        if grouping not in GROUPING_LABELS:
            return
        self.buttons[grouping].setChecked(True)
        if grouping == self._grouping:
            return
        self._grouping = grouping
        self._rebuild()
        self.grouping_changed.emit(grouping)

    def grouping(self) -> str:
        return self._grouping

    def type_color(self, node: StorageNode) -> str | None:
        if self._data is None or node.ext is None:
            return None
        return self._data.type_colors.get(node.ext)

    def open_node(self, index: QModelIndex) -> None:
        node = self._node(index)
        if node is None or node.relpath is None:
            return
        path = self._root / node.relpath if node.relpath else self._root
        if not path.is_dir():
            self.message.emit(NOT_FOUND.format(path=path))
            return
        open_folder(path)

    def context_menu(self, index: QModelIndex) -> QMenu:
        menu = QMenu(self)
        node = self._node(index)
        action = menu.addAction("Open in Explorer")
        action.setEnabled(node is not None and node.relpath is not None)
        action.triggered.connect(lambda: self.open_node(index))
        return menu

    # ── internals ───────────────────────────────────────────────────────────
    @staticmethod
    def _node(index: QModelIndex) -> StorageNode | None:
        if not index.isValid():
            return None
        return index.siblingAtColumn(0).data(NODE_ROLE)

    def _rebuild(self) -> None:
        data = self._data
        if data is None:
            return
        top = data.by_folder if self._grouping == "folder" else data.by_type
        model = QStandardItemModel(self)
        model.setHorizontalHeaderLabels(list(COLUMNS))
        model.invisibleRootItem().appendRow(self._row(top, None, data))
        self.tree.setModel(model)
        self.tree.selectionModel().currentChanged.connect(self._on_current)
        self.tree.expand(model.index(0, 0))
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(SHARE_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(SHARE_COLUMN, SHARE_WIDTH)

    def _row(
        self, node: StorageNode, parent: StorageNode | None, data: StorageTree
    ) -> list[QStandardItem]:
        name = QStandardItem(node.name)
        name.setData(node, NODE_ROLE)
        if node.kind == TYPE and node.ext in data.type_colors:
            name.setData(
                QColor(data.type_colors[node.ext]), Qt.ItemDataRole.DecorationRole
            )
        if parent is None:
            share = 1.0
        else:
            share = node.bytes / parent.bytes if parent.bytes else 0.0
        size = QStandardItem(format_bytes(node.bytes))
        size.setTextAlignment(_RIGHT)
        pct = QStandardItem(f"{share * 100:.0f}%")
        pct.setData(share, SHARE_ROLE)
        files = QStandardItem(f"{node.files:,}")
        files.setTextAlignment(_RIGHT)
        items = [name, size, pct, files]
        for item in items:
            item.setEditable(False)
        for child in node.children:
            name.appendRow(self._row(child, node, data))
        return items

    def _on_current(self, current: QModelIndex, _previous: QModelIndex) -> None:
        node = self._node(current)
        self.folder_selected.emit(node.relpath if node is not None else None)

    def _on_menu_requested(self, pos: QPoint) -> None:
        index = self.tree.indexAt(pos)
        if index.isValid():
            self.context_menu(index).exec(self.tree.viewport().mapToGlobal(pos))
