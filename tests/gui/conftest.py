"""GUI tests run headless and are skipped when the optional 'gui' extra is missing."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/gui" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.gui)


@pytest.fixture(autouse=True)
def _app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_APP_DATA", str(tmp_path / "appdata"))
