"""The galleries the owner has opened or created, persisted as JSON. No Qt here."""

from __future__ import annotations

import json
import os
import sqlite3
import string
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from smart_gallery.config import drive_root_of
from smart_gallery.db import GalleryRepository


@dataclass(frozen=True)
class Gallery:
    db_uuid: str
    db_path: str
    label: str

    @property
    def root(self) -> Path:
        return drive_root_of(self.db_path)


def is_connected(gallery: Gallery) -> bool:
    return Path(gallery.db_path).exists()


def existing_drives() -> list[str]:
    """Roots of the drive letters that exist right now."""
    listdrives = getattr(os, "listdrives", None)  # Windows, Python 3.12+
    if listdrives is not None:
        try:
            return listdrives()
        except OSError:
            pass
    return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


def locate(gallery: Gallery, drives: Iterable[str]) -> Gallery | None:
    """Find a disconnected gallery's catalog on one of ``drives``.

    The path after the stored anchor is tried under each drive, and a
    candidate only counts when its ``db_uuid`` matches.
    """
    db_path = Path(gallery.db_path)
    relative = db_path.relative_to(db_path.anchor)
    for drive in drives:
        candidate = Path(drive) / relative
        if not candidate.is_file():
            continue
        try:
            with GalleryRepository.open(candidate, read_only=True) as repo:
                found = repo.get_meta("db_uuid")
        except (OSError, sqlite3.Error):
            continue
        if found == gallery.db_uuid:
            return replace(gallery, db_path=str(candidate))
    return None


class GalleryStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def all(self) -> list[Gallery]:
        found = []
        for e in self._read().get("galleries", []):
            try:
                found.append(Gallery(e["db_uuid"], e["db_path"], e["label"]))
            except (KeyError, TypeError):
                continue  # a hand-edited or damaged entry should not hide the rest
        return found

    def add(self, target) -> Gallery:
        """Register the catalog at ``target``; a known ``db_uuid`` updates its path.

        The catalog is opened read-only, so adding never migrates it.
        """
        with GalleryRepository.open(target, read_only=True) as repo:
            db_uuid = repo.get_meta("db_uuid")
            if not db_uuid:
                raise ValueError(f"{repo.db_path} has no catalog id; run init again.")
            label = repo.get_meta("drive_label") or str(repo.drive_root)
            gallery = Gallery(db_uuid, str(repo.db_path), label)
        data = self._read()
        entries = list(data.get("galleries", []))
        for i, entry in enumerate(entries):
            if entry.get("db_uuid") == db_uuid:
                entries[i] = asdict(gallery)  # remounted: keep its place in the list
                break
        else:
            entries.append(asdict(gallery))
        data["galleries"] = entries
        self._write(data)
        return gallery

    def remove(self, db_uuid: str) -> None:
        data = self._read()
        data["galleries"] = [
            e for e in data.get("galleries", []) if e.get("db_uuid") != db_uuid
        ]
        if data.get("last") == db_uuid:
            data.pop("last")
        self._write(data)

    def last(self) -> str | None:
        return self._read().get("last")

    def set_last(self, db_uuid: str | None) -> None:
        data = self._read()
        if db_uuid is None:
            data.pop("last", None)
        else:
            data["last"] = db_uuid
        self._write(data)

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
