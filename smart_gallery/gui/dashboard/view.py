"""Dashboard area: the shared Plotly figures in a web view, plus data tables.

Filters are the same panel and state as the Library. The catalog loads on a
worker thread; the page is rewritten and reloaded when it arrives.
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QLabel,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.dashboard import figures
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.dashboard.model import build_sections, write_dashboard
from smart_gallery.gui.filters_panel import FilterPanel
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.organize.filters import is_query_empty

NO_GALLERY = "Open or create a gallery to see its dashboard."
DISCONNECTED = "Drive not connected. Plug in {label} to see its dashboard."
READ_ERROR = "Could not read the catalog: {error}"
TABS = ("Images", "Videos", "Others")


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


class DashboardView(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.out_dir = app_data_dir() / "dashboard"
        self.index_path = self.out_dir / "index.html"
        self._query = RepoQuery(ctx, self)

        self.filters = FilterPanel(ctx)
        self.web = QWebEngineView()
        self.tables: dict[str, QTableView] = {}
        self.tabs = QTabWidget()
        for name in TABS:
            table = QTableView()
            table.setModel(DataFrameModel(parent=table))
            table.setAlternatingRowColors(True)
            self.tables[name] = table
            self.tabs.addTab(table, name)

        charts = QSplitter(Qt.Orientation.Vertical)
        charts.addWidget(self.web)
        charts.addWidget(self.tabs)
        charts.setSizes([560, 240])
        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(charts)
        self.stack.addWidget(self.message)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.filters)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 940])
        QVBoxLayout(self).addWidget(splitter)

        self._query.finished.connect(self._on_df)
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

        def load(repo):
            return figures.load_catalog_df(repo.db_path, filters)

        self._query.run(load)

    def _on_df(self, df: pd.DataFrame) -> None:
        fi, fv, fo = figures.split_media(df)
        for name, frame in zip(TABS, (fi, fv, fo)):
            self.tables[name].model().set_frame(frame)
        write_dashboard(self.out_dir, figures.metrics(fi, fv, fo), build_sections(df))
        self.web.load(QUrl.fromLocalFile(str(self.index_path)))
        self.stack.setCurrentIndex(0)

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
