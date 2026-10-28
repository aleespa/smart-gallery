"""The Import / Export area: an Import | Export switch in the page header
over the two pages. History's "Run again" on an import shows Import."""

from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.exports.view import ExportView
from smart_gallery.gui.imports.view import ImportView
from smart_gallery.gui.segment import segment


class TransferPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.import_view = ImportView(ctx)
        self.export_view = ExportView(ctx)
        self.header_controls, _group, buttons = segment("Import", "Export")
        self.import_button, self.export_button = buttons

        self.stack = QStackedWidget()
        self.stack.addWidget(self.import_view)
        self.stack.addWidget(self.export_view)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        self.import_button.toggled.connect(
            lambda on: on and self.stack.setCurrentWidget(self.import_view)
        )
        self.export_button.toggled.connect(
            lambda on: on and self.stack.setCurrentWidget(self.export_view)
        )
        ctx.open_in_import.connect(lambda _name: self.show_import())

    def show_import(self) -> None:
        self.import_button.setChecked(True)

    def show_export(self) -> None:
        self.export_button.setChecked(True)
