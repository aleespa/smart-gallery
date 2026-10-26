"""label-faces: apply the user's corrections to how faces are filed.

The desktop app writes a JSON list of corrections; the CLI can also take a
single correction on the command line. Everything is validated before anything
is written, and all entries are applied in one transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Sequence, get_args

from smart_gallery.db import GalleryRepository

Action = Literal["confirm", "reject", "move", "new_person", "ignore"]
ACTIONS: tuple[str, ...] = get_args(Action)
_NEEDS_PERSON = ("confirm", "reject", "move")


@dataclass(frozen=True)
class LabelEntry:
    action: Action
    faces: tuple[int, ...]
    person_id: Optional[int] = None  # confirm / reject / move
    name: Optional[str] = None  # new_person


@dataclass
class LabelReport:
    faces_changed: int = 0
    people_created: int = 0
    missing_faces: int = 0


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_entry(entry: LabelEntry) -> None:
    if entry.action not in ACTIONS:
        raise ValueError(f"Unknown action {entry.action!r}.")
    if not entry.faces or not all(_is_int(f) for f in entry.faces):
        raise ValueError(f"'{entry.action}' needs a list of face ids.")
    if entry.action in _NEEDS_PERSON and not _is_int(entry.person_id):
        raise ValueError(f"'{entry.action}' needs a person_id.")
    if entry.action == "new_person" and not (entry.name and entry.name.strip()):
        raise ValueError("'new_person' needs a name.")


def write_label_list(path: Path, entries: Sequence[LabelEntry]) -> None:
    out = []
    for e in entries:
        item: dict = {"action": e.action, "faces": list(e.faces)}
        if e.person_id is not None:
            item["person_id"] = e.person_id
        if e.name is not None:
            item["name"] = e.name
        out.append(item)
    Path(path).write_text(json.dumps(out, indent=2), encoding="utf-8")


def read_label_list(path: Path) -> list[LabelEntry]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc}).") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of corrections.")
    entries = []
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: entry {i} is not an object.")
        faces = item.get("faces")
        if not isinstance(faces, list):
            raise ValueError(f"{path}: entry {i} needs a 'faces' list.")
        entry = LabelEntry(
            action=item.get("action"),
            faces=tuple(faces),
            person_id=item.get("person_id"),
            name=item.get("name"),
        )
        try:
            _check_entry(entry)
        except ValueError as exc:
            raise ValueError(f"{path}: entry {i}: {exc}") from exc
        entries.append(entry)
    return entries


def label_faces(repo: GalleryRepository, entries: Sequence[LabelEntry]) -> LabelReport:
    for e in entries:
        _check_entry(e)
    wanted = {e.person_id for e in entries if e.person_id is not None}
    if wanted:
        ph = ", ".join("?" for _ in wanted)
        found = {
            r[0]
            for r in repo.conn.execute(
                f"SELECT id FROM persons WHERE id IN ({ph})", tuple(wanted)
            )
        }
        if wanted - found:
            missing = ", ".join(str(p) for p in sorted(wanted - found))
            raise ValueError(f"No such person id: {missing}.")
    for e in entries:
        if e.action == "new_person":
            repo.person_named(e.name)  # raises if the name is ambiguous
    return repo.apply_labels(entries)
