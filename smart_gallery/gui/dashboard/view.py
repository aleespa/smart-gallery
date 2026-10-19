"""Dashboard area: stat tiles, native chart panels, the photo map, data tables.

Filters are the same panel and state as the Library. The catalog loads and
every chart's numbers are computed on a worker thread (``load_dashboard``);
when they arrive, the panels are rebuilt here on the GUI thread.
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, QUrl
from PySide6.QtGui import QPalette
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.dashboard.data import (
    SETTINGS_COLORS,
    VIDEO_COLOR,
    file_type_colors,
)
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.dashboard import charts
from smart_gallery.gui.dashboard.model import DashboardData, load_dashboard
from smart_gallery.gui.filters_panel import FilterPanel
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.organize.filters import is_query_empty

NO_GALLERY = "Open or create a gallery to see its dashboard."
DISCONNECTED = "Drive not connected. Plug in {label} to see its dashboard."
READ_ERROR = "Could not read the catalog: {error}"
MAP_CAPTION = "Displaying {count:,} geotagged photos"
TABS = ("Images", "Videos", "Others")

FILES_PANEL = "File Types, Cameras, Lenses"
SETTINGS_PANEL = "Camera Settings Distribution"
TIMELINE_PANEL = "Photography Timeline"
VIDEO_PANEL = "Video Recording Summary"
MAP_PANEL = "Photo Map"
PANELS = (FILES_PANEL, SETTINGS_PANEL, TIMELINE_PANEL, VIDEO_PANEL, MAP_PANEL)

# Chart-area widths (px) at which a grid goes multi-column. The ranked camera
# and lens bars need the width for their long names.
FILES_WIDE = 1300
SETTINGS_WIDE = 700

# Chart heights (px).
FILES_HEIGHT = 340
SETTINGS_HEIGHT = 260
TIMELINE_HEIGHT = 380
VIDEO_HEIGHT = 300
MAP_HEIGHT = 500


class DataFrameModel(QAbstractTableModel):
    """Read-only view of a DataFrame; cells are read from the frame in place."""

    def __init__(self, df: pd.DataFrame | None = None, parent=None):
        super().__init__(parent)
        self._df = pd.DataFrame() if df is None else df

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._df.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        value = self._df.iat[index.row(), index.column()]
        return "" if pd.isna(value) else str(value)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return str(self._df.columns[section])
        return str(section + 1)

    def set_frame(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df
        self.endResetModel()


def _scaled_font(label: QLabel, factor: float, bold: bool = False) -> None:
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * factor)
    font.setBold(bold)
    label.setFont(font)


class StatTile(QFrame):
    """A headline number: a large value above a small caption."""

    def __init__(self, caption: str, value: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.value = QLabel(value)
        _scaled_font(self.value, 1.8, bold=True)
        self.caption = QLabel(caption)
        _scaled_font(self.caption, 0.9)
        # A palette role, not a colour, so it follows light/dark switches.
        self.caption.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout = QVBoxLayout(self)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)


class ReflowGrid(QWidget):
    """Widgets in a grid of ``columns`` columns, or in one column when narrow."""

    def __init__(self, columns: int, wide_at: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._max_columns = columns
        self._wide_at = wide_at
        self._items: list[QWidget] = []
        self.columns = 1
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)

    def add(self, widget: QWidget) -> None:
        self._items.append(widget)
        self._place()

    def fit(self, width: int) -> None:
        columns = self._max_columns if width >= self._wide_at else 1
        if columns != self.columns:
            self.columns = columns
            self._place()

    def _place(self) -> None:
        for widget in self._items:
            self._grid.removeWidget(widget)
        for i, widget in enumerate(self._items):
            self._grid.addWidget(widget, i // self.columns, i % self.columns)
        for col in range(self._max_columns):
            self._grid.setColumnStretch(col, 1 if col < self.columns else 0)


class Panel(QFrame):
    """A titled dashboard section; its ``body`` is replaced on every result."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.title = QLabel(title)
        _scaled_font(self.title, 1.3, bold=True)
        self.body: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.addWidget(self.title)

    def set_body(self, body: QWidget | None) -> None:
        """Show ``body``, or hide the panel when it is None.

        The old body is deleted, not just hidden, so its chart views leave
        the ThemeWatcher too."""
        if self.body is not None:
            self.body.hide()
            self._layout.removeWidget(self.body)
            self.body.deleteLater()
        self.body = body
        if body is not None:
            self._layout.addWidget(body)
        self.setVisible(body is not None)


