"""Forms for the terminal and desktop UIs, generated from the CLI's argparse definition.

Each subcommand becomes a ``CommandForm``. Its argument groups become form
sections, and each option becomes a ``Field`` whose ``kind`` picks the widget.
Reading the parser instead of keeping a second hand-written list means a new CLI
flag shows up in the UI automatically. Only labels and a few hints are
overridden here, purely for presentation.
"""

from __future__ import annotations

import argparse
import shlex
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REQUIRED = "Required"
OPTIONS = "Options"

# Commands not offered in the terminal UI (the launchers themselves).
HIDDEN_COMMANDS = {"ui", "gui"}

# The desktop app also hides the dashboard, which is a browser launcher.
GUI_HIDDEN_COMMANDS = {"ui", "gui", "dashboard"}

# Sections with more fields than this start folded.
COLLAPSE_OVER = 6

# Multi-choice options whose order is meaningful (folder levels).
ORDERED_CHOICES = {"structure"}

# argparse's own group titles for ungrouped arguments.
_DEFAULT_GROUPS = {"positional arguments", "options", "optional arguments"}

# Display labels, keyed by "<command>.<key>" or just "<key>". Anything missing
# falls back to the option name ("min_aperture" -> "Min aperture").
LABELS = {
    "drive": "Drive or catalog",
    "import.drive": "Target drive",
    "import.sources": "Source files/folders",
    "import.output": "Output folder",
    "export.drive": "Source drive",
    "export.dest": "Destination folder",
    "report.output": "Excel file",
    "compare.output": "Report file",
    "convert-raw.sources": "RAW files/folders",
    "recycle.list": "File list",
    "merge-persons.dest": "Keep person ID",
    "merge-persons.sources": "Merge person IDs",
    "identify-faces.samples": "Samples folder",
    "people.samples": "Photos per person",
    "gallery_a": "First gallery",
    "gallery_b": "Second gallery",
    "person_id": "Person ID",
    "person_ids": "Person IDs",
    "by_media_type": "Split Photos/Videos",
    "manifest": "Write manifest",
    "mirror": "Mirror drive paths",
    "portable_db": "Portable catalog",
    "on_exist": "If file exists",
    "structure": "Folder structure",
    "date_start": "Taken from",
    "date_end": "Taken until",
    "min_iso": "Min ISO",
    "max_iso": "Max ISO",
    "algo": "Algorithm",
    "eps": "Epsilon",
    "pca": "PCA dimensions",
}

# Hint text inside an empty input, by field kind.
PLACEHOLDERS = {
    "path": "path",
    "path_list": "one or more paths; quote paths containing spaces",
    "list": "values separated by spaces; quote values containing spaces",
    "int_list": "numbers separated by spaces",
    "int": "whole number",
    "float": "number",
    "date": "YYYY-MM-DD",
}

# Switches shown "on by default" are backed by a --no-... flag whose help text
# is phrased negatively; describe what the switch itself does instead.
HINTS = {
    "by_media_type": "Put images under Photos/ and videos under Videos/.",
    "manifest": "Write _smart_gallery_manifest.csv at the destination.",
}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    hint: str
    kind: str
    section: str
    flag: str | None = None  # the option string to emit; None for positionals
    positional: bool = False
    required: bool = False
    default: object = None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    title: str
    fields: tuple[Field, ...]
    collapsed: bool


@dataclass(frozen=True, eq=False)
class CommandForm:
    name: str
    description: str
    help_text: str
    sections: tuple[Section, ...]
    fields: dict[str, Field]


def command_forms(
    parser: argparse.ArgumentParser, hidden: Collection[str] = HIDDEN_COMMANDS
) -> dict[str, CommandForm]:
    """One form per subcommand, in CLI order, without aliases or hidden commands."""
    subparsers = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    forms = {}
    for choice in subparsers._choices_actions:  # primary names only
        name = choice.dest
        if name in hidden:
            continue
        forms[name] = _command_form(name, choice.help or "", subparsers.choices[name])
    return forms


