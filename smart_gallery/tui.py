"""Interactive terminal interface for running Smart Gallery commands."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

COMMANDS = (
    ("init", "Create a catalog and scan a drive"),
    ("import", "Copy media into a catalog"),
    ("sync", "Reconcile a catalog with its drive"),
    ("compare", "Compare two catalogs"),
    ("export", "Export a filtered selection"),
    ("convert-raw", "Extract JPEG previews from RAW files"),
    ("dashboard", "Open the Streamlit dashboard"),
    ("report", "Write an Excel report"),
    ("scan-faces", "Detect and embed faces (GPU)"),
    ("identify-faces", "Match faces against sample folders"),
    ("cluster-faces", "Group unassigned faces"),
    ("people", "List people and photo counts"),
    ("name-person", "Name or clear a person cluster"),
    ("merge-persons", "Merge person clusters"),
    ("split-person", "Split an impure person cluster"),
    ("delete-person", "Delete a person cluster"),
)


@dataclass(frozen=True)
class FormField:
    label: str
    argument: str
    placeholder: str
    required: bool = False
    multiple: bool = False


COMMAND_FORMS = {
    "init": (FormField("Drive or catalog path", "drive", "E:/ or /photos", True),),
    "import": (
        FormField(
            "Source files or folders",
            "sources",
            "One or more paths; quote paths containing spaces",
            True,
            True,
        ),
        FormField("Target drive", "--to", "Drive or catalog path", True),
        FormField("Output folder", "--output", "Optional folder on the drive"),
    ),
    "sync": (FormField("Drive or catalog path", "drive", "E:/ or /photos", True),),
    "compare": (
        FormField("First gallery", "gallery_a", "Drive path or gallery.db", True),
        FormField("Second gallery", "gallery_b", "Drive path or gallery.db", True),
        FormField("Report output file", "--output", "Optional output file"),
    ),
    "export": (
        FormField("Source drive", "--from", "Drive or catalog path", True),
        FormField("Destination folder", "--to", "Folder to receive the export", True),
    ),
    "convert-raw": (
        FormField(
            "RAW files or folders",
            "sources",
            "One or more paths; quote paths containing spaces",
            True,
            True,
        ),
    ),
    "dashboard": (FormField("Drive or catalog path", "drive", "E:/ or /photos", True),),
    "report": (
        FormField("Drive or catalog path", "drive", "E:/ or /photos", True),
        FormField("Excel output file", "--to", "Path ending in .xlsx", True),
    ),
    "scan-faces": (
        FormField("Drive or catalog path", "drive", "E:/ or /photos", True),
    ),
    "identify-faces": (
        FormField("Drive or catalog path", "drive", "E:/ or /photos", True),
        FormField(
            "Sample photos folder",
            "--samples",
            "Folder containing person folders",
            True,
        ),
    ),
    "cluster-faces": (
        FormField("Drive or catalog path", "drive", "E:/ or /photos", True),
    ),
    "people": (FormField("Drive or catalog path", "drive", "E:/ or /photos", True),),
    "name-person": (
        FormField("Drive or catalog path", "drive", "E:/ or /photos", True),
        FormField("Person ID", "person_id", "Numeric person ID", True),
        FormField("Name", "name", "Optional name; leave blank to clear"),
    ),
    "merge-persons": (
        FormField("Drive or catalog path", "drive", "E:/ or /photos", True),
        FormField("Destination person ID", "dest", "Numeric person ID to keep", True),
        FormField(
            "Source person IDs", "sources", "IDs separated by spaces", True, True
        ),
    ),
    "split-person": (
        FormField("Drive or catalog path", "drive", "E:/ or /photos", True),
        FormField("Person ID", "person_id", "Numeric person ID", True),
    ),
    "delete-person": (
        FormField("Drive or catalog path", "drive", "E:/ or /photos", True),
        FormField("Person ID", "person_id", "Numeric person ID", True),
    ),
}


class SmartGalleryApp(App):
    """Command launcher with an output pane and access to every CLI option."""

    TITLE = "Smart Gallery"
    SUB_TITLE = "Terminal command center"
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar { width: 38; border: round $primary; padding: 0 1; }
    #main { width: 1fr; padding: 0 1; }
    #commands { height: 1fr; }
    #form-fields { height: auto; max-height: 12; border: round $primary; }
    .field-label { width: 24; }
    #run { width: 14; }
    #stop { width: 14; }
    #help { height: 1fr; border: round $primary; }
    #output { height: 1fr; border: round $primary; }
    #status { height: 1; color: $text-muted; }
    .hint { color: $text-muted; height: auto; padding-bottom: 1; }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("escape", "clear_command", "Clear fields"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.process: asyncio.subprocess.Process | None = None
        self.help_process: asyncio.subprocess.Process | None = None
        self.selected_command: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("COMMANDS", classes="section-title")
                yield Static(
                    "Select a command, then add its required arguments.", classes="hint"
                )
                yield ListView(
                    *(
                        ListItem(
                            Label(f"{name:<15} {description}"), id=f"command-{name}"
                        )
                        for name, description in COMMANDS
                    ),
                    id="commands",
                )
            with Vertical(id="main"):
                yield Label("Command setup")
                with Horizontal():
                    yield Input(
                        placeholder="Extra CLI options, e.g. --dry-run or --structure Year Month",
                        id="extra-options",
                    )
                    yield Button("Run", id="run", variant="primary")
                    yield Button("Stop", id="stop", variant="error", disabled=True)
                yield Static(
                    "Select a command to fill its required fields. Add optional CLI flags above.",
                    classes="hint",
                )
                yield Label("Required paths and values")
                with VerticalScroll(id="form-fields"):
                    yield Static(
                        "Choose a command to create its form.", id="form-placeholder"
                    )
                yield Label("Usage and options")
                yield RichLog(
                    id="help",
                    highlight=False,
                    markup=False,
                    wrap=True,
                    auto_scroll=True,
                )
                yield Label("Command output")
                yield RichLog(
                    id="output",
                    highlight=False,
                    markup=False,
                    wrap=True,
                    auto_scroll=True,
                )
                yield Static("Ready", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#help", RichLog).write(
            "Select a command on the left to see its usage and available options."
        )
        output = self.query_one("#output", RichLog)
        output.write("Select a command, complete its fields, and press Run.")

    @on(ListView.Selected)
    async def select_command(self, event: ListView.Selected) -> None:
        command = event.item.id.removeprefix("command-")
        self.selected_command = command
        fields = self.query_one("#form-fields", VerticalScroll)
        await fields.remove_children()
        for index, field in enumerate(COMMAND_FORMS[command]):
            label = field.label + (" *" if field.required else "")
            await fields.mount(
                Horizontal(
                    Label(label, classes="field-label"),
                    Input(placeholder=field.placeholder, id=f"form-field-{index}"),
                    classes="field-row",
                )
            )
        await fields.mount(
            Static(
                "Fields marked * are required. Paths with spaces can be entered as-is."
            )
        )
        help_log = self.query_one("#help", RichLog)
        help_log.clear()
        help_log.write(f"Loading `smart-gallery {command} --help`…")
        self.run_worker(self._load_help(command), group="command-help", exclusive=True)

    @on(Button.Pressed, "#run")
    def run_button(self) -> None:
        self.run_command()

    @on(Input.Submitted, "#extra-options")
    def submit_command(self) -> None:
        self.run_command()

    @on(Button.Pressed, "#stop")
    async def stop_button(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            self.query_one("#status", Static).update("Stopping command…")

    def run_command(self) -> None:
        if self.process and self.process.returncode is None:
            return
        if not self.selected_command:
            self.query_one("#status", Static).update("Select a command first.")
            return
        try:
            parts = self._build_command()
        except ValueError as exc:
            self.query_one("#status", Static).update(str(exc))
            return
        output = self.query_one("#output", RichLog)
        output.write(f"\n$ smart-gallery {' '.join(shlex.quote(arg) for arg in parts)}")
        self.query_one("#status", Static).update("Running…")
        self.query_one("#run", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        self.run_worker(self._execute(parts), exclusive=True)

    @staticmethod
    def _split_command(command_text: str) -> list[str]:
        lexer = shlex.shlex(command_text, posix=os.name != "nt")
        lexer.whitespace_split = True
        lexer.commenters = ""
        parts = list(lexer)
        if os.name == "nt":
            parts = [
                (
                    token[1:-1]
                    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
                    else token
                )
                for token in parts
            ]
        return parts

    def _build_command(self) -> list[str]:
        assert self.selected_command is not None
        command = self.selected_command
        parts = [command]
        for index, field in enumerate(COMMAND_FORMS[command]):
            value = self.query_one(f"#form-field-{index}", Input).value.strip()
            if not value:
                if field.required:
                    raise ValueError(f"{field.label} is required.")
                continue
            if field.multiple:
                values = self._split_command(value)
                if not values:
                    if field.required:
                        raise ValueError(f"{field.label} is required.")
                    continue
            else:
                values = [self._single_value(value)]
            if field.argument.startswith("-"):
                parts.append(field.argument)
            parts.extend(values)
        extra = self.query_one("#extra-options", Input).value.strip()
        if extra:
            parts.extend(self._split_command(extra))
        return parts

    @staticmethod
    def _single_value(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            return value[1:-1]
        return value

    async def _load_help(self, command: str) -> None:
        help_log = self.query_one("#help", RichLog)
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "smart_gallery.cli",
                command,
                "--help",
                cwd=Path.cwd(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self.help_process = process
            stdout, _ = await process.communicate()
            help_log.clear()
            help_text = stdout.decode(errors="replace").strip()
            for line in help_text.splitlines():
                help_log.write(line)
            if not help_text:
                help_log.write("No help text was returned for this command.")
        except asyncio.CancelledError:
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        except Exception as exc:  # noqa: BLE001
            help_log.clear()
            help_log.write(f"Could not load command help: {exc}")
        finally:
            if self.help_process is process:
                self.help_process = None

    async def _execute(self, parts: list[str]) -> None:
        output = self.query_one("#output", RichLog)
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "smart_gallery.cli",
                *parts,
                cwd=Path.cwd(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert self.process.stdout is not None
            while line := await self.process.stdout.readline():
                output.write(line.decode(errors="replace").rstrip("\r\n"))
            return_code = await self.process.wait()
            if return_code == 0:
                self.query_one("#status", Static).update("Completed successfully.")
            else:
                self.query_one("#status", Static).update(
                    f"Command exited with status {return_code}."
                )
        except Exception as exc:  # noqa: BLE001
            output.write(f"Could not run command: {exc}")
            self.query_one("#status", Static).update("Command failed to start.")
        finally:
            self.process = None
            self.query_one("#run", Button).disabled = False
            self.query_one("#stop", Button).disabled = True

    def action_clear_command(self) -> None:
        if not (self.process and self.process.returncode is None):
            self.query_one("#extra-options", Input).value = ""
            if self.selected_command:
                for index in range(len(COMMAND_FORMS[self.selected_command])):
                    self.query_one(f"#form-field-{index}", Input).value = ""


def run() -> None:
    """Start the Textual command interface."""
    SmartGalleryApp().run()
