"""Progress reporting for long-running commands.

Services report ``ProgressEvent``s: a named step, how far along it is, and, for
steps whose items can turn out differently (copying), how the finished items
split into outcomes. The CLI renders them as a terminal bar, or, when the
terminal UI runs it with ``SG_PROGRESS=json``, writes them as tagged JSON lines
that the UI turns into its progress panel instead of printing. When the desktop
app sets ``SG_CANCEL_FILE``, creating that file asks the running command to stop
at its next progress event, or sooner where a long step calls ``check_cancelled``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, TextIO, TypeVar

from tqdm import tqdm

PROGRESS_ENV = "SG_PROGRESS"
PROGRESS_PREFIX = "@sg-progress "
CANCEL_ENV = "SG_CANCEL_FILE"
CANCELLED_EXIT = 130  # the conventional exit code for an interrupted command

# Step names, shown as-is in the terminal and the UI.
FINDING = "Finding files"
READING = "Reading metadata"
COPYING = "Copying files"
SCANNING = "Scanning faces"
RECYCLING = "Moving files to Recycle Bin"


@dataclass(frozen=True)
class ProgressEvent:
    step: str
    done: int
    total: int = 0  # 0 while the total is still unknown (e.g. walking a drive)
    outcomes: tuple[tuple[str, int], ...] = ()  # partitions ``done``, in order
    unit: str = "files"

    @property
    def finished(self) -> bool:
        return self.total > 0 and self.done >= self.total

    def to_line(self) -> str:
        payload = {
            "step": self.step,
            "done": self.done,
            "total": self.total,
            "outcomes": [list(pair) for pair in self.outcomes],
            "unit": self.unit,
        }
        return PROGRESS_PREFIX + json.dumps(payload)

    @classmethod
    def from_line(cls, line: str) -> Optional["ProgressEvent"]:
        """Parse a tagged progress line; None for any other output."""
        if not line.startswith(PROGRESS_PREFIX):
            return None
        try:
            data = json.loads(line[len(PROGRESS_PREFIX) :])
            return cls(
                step=str(data["step"]),
                done=int(data["done"]),
                total=int(data.get("total", 0)),
                outcomes=tuple(
                    (str(name), int(count)) for name, count in data.get("outcomes", ())
                ),
                unit=str(data.get("unit", "files")),
            )
        except (ValueError, KeyError, TypeError):
            return None


ProgressCallback = Callable[[ProgressEvent], None]

T = TypeVar("T")


def counted(
    items: Iterable[T],
    step: str,
    progress: Optional[ProgressCallback],
    *,
    every: int = 500,
) -> Iterator[T]:
    """Yield ``items`` while reporting how many have been seen, every ``every``
    items and once more at the end with the final total."""
    n = 0
    for n, item in enumerate(items, 1):
        if progress and n % every == 0:
            progress(ProgressEvent(step, n))
        yield item
    if progress:
        progress(ProgressEvent(step, n, n))


class JsonProgress:
    """Writes events as tagged lines for the terminal UI. Within a step, at most
    one line per ``interval`` seconds; a step's first and final events, and the
    last held-back event before the step changes or the run ends, always go out."""

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        interval: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = stream
        self._interval = interval
        self._clock = clock
        self._step: Optional[str] = None
        self._last_write = 0.0
        self._held: Optional[ProgressEvent] = None

    def __call__(self, event: ProgressEvent) -> None:
        now = self._clock()
        if event.step != self._step:
            self._flush_held()
            self._step = event.step
        elif not event.finished and now - self._last_write < self._interval:
            self._held = event
            return
        self._write(event)
        self._last_write = now

    def close(self) -> None:
        self._flush_held()

    def _flush_held(self) -> None:
        if self._held is not None:
            self._write(self._held)

    def _write(self, event: ProgressEvent) -> None:
        self._held = None
        stream = self._stream or sys.stdout
        stream.write(event.to_line() + "\n")
        stream.flush()


class TerminalProgress:
    """A tqdm bar per step, with outcome counts (copied=, skipped=, ...)."""

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        self._stream = stream
        self._bar: Optional[tqdm] = None
        self._step: Optional[str] = None

    def __call__(self, event: ProgressEvent) -> None:
        if event.step != self._step:
            self.close()
            self._step = event.step
            self._bar = tqdm(
                total=event.total or None,
                desc=event.step,
                unit=f" {event.unit}",
                file=self._stream or sys.stderr,
            )
        bar = self._bar
        if event.total and bar.total != event.total:
            bar.total = event.total
        bar.n = event.done
        if event.outcomes:
            bar.set_postfix(dict(event.outcomes), refresh=False)
        bar.refresh()

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None
            self._step = None


class Cancelled(Exception):
    """Raised from a progress callback once cancellation has been requested."""


def check_cancelled(stop_file: Optional[Path] = None) -> None:
    """Raise ``Cancelled`` if the stop file exists (by default the one named in
    ``$SG_CANCEL_FILE``). Long single steps (one big copy, one ExifTool batch)
    call this so a stop request doesn't wait for the next progress event."""
    path = stop_file or os.environ.get(CANCEL_ENV)
    if path and os.path.exists(path):
        raise Cancelled()


class CancellableProgress:
    """Wraps a reporter and raises ``Cancelled`` at the first event after the
    stop file appears, so services stop between items, never mid-item."""

    def __init__(self, inner: ProgressCallback, stop_file: Path) -> None:
        self._inner = inner
        self._stop_file = stop_file

    def __call__(self, event: ProgressEvent) -> None:
        check_cancelled(self._stop_file)
        self._inner(event)

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()


def progress_reporter() -> JsonProgress | TerminalProgress | CancellableProgress:
    """JSON lines when the terminal UI is listening, a terminal bar otherwise;
    cancellable when the app has named a stop file."""
    if os.environ.get(PROGRESS_ENV) == "json":
        reporter = JsonProgress()
    else:
        reporter = TerminalProgress()
    stop_file = os.environ.get(CANCEL_ENV)
    if stop_file:
        return CancellableProgress(reporter, Path(stop_file))
    return reporter