class DashboardView(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.out_dir = app_data_dir() / "dashboard"
        self.data: DashboardData | None = None
        self._query = RepoQuery(ctx, self)
        self.theme_watcher = charts.ThemeWatcher([], self)

        self.filters = FilterPanel(ctx)
        self.tables: dict[str, QTableView] = {}
        self.tabs = QTabWidget()
        for name in TABS:
            table = QTableView()
            table.setModel(DataFrameModel(parent=table))
            table.setAlternatingRowColors(True)
            self.tables[name] = table
            self.tabs.addTab(table, name)

        self.tiles: list[StatTile] = []
        self._tiles_row = QHBoxLayout()

        self.panels = {title: Panel(title) for title in PANELS}
        self.map_caption = QLabel()
        self.map_view = QWebEngineView()
        self.map_view.setFixedHeight(MAP_HEIGHT)
        # The map page is a local file we write; its tiles come from the
        # internet and plotly.js from a file beside it. Allowing remote URLs
        # turns local-file access off, so allow files again afterwards.
        settings = self.map_view.settings()
        attrs = QWebEngineSettings.WebAttribute
        settings.setAttribute(attrs.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(attrs.LocalContentCanAccessFileUrls, True)
        map_body = QWidget()
        map_layout = QVBoxLayout(map_body)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.addWidget(self.map_caption)
        map_layout.addWidget(self.map_view)
        # Kept for the view's lifetime: one web view, reloaded on each result.
        self.panels[MAP_PANEL].set_body(map_body)

        content = QWidget()
        column = QVBoxLayout(content)
        for panel in self.panels.values():
            panel.hide()
            column.addWidget(panel)
        column.addStretch(1)
        self.charts_area = QScrollArea()
        self.charts_area.setWidgetResizable(True)
        self.charts_area.setFrameShape(QFrame.Shape.NoFrame)
        self.charts_area.setWidget(content)
        self.charts_area.installEventFilter(self)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addLayout(self._tiles_row)
        top_layout.addWidget(self.charts_area, 1)

        charts_split = QSplitter(Qt.Orientation.Vertical)
        charts_split.addWidget(top)
        charts_split.addWidget(self.tabs)
        charts_split.setSizes([560, 240])
        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(charts_split)
        self.stack.addWidget(self.message)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.filters)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 940])
        QVBoxLayout(self).addWidget(splitter)

        self._query.finished.connect(self._on_data)
        self._query.failed.connect(self._on_failed)
        ctx.gallery_changed.connect(self.reload)
        ctx.filters_changed.connect(self.reload)
        ctx.catalog_updated.connect(self.reload)
        self.reload()

    def reload(self, *_args) -> None:
        if self.ctx.current is None:
            self._query.cancel()
            self._show_message(NO_GALLERY)
            return
        filters = self.ctx.filters
        filters = None if is_query_empty(filters) else filters
        out_dir = self.out_dir
        # Everything heavy happens in load_dashboard, off the GUI thread.
        self._query.run(lambda repo: load_dashboard(repo.db_path, filters, out_dir))

    def eventFilter(self, obj, event) -> bool:
        if obj is self.charts_area and event.type() == QEvent.Type.Resize:
            self._reflow()
        return super().eventFilter(obj, event)

    def _reflow(self) -> None:
        width = self.charts_area.width()
        for title in (FILES_PANEL, SETTINGS_PANEL):
            body = self.panels[title].body
            if isinstance(body, ReflowGrid):
                body.fit(width)

    def _on_data(self, result: DashboardData) -> None:
        self.data = result
        for name, frame in zip(TABS, result.split):
            self.tables[name].model().set_frame(frame)
        self._set_tiles(result.metrics)
        self.panels[FILES_PANEL].set_body(self._files_body(result))
        self.panels[SETTINGS_PANEL].set_body(self._settings_body(result.settings))
        self.panels[TIMELINE_PANEL].set_body(
            self._month_body(result.timeline, {}, "photos", True, TIMELINE_HEIGHT)
        )
        self.panels[VIDEO_PANEL].set_body(
            self._month_body(
                result.video, {"Minutes": VIDEO_COLOR}, "min", False, VIDEO_HEIGHT
            )
        )
        self._show_map(result)
        self._reflow()
        self.stack.setCurrentIndex(0)

    def _set_tiles(self, metrics) -> None:
        for tile in self.tiles:
            self._tiles_row.removeWidget(tile)
            tile.deleteLater()
        self.tiles = [StatTile(label, value) for label, value in metrics]
        for tile in self.tiles:
            self._tiles_row.addWidget(tile)

    def _chart(self, view, height: int):
        view.setFixedHeight(height)
        self.theme_watcher.add(view)
        return view

    def _titled(self, title: str, view) -> QWidget:
        cell = QWidget()
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        _scaled_font(label, 1.0, bold=True)
        layout.addWidget(label)
        layout.addWidget(view)
        return cell

    def _files_body(self, result: DashboardData) -> QWidget | None:
        if not result.file_types:
            return None
        grid = ReflowGrid(3, FILES_WIDE)
        donut = charts.donut_chart(
            result.file_types, file_type_colors(result.file_types)
        )
        grid.add(self._titled("File Types", self._chart(donut, FILES_HEIGHT)))
        for title, counts in (("Cameras", result.cameras), ("Lenses", result.lenses)):
            if counts.items:
                bars = self._chart(charts.ranked_bar_chart(counts), FILES_HEIGHT)
                grid.add(self._titled(title, bars))
        return grid

    def _settings_body(self, settings: dict) -> QWidget | None:
        if not settings:
            return None
        grid = ReflowGrid(2, SETTINGS_WIDE)
        for name, buckets in settings.items():
            view = charts.stop_bar_chart(buckets, SETTINGS_COLORS[name])
            grid.add(self._titled(name, self._chart(view, SETTINGS_HEIGHT)))
        return grid

    def _month_body(self, ms, colors, unit, stacked, height) -> QWidget | None:
        if ms is None:
            return None
        return self._chart(charts.month_bar_chart(ms, colors, unit, stacked), height)

    def _show_map(self, result: DashboardData) -> None:
        panel = self.panels[MAP_PANEL]
        panel.setVisible(result.map_page is not None)
        if result.map_page is not None:
            self.map_caption.setText(MAP_CAPTION.format(count=result.geotag_count))
            self.map_view.load(QUrl.fromLocalFile(str(result.map_page)))

    def _on_failed(self, error) -> None:
        if self.ctx.current is None:
            text = NO_GALLERY
        elif isinstance(error, FileNotFoundError):
            label = getattr(self.ctx.current, "label", "") or str(self.ctx.current.root)
            text = DISCONNECTED.format(label=label)
        else:
            text = READ_ERROR.format(error=error)
        self._show_message(text)

    def _show_message(self, text: str) -> None:
        for table in self.tables.values():
            table.model().set_frame(pd.DataFrame())
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)
