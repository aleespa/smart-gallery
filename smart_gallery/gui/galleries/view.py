"""Gallery picker in the top bar: pick, add or create the open catalog.

One drop-down button names the open gallery; its menu lists every gallery,
then the commands that add one, create one or look for drives again.
"""

from __future__ import annotations

import sqlite3
import time

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QFileDialog,
    QMenu,
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
NO_GALLERY = "No gallery"
CREATE_DISABLED_TIP = "Available once jobs are wired"
JOB_RUNNING = "A job is already running."
# Switching back to the window re-checks the drives at most this often.
RECHECK_INTERVAL_S = 2.0


class GalleriesPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.store = ctx.galleries

        self.menu = QMenu(self)
        self.menu.setToolTipsVisible(True)
        self.button = QPushButton()
        self.button.setMenu(self.menu)  # a click opens the menu
        self.button.setMinimumWidth(150)
        self.button.setToolTip("Switch, add or create a gallery")

        self.add_action = QAction("Add existing…", self)
        self.create_action = QAction("Create on drive…", self)
        # Creating a catalog is an "init" job, so it needs the job runner.
        self.create_action.setEnabled(ctx.jobs is not None)
        if ctx.jobs is None:
            self.create_action.setToolTip(CREATE_DISABLED_TIP)
        self.refresh_action = QAction("Refresh drives", self)
        self.refresh_action.setToolTip("Look again for drives plugged in or removed")
        self._group = QActionGroup(self)  # the gallery entries, one checked
        self._group.setExclusive(True)
        self._creating: str | None = None  # folder of the init job we started
        # db_uuid -> connected, as the menu last showed it.
        self._connected: dict[str, bool] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.button)

        self.refresh_action.triggered.connect(self.recheck)
        self.add_action.triggered.connect(self._add_existing)
        self.create_action.triggered.connect(self._create_on_drive)
        ctx.gallery_changed.connect(self._show_current)
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

    def gallery_actions(self) -> list[QAction]:
        """The menu's gallery entries, in store order."""
        return list(self._group.actions())

    def refresh(self, select: str | None = None, ask: bool = False) -> bool:
        """Rebuild the menu from the store.

        Selects ``select`` (a db_uuid), else the open gallery, else the last one.
        With ``ask``, a switch first asks the leave guards (a drive coming or
        going is not the user leaving, so the default does not). Returns False
        when a guard kept the open gallery.
        """
        galleries = self.store.all()
        current = self.ctx.current.db_uuid if self.ctx.current else None
        wanted = select or current or self.store.last()
        for action in self._group.actions():
            self._group.removeAction(action)
            action.deleteLater()
        self.menu.clear()
        self._connected.clear()
        self.menu.addSection("Galleries")
        for g in galleries:
            connected = self._connected[g.db_uuid] = is_connected(g)
            action = QAction(g.label if connected else g.label + NOT_CONNECTED, self)
            action.setCheckable(True)
            action.setData(g)
            if not connected:
                font = action.font()
                font.setItalic(True)
                action.setFont(font)
            # Bind g now: the loop variable moves on.
            action.triggered.connect(lambda _checked=False, g=g: self._select(g))
            self._group.addAction(action)
            self.menu.addAction(action)
        self.menu.addSeparator()
        self.menu.addAction(self.add_action)
        self.menu.addAction(self.create_action)
        self.menu.addAction(self.refresh_action)
        index = next((i for i, g in enumerate(galleries) if g.db_uuid == wanted), 0)
        if galleries:
            return self._select(galleries[index], ask=ask)
        self._show_current()
        return True

    def _select(self, gallery: Gallery, ask: bool = True) -> bool:
        """Open ``gallery``. Unless ``ask`` is False, a leave guard can refuse:
        the open gallery stays ticked and False is returned."""
        if self.ctx.current != gallery:
            if ask and not self.ctx.can_leave():
                self._show_current()
                return False
            self.ctx.set_gallery(gallery)
        self.store.set_last(gallery.db_uuid)
        self._show_current()
        return True

    def _show_current(self, *_args) -> None:
        """Name the open gallery on the button and tick it in the menu."""
        uuid = getattr(self.ctx.current, "db_uuid", None)
        text = NO_GALLERY
        for action in self._group.actions():
            mine = action.data().db_uuid == uuid
            action.setChecked(mine)
            if mine:
                text = action.text()
        self.button.setText(text)

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
        self.refresh(select=gallery.db_uuid, ask=True)

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
        if not self.refresh(select=gallery.db_uuid, ask=True):
            return  # a leave guard kept the open gallery
        if self.ctx.current != gallery:  # refresh opens it unless it is already open
            self.ctx.set_gallery(gallery)
