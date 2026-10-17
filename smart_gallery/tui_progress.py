"""The terminal UI's progress panel.

A running command reports ``ProgressEvent``s (see ``smart_gallery.progress``).
The panel shows one row per step: an icon (running / done / failed), the step
name, a bar, and counts with elapsed time and an estimate of what's left. Copy
steps split their bar into copied / skipped / failed, with a legend underneath.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Static

from smart_gallery.progress import ProgressEvent

STYLES = {
    "copied": "green",
    "skipped": "yellow",
    "failed": "red",
    "done": "dodger_blue1",
    "remaining": "grey30",
}
ICONS = {"running": "▶", "done": "✓", "failed": "✗"}
BAR_CHAR = "━"


def bar_cells(width: int, event: ProgressEvent) -> list[tuple[str, int]]:
    """Split ``width`` cells into (segment, cells): one segment per outcome (or
    "done" for a step without outcomes) and "remaining" for the rest. Any
    outcome above zero gets at least one cell so a few failures stay visible."""
    if event.total <= 0 or width <= 0:
        return [("remaining", max(width, 0))]
    parts = list(event.outcomes) or [("done", event.done)]
    cells = []
    for name, count in parts:
        n = int(count / event.total * width + 0.5)
        cells.append([name, max(n, 1) if count > 0 else 0])

    def largest():
        return max(cells, key=lambda c: c[1])

    while sum(n for _, n in cells) > width:
        largest()[1] -= 1
    if event.finished:
        largest()[1] += width - sum(n for _, n in cells)

    segments = [(name, n) for name, n in cells if n > 0]
    remaining = width - sum(n for _, n in segments)
    if remaining > 0:
        segments.append(("remaining", remaining))
    return segments


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def summarize(event: ProgressEvent, elapsed: float, running: bool) -> str:
    """Counts, elapsed time, and (while running) a rough time left."""
    if event.total <= 0:
        return f"{event.done:,} {event.unit} so far · {_clock(elapsed)}"
    text = f"{event.done:,} / {event.total:,} {event.unit} · {_clock(elapsed)}"
    if running and 0 < event.done < event.total:
        left = elapsed / event.done * (event.total - event.done)
        text += f" · ~{_clock(left)} left"
    return text


def legend(event: ProgressEvent) -> str:
    return "   ".join(f"{count:,} {name}" for name, count in event.outcomes)


class SegmentBar(Widget):
    DEFAULT_CSS = "SegmentBar { height: 1; width: 1fr; }"

    def __init__(self) -> None:
        super().__init__()
        self.event: ProgressEvent | None = None

    def show(self, event: ProgressEvent) -> None:
        self.event = event
        self.refresh()

    def render(self) -> Text:
        text = Text()
        if self.event is not None:
            for name, cells in bar_cells(self.size.width, self.event):
                text.append(BAR_CHAR * cells, style=STYLES.get(name, ""))
        return text


class StepRow(Vertical):
    """One step: icon, name, bar and summary, with an outcome legend below."""

    DEFAULT_CSS = """
    StepRow { height: auto; }
    StepRow .step-line { height: 1; }
    StepRow .step-icon { width: 2; }
    StepRow .step-name { width: 20; }
    StepRow .step-summary { width: 48; padding-left: 2; text-align: right; }
    StepRow .step-legend { height: auto; padding-left: 22; }
    """

    def __init__(self, event: ProgressEvent) -> None:
        super().__init__()
        self.step = event.step
        self.event = event
        self.state = "running"
        self.started = time.monotonic()
        self.ended: float | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="step-line"):
            yield Static(ICONS["running"], classes="step-icon")
            yield Static(self.step, classes="step-name", markup=False)
            yield SegmentBar()
            yield Static("", classes="step-summary", markup=False)
        yield Static("", classes="step-legend")

    def on_mount(self) -> None:
        self.redraw()

    def show(self, event: ProgressEvent) -> None:
        self.event = event
        self.redraw()

    def end(self, state: str) -> None:
        self.state = state
        self.ended = time.monotonic()
        self.redraw()

    def redraw(self) -> None:
        elapsed = (self.ended or time.monotonic()) - self.started
        try:
            self.query_one(".step-icon", Static).update(ICONS[self.state])
            self.query_one(SegmentBar).show(self.event)
            self.query_one(".step-summary", Static).update(
                summarize(self.event, elapsed, self.state == "running")
            )
            legend_widget = self.query_one(".step-legend", Static)
        except NoMatches:  # not composed yet
            return
        text = Text()
        for index, (name, count) in enumerate(self.event.outcomes):
            if index:
                text.append("   ")
            text.append(f"■ {count:,} {name}", style=STYLES.get(name, ""))
        legend_widget.update(text)
        legend_widget.display = bool(self.event.outcomes)


class ProgressPanel(Vertical):
    """Rows for the steps of the current run; hidden until the first event."""

    DEFAULT_CSS = "ProgressPanel { height: auto; display: none; }"

    def on_mount(self) -> None:
        # Keep elapsed time and estimates moving between events.
        self.set_interval(1.0, self._tick)

    def _rows(self) -> list[StepRow]:
        return list(self.query(StepRow))

    async def reset(self) -> None:
        await self.remove_children()
        self.display = False

    async def show_event(self, event: ProgressEvent) -> None:
        self.display = True
        row = next((r for r in self._rows() if r.step == event.step), None)
        if row is not None:
            row.show(event)
            return
        for previous in self._rows():
            if previous.state == "running":
                previous.end("done")
        await self.mount(StepRow(event))

    def finish(self, ok: bool) -> None:
        for row in self._rows():
            if row.state == "running":
                row.end("done" if ok else "failed")

    def _tick(self) -> None:
        for row in self._rows():
            if row.state == "running":
                row.redraw()
