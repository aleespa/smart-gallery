import subprocess
import sys

import pytest

from smart_gallery.gui.state_store import load_json, save_json


def test_round_trip(tmp_path):
    path = tmp_path / "state.json"
    save_json(path, {"collapsed": ["a", "b"]})
    assert load_json(path) == {"collapsed": ["a", "b"]}
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_is_empty(tmp_path):
    assert load_json(tmp_path / "nope.json") == {}


@pytest.mark.parametrize("text", ["{not json", "[]"])
def test_bad_file_is_empty(tmp_path, text):
    path = tmp_path / "state.json"
    path.write_text(text, encoding="utf-8")
    assert load_json(path) == {}


def test_module_imports_no_qt():
    code = (
        "import smart_gallery.gui.state_store; import sys; "
        "assert 'PySide6' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
