"""The log of jobs the app has run, one JSON object per line. No Qt here."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

TAIL_LINES = 200  # output lines kept per job


@dataclass
class JobRecord:
    title: str
    argvs: list[list[str]]
    started_at: str  # ISO 8601
    ended_at: str
    status: Literal["ok", "failed", "cancelled"]
    exit_code: int | None
    summary: str
    log_path: str | None
    output_tail: list[str]


class HistoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, record: JobRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(record), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")

    def load(self) -> list[JobRecord]:
        """All records, newest first; lines that do not parse are skipped."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records = []
        for line in lines:
            try:
                records.append(JobRecord(**json.loads(line)))
            except (ValueError, TypeError):
                continue  # a torn write or hand edit should not hide the rest
        records.reverse()
        return records
