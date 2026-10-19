"""Filter panel shared by the Library and the Dashboard.

It edits a ``FilterValues`` and publishes the parsed query with
``ctx.set_filters`` on Apply or after a short pause in editing. Every panel
also follows ``filters_changed``, so two panels never show different filters.

Each range (dates, aperture, ISO, shutter) is one line: lower bound, then
upper. The date boxes open a calendar. Cameras, lenses, extensions and people
are chip pickers, since a catalog can hold dozens of each.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.filter_widgets import ChipPicker, DateField
from smart_gallery.gui.filters_model import (
    FilterValues,
    filter_choices,
    from_filter_options,
    to_filter_options,
)
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.organize.filters import FilterOptions

DEBOUNCE_MS = 300
FILE_TYPES = (("image", "Photos"), ("video", "Videos"), ("other", "Other files"))
DATE_HINT = "A date like 2026-06-30, or pick one from the calendar."
ERROR_STYLE = "border: 1px solid #d04040;"
RANGE_DASH = "–"

# (field, label in error messages, placeholder) for the text boxes.
_TEXT_FIELDS = (
    ("date_from", "From", "From"),
    ("date_to", "To", "To"),
    ("min_aperture", "Aperture from", "f/1.8"),
    ("max_aperture", "Aperture to", "f/8"),
    ("min_iso", "ISO from", "100"),
    ("max_iso", "ISO to", "3200"),
    ("min_shutter", "Shutter from", "1/1000"),
    ("max_shutter", "Shutter to", "2s"),
)
_LABELS = {name: label for name, label, _ in _TEXT_FIELDS}
# (row label, lower field, upper field, what the bounds mean) for Exposure.
_EXPOSURE = (
    ("Aperture", "min_aperture", "max_aperture", "f-number, widest to narrowest"),
    ("ISO", "min_iso", "max_iso", "ISO, lowest to highest"),
    ("Shutter", "min_shutter", "max_shutter", "Exposure time, fastest to slowest"),
)
# (field, section title, noun for the search hint)
_PICKERS = (
    ("cameras", "Cameras", "cameras"),
    ("lenses", "Lenses", "lenses"),
    ("extensions", "File extensions", "extensions"),
    ("people", "People", "people"),
)
_LISTS = tuple(name for name, _, _ in _PICKERS)


class FilterPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._syncing = False  # widgets are being set from code, not edited
        self._choices = RepoQuery(ctx, self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)

        self.texts: dict[str, QLineEdit] = {}
        for name, _label, placeholder in _TEXT_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setClearButtonEnabled(True)
            edit.textEdited.connect(self._edited)
            self.texts[name] = edit
        self.date_fields = {
            "date_from": DateField(self.texts["date_from"], "Jump to first photo"),
            "date_to": DateField(self.texts["date_to"], "Jump to last photo"),
        }
        for field in self.date_fields.values():
            field.edit.setToolTip(DATE_HINT)
            field.picked.connect(self._edited)
        self.filetypes: dict[str, QCheckBox] = {}
        for value, label in FILE_TYPES:
            box = QCheckBox(label)
            box.toggled.connect(self._edited)
            self.filetypes[value] = box
        for name, _title, noun in _PICKERS:
            picker = ChipPicker(noun)
            picker.changed.connect(self._edited)
            setattr(self, name, picker)

        content = QWidget()
        body = QVBoxLayout(content)
        body.addWidget(self._dates_box())
        types = QGroupBox("Type")
        types_layout = QVBoxLayout(types)
        for box in self.filetypes.values():
            types_layout.addWidget(box)
        body.addWidget(types)
        self._boxes: dict[str, QGroupBox] = {}
        for name, title, _noun in _PICKERS:
            box = QGroupBox(title)
            QVBoxLayout(box).addWidget(getattr(self, name))
            self._boxes[name] = box
            if name != "people":
                body.addWidget(box)
        body.addWidget(self._exposure_box())
        self.people_box = self._boxes["people"]
        self.people_box.setVisible(False)
        body.addWidget(self.people_box)
        body.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        self.errors = QLabel()
        self.errors.setWordWrap(True)
        self.errors.setStyleSheet("color: #d04040;")
        self.errors.setVisible(False)
        self.apply_btn = QPushButton("Apply")
        self.clear_btn = QPushButton("Clear")
        buttons = QHBoxLayout()
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.errors)
        layout.addLayout(buttons)
        self.setMinimumWidth(250)

        self._timer.timeout.connect(self.apply)
        self.apply_btn.clicked.connect(self.apply)
        self.clear_btn.clicked.connect(self.clear)
        self._choices.finished.connect(self._set_choices)
        self._choices.failed.connect(lambda _error: self._set_choices({}))
        ctx.filters_changed.connect(self._follow)
        ctx.gallery_changed.connect(self.reload_choices)
        ctx.catalog_updated.connect(self.reload_choices)

        self.set_values(from_filter_options(ctx.filters))
        self.reload_choices()

    def _dates_box(self) -> QGroupBox:
        box = QGroupBox("Date taken")
        row = QHBoxLayout(box)
        row.addWidget(self.date_fields["date_from"], 1)
        row.addWidget(QLabel(RANGE_DASH))
        row.addWidget(self.date_fields["date_to"], 1)
        return box

    def _exposure_box(self) -> QGroupBox:
        box = QGroupBox("Exposure")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        for i, (label, low, high, meaning) in enumerate(_EXPOSURE):
            name = QLabel(label)
            name.setToolTip(meaning)
            grid.addWidget(name, i, 0)
            grid.addWidget(self.texts[low], i, 1)
            grid.addWidget(QLabel(RANGE_DASH), i, 2)
            grid.addWidget(self.texts[high], i, 3)
        return box

    # ── values ──────────────────────────────────────────────────────────────
    def values(self) -> FilterValues:
        return FilterValues(
            **{name: edit.text() for name, edit in self.texts.items()},
            filetypes=tuple(v for v, box in self.filetypes.items() if box.isChecked()),
            **{name: getattr(self, name).selected() for name in _LISTS},
        )

    def set_values(self, values: FilterValues) -> None:
        self._syncing = True
        try:
            for name, edit in self.texts.items():
                edit.setText(getattr(values, name))
            for value, box in self.filetypes.items():
                box.setChecked(value in values.filetypes)
            for name in _LISTS:
                getattr(self, name).set_selected(getattr(values, name))
            self._show_errors({})
        finally:
            self._syncing = False

    # ── applying ────────────────────────────────────────────────────────────
    def _edited(self, *_args) -> None:
        if not self._syncing:
            self._timer.start()

    def apply(self) -> None:
        self._timer.stop()
        options, errors = to_filter_options(self.values())
        self._show_errors(errors)
        if errors:
            return
        options = options or FilterOptions()
        if options != self.ctx.filters:
            self.ctx.set_filters(options)

    def clear(self) -> None:
        self.set_values(FilterValues())
        self.apply()

    def _show_errors(self, errors: dict[str, str]) -> None:
        for name, edit in self.texts.items():
            edit.setStyleSheet(ERROR_STYLE if name in errors else "")
            default = DATE_HINT if name in self.date_fields else ""
            edit.setToolTip(errors.get(name, default))
        lines = [f"{_LABELS[name]}: {message}" for name, message in errors.items()]
        self.errors.setText("\n".join(lines))
        self.errors.setVisible(bool(lines))

    def _follow(self, options: FilterOptions) -> None:
        """Show filters set elsewhere (another panel) without re-applying."""
        mine, errors = to_filter_options(self.values())
        if not errors and (mine or FilterOptions()) == options:
            return
        self._timer.stop()
        self.set_values(from_filter_options(options))

    # ── choices ─────────────────────────────────────────────────────────────
    def reload_choices(self, *_args) -> None:
        if self.ctx.current is None:
            self._choices.cancel()
            self._set_choices({})
            return
        self._choices.run(filter_choices)

    def _set_choices(self, choices: dict[str, list]) -> None:
        values = self.values()
        for name in _LISTS:
            getattr(self, name).set_options(choices.get(name, []))
        self.people_box.setVisible("people" in choices or bool(values.people))
        bounds = choices.get("date_bounds") or []
        first, last = (
            (date.fromisoformat(b) for b in bounds) if bounds else (None, None)
        )
        self.date_fields["date_from"].jump_to = first
        self.date_fields["date_to"].jump_to = last
