"""Main window: area rail, lazily built area pages, and the app entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui.areas import AREAS
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import GalleryStore
from smart_gallery.gui.galleries.view import GalleriesPanel
from smart_gallery.gui.history.store import HistoryStore
from smart_gallery.gui.jobs import JobRunner
from smart_gallery.gui.paths import app_data_dir, exiftool_available
from smart_gallery.gui.statusbar import JobStatusBar
from smart_gallery.gui.thumbs.loader import ThumbnailLoader

RAIL_MARGIN = 8  # px between the window edge and the rail column
QUIT_WHILE_RUNNING = "A job is still running. Cancel it and quit?"
EXIFTOOL_BANNER = (
    "ExifTool not found. Install it from https://exiftool.org to read metadata."
)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext | None = None):
        super().__init__()
        self.setWindowTitle("Smart Gallery")
        self.ctx = ctx or AppContext(self)
        if self.ctx.galleries is None:
            self.ctx.galleries = GalleryStore(app_data_dir() / "galleries.json")
        if self.ctx.jobs is None:
            data = app_data_dir()
            self.ctx.jobs = JobRunner(
                HistoryStore(data / "history.jsonl"), workdir=data
            )
        if self.ctx.thumbs is None:
            self.ctx.thumbs = ThumbnailLoader(self)
        self.ctx.jobs.catalog_written.connect(self.ctx.catalog_updated.emit)
        self._pages: dict[str, QWidget] = {}
        self._closing = False  # waiting for a cancelled job before closing
        self._prompting = False  # the quit question is open

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)

        self.banner = QLabel(EXIFTOOL_BANNER)
        self.banner.setStyleSheet("background: #f0c040; color: black; padding: 6px;")
        self.banner.setVisible(not exiftool_available())
        outer.addWidget(self.banner)

        body = QHBoxLayout()
        outer.addLayout(body, 1)

        left = QVBoxLayout()
        left.setContentsMargins(RAIL_MARGIN, 0, 0, 0)  # off the window edge
        # Top of the rail; the Galleries panel is mounted here.
        self.galleries_slot = QVBoxLayout()
        left.addLayout(self.galleries_slot)
        self.galleries_panel = GalleriesPanel(self.ctx)
        self.galleries_panel.setFixedWidth(180)
        self.galleries_slot.addWidget(self.galleries_panel)
        self.rail = QListWidget()
        self.rail.setFixedWidth(180)
        for area in AREAS:
            self.rail.addItem(area.label)
        left.addWidget(self.rail, 1)
        body.addLayout(left)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.job_bar = JobStatusBar(self.ctx.jobs)
        outer.addWidget(self.job_bar)
        self.setCentralWidget(central)

        self.rail.currentRowChanged.connect(self._show_area)
        self.ctx.navigate.connect(self._navigate)
        self.resize(1200, 800)
        self.rail.setCurrentRow(0)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WindowActivate:
            self.galleries_panel.on_window_activated()
        return super().event(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        jobs = self.ctx.jobs
        if jobs is None or not jobs.running:
            event.accept()
            return
        event.ignore()  # never wait for the job here: that would freeze the GUI
        if self._closing or self._prompting:
            return
        self._prompting = True
        try:
            answer = QMessageBox.question(
                self,
                "Quit",
                QUIT_WHILE_RUNNING,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        finally:
            self._prompting = False
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not jobs.running:
            event.accept()  # the job finished while the question was open
            return
        self._closing = True
        jobs.finished.connect(
            self._close_after_job, Qt.ConnectionType.SingleShotConnection
        )
        if not jobs.cancelling:  # a second cancel would force-stop the job
            jobs.cancel()

    def _close_after_job(self, _record) -> None:
        self._closing = False
        self.close()

    def _show_area(self, row: int) -> None:
        if row < 0:
            return
        area = AREAS[row]
        page = self._pages.get(area.id)
        if page is None:
            page = self._pages[area.id] = area.factory(self.ctx)
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def _navigate(self, area_id: str) -> None:
        for row, area in enumerate(AREAS):
            if area.id == area_id:
                self.rail.setCurrentRow(row)
                return


def run() -> None:
    # QtWebEngine (Dashboard) must be imported before the QApplication exists.
    from PySide6 import QtWebEngineWidgets  # noqa: F401

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Smart Gallery")
    window = MainWindow()
    window.show()
    app.exec()
