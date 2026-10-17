"""Galleries panel at the top of the rail: pick, add or create the open catalog."""

from __future__ import annotations

import sqlite3

from PySide6.QtGui import QPalette
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import (
    Gallery,
    existing_drives,
    is_connected,
    locate,
)

NOT_CONNECTED = " (not connected)"
CREATE_DISABLED_TIP = "Available once jobs are wired"


class GalleriesPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.store = ctx.galleries

        self.combo = QComboBox()
        self.add_btn = QPushButton("Add existing…")
        self.create_btn = QPushButton("Create on drive…")
        # Task 6 wires this to an "init" job once ctx.jobs exists.
        self.create_btn.setEnabled(False)
        self.create_btn.setToolTip(CREATE_DISABLED_TIP)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo)
        layout.addWidget(self.add_btn)
        layout.addWidget(self.create_btn)

        self.combo.currentIndexChanged.connect(self._on_index_changed)
        self.add_btn.clicked.connect(self._add_existing)

        self._relocate_disconnected()
        self.refresh()

    def refresh(self, select: str | None = None) -> None:
        """Reload the combo from the store.

        Selects ``select`` (a db_uuid), else the open gallery, else the last one.
        """
        galleries = self.store.all()
        current = self.ctx.current.db_uuid if self.ctx.current else None
        wanted = select or current or self.store.last()
        grey = self.palette().color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
        )
        self.combo.blockSignals(True)
        self.combo.clear()
        for g in galleries:
            connected = is_connected(g)
            self.combo.addItem(g.label if connected else g.label + NOT_CONNECTED, g)
            if not connected:
                self.combo.setItemData(
                    self.combo.count() - 1, grey, Qt.ItemDataRole.ForegroundRole
                )
        index = next((i for i, g in enumerate(galleries) if g.db_uuid == wanted), 0)
        if galleries:
            self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        if galleries:
            self._select(galleries[index])

    def _select(self, gallery: Gallery) -> None:
        if self.ctx.current != gallery:
            self.ctx.set_gallery(gallery)
        self.store.set_last(gallery.db_uuid)

    def _on_index_changed(self, index: int) -> None:
        gallery = self.combo.itemData(index, Qt.ItemDataRole.UserRole)
        if gallery is not None:
            self._select(gallery)

    def _relocate_disconnected(self) -> None:
        """Look for galleries whose drive came back under another letter."""
        missing = [g for g in self.store.all() if not is_connected(g)]
        if not missing:
            return
        drives = existing_drives()
        for g in missing:
            found = locate(g, drives)
            if found is not None:
                self.store.add(found.db_path)

    def _add_existing(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a drive or folder that has a catalog"
        )
        if not folder:
            return
        try:
            gallery = self.store.add(folder)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Add existing gallery", str(exc))
            return
        self.refresh(select=gallery.db_uuid)
