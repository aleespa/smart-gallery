"""Main window: top bar, page header, lazily built area pages, and the app
entry point.

The window has no system frame: the top bar is its title bar, holding the
brand, the gallery picker, one tab per area and the window buttons (see
``titlebar``). Under it a header names the page; an area page can put its own controls on the
header's right by exposing a ``header_controls`` widget.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCloseEvent, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui import theme
from smart_gallery.gui.areas import AREAS, Area
from smart_gallery.gui.brand import BrandLabel
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import GalleryStore
from smart_gallery.gui.galleries.view import GalleriesPanel
from smart_gallery.gui.history.store import HistoryStore
from smart_gallery.gui.jobs import JobRunner
from smart_gallery.gui.paths import app_data_dir, exiftool_available
from smart_gallery.gui.statusbar import JobStatusBar
from smart_gallery.gui.thumbs.loader import ThumbnailLoader
from smart_gallery.gui.titlebar import (
    RESIZE_MARGIN,
    ResizeFrame,
    TitleBar,
    WindowButtons,
    round_corners,
)

BAR_MARGIN = 12  # px between the window edge and the top bar's contents
HEADER_MARGIN = 24  # px around the page header
NO_GALLERY_OPEN = "No gallery open"
# The tabs underline the open area in the accent colour.
WINDOW_STYLE = f"""
QToolButton#areaTab {{
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 12px 8px 12px;
    font-size: {theme.TAB_PT}pt;
}}
QToolButton#areaTab:hover {{ border-bottom-color: palette(mid); }}
QToolButton#areaTab:checked {{ border-bottom-color: palette(highlight); }}
QGroupBox {{ font-family: "{theme.SERIF[0]}"; font-size: {theme.SECTION_PT}pt; }}
"""
QUIT_WHILE_RUNNING = "A job is still running. Cancel it and quit?"
EXIFTOOL_BANNER = (
    "ExifTool not found. Install it from https://exiftool.org to read metadata."
)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext | None = None):
        super().__init__()
        self.setWindowTitle("Smart Gallery")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
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
        self._controls: dict[str, QWidget] = {}  # area id -> its header controls
        self._area: Area | None = None
        self._closing = False  # waiting for a cancelled job before closing
        self._prompting = False  # the quit question is open

        self.setStyleSheet(WINDOW_STYLE)
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_top_bar())
        outer.addWidget(_rule())
        self.banner = QLabel(EXIFTOOL_BANNER)
        self.banner.setStyleSheet("background: #f0c040; color: black; padding: 6px;")
        self.banner.setVisible(not exiftool_available())
        outer.addWidget(self.banner)
        outer.addWidget(self._build_page_header())
        outer.addWidget(_rule())
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.job_bar = JobStatusBar(self.ctx.jobs)
        outer.addWidget(self.job_bar)
        self.frame = ResizeFrame(central)
        self.setCentralWidget(self.frame)
        self._web_anchor = _web_anchor(central)

        self.ctx.navigate.connect(self.show_area)
        self.ctx.gallery_changed.connect(self._update_header)
        self.resize(1200, 800)
        self.show_area(AREAS[0].id)

    def _build_top_bar(self) -> QWidget:
        bar = TitleBar()
        row = QHBoxLayout(bar)
        row.setContentsMargins(BAR_MARGIN, 0, 0, 0)  # window buttons sit flush
        row.setSpacing(4)

        self.brand = BrandLabel()
        row.addWidget(self.brand)
        row.addSpacing(16)
        self.galleries_panel = GalleriesPanel(self.ctx)
        row.addWidget(self.galleries_panel)
        row.addSpacing(16)

        self.tabs: dict[str, QToolButton] = {}
        for area in AREAS:
            tab = QToolButton()
            tab.setObjectName("areaTab")
            tab.setText(area.label)
            tab.setCheckable(True)
            tab.clicked.connect(lambda _checked=False, i=area.id: self.show_area(i))
            self.tabs[area.id] = tab
            row.addWidget(tab)
        row.addStretch(1)
        self.window_buttons = WindowButtons(self)
        row.addWidget(self.window_buttons, 0, Qt.AlignmentFlag.AlignTop)
        return bar

    def _build_page_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(HEADER_MARGIN, 16, HEADER_MARGIN, 12)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.eyebrow = QLabel()
        small = QFont(self.eyebrow.font())
        small.setCapitalization(QFont.Capitalization.AllUppercase)
        small.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        small.setPointSizeF(max(small.pointSizeF() - 1, 7))
        self.eyebrow.setFont(small)
        muted = self.eyebrow.palette()
        muted.setColor(
            QPalette.ColorRole.WindowText,
            muted.color(QPalette.ColorRole.PlaceholderText),
        )
        self.eyebrow.setPalette(muted)
        self.title = QLabel()
        self.title.setFont(theme.serif_font(theme.TITLE_PT))
        names.addWidget(self.eyebrow)
        names.addWidget(self.title)
        row.addLayout(names)
        row.addStretch(1)
        self.header_controls = QStackedWidget()
        row.addWidget(self.header_controls, 0, Qt.AlignmentFlag.AlignBottom)
        return header

    @property
    def current_area(self) -> str | None:
        return self._area.id if self._area else None

    def show_area(self, area_id: str) -> None:
        """Open an area by id, building its page on the first visit."""
        area = next((a for a in AREAS if a.id == area_id), None)
        if area is None:
            return
        page = self._pages.get(area.id)
        if page is None:
            page = self._pages[area.id] = area.factory(self.ctx)
            self.stack.addWidget(page)
            controls = getattr(page, "header_controls", None) or QWidget()
            self._controls[area.id] = controls
            self.header_controls.addWidget(controls)
        self.stack.setCurrentWidget(page)
        self.header_controls.setCurrentWidget(self._controls[area.id])
        self._area = area
        for tab_id, tab in self.tabs.items():
            tab.setChecked(tab_id == area.id)
        self._update_header()

    def _update_header(self, *_args) -> None:
        if self._area is None:
            return
        current = self.ctx.current
        self.eyebrow.setText(current.label if current is not None else NO_GALLERY_OPEN)
        self.title.setText(self._area.label)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        round_corners(self)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            # No resize border while the window fills the screen.
            full = self.isMaximized() or self.isFullScreen()
            self.frame.set_margin(0 if full else RESIZE_MARGIN)
        super().changeEvent(event)

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


def _web_anchor(parent: QWidget) -> QWidget:
    """A zero-size web view, shown, so the window is GPU-composited from the
    start. Otherwise the first real web view (the Dashboard map) switches the
    shown window over, which recreates its native window and makes it flash.
    A hidden one would not count."""
    from PySide6.QtWebEngineWidgets import QWebEngineView

    view = QWebEngineView(parent)
    view.setFixedSize(0, 0)
    view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    view.setAccessibleName("")
    return view


def _rule() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet("color: palette(mid);")
    return line


def run() -> None:
    # QtWebEngine (Dashboard) must be imported before the QApplication exists.
    from PySide6 import QtWebEngineWidgets  # noqa: F401

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Smart Gallery")
    theme.apply_theme(app)
    window = MainWindow()
    window.show()
    app.exec()
