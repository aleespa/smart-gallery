"""The viewer remembers its window and panels."""

from PySide6.QtCore import QRect, QSize

from smart_gallery.gui.viewer.state import ViewerState, load_state, place, save_state


def test_state_round_trip(tmp_path):
    path = tmp_path / "viewer.json"
    save_state(path, ViewerState((10, 20, 900, 700), False, True))
    assert load_state(path) == ViewerState((10, 20, 900, 700), False, True)


def test_state_defaults_on_missing_or_bad_file(tmp_path):
    assert load_state(tmp_path / "none.json") == ViewerState()
    path = tmp_path / "viewer.json"
    path.write_text('{"info_open": "yes"')
    assert load_state(path) == ViewerState()


def test_state_ignores_wrong_types_field_by_field(tmp_path):
    path = tmp_path / "viewer.json"
    path.write_text('{"info_open": "yes", "strip_open": true, "geometry": [1, 2]}')
    assert load_state(path) == ViewerState(None, True, True)


def test_save_state_ignores_unwritable_place(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    save_state(blocker / "viewer.json", ViewerState())  # no exception


def test_place_first_time_is_centred_80_percent():
    assert place(None, QRect(0, 0, 1920, 1080)) == QRect(192, 108, 1536, 864)


def test_place_brings_an_off_screen_window_back():
    screen = QRect(0, 0, 1920, 1080)
    rect = place((2500, 900, 1000, 800), screen)  # from a monitor that is gone
    assert screen.contains(rect) and rect.size() == QSize(1000, 800)
    assert place((-500, -300, 800, 600), screen).topLeft() == screen.topLeft()


def test_place_shrinks_a_window_larger_than_the_screen():
    screen = QRect(0, 0, 1920, 1080)
    assert place((0, 0, 4000, 3000), screen) == screen
