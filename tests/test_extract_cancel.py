"""Cancelling metadata extraction while an ExifTool batch is still running."""

import threading
import time

import pytest

from smart_gallery.analysis import extract
from smart_gallery.progress import CANCEL_ENV, Cancelled


def test_analyze_paths_stops_while_a_batch_is_running(tmp_path, monkeypatch):
    release = threading.Event()
    running = threading.Event()

    def slow_batch(paths, tag_profile="all"):
        running.set()
        release.wait(30)  # an ExifTool batch over thousands of files
        return []

    monkeypatch.setattr(extract, "run_exiftool_batch", slow_batch)
    stop = tmp_path / "stop"
    monkeypatch.setenv(CANCEL_ENV, str(stop))
    threading.Timer(0.3, stop.touch).start()
    paths = [tmp_path / f"IMG_{i}.jpg" for i in range(10)]
    try:
        started = time.monotonic()
        with pytest.raises(Cancelled):
            extract.analyze_paths(paths)
        assert running.is_set()
        assert time.monotonic() - started < 2.5
    finally:
        release.set()
