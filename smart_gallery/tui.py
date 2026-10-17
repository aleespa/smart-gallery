"""Interactive terminal interface for running Smart Gallery commands.

Each command's form is generated from the CLI parser (see ``tui_forms``):
required arguments first, then one foldable section per option group, every
option with a real control and its help text as a hint. A live preview shows
the exact command line; Run executes it as a ``python -m smart_gallery.cli``
subprocess and streams its output.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
    Switch,
)

from smart_gallery.cli import build_parser
from smart_gallery.progress import PROGRESS_ENV, ProgressEvent
from smart_gallery.tui_forms import (
    REQUIRED,
    BuildResult,
    CommandForm,
    Field,
    Row,
    build_argv,
    command_forms,
    default_values,
    field_rows,
    format_command,
)
from smart_gallery.tui_progress import ProgressPanel

# Carried from one command to the next so the drive is typed only once.
DRIVE_KEY = "drive"

PLACEHOLDERS = {
    "path": "path",
    "path_list": "one or more paths; quote paths containing spaces",
    "list": "values separated by spaces; quote values containing spaces",
    "int_list": "numbers separated by spaces",
    "int": "whole number",
    "float": "number",
    "date": "YYYY-MM-DD",
}

SUPERSCRIPTS = "¹²³⁴⁵⁶⁷⁸⁹"


def _section_id(title: str) -> str:
    return "section-" + title.replace(" ", "-")


class ChoiceRow(Horizontal):
    """Checkboxes for a multi-choice option. When ``ordered``, the order boxes
    are ticked in is the value order, and each label shows its position."""

    def __init__(
        self, choices: tuple[str, ...], value: tuple[str, ...], ordered: bool, id: str
    ) -> None:
        super().__init__(id=id, classes="choice-row")
        self.choices = choices
        self.ordered = ordered
        self._picked = [c for c in value if c in choices]

    def compose(self) -> ComposeResult:
        for choice in self.choices:
            yield Checkbox(
                self._label(choice),
                value=choice in self._picked,
                id=f"{self.id}-{choice}",
                compact=True,
            )

    @property
    def value(self) -> tuple[str, ...]:
        if self.ordered:
            return tuple(self._picked)
        return tuple(c for c in self.choices if c in self._picked)

    def _label(self, choice: str) -> str:
        if self.ordered and choice in self._picked:
            return f"{choice} {SUPERSCRIPTS[self._picked.index(choice)]}"
        return choice

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # Runs before the event bubbles on to the app, which then rebuilds the
        # preview from ``value``.
        choice = event.checkbox.id.removeprefix(f"{self.id}-")
        if event.value and choice not in self._picked:
            self._picked.append(choice)
        elif not event.value and choice in self._picked:
            self._picked.remove(choice)
        if self.ordered:
            for checkbox in self.query(Checkbox):
                checkbox.label = self._label(checkbox.id.removeprefix(f"{self.id}-"))


class HelpScreen(ModalScreen[None]):
    """The command's full ``--help`` text."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("f1", "dismiss", "Close"),
    ]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help-box { width: 90%; height: 85%; border: round $primary; background: $surface; }
    #help-title { text-style: bold; padding: 0 1; }
    """

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.help_title = title
        self.help_text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Label(self.help_title, id="help-title")
            with VerticalScroll():
                yield Static(self.help_text, id="help-text", markup=False)


class SmartGalleryApp(App):
    """Command launcher: generated option forms, live preview, streamed output."""

    TITLE = "Smart Gallery"
    SUB_TITLE = "Terminal command center"
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar { width: 22; border: round $primary; padding: 0 1; }
    #commands { height: 1fr; }
    #main { width: 1fr; padding: 0 1; }
    #command-title { height: auto; text-style: bold; color: $accent; }
    #preview-bar { height: auto; }
    #preview { width: 1fr; height: auto; min-height: 3; padding: 0 1;
               border: round $success; color: $success; }
    #run, #stop { width: 10; min-width: 10; margin-left: 1; }
    #problems { height: 1; color: $warning; }
    #form { height: 3fr; border: round $primary; }
    #progress { border: round $primary; padding: 0 1; }
    #output { height: 1fr; min-height: 6; border: round $primary; }
    #status { height: 1; color: $text-muted; }
    .field { height: auto; }
    .field-row { height: auto; }
    .field-label { width: 24; }
    .field-row Input, .field-row Select, .field-row .choice-row { width: 1fr; }
    .field-row Input { background: $boost; }
    .field-row Switch { border: none; height: 1; padding: 0; width: 8; }
    .choice-row { height: auto; }
    .choice-row Checkbox { width: auto; margin-right: 2; }
    .range-sep { width: 3; content-align: center middle; }
    .hint { height: auto; padding-left: 24; color: $text-muted; }
    .field-error { height: auto; padding-left: 24; color: $error; display: none; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("f1", "show_help", "Full help"),
        Binding("escape", "reset_form", "Reset fields"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.forms = command_forms(build_parser())
        self.form: CommandForm | None = None
        self.result: BuildResult | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.saved: dict[str, tuple[dict, str]] = {}
        self.last_drive = ""
        self.show_missing = False

    # ── layout ───────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("COMMANDS")
                yield ListView(
                    *(
                        ListItem(Label(name), id=f"command-{name}")
                        for name in self.forms
                    ),
                    id="commands",
                )
            with Vertical(id="main"):
                yield Static(
                    "Select a command on the left.", id="command-title", markup=False
                )
                with Horizontal(id="preview-bar"):
                    yield Static("", id="preview", markup=False)
                    yield Button("Run", id="run", variant="primary")
                    yield Button("Stop", id="stop", variant="error", disabled=True)
                yield Static("", id="problems", markup=False)
                yield VerticalScroll(id="form")
                yield ProgressPanel(id="progress")
                yield RichLog(
                    id="output",
                    highlight=False,
                    markup=False,
                    wrap=True,
                    auto_scroll=True,
                )
                yield Static("Ready", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#output").border_title = "Output"
        self.query_one("#progress").border_title = "Progress"

    def _form_widgets(self, form: CommandForm, values: dict, extra: str):
        for section in form.sections:
            yield Collapsible(
                *(self._row(row, values) for row in field_rows(section.fields)),
                title=section.title,
                collapsed=section.collapsed,
                id=_section_id(section.title),
            )
        yield Collapsible(
            Vertical(
                Horizontal(
                    Label("Extra flags", classes="field-label"),
                    Input(
                        extra,
                        placeholder="e.g. --min-iso 100",
                        id="extra-options",
                        compact=True,
                    ),
                    classes="field-row",
                ),
                Static(
                    "Anything not covered above, appended to the command as typed.",
                    classes="hint",
                ),
                Static("", id="error-extra", classes="field-error", markup=False),
                classes="field",
            ),
            title="More CLI options",
            collapsed=False,
            id="section-extra",
        )

    def _row(self, row: Row, values: dict) -> Vertical:
        controls: list[Widget] = []
        for index, f in enumerate(row.fields):
            if index:
                controls.append(Static("→", classes="range-sep"))
            controls.append(self._control(f, values.get(f.key)))
        label = row.label + (" *" if any(f.required for f in row.fields) else "")
        return Vertical(
            Horizontal(
                Label(label, classes="field-label"), *controls, classes="field-row"
            ),
            Static(row.hint, classes="hint", markup=False),
            *(
                Static("", id=f"error-{f.key}", classes="field-error", markup=False)
                for f in row.fields
            ),
            classes="field",
        )

    @staticmethod
    def _control(f: Field, value) -> Widget:
        widget_id = f"field-{f.key}"
        if f.kind == "switch":
            return Switch(value=bool(value), id=widget_id)
        if f.kind == "choice":
            return Select(
                [(c, c) for c in f.choices],
                value=Select.NULL if value is None else value,
                allow_blank=f.default is None,
                compact=True,
                id=widget_id,
            )
        if f.kind == "mode":
            return Select(
                [(c.lstrip("-"), c) for c in f.choices],
                value=value or Select.NULL,
                prompt="default",
                compact=True,
                id=widget_id,
            )
        if f.kind in ("multi", "ordered"):
            return ChoiceRow(
                f.choices, tuple(value or ()), ordered=f.kind == "ordered", id=widget_id
            )
        return Input(
            str(value or ""),
            placeholder=PLACEHOLDERS.get(f.kind, ""),
            compact=True,
            id=widget_id,
        )

    # ── form state ───────────────────────────────────────────────────────────
    @on(ListView.Selected, "#commands")
    async def select_command(self, event: ListView.Selected) -> None:
        name = event.item.id.removeprefix("command-")
        if self.form is not None:
            self.saved[self.form.name] = (self._values(), self._extra())
        values, extra = self.saved.get(name, (None, ""))
        if values is None:
            values = default_values(self.forms[name])
            if DRIVE_KEY in values and self.last_drive:
                values[DRIVE_KEY] = self.last_drive
        await self._mount_form(name, values, extra)

    async def _mount_form(self, name: str, values: dict, extra: str) -> None:
        self.form = self.forms[name]
        self.show_missing = False
        container = self.query_one("#form", VerticalScroll)
        await container.remove_children()
        await container.mount_all(self._form_widgets(self.form, values, extra))
        container.scroll_home(animate=False)
        container.border_title = name
        self.query_one("#command-title", Static).update(
            f"{name} — {self.form.description}  (F1: full help)"
        )
        self.refresh_preview()

    def _values(self) -> dict:
        values = {}
        for key, f in self.form.fields.items():
            value = self.query_one(f"#field-{key}").value
            if f.kind in ("choice", "mode") and not isinstance(value, str):
                value = None  # Select.NULL: nothing picked
            values[key] = value
        return values

    def _extra(self) -> str:
        return self.query_one("#extra-options", Input).value

    @on(Input.Changed)
    @on(Switch.Changed)
    @on(Select.Changed)
    @on(Checkbox.Changed)
    def _form_changed(self, event) -> None:
        if isinstance(event, Input.Changed) and event.input.id == f"field-{DRIVE_KEY}":
            self.last_drive = event.value.strip()
        self.refresh_preview()

    def refresh_preview(self) -> None:
        """Rebuild the command line from the form and show what's wrong with it."""
        if self.form is None:
            return
        try:
            result = build_argv(self.form, self._values(), self._extra())
        except NoMatches:  # the form is still being mounted
            return
        self.result = result
        self.query_one("#preview", Static).update("$ " + format_command(result.argv))

        for key in [*self.form.fields, "extra"]:
            message = result.errors.get(key)
            if message is None and self.show_missing and key in result.missing:
                message = "Required."
            error = self.query_one(f"#error-{key}", Static)
            error.update(message or "")
            error.display = bool(message)

        self.query_one("#problems", Static).update(self._problems(result))

        for section in self.form.sections:
            if section.title == REQUIRED:
                continue
            count = sum(1 for f in section.fields if f.key in result.changed)
            collapsible = self.query_one(f"#{_section_id(section.title)}", Collapsible)
            collapsible.title = (
                f"{section.title}  ({count} set)" if count else section.title
            )

    def _problems(self, result: BuildResult) -> str:
        parts = []
        if result.missing:
            labels = [self.form.fields[k].label for k in result.missing]
            parts.append("Needs: " + ", ".join(labels))
        if result.errors:
            labels = [
                (
                    self.form.fields[k].label
                    if k in self.form.fields
                    else "More CLI options"
                )
                for k in result.errors
            ]
            parts.append("Fix: " + ", ".join(labels))
        return "   ".join(parts)

    def _unfold_problem_sections(self, result: BuildResult) -> None:
        """Open folded sections that hold a field needing attention."""
        flagged = set(result.errors) | set(result.missing)
        for section in self.form.sections:
            if any(f.key in flagged for f in section.fields):
                self.query_one(
                    f"#{_section_id(section.title)}", Collapsible
                ).collapsed = False

    async def action_reset_form(self) -> None:
        if self.form is None or self._command_running():
            return
        self.saved.pop(self.form.name, None)
        await self._mount_form(self.form.name, default_values(self.form), "")

    def action_show_help(self) -> None:
        if self.form is not None:
            self.push_screen(
                HelpScreen(
                    f"smart-gallery {self.form.name} --help", self.form.help_text
                )
            )

    # ── running ──────────────────────────────────────────────────────────────
    def _command_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    @on(Button.Pressed, "#run")
    @on(Input.Submitted, "#extra-options")
    def run_command(self) -> None:
        if self._command_running():
            return
        status = self.query_one("#status", Static)
        if self.form is None:
            status.update("Select a command first.")
            return
        self.show_missing = True
        self.refresh_preview()
        if self.result is None or not self.result.ok:
            status.update(self._problems(self.result))
            self._unfold_problem_sections(self.result)
            return
        argv = self.result.argv
        self.query_one("#output", RichLog).write(f"\n$ {format_command(argv)}")
        status.update("Running…")
        self.query_one("#run", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        self.run_worker(self._execute(argv), exclusive=True)

    @on(Button.Pressed, "#stop")
    def stop_command(self) -> None:
        if self._command_running():
            self.process.terminate()
            self.query_one("#status", Static).update("Stopping command…")

    async def _execute(self, argv: list[str]) -> None:
        output = self.query_one("#output", RichLog)
        status = self.query_one("#status", Static)
        panel = self.query_one(ProgressPanel)
        await panel.reset()
        return_code = None
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "smart_gallery.cli",
                *argv,
                cwd=Path.cwd(),
                env={
                    **os.environ,
                    # The child writes to a pipe, which would use the locale
                    # code page (cp1252 on Windows) and garble "—" and the like.
                    "PYTHONIOENCODING": "utf-8",
                    # Report progress as tagged lines for the panel, not a bar.
                    PROGRESS_ENV: "json",
                },
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert self.process.stdout is not None
            while line := await self.process.stdout.readline():
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                event = ProgressEvent.from_line(text)
                if event is not None:
                    await panel.show_event(event)
                else:
                    # Log lines may carry ANSI colors; render them, not the codes.
                    output.write(Text.from_ansi(text))
            return_code = await self.process.wait()
            if return_code == 0:
                status.update("Completed successfully.")
            else:
                status.update(f"Command exited with status {return_code}.")
        except Exception as exc:  # noqa: BLE001
            output.write(f"Could not run command: {exc}")
            status.update("Command failed to start.")
        finally:
            panel.finish(ok=return_code == 0)
            self.process = None
            self.query_one("#run", Button).disabled = False
            self.query_one("#stop", Button).disabled = True


def run() -> None:
    """Start the Textual command interface."""
    SmartGalleryApp().run()
