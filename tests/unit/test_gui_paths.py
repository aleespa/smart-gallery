"""The no-Qt half of the desktop app must work without PySide6."""

import subprocess
import sys

from smart_gallery.gui import paths


def test_app_data_dir_honors_env(tmp_path, monkeypatch):
    target = tmp_path / "data"
    monkeypatch.setenv("SG_APP_DATA", str(target))
    assert paths.app_data_dir() == target
    assert target.is_dir()


def test_cli_command_dev_mode():
    assert paths.cli_command() == [sys.executable, "-m", "smart_gallery.cli"]


def test_core_import_without_pyside():
    code = (
        "import sys; sys.modules['PySide6'] = None\n"
        "import smart_gallery.cli, smart_gallery.gui, smart_gallery.gui.paths\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