def _command_form(
    name: str, description: str, parser: argparse.ArgumentParser
) -> CommandForm:
    group_title = {
        id(action): group.title
        for group in parser._action_groups
        for action in group._group_actions
    }
    mutex_of = {
        id(action): group
        for group in parser._mutually_exclusive_groups
        for action in group._group_actions
    }

    by_dest: dict[str, list[argparse.Action]] = defaultdict(list)
    for action in parser._actions:
        if not isinstance(action, argparse._HelpAction):
            by_dest[action.dest].append(action)

    fields: list[Field] = []
    seen_mutex: set[int] = set()
    for dest, actions in by_dest.items():
        first = actions[0]
        section = _section_for(group_title.get(id(first)))
        mutex = mutex_of.get(id(first))
        if mutex is not None:
            if id(mutex) not in seen_mutex:
                seen_mutex.add(id(mutex))
                fields.append(_mode_field(name, mutex, section))
            continue
        if isinstance(first, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            switch = _switch_field(name, dest, actions, parser, section)
            if switch is not None:
                fields.append(switch)
            continue
        fields.append(_value_field(name, first, section))

    return CommandForm(
        name=name,
        description=description,
        help_text=parser.format_help(),
        sections=_sections(fields, parser, group_title),
        fields={f.key: f for f in fields},
    )


def _section_for(title: str | None) -> str:
    if title is None or title in _DEFAULT_GROUPS:
        return OPTIONS
    return title


def _sections(fields, parser, group_title) -> tuple[Section, ...]:
    """Required first, then argument groups in declaration order, then Options."""
    order = [REQUIRED]
    for group in parser._action_groups:
        title = _section_for(group.title)
        if title not in order and title != OPTIONS:
            order.append(title)
    order.append(OPTIONS)

    sections = []
    for title in order:
        members = tuple(
            f for f in fields if (REQUIRED if f.required else f.section) == title
        )
        if members:
            collapsed = title != REQUIRED and len(members) > COLLAPSE_OVER
            sections.append(Section(title, members, collapsed))
    return tuple(sections)


def _label(command: str, key: str) -> str:
    return (
        LABELS.get(f"{command}.{key}")
        or LABELS.get(key)
        or key.replace("_", " ").capitalize()
    )


def _long_flag(action: argparse.Action) -> str:
    longs = [s for s in action.option_strings if s.startswith("--")]
    return (longs or action.option_strings)[-1]


def _value_kind(action: argparse.Action) -> str:
    many = action.nargs in ("+", "*")
    if action.choices:
        if many:
            return "ordered" if action.dest in ORDERED_CHOICES else "multi"
        return "choice"
    if action.type is int:
        return "int_list" if many else "int"
    if action.type is float:
        return "float"
    if action.type == date.fromisoformat:
        return "date"
    if action.type is Path:
        return "path_list" if many else "path"
    return "list" if many else "text"


def _value_field(command: str, action: argparse.Action, section: str) -> Field:
    positional = not action.option_strings
    required = action.nargs not in ("?", "*") if positional else bool(action.required)
    return Field(
        key=action.dest,
        label=_label(command, action.dest),
        hint=HINTS.get(action.dest) or action.help or "",
        kind=_value_kind(action),
        section=section,
        flag=None if positional else _long_flag(action),
        positional=positional,
        required=required,
        default=action.default,
        choices=tuple(action.choices or ()),
    )


def _switch_field(command, dest, actions, parser, section) -> Field | None:
    """A store_true/store_false pair (or single flag) as one on/off switch whose
    flag is the one that moves it away from the default."""
    default = bool(parser.get_default(dest))
    toggles = [a for a in actions if a.const is not default]
    if not toggles:
        return None
    return Field(
        key=dest,
        label=_label(command, dest),
        hint=HINTS.get(dest) or next((a.help for a in actions if a.help), ""),
        kind="switch",
        section=section,
        flag=_long_flag(toggles[0]),
        default=default,
    )


def _mode_field(command, group, section) -> Field:
    """Mutually exclusive flags as one pick-one field (blank = none of them)."""
    flags = tuple(_long_flag(a) for a in group._group_actions)
    hint = " ".join(f"{_long_flag(a)}: {a.help}" for a in group._group_actions)
    return Field(
        key="mode",
        label=_label(command, "mode"),
        hint=hint,
        kind="mode",
        section=section,
        flag=None,
        default=None,
        choices=flags,
    )


# ── layout ───────────────────────────────────────────────────────────────────
# Range options (min_X/max_X, X_start/X_end) are shown as one row.
RANGE_LABELS = {
    "date": "Taken between",
    "aperture": "Aperture (f/)",
    "iso": "ISO",
    "shutter_speed": "Shutter speed",
}
RANGE_HINTS = {
    "date": "Capture date range, YYYY-MM-DD. Either end may be left blank.",
    "aperture": "f-number range, e.g. 2.8 to 8.",
    "iso": "ISO range, e.g. 100 to 1600.",
    "shutter_speed": "Shutter range, e.g. 1/1000 to 2s.",
}


@dataclass(frozen=True)
class Row:
    label: str
    hint: str
    fields: tuple[Field, ...]


def _range_partner_key(key: str) -> tuple[str, str] | None:
    """For the low end of a range, return (stem, key of the high end)."""
    if key.startswith("min_"):
        return key[4:], "max_" + key[4:]
    if key.endswith("_start"):
        return key[:-6], key[:-6] + "_end"
    return None


def field_rows(fields: tuple[Field, ...]) -> list[Row]:
    """Group a section's fields into display rows, pairing range ends."""
    by_key = {f.key: f for f in fields}
    paired: set[str] = set()
    rows = []
    for f in fields:
        if f.key in paired:
            continue
        match = _range_partner_key(f.key)
        partner = by_key.get(match[1]) if match else None
        if match and partner is not None and partner.kind == f.kind:
            stem = match[0]
            paired.add(partner.key)
            rows.append(
                Row(
                    label=RANGE_LABELS.get(stem) or stem.replace("_", " ").capitalize(),
                    hint=RANGE_HINTS.get(stem) or f"{f.hint} {partner.hint}",
                    fields=(f, partner),
                )
            )
        else:
            rows.append(Row(label=f.label, hint=f.hint, fields=(f,)))
    return rows


# ── values -> command line ───────────────────────────────────────────────────
_LIST_KINDS = {"list", "path_list", "int_list"}
_CHOICE_SET_KINDS = {"multi", "ordered"}
_CONVERTERS = {
    "int": (int, "Enter a whole number."),
    "int_list": (int, "Enter whole numbers separated by spaces."),
    "float": (float, "Enter a number."),
    "date": (date.fromisoformat, "Use YYYY-MM-DD."),
}


@dataclass
class BuildResult:
    argv: list[str]
    errors: dict[str, str] = field(default_factory=dict)  # key -> message
    missing: list[str] = field(default_factory=list)  # empty required keys
    changed: set[str] = field(default_factory=set)  # keys that emitted tokens

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing


def default_values(form: CommandForm) -> dict[str, object]:
    """The value each widget starts with: the CLI default, shown as typed text
    for input boxes, a bool for switches and a tuple for choice sets."""
    return {key: _initial_value(f) for key, f in form.fields.items()}


def _initial_value(f: Field) -> object:
    if f.kind == "switch":
        return bool(f.default)
    if f.kind in _CHOICE_SET_KINDS:
        return tuple(f.default or ())
    if f.kind in ("mode", "choice"):
        return f.default
    if f.default is None:
        return ""
    if isinstance(f.default, (list, tuple)):
        return " ".join(str(v) for v in f.default)
    return str(f.default)


def split_values(text: str) -> list[str]:
    """Split space-separated values. Quotes group words; backslashes stay
    literal so Windows paths survive."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    return list(lexer)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _convert(kind: str, text: str):
    convert, message = _CONVERTERS[kind]
    try:
        return convert(text)
    except ValueError:
        raise ValueError(message) from None


def _with_flag(f: Field, items: list[str]) -> list[str]:
    return list(items) if f.positional else [f.flag, *items]


def _field_tokens(f: Field, value) -> list[str]:
    """The argv tokens for one field: empty when the field is blank or equal to
    its default. Raises ValueError with a user-facing message when invalid."""
    if f.kind == "switch":
        return [f.flag] if bool(value) != f.default else []
    if f.kind == "mode":
        return [value] if value else []
    if f.kind == "choice":
        return [] if value in (None, f.default) else _with_flag(f, [str(value)])
    if f.kind in _CHOICE_SET_KINDS:
        chosen = list(value or ())
        if f.kind == "ordered" and not chosen:
            raise ValueError("Choose at least one.")
        if f.kind == "multi":
            chosen = [c for c in f.choices if c in chosen]
        return [] if chosen == list(f.default or ()) else _with_flag(f, chosen)
    if f.kind in _LIST_KINDS:
        try:
            items = split_values(str(value or ""))
        except ValueError:
            raise ValueError("Close the quotation mark.") from None
        if f.kind == "int_list":
            for item in items:
                _convert("int_list", item)
        if not items or (f.default is not None and items == list(map(str, f.default))):
            return []
        return _with_flag(f, items)

    text = _unquote(str(value or ""))
    if not text:
        return []
    if f.kind in _CONVERTERS:
        if _convert(f.kind, text) == f.default:
            return []
    elif f.default is not None and text == str(f.default):
        return []
    return _with_flag(f, [text])


def build_argv(form: CommandForm, values: dict, extra: str = "") -> BuildResult:
    """Turn widget values into ``[command, *args]`` for ``smart_gallery.cli``.

    Positionals go first (so a multi-value option can't swallow them), then
    options in CLI order, then the free-text extra options. Problems are
    collected per field instead of stopping at the first one.
    """
    result = BuildResult(argv=[form.name])
    positionals: list[str] = []
    options: list[str] = []
    for f in form.fields.values():
        try:
            tokens = _field_tokens(f, values.get(f.key))
        except ValueError as exc:
            result.errors[f.key] = str(exc)
            continue
        if not tokens:
            if f.required:
                result.missing.append(f.key)
            continue
        result.changed.add(f.key)
        (positionals if f.positional else options).extend(tokens)
    result.argv += positionals + options
    if extra.strip():
        try:
            result.argv += split_values(extra)
        except ValueError:
            result.errors["extra"] = "Close the quotation mark."
    return result


def format_command(argv: list[str]) -> str:
    """Display form of a command line, quoting values that contain spaces."""
    shown = [f'"{t}"' if (not t or any(c.isspace() for c in t)) else t for t in argv]
    return " ".join(["smart-gallery", *shown])
