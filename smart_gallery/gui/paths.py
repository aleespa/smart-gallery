"""Where the desktop app keeps its data and how it launches the CLI. No Qt here."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path


def app_data_dir() -> Path:
    """Per-user app data folder (``$SG_APP_DATA``, else ``%LOCALAPPDATA%/SmartGallery``)."""
    override = os.environ.get("SG_APP_DATA")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / "SmartGallery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def cli_command() -> list[str]:
    """argv prefix that runs the Smart Gallery CLI as a subprocess."""
    if is_frozen():
        return [str(Path(sys.executable).with_name("smart-gallery.exe"))]
    return [sys.executable, "-m", "smart_gallery.cli"]


def exiftool_available() -> bool:
    from smart_gallery.analysis.extract import _get_exiftool_path

    exe = _get_exiftool_path()
    return shutil.which(exe) is not None or Path(exe).is_file()


def faces_available() -> bool:
    """True when the 'faces' extra is installed (checked without importing it)."""
    return importlib.util.find_spec("insightface") is not None
