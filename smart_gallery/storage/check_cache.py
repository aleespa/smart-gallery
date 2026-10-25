"""Remembered picture-check verdicts, kept in a JSON file outside the catalog.

No Qt. A verdict is keyed by both files' paths, sizes and mtimes, so changing
either file makes the old verdict a miss.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from smart_gallery.storage.pairs import Pair

VERDICTS = frozenset({"match", "different", "unreadable"})
_VERSION = 1


def _key(pair: Pair) -> str:
    raw, jpeg = pair.raw, pair.jpeg
    return "\t".join(
        [
            raw.relpath.casefold(),
            str(raw.size_bytes),
            str(raw.mtime_ns),
            jpeg.relpath.casefold(),
            str(jpeg.size_bytes),
            str(jpeg.mtime_ns),
        ]
    )


class CheckCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._checks: dict[str, str] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            checks = data["checks"]
            if isinstance(checks, dict):
                self._checks = {
                    k: v
                    for k, v in checks.items()
                    if isinstance(k, str) and v in VERDICTS
                }
        except (OSError, ValueError, KeyError, TypeError):
            self._checks = {}

    def get(self, pair: Pair) -> str | None:
        return self._checks.get(_key(pair))

    def put(self, pair: Pair, verdict: str) -> None:
        if verdict not in VERDICTS:
            raise ValueError(f"Unknown verdict: {verdict}")
        self._checks[_key(pair)] = verdict

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps({"version": _VERSION, "checks": self._checks}),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
