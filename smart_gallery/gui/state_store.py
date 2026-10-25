"""Tiny JSON state files for the desktop app. No Qt."""

from __future__ import annotations

import json
import os
from pathlib import Path


def load_json(path: Path) -> dict:
    """Read a JSON object; ``{}`` for a missing file, bad JSON or a non-object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: dict) -> None:
    """Write atomically: a ``.tmp`` beside the file, then ``os.replace``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
