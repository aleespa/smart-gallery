"""What a background job runs. Qt-free, so models can build one without PySide6."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobSpec:
    title: str
    argvs: tuple[tuple[str, ...], ...]  # run in order; stops at the first failure
    writes_catalog: bool = True


JOB_RUNNING = "A job is already running."  # shown when JobRunner.start refuses
