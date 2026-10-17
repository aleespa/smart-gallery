"""Filter panel shared by the Library and the Dashboard.

It edits a ``FilterValues`` and publishes the parsed query with
``ctx.set_filters`` on Apply or after a short pause in editing. Every panel
also follows ``filters_changed``, so two panels never show different filters.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui.context import AppContext
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
DATE_PLACEHOLDER = "YYYY-MM-DD"
ERROR_STYLE = "border: 1px solid #d04040;"

# (field, label, placeholder) for the text boxes, in panel order.
_TEXT_FIELDS = (
    ("date_from", "From", DATE_PLACEHOLDER),
    ("date_to", "To", DATE_PLACEHOLDER),
    ("min_aperture", "Aperture from", "f-number, e.g. 1.8"),
    ("max_aperture", "Aperture to", "e.g. 8"),
    ("min_iso", "ISO from", "e.g. 100"),
    ("max_iso", "ISO to", "e.g. 3200"),
    ("min_shutter", "Shutter from", "fastest, e.g. 1/1000"),
    ("max_shutter", "Shutter to", "slowest, e.g. 2s"),
)
_LABELS = {name: label for name, label, _ in _TEXT_FIELDS}
_LISTS = ("cameras", "lenses", "extensions", "people")


def _checklist() -> QListWidget:
    widget = QListWidget()
    widget.setMaximumHeight(130)
    return widget


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
        self.filetypes: dict[str, QCheckBox] = {}
        for value, label in FILE_TYPES:
            box = QCheckBox(label)
            box.toggled.connect(self._edited)
            self.filetypes[value] = box
        self.cameras = _checklist()
        self.lenses = _checklist()
        self.extensions = _checklist()
        self.people = _checklist()
        for name in _LISTS:
            getattr(self, name).itemChanged.connect(self._edited)

        content = QWidget()
        body = QVBoxLayout(content)
        body.addWidget(self._form("Date taken", "date_from", "date_to"))
        types = QGroupBox("Type")
        types_layout = QVBoxLayout(types)
        for box in self.filetypes.values():
            types_layout.addWidget(box)
        body.addWidget(types)
        body.addWidget(self._group("Cameras", self.cameras))
        body.addWidget(self._group("Lenses", self.lenses))
        body.addWidget(self._group("File extensions", self.extensions))
        exposure = [name for name, *_ in _TEXT_FIELDS[2:]]
        body.addWidget(self._form("Exposure", *exposure))
        self.people_box = self._group("People", self.people)
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
        self.setMinimumWidth(230)

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

    def _form(self, title: str, *names: str) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        for name in names:
            form.addRow(_LABELS[name], self.texts[name])
        return box

    @staticmethod
    def _group(title: str, widget: QWidget) -> QGroupBox:
        box = QGroupBox(title)
        QVBoxLayout(box).addWidget(widget)
        return box

    # ── values ──────────────────────────────────────────────────────────────
    def values(self) -> FilterValues:
        def checked(widget: QListWidget) -> tuple[str, ...]:
            return tuple(
                widget.item(i).text()
                for i in range(widget.count())
                if widget.item(i).checkState() == Qt.CheckState.Checked
            )

        return FilterValues(
            **{name: edit.text() for name, edit in self.texts.items()},
            filetypes=tuple(v for v, box in self.filetypes.items() if box.isChecked()),
            **{name: checked(getattr(self, name)) for name in _LISTS},
        )

    def set_values(self, values: FilterValues) -> None:
        self._syncing = True
        try:
            for name, edit in self.texts.items():
                edit.setText(getattr(values, name))
            for value, box in self.filetypes.items():
                box.setChecked(value in values.filetypes)
            for name in _LISTS:
                widget = getattr(self, name)
                options = [widget.item(i).text() for i in range(widget.count())]
                self._fill(widget, options, set(getattr(values, name)))
            self._show_errors({})
        finally:
            self._syncing = False

    @staticmethod
    def _fill(widget: QListWidget, options: list[str], checked: set[str]) -> None:
        """Show ``options`` plus any checked value missing from them; a filter
        is never dropped just because the catalog no longer offers it."""
        widget.clear()
        for text in options + sorted(checked - set(options)):
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = text in checked
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            widget.addItem(item)

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
            edit.setToolTip(errors.get(name, ""))
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

    def _set_choices(self, choices: dict[str, list[str]]) -> None:
        values = self.values()
        self._syncing = True
        try:
            for name in _LISTS:
                self._fill(
                    getattr(self, name),
                    choices.get(name, []),
                    set(getattr(values, name)),
                )
        finally:
            self._syncing = False
        self.people_box.setVisible("people" in choices or bool(values.people))
        bounds = choices.get("date_bounds") or []
        self.texts["date_from"].setPlaceholderText(
            f"e.g. {bounds[0]}" if bounds else DATE_PLACEHOLDER
        )
        self.texts["date_to"].setPlaceholderText(
            f"e.g. {bounds[1]}" if bounds else DATE_PLACEHOLDER
        )
