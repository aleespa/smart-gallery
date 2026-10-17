"""Dialog for creating and editing an import profile."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.forms import default_values
from smart_gallery.gui.actions.fields import FieldEditor, make_editor
from smart_gallery.gui.actions.model import gui_forms
from smart_gallery.import_profiles import ImportProfile, ImportRule

# Values the rule itself supplies (or the run chooses), so they aren't options.
_NOT_OPTIONS = frozenset(
    {"sources", "drive", "output", "file_types", "extensions", "dry_run"}
)


def _norm(value):
    if value is None:
        return ""
    return tuple(value) if isinstance(value, (list, tuple)) else value


class RuleEditor(QWidget):
    """One rule: file type, extensions, output folder and the import options."""

    def __init__(self, rule: ImportRule, parent: QWidget | None = None):
        super().__init__(parent)
        form = gui_forms()["import"]
        self._defaults = default_values(form)
        layout = QFormLayout(self)
        self.file_type = QComboBox()
        self.file_type.addItems(["image", "video"])
        self.file_type.setCurrentText(rule.file_type)
        self.extensions = QLineEdit(" ".join(rule.extensions))
        self.extensions.setPlaceholderText(".cr3 .jpg")
        self.output = QLineEdit(rule.output)
        self.output.setPlaceholderText("Folder inside the gallery, e.g. Photos/Canon")
        layout.addRow("File type", self.file_type)
        layout.addRow("Extensions", self.extensions)
        layout.addRow("Output folder", self.output)
        self.option_editors: dict[str, FieldEditor] = {}
        for section in form.sections:
            for field in section.fields:
                if field.key in _NOT_OPTIONS:
                    continue
                editor = make_editor(field, "import")
                editor.set_value(rule.options.get(field.key, self._defaults[field.key]))
                editor.setToolTip(field.hint)
                self.option_editors[field.key] = editor
                layout.addRow(field.label, editor)

    def rule(self) -> ImportRule:
        options = {}
        for key, editor in self.option_editors.items():
            value = editor.value()
            if _norm(value) != _norm(self._defaults[key]):
                options[key] = value
        return ImportRule(
            file_type=self.file_type.currentText(),
            extensions=self.extensions.text().replace(",", " ").split(),
            output=self.output.text().strip().replace("\\", "/"),
            options=options,
        )


class ProfileEditor(QDialog):
    def __init__(self, profile: ImportProfile | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import profile")
        self.resize(640, 560)
        profile = profile or ImportProfile(
            "", [ImportRule("image", [".jpg"], "Photos", {})]
        )
        self.name_edit = QLineEdit(profile.name)
        self.name_edit.setPlaceholderText("Profile name")
        self.rule_list = QListWidget()
        self.rule_list.setMaximumHeight(90)
        self.stack = QStackedWidget()
        self.rule_editors: list[RuleEditor] = []
        self.add_button = QPushButton("Add rule")
        self.remove_button = QPushButton("Remove rule")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )

        header = QHBoxLayout()
        header.addWidget(self.add_button)
        header.addWidget(self.remove_button)
        header.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.stack)
        layout = QVBoxLayout(self)
        layout.addWidget(self.name_edit)
        layout.addWidget(self.rule_list)
        layout.addLayout(header)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)

        self.add_button.clicked.connect(lambda: self._add(None))
        self.remove_button.clicked.connect(self._remove)
        self.rule_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        for rule in profile.rules:
            self._add(rule)

    def _add(self, rule: ImportRule | None) -> None:
        rule = rule or ImportRule("image", [".jpg"], "Photos", {})
        editor = RuleEditor(rule)
        self.rule_editors.append(editor)
        self.stack.addWidget(editor)
        self.rule_list.addItem(f"Rule {len(self.rule_editors)}")
        self.rule_list.setCurrentRow(len(self.rule_editors) - 1)

    def _remove(self) -> None:
        row = self.rule_list.currentRow()
        if row < 0:
            return
        editor = self.rule_editors.pop(row)
        self.stack.removeWidget(editor)
        editor.deleteLater()
        self.rule_list.takeItem(self.rule_list.count() - 1)  # numbering stays 1..n

    def profile(self) -> ImportProfile:
        return ImportProfile(
            self.name_edit.text().strip(), [e.rule() for e in self.rule_editors]
        )

    def _accept(self) -> None:
        if not self.name_edit.text().strip() or not self.rule_editors:
            self.name_edit.setFocus()
            QMessageBox.warning(
                self, "Import profile", "Give the profile a name and at least one rule."
            )
            return
        self.accept()
