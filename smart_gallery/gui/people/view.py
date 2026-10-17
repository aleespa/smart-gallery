"""People area placeholder until the face redesign lands."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget

from smart_gallery.gui.context import AppContext


class PeopleView(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Face naming arrives with the face redesign."))
        button = QPushButton("Open face commands")
        button.clicked.connect(lambda: ctx.navigate.emit("actions"))
        layout.addWidget(button)
        self.persons = QListWidget()
        layout.addWidget(self.persons)
        ctx.gallery_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        self.persons.clear()
        if self.ctx.current is None:
            return
        try:
            with self.ctx.open_repo() as repo:
                persons = repo.list_persons()
        except FileNotFoundError:
            return
        for p in persons:
            name = p["name"] or f"Person {p['id']}"
            self.persons.addItem(f"{name} ({p['face_count']} faces)")
