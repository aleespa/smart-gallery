"""The no-Qt half of the desktop app must work without PySide6."""

import subprocess
import sys
from pathlib import Path

from smart_gallery.gui import paths


def test_app_data_dir_honors_env(tmp_path, monkeypatch):
    target = tmp_path / "data"
    monkeypatch.setenv("SG_APP_DATA", str(target))
    assert paths.app_data_dir() == target
    assert target.is_dir()


def test_cli_command_dev_mode():
    assert paths.cli_command() == [sys.executable, "-m", "smart_gallery.cli"]


def _qt_free_gui_modules() -> list[str]:
    """The desktop app's model/store modules, which must not need Qt."""
    gui = Path(paths.__file__).parent
    files = [
        *gui.glob("**/model.py"),
        *gui.glob("**/store.py"),
        gui / "thumbs" / "cache.py",
        gui / "filters_model.py",
        gui / "jobspec.py",
    ]
    package_root = gui.parent.parent
    return sorted(
        ".".join(f.relative_to(package_root).with_suffix("").parts) for f in files
    )


def test_core_import_without_pyside():
    modules = [
        "smart_gallery.cli",
        "smart_gallery.gui",
        "smart_gallery.gui.paths",
        *_qt_free_gui_modules(),
    ]
    assert "smart_gallery.gui.imports.model" in modules
    code = "import sys; sys.modules['PySide6'] = None\n" + "".join(
        f"import {name}\n" for name in modules
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
