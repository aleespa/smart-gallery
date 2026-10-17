"""Progress events: how services report them and how the CLI writes them."""

import io

from smart_gallery.progress import (
    JsonProgress,
    ProgressEvent,
    TerminalProgress,
    counted,
)

COPY = ProgressEvent(
    "Copying files", 3, 10, outcomes=(("copied", 2), ("skipped", 0), ("failed", 1))
)


def test_event_survives_a_round_trip_through_a_line():
    assert ProgressEvent.from_line(COPY.to_line()) == COPY


def test_ordinary_output_lines_are_not_events():
    assert ProgressEvent.from_line("2026-10-11 | INFO | Sync complete") is None
    assert ProgressEvent.from_line("@sg-progress {not json") is None


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _written(stream):
    return [ProgressEvent.from_line(line) for line in stream.getvalue().splitlines()]


def test_json_progress_throttles_within_a_step_but_keeps_first_and_last():
    stream, clock = io.StringIO(), FakeClock()
    report = JsonProgress(stream=stream, interval=0.5, clock=clock)
    for done in (1, 2, 3):
        report(ProgressEvent("Reading metadata", done, 4))
    assert _written(stream) == [ProgressEvent("Reading metadata", 1, 4)]

    report(ProgressEvent("Reading metadata", 4, 4))  # finished: always written
    assert _written(stream)[-1] == ProgressEvent("Reading metadata", 4, 4)


def test_json_progress_flushes_the_held_back_event_when_the_step_changes():
    stream, clock = io.StringIO(), FakeClock()
    report = JsonProgress(stream=stream, interval=0.5, clock=clock)
    report(ProgressEvent("Finding files", 500))
    report(ProgressEvent("Finding files", 1000))  # held back by the throttle
    report(ProgressEvent("Reading metadata", 1, 1200))
    assert _written(stream) == [
        ProgressEvent("Finding files", 500),
        ProgressEvent("Finding files", 1000),
        ProgressEvent("Reading metadata", 1, 1200),
    ]


def test_json_progress_writes_again_once_the_interval_passes():
    stream, clock = io.StringIO(), FakeClock()
    report = JsonProgress(stream=stream, interval=0.5, clock=clock)
    report(ProgressEvent("Copying files", 1, 9))
    clock.now = 0.6
    report(ProgressEvent("Copying files", 2, 9))
    assert [e.done for e in _written(stream)] == [1, 2]


def test_json_progress_close_flushes_what_was_held_back():
    stream, clock = io.StringIO(), FakeClock()
    report = JsonProgress(stream=stream, interval=0.5, clock=clock)
    report(ProgressEvent("Finding files", 500))
    report(ProgressEvent("Finding files", 900))
    report.close()
    assert _written(stream)[-1] == ProgressEvent("Finding files", 900)


def test_terminal_progress_shows_counts_and_outcomes():
    stream = io.StringIO()
    report = TerminalProgress(stream=stream)
    report(COPY)
    report.close()
    text = stream.getvalue()
    assert "Copying files" in text
    assert "3/10" in text
    assert "copied=2" in text
    assert "failed=1" in text


def test_counted_reports_every_n_items_and_a_final_total():
    events = []
    items = list(counted(range(1, 8), "Finding files", events.append, every=3))
    assert items == [1, 2, 3, 4, 5, 6, 7]
    assert events == [
        ProgressEvent("Finding files", 3),
        ProgressEvent("Finding files", 6),
        ProgressEvent("Finding files", 7, 7),
    ]


def test_counted_without_a_callback_just_yields():
    assert list(counted(["a", "b"], "Finding files", None)) == ["a", "b"]
