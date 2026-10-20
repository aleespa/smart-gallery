"""Actions area: a generated form per CLI command, run as a background job."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.forms import (
    REQUIRED,
    BuildResult,
    CommandForm,
    Row,
    Section,
    build_argv,
    field_rows,
    format_command,
)
from smart_gallery.gui import theme
from smart_gallery.gui.actions import model
from smart_gallery.gui.actions.fields import FieldEditor, make_editor
from smart_gallery.gui.actions.model import READ_ONLY_COMMANDS
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.jobs import JobSpec

logger = logging.getLogger(__name__)

EXTRA = "extra"
LABEL_WIDTH = 170
JOB_RUNNING = "A job is already running."
NO_JOBS = "Jobs are not available."


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def _error_label() -> QLabel:
    label = QLabel()
    label.setWordWrap(True)
    label.setStyleSheet(theme.ERROR_TEXT_STYLE)
    label.setVisible(False)
    return label


class SectionBox(QGroupBox):
    """A group box for one form section. A collapsed section can be folded with
    its title check box; its contents are hidden while folded."""

    def __init__(self, section: Section):
        super().__init__(section.title)
        self.section_title = section.title
        self.content = QWidget()
        self.form_layout = QFormLayout(self.content)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        QVBoxLayout(self).addWidget(self.content)
        if section.collapsed:
            self.setCheckable(True)
            self.setChecked(False)
            self.content.setVisible(False)
            self.toggled.connect(self.content.setVisible)

    def is_folded(self) -> bool:
        return self.content.isHidden()

    def unfold(self) -> None:
        if self.isCheckable():
            self.setChecked(True)

    def show_count(self, count: int) -> None:
        suffix = f"  ({count} set)" if count and self.isCheckable() else ""
        self.setTitle(self.section_title + suffix)


class CommandPage(QScrollArea):
    """The editable form for one command. Keeps its own values while another
    command is shown."""

    changed = Signal()

    def __init__(self, form: CommandForm, values: dict):
        super().__init__()
        self.form = form
        self.editors: dict[str, FieldEditor] = {}
        self.error_labels: dict[str, QLabel] = {}
        self.sections: dict[str, SectionBox] = {}
        self.edited: set[str] = set()  # keys the user has typed into
        self.show_missing = False
        self.result = BuildResult(argv=[form.name])
        self._applying = False

        body = QWidget()
        layout = QVBoxLayout(body)
        for section in form.sections:
            box = SectionBox(section)
            self.sections[section.title] = box
            for row in field_rows(section.fields):
                box.form_layout.addRow(*self._row(row))
            layout.addWidget(box)
        layout.addWidget(self._extra_box())
        layout.addStretch(1)
        self.setWidget(body)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        self.set_values(values)
        self.refresh()

    def _row(self, row: Row) -> tuple[QLabel, QWidget]:
        label = QLabel(
            row.label + (" *" if any(f.required for f in row.fields) else "")
        )
        label.setFixedWidth(LABEL_WIDTH)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        cell = QWidget()
        column = QVBoxLayout(cell)
        column.setContentsMargins(0, 0, 0, 6)
        controls = QHBoxLayout()
        for index, f in enumerate(row.fields):
            if index:
                controls.addWidget(QLabel("→"))
            editor = make_editor(f, command=self.form.name)
            editor.changed.connect(lambda key=f.key: self._edited(key))
            self.editors[f.key] = editor
            controls.addWidget(editor, 1)
        column.addLayout(controls)
        column.addWidget(_hint(row.hint))
        for f in row.fields:
            self.error_labels[f.key] = _error_label()
            column.addWidget(self.error_labels[f.key])
        return label, cell

    def _extra_box(self) -> QGroupBox:
        box = QGroupBox("Extra options")
        layout = QVBoxLayout(box)
        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText("e.g. --min-iso 100")
        self.extra_edit.textChanged.connect(lambda _text: self.refresh())
        layout.addWidget(self.extra_edit)
        layout.addWidget(
            _hint("Anything not covered above, appended to the command as typed.")
        )
        self.error_labels[EXTRA] = _error_label()
        layout.addWidget(self.error_labels[EXTRA])
        return box

    def _edited(self, key: str) -> None:
        if not self._applying:
            self.edited.add(key)
        self.refresh()

    # ── values ───────────────────────────────────────────────────────────────
    def values(self) -> dict:
        return {key: editor.value() for key, editor in self.editors.items()}

    def set_values(self, values: dict, mark_edited: bool = False) -> None:
        self._applying = True
        try:
            for key, value in values.items():
                if key in self.editors:
                    self.editors[key].set_value(value)
                    if mark_edited:
                        self.edited.add(key)
        finally:
            self._applying = False
        self.refresh()

    def set_default_drive(self, value: str) -> None:
        """Point the drive field at the open gallery, unless the user chose one."""
        if model.DRIVE_KEY in self.editors and model.DRIVE_KEY not in self.edited:
            self.set_values({model.DRIVE_KEY: value})

    # ── validation ───────────────────────────────────────────────────────────
    def refresh(self) -> BuildResult:
        """Rebuild the command from the form and show what is wrong with it."""
        result = build_argv(self.form, self.values(), self.extra_edit.text())
        self.result = result
        for key, label in self.error_labels.items():
            message = result.errors.get(key)
            if message is None and self.show_missing and key in result.missing:
                message = "Required."
            label.setText(message or "")
            label.setVisible(bool(message))
        for section in self.form.sections:
            if section.title != REQUIRED:
                count = sum(1 for f in section.fields if f.key in result.changed)
                self.sections[section.title].show_count(count)
        self.changed.emit()
        return result

    def unfold_problems(self, result: BuildResult) -> None:
        flagged = set(result.errors) | set(result.missing)
        for section in self.form.sections:
            if any(f.key in flagged for f in section.fields):
                self.sections[section.title].unfold()


class ActionsView(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.forms = model.gui_forms()
        self._pages: dict[str, CommandPage] = {}
        self._busy = bool(ctx.jobs is not None and ctx.jobs.running)

        self.commands = QListWidget()
        self.commands.setFixedWidth(180)
        for name in self.forms:
            self.commands.addItem(name)

        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight: bold;")
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(theme.WARNING_BANNER_STYLE)
        self.banner.setVisible(False)
        self.stack = QStackedWidget()
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.preview.setStyleSheet("font-family: Consolas, monospace; padding: 4px;")
        self.copy_button = QPushButton("Copy as CLI command")
        self.run_button = QPushButton("Run")
        self.run_button.setDefault(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.preview, 1)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.run_button)
        right = QVBoxLayout()
        right.addWidget(self.title)
        right.addWidget(self.banner)
        right.addWidget(self.stack, 1)
        right.addLayout(buttons)
        layout = QHBoxLayout(self)
        layout.addWidget(self.commands)
        layout.addLayout(right, 1)

        self.commands.currentRowChanged.connect(self._show_row)
        self.copy_button.clicked.connect(self._copy)
        self.run_button.clicked.connect(self._run)
        ctx.gallery_changed.connect(self._on_gallery_changed)
        ctx.open_in_actions.connect(self._open_argv)
        if ctx.jobs is not None:
            ctx.jobs.started.connect(lambda _spec: self._set_busy(True))
            ctx.jobs.finished.connect(lambda _record: self._set_busy(False))
        self.commands.setCurrentRow(0)

    # ── access (also used by tests) ──────────────────────────────────────────
    def page(self) -> CommandPage:
        return self.stack.currentWidget()

    def editor(self, key: str) -> FieldEditor:
        return self.page().editors[key]

    @property
    def extra_edit(self) -> QLineEdit:
        return self.page().extra_edit

    def section(self, title: str) -> SectionBox:
        return self.page().sections[title]

    def error_text(self, key: str) -> str:
        return self.page().error_labels[key].text()

    # ── selection ────────────────────────────────────────────────────────────
    def select_command(self, name: str) -> None:
        row = list(self.forms).index(name)
        self.commands.setCurrentRow(row)

    def _gallery_root(self) -> str | None:
        gallery = self.ctx.current
        return str(gallery.root) if gallery is not None else None

    def _show_row(self, row: int) -> None:
        if row < 0:
            return
        name = self.commands.item(row).text()
        page = self._pages.get(name)
        if page is None:
            form = self.forms[name]
            page = CommandPage(form, model.initial_values(form, self._gallery_root()))
            page.changed.connect(lambda p=page: self._page_changed(p))
            self._pages[name] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.title.setText(f"{name} — {page.form.description}")
        self._show_state()

    def _page_changed(self, page: CommandPage) -> None:
        if page is self.page():
            self._show_state()

    def _show_state(self) -> None:
        page = self.page()
        self.preview.setText(format_command(page.result.argv))
        reason = model.unavailable_reason(page.form.name)
        self.banner.setText(reason or "")
        self.banner.setVisible(bool(reason))
        if self.ctx.jobs is None:
            tip = NO_JOBS
        else:
            tip = JOB_RUNNING if self._busy else ""
        self.run_button.setToolTip(tip)
        self.run_button.setEnabled(
            self.ctx.jobs is not None and not self._busy and reason is None
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._show_state()

    # ── context signals ──────────────────────────────────────────────────────
    def _on_gallery_changed(self, gallery) -> None:
        root = str(gallery.root) if gallery is not None else None
        for page in self._pages.values():
            if model.DRIVE_KEY in page.form.fields:
                values = model.initial_values(page.form, root)
                page.set_default_drive(values[model.DRIVE_KEY])

    def _open_argv(self, argv) -> None:
        """Show ``argv`` in its command's form (from History, Library, ...)."""
        name = model.command_name(argv[0]) if argv else None
        if name not in self.forms:
            return
        try:
            values = model.values_from_argv(self.forms[name], list(argv))
        except ValueError as exc:
            logger.warning("Cannot show %s in Actions: %s", argv, exc)
            return
        self.select_command(name)
        self.page().set_values(values, mark_edited=True)
        self.page().extra_edit.clear()

    # ── buttons ──────────────────────────────────────────────────────────────
    def _copy(self) -> None:
        QApplication.clipboard().setText(format_command(self.page().result.argv))

    def _run(self) -> None:
        page = self.page()
        page.show_missing = True
        result = page.refresh()
        if not result.ok:
            page.unfold_problems(result)
            return
        if not self.run_button.isEnabled():
            return
        form = page.form
        self.ctx.jobs.start(
            JobSpec(
                form.name,
                (tuple(result.argv),),
                writes_catalog=form.name not in READ_ONLY_COMMANDS,
            )
        )
