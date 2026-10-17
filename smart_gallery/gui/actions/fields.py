"""One editor widget per form field kind.

Every editor reads and writes the same value types as ``forms.default_values``
(text for input boxes, bool for switches, tuples for choice sets), so the
forms module can turn them into a command line without knowing about Qt.
"""

from __future__ import annotations

from PySide6.QtCore import QDir, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.forms import PLACEHOLDERS, Field
from smart_gallery.gui.actions.model import join_values

# Output files (not folders), by (command, key), with the save dialog's filter.
SAVE_FILE_FILTERS = {
    ("report", "output"): "Excel workbook (*.xlsx);;All files (*)",
    ("compare", "output"): "All files (*)",
}


class FieldEditor(QWidget):
    """Base class: ``value()``, ``set_value(v)`` and a ``changed`` signal."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)

    def value(self):
        raise NotImplementedError

    def set_value(self, value) -> None:
        raise NotImplementedError


class TextEditor(FieldEditor):
    """A line edit for text, lists, numbers and dates (kept as typed)."""

    def __init__(self, field: Field, parent: QWidget | None = None):
        super().__init__(parent)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(PLACEHOLDERS.get(field.kind, ""))
        self.edit.textChanged.connect(lambda _text: self.changed.emit())
        self.row.addWidget(self.edit, 1)

    def value(self) -> str:
        return self.edit.text()

    def set_value(self, value) -> None:
        self.edit.setText("" if value is None else str(value))


class PathEditor(TextEditor):
    """A line edit with a Browse button: folder, output file or "add folder"."""

    def __init__(self, field: Field, command: str = "", parent: QWidget | None = None):
        super().__init__(field, parent)
        self._filter = SAVE_FILE_FILTERS.get((command, field.key))
        self._adds = field.kind == "path_list"
        if self._filter:
            label = "Browse…"
        else:
            label = "Add folder…" if self._adds else "Browse…"
        self.browse = QPushButton(label)
        self.browse.clicked.connect(self._browse)
        self.row.addWidget(self.browse)

    def _start_dir(self) -> str:
        return "" if self._adds else self.edit.text().strip().strip('"')

    def _browse(self) -> None:
        if self._filter:
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Save as", self._start_dir(), self._filter
            )
        else:
            chosen = QFileDialog.getExistingDirectory(
                self, "Choose folder", self._start_dir()
            )
        if not chosen:
            return
        chosen = QDir.toNativeSeparators(chosen)
        if self._adds:
            current = self.edit.text().strip()
            self.edit.setText(f"{current} {join_values([chosen])}".strip())
        else:
            self.edit.setText(chosen)


class ChoiceEditor(FieldEditor):
    """A combo box for ``choice`` and ``mode`` fields; blank means "none"."""

    def __init__(self, field: Field, parent: QWidget | None = None):
        super().__init__(parent)
        self.combo = QComboBox()
        # A choice with a default always has a value; everything else may be unset.
        self._blank = field.kind == "mode" or field.default is None
        if self._blank:
            self.combo.addItem("", None)
        for choice in field.choices:
            text = choice.lstrip("-") if field.kind == "mode" else choice
            self.combo.addItem(text, choice)
        self.combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self._default = field.default
        self.row.addWidget(self.combo, 1)

    def value(self):
        return self.combo.currentData()

    def set_value(self, value) -> None:
        target = self._default if value is None else value
        index = 0 if target is None else self.combo.findData(target)
        self.combo.setCurrentIndex(max(index, 0))


class MultiEditor(FieldEditor):
    """Checkable list: the value is the ticked choices, in choice order."""

    def __init__(self, field: Field, parent: QWidget | None = None):
        super().__init__(parent)
        self.list = QListWidget()
        for choice in field.choices:
            item = QListWidgetItem(choice)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.setFixedHeight(self.list.sizeHintForRow(0) * len(field.choices) + 8)
        self.list.itemChanged.connect(lambda _item: self.changed.emit())
        self.row.addWidget(self.list, 1)

    def _items(self) -> list[QListWidgetItem]:
        return [self.list.item(i) for i in range(self.list.count())]

    def _checked(self) -> list[str]:
        return [
            i.text() for i in self._items() if i.checkState() == Qt.CheckState.Checked
        ]

    def value(self) -> tuple[str, ...]:
        return tuple(self._checked())

    def set_value(self, value) -> None:
        wanted = set(value or ())
        self.list.blockSignals(True)
        for item in self._items():
            item.setCheckState(
                Qt.CheckState.Checked
                if item.text() in wanted
                else Qt.CheckState.Unchecked
            )
        self.list.blockSignals(False)
        self.changed.emit()


class OrderedEditor(MultiEditor):
    """Like ``MultiEditor`` but the list order is the value order (Up/Down)."""

    def __init__(self, field: Field, parent: QWidget | None = None):
        super().__init__(field, parent)
        buttons = QVBoxLayout()
        self.up = QPushButton("Up")
        self.down = QPushButton("Down")
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        buttons.addWidget(self.up)
        buttons.addWidget(self.down)
        buttons.addStretch(1)
        self.row.addLayout(buttons)
        self.list.setFixedHeight(
            max(self.list.maximumHeight(), self.up.sizeHint().height() * 2 + 12)
        )

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.list.count():
            return
        self.list.blockSignals(True)
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self.list.blockSignals(False)
        self.changed.emit()

    def set_value(self, value) -> None:
        # Chosen items first, in value order; the rest keep their order after.
        chosen = [c for c in (value or ()) if self._find(c) is not None]
        self.list.blockSignals(True)
        for position, name in enumerate(chosen):
            self.list.insertItem(position, self.list.takeItem(self._find(name)))
        self.list.blockSignals(False)
        super().set_value(chosen)

    def _find(self, name: str) -> int | None:
        for row, item in enumerate(self._items()):
            if item.text() == name:
                return row
        return None


class SwitchEditor(FieldEditor):
    def __init__(self, field: Field, parent: QWidget | None = None):
        super().__init__(parent)
        self.check = QCheckBox()
        self.check.toggled.connect(lambda _on: self.changed.emit())
        self.row.addWidget(self.check)
        self.row.addStretch(1)

    def value(self) -> bool:
        return self.check.isChecked()

    def set_value(self, value) -> None:
        self.check.setChecked(bool(value))


def make_editor(field: Field, command: str = "") -> FieldEditor:
    """The editor for ``field``. ``command`` only picks a save dialog for output files."""
    kind = field.kind
    if kind in ("path", "path_list"):
        return PathEditor(field, command)
    if kind in ("choice", "mode"):
        return ChoiceEditor(field)
    if kind == "multi":
        return MultiEditor(field)
    if kind == "ordered":
        return OrderedEditor(field)
    if kind == "switch":
        return SwitchEditor(field)
    return TextEditor(field)
