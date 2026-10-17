"""How the progress panel turns a step's counts into a bar and a summary."""

from smart_gallery.progress import COPYING, FINDING, READING, ProgressEvent
from smart_gallery.tui_progress import bar_cells, legend, summarize


def _copy(done, total, copied, skipped, failed):
    outcomes = (("copied", copied), ("skipped", skipped), ("failed", failed))
    return ProgressEvent(COPYING, done, total, outcomes=outcomes)


def test_outcomes_get_cells_in_proportion_and_the_rest_is_remaining():
    cells = bar_cells(10, _copy(8, 10, 5, 2, 1))
    assert cells == [("copied", 5), ("skipped", 2), ("failed", 1), ("remaining", 2)]


def test_a_few_failures_still_show_as_at_least_one_cell():
    cells = bar_cells(60, _copy(1204, 3000, 1150, 40, 14))
    assert cells == [("copied", 23), ("skipped", 1), ("failed", 1), ("remaining", 35)]


def test_a_finished_step_fills_the_whole_bar():
    cells = bar_cells(10, _copy(3, 3, 1, 1, 1))
    assert cells == [("copied", 4), ("skipped", 3), ("failed", 3)]


def test_a_step_without_outcomes_is_a_plain_bar():
    assert bar_cells(10, ProgressEvent(READING, 1, 5)) == [
        ("done", 2),
        ("remaining", 8),
    ]


def test_an_unknown_total_leaves_the_bar_empty():
    assert bar_cells(10, ProgressEvent(FINDING, 1500)) == [("remaining", 10)]


def test_running_summary_shows_counts_elapsed_and_time_left():
    event = _copy(1204, 3000, 1150, 40, 14)
    assert summarize(event, elapsed=31, running=True) == (
        "1,204 / 3,000 files · 0:31 · ~0:46 left"
    )


def test_finished_summary_drops_the_estimate():
    event = ProgressEvent(READING, 3000, 3000)
    assert summarize(event, elapsed=42, running=False) == "3,000 / 3,000 files · 0:42"


def test_counting_summary_without_a_total():
    event = ProgressEvent(FINDING, 1500)
    assert summarize(event, elapsed=5, running=True) == "1,500 files so far · 0:05"


def test_legend_lists_each_outcome_count():
    assert legend(_copy(1204, 3000, 1150, 40, 14)) == (
        "1,150 copied   40 skipped   14 failed"
    )
    assert legend(ProgressEvent(READING, 1, 2)) == ""
