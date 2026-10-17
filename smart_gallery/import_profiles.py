"""Saved import profiles: each expands into a chain of ``import`` commands."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Sequence

from .cli import build_parser
from .forms import build_argv, command_forms, default_values

# Values the rule itself supplies; options may not override them.
_RULE_KEYS = frozenset({"sources", "drive", "output", "file_types", "extensions"})


@dataclass
class ImportRule:
    file_type: Literal["image", "video"]
    extensions: list[str]
    output: str  # relative to the gallery root, POSIX
    options: dict[str, object] = field(default_factory=dict)


@dataclass
class ImportProfile:
    name: str
    rules: list[ImportRule]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile"


def _to_json(profile: ImportProfile) -> dict:
    return {
        "name": profile.name,
        "rules": [
            {
                "file_type": r.file_type,
                "extensions": list(r.extensions),
                "output": r.output,
                "options": dict(r.options),
            }
            for r in profile.rules
        ],
    }


def _from_json(data: dict) -> ImportProfile:
    return ImportProfile(
        name=str(data["name"]),
        rules=[
            ImportRule(
                file_type=r["file_type"],
                extensions=list(r["extensions"]),
                output=r["output"],
                options=dict(r.get("options", {})),
            )
            for r in data["rules"]
        ],
    )


def _read(path: Path) -> ImportProfile | None:
    try:
        return _from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_profiles(dirpath: Path) -> list[ImportProfile]:
    profiles = []
    for path in sorted(Path(dirpath).glob("*.json")):
        profile = _read(path)
        if profile is not None:
            profiles.append(profile)
    return sorted(profiles, key=lambda p: p.name)


def save_profile(dirpath: Path, profile: ImportProfile) -> Path:
    dirpath = Path(dirpath)
    dirpath.mkdir(parents=True, exist_ok=True)
    base = _slug(profile.name)
    path = dirpath / f"{base}.json"
    n = 2
    while path.exists():
        existing = _read(path)
        if existing is not None and existing.name == profile.name:
            break
        path = dirpath / f"{base}-{n}.json"
        n += 1
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_to_json(profile), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def delete_profile(dirpath: Path, name: str) -> None:
    for path in Path(dirpath).glob("*.json"):
        profile = _read(path)
        if profile is not None and profile.name == name:
            path.unlink()


def _rules(photo_dir: str) -> list[ImportRule]:
    options = {
        "by_media_type": False,
        "structure": ("Year", "Month"),
        "on_exist": "skip",
    }
    return [
        ImportRule("image", [".cr3", ".jpg"], photo_dir, dict(options)),
        ImportRule("video", [".mp4"], "Videos", dict(options)),
    ]


def starter_profiles() -> list[ImportProfile]:
    return [
        ImportProfile("Canon SD card", _rules("Photos/Canon")),
        ImportProfile("Pixel dump", _rules("Photos/Other cameras")),
    ]


def seed_starter_profiles(dirpath: Path) -> None:
    dirpath = Path(dirpath)
    if dirpath.is_dir() and any(dirpath.glob("*.json")):
        return
    for profile in starter_profiles():
        save_profile(dirpath, profile)


def _quote(text: str) -> str:
    if text and not any(c.isspace() or c in "\"'" for c in text):
        return text
    quote = "'" if '"' in text else '"'
    return f"{quote}{text}{quote}"


def expand(
    profile: ImportProfile, source: str, gallery_roots: Sequence[str]
) -> list[list[str]]:
    form = command_forms(build_parser())["import"]
    argvs = []
    for rule in profile.rules:
        bad = [k for k in rule.options if k not in form.fields or k in _RULE_KEYS]
        if bad:
            raise ValueError(f"Unsupported import option: {', '.join(sorted(bad))}")
        for root in gallery_roots:
            values = default_values(form)
            values.update(
                sources=_quote(source),
                drive=str(root),
                output=_quote(str(PureWindowsPath(root) / PurePosixPath(rule.output))),
                file_types=(rule.file_type,),
                extensions=" ".join(_quote(e) for e in rule.extensions),
            )
            values.update(rule.options)
            result = build_argv(form, values)
            if not result.ok:
                problems = {**result.errors, **{k: "required" for k in result.missing}}
                raise ValueError(f"Invalid import profile: {problems}")
            argvs.append(result.argv)
    return argvs
