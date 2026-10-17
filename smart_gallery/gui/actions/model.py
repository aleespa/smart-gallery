"""Form data for the Actions area, built from the CLI parser. No Qt here."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io

from smart_gallery.cli import build_parser
from smart_gallery.forms import (
    GUI_HIDDEN_COMMANDS,
    CommandForm,
    Field,
    command_forms,
    default_values,
)
from smart_gallery.gui.paths import faces_available, is_frozen

DRIVE_KEY = "drive"

FACE_COMMANDS = {"scan-faces", "identify-faces", "cluster-faces", "split-person"}
FACES_MISSING = "Face features aren't installed in this build"

# Commands that never change the catalog (so a job history entry need not refresh it).
READ_ONLY_COMMANDS = {"export", "report", "people", "compare", "convert-raw"}


def gui_forms() -> dict[str, CommandForm]:
    """One form per command offered in the desktop app, in CLI order."""
    forms = command_forms(build_parser(), hidden=GUI_HIDDEN_COMMANDS)
    if is_frozen() and "report" in forms:
        # Charts need the 'figures' extra, which the packaged app leaves out.
        forms["report"] = _without_field(forms["report"], "figures")
    return forms


def _without_field(form: CommandForm, key: str) -> CommandForm:
    sections = tuple(
        dataclasses.replace(
            section, fields=tuple(f for f in section.fields if f.key != key)
        )
        for section in form.sections
    )
    return dataclasses.replace(
        form,
        sections=tuple(s for s in sections if s.fields),
        fields={k: f for k, f in form.fields.items() if k != key},
    )


def initial_values(form: CommandForm, gallery_root: str | None) -> dict:
    """Default values, with the drive field pointing at the open gallery."""
    values = default_values(form)
    if gallery_root and DRIVE_KEY in values:
        values[DRIVE_KEY] = gallery_root
    return values


def unavailable_reason(command: str) -> str | None:
    if command in FACE_COMMANDS and not faces_available():
        return FACES_MISSING
    return None


def join_values(items: list[str]) -> str:
    """Space-separated text that ``forms.split_values`` reads back as ``items``."""
    return " ".join(f'"{i}"' if any(c.isspace() for c in i) else i for i in items)


def values_from_argv(form: CommandForm, argv: list[str]) -> dict:
    """Widget values that rebuild to ``argv``'s options (same types as
    ``default_values``). Raises ValueError when the CLI rejects ``argv``."""
    parser = build_parser()
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors):
            namespace = parser.parse_args(argv)
    except SystemExit:
        lines = errors.getvalue().strip().splitlines()
        raise ValueError(lines[-1] if lines else "Invalid command line") from None
    return {key: _value_of(f, namespace) for key, f in form.fields.items()}


def _value_of(f: Field, namespace: argparse.Namespace):
    if f.kind == "mode":
        # The field's choices are the group's long flags; the set one wins.
        return next(
            (
                flag
                for flag in f.choices
                if getattr(namespace, flag.lstrip("-").replace("-", "_"), False)
            ),
            None,
        )
    value = getattr(namespace, f.key, f.default)
    if f.kind == "switch":
        return bool(value)
    if f.kind == "choice":
        return value
    if f.kind in ("multi", "ordered"):
        return tuple(value or ())
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return join_values([str(v) for v in value])
    return str(value)


def command_name(token: str) -> str | None:
    """The primary command name for ``token`` (resolving aliases), or None."""
    subparsers = next(
        a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    target = subparsers.choices.get(token)
    if target is None:
        return None
    return next(name for name, p in subparsers.choices.items() if p is target)
