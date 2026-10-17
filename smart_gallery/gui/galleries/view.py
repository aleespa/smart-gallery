"""Galleries panel at the top of the rail: pick, add or create the open catalog."""

from __future__ import annotations

import sqlite3
import time

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
from smart_gallery.gui.jobs import JobSpec

NOT_CONNECTED = " (not connected)"
CREATE_DISABLED_TIP = "Available once jobs are wired"
JOB_RUNNING = "A job is already running."
# Switching back to the window re-checks the drives at most this often.
RECHECK_INTERVAL_S = 2.0


class GalleriesPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.store = ctx.galleries

        self.combo = QComboBox()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Look again for drives plugged in or removed")
        self.add_btn = QPushButton("Add existing…")
        self.create_btn = QPushButton("Create on drive…")
        # Creating a catalog is an "init" job, so it needs the job runner.
        self.create_btn.setEnabled(ctx.jobs is not None)
        if ctx.jobs is None:
            self.create_btn.setToolTip(CREATE_DISABLED_TIP)
        self._creating: str | None = None  # folder of the init job we started
        # db_uuid -> connected, as the combo last showed it.
        self._connected: dict[str, bool] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.add_btn)
        layout.addWidget(self.create_btn)

        self.combo.currentIndexChanged.connect(self._on_index_changed)
        self.refresh_btn.clicked.connect(self.recheck)
        self.add_btn.clicked.connect(self._add_existing)
        self.create_btn.clicked.connect(self._create_on_drive)
        if ctx.jobs is not None:
            ctx.jobs.finished.connect(self._on_job_finished)

        self._relocate_disconnected()
        self.refresh()
        self._last_check = time.monotonic()

    def recheck(self) -> None:
        """Look again for drives that were plugged in, moved or removed, and
        reload the areas if the open gallery came back or went away."""
        self._last_check = time.monotonic()
        current = self.ctx.current
        was_connected = self._connected.get(current.db_uuid) if current else None
        self._relocate_disconnected()
        self.refresh(select=current.db_uuid if current else None)
        now = self.ctx.current
        # A new path already reloaded the areas through refresh(); a drive that
        # came back (or went) at the same path did not.
        if now is not None and now == current:
            if self._connected.get(now.db_uuid) != was_connected:
                self.ctx.set_gallery(now)

    def on_window_activated(self) -> None:
        """The main window was switched back to: the owner may have just
        plugged a drive in."""
        now = time.monotonic()
        if now - self._last_check >= RECHECK_INTERVAL_S:
            self._last_check = now
            self.recheck()

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
        self._connected.clear()
        for g in galleries:
            connected = self._connected[g.db_uuid] = is_connected(g)
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

    def _create_on_drive(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a drive or folder for the new catalog"
        )
        if not folder:
            return
        # Set first: start() can emit ``finished`` before it returns.
        self._creating = folder
        if not self.ctx.jobs.start(JobSpec("init", (("init", folder),))):
            self._creating = None
            QMessageBox.information(self, "Create on drive", JOB_RUNNING)

    def _on_job_finished(self, record) -> None:
        folder, self._creating = self._creating, None
        if folder is None or record.status != "ok":
            return
        try:
            gallery = self.store.add(folder)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Create on drive", str(exc))
            return
        self.refresh(select=gallery.db_uuid)
        if self.ctx.current != gallery:  # refresh opens it unless it is already open
            self.ctx.set_gallery(gallery)
