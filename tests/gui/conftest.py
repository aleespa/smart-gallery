"""GUI tests run headless and are skipped when the optional 'gui' extra is missing."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    # The offscreen platform has no fonts of its own; give it the real ones so
    # rendered text (chart axis labels) is measured as on the desktop.
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

import pytest

pytest.importorskip("PySide6")

# QtWebEngine must be imported before any QApplication exists.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
try:
    from PySide6 import QtWebEngineWidgets  # noqa: F401
except ImportError:
    pass


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/gui" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.gui)


@pytest.fixture(autouse=True)
def _app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_APP_DATA", str(tmp_path / "appdata"))
