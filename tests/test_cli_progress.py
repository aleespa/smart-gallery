"""The CLI's progress output and log files, run as a real subprocess."""

import os
import subprocess
import sys

import pytest

from smart_gallery import cli
from smart_gallery.db import GalleryRepository
from smart_gallery.progress import COPYING, Cancelled, ProgressEvent


def run_cli(args, cwd, progress=None):
    env = {k: v for k, v in os.environ.items() if k != "SG_PROGRESS"}
    env["PYTHONIOENCODING"] = "utf-8"
    if progress:
        env["SG_PROGRESS"] = progress
    return subprocess.run(
        [sys.executable, "-m", "smart_gallery.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.fixture
def card_and_drive(tmp_path, drive):
    card = tmp_path / "card"
    card.mkdir()
    for name in ("a.txt", "b.txt"):
        (card / name).write_bytes(b"x")
    GalleryRepository.create(drive).close()
    return card, drive


def test_terminal_shows_counts_and_moves_file_details_to_a_log(
    tmp_path, card_and_drive
):
    card, drive = card_and_drive
    result = run_cli(["import", str(card), "--to", str(drive)], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Copying files" in result.stderr
    assert "copied=2" in result.stderr
    assert "Copied:" not in result.stderr

    logs = list((tmp_path / "logs").glob("import-*.log"))
    assert len(logs) == 1
    assert logs[0].read_text(encoding="utf-8").count("Copied:") == 2
    assert f"Details: {logs[0]}" in result.stderr


def test_ui_mode_writes_progress_lines_instead_of_a_bar(tmp_path, card_and_drive):
    card, drive = card_and_drive
    result = run_cli(
        ["import", str(card), "--to", str(drive)], cwd=tmp_path, progress="json"
    )
    assert result.returncode == 0, result.stderr
    events = [ProgressEvent.from_line(line) for line in result.stdout.splitlines()]
    events = [e for e in events if e is not None]
    assert events[-1] == ProgressEvent(
        COPYING, 2, 2, outcomes=(("copied", 2), ("skipped", 0), ("failed", 0))
    )
    assert "Copying files" not in result.stderr  # no terminal bar


def test_quick_commands_do_not_write_log_files(tmp_path, card_and_drive):
    _, drive = card_and_drive
    result = run_cli(["people", str(drive)], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "logs").exists()


def test_main_exits_130_when_cancelled(tmp_path, drive, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)  # sync writes ./logs/ into the cwd

    def cancelled(*args, **kwargs):
        raise Cancelled()

    monkeypatch.setattr(cli, "sync_drive", cancelled)
    with pytest.raises(SystemExit) as exc:
        cli.main(["sync", str(drive)])
    assert exc.value.code == 130


@pytest.mark.parametrize("progress, expected", [("json", "utf-8"), (None, "cp1252")])
def test_json_progress_pipes_are_utf8(monkeypatch, progress, expected):
    """A GUI/TUI reads the CLI through a pipe; its output must be UTF-8 whatever
    the console code page (a frozen exe ignores PYTHONIOENCODING)."""
    import io

    if progress:
        monkeypatch.setenv("SG_PROGRESS", progress)
    else:
        monkeypatch.delenv("SG_PROGRESS", raising=False)
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert out.encoding == expected
    assert err.encoding == expected
