"""The viewer's filmstrip: hidden by default, S or the tab shows it."""

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from smart_gallery.gui.library.model import LibraryRow
from smart_gallery.gui.viewer.filmstrip import STRIP_HEIGHT, Filmstrip
from tests.gui.test_viewer_window import _ctx, _open, _photo, _rows


@pytest.fixture(autouse=True)
def _app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_APP_DATA", str(tmp_path / "appdata"))


@pytest.fixture
def photos(repo, make_item):
    for i in range(6):
        _photo(repo, make_item, f"a/{i}.jpg", (300, 200))
    return _rows(repo)


def test_strip_hidden_by_default_and_s_toggles_it(qtbot, repo, photos):
    viewer, _ = _open(qtbot, repo)
    assert not viewer.strip.isVisible()
    assert viewer.stage.strip_tab.isVisible()
    arrows = viewer.prev_btn.mapTo(viewer, QPoint()).x()
    top = viewer.info_btn.mapTo(viewer, QPoint())
    QTest.keyClick(viewer, Qt.Key.Key_S)
    assert viewer.strip.isVisible() and viewer.strip.height() == STRIP_HEIGHT
    assert not viewer.stage.strip_tab.isVisible()
    assert viewer.prev_btn.mapTo(viewer, QPoint()).x() == arrows
    assert viewer.info_btn.mapTo(viewer, QPoint()) == top
    QTest.keyClick(viewer, Qt.Key.Key_S)
    assert not viewer.strip.isVisible()


def test_tab_opens_the_strip(qtbot, repo, photos):
    viewer, _ = _open(qtbot, repo)
    viewer.stage.strip_tab.click()
    assert viewer.strip.isVisible()


def test_click_in_strip_jumps(qtbot, repo, photos):
    viewer, _ = _open(qtbot, repo)
    QTest.keyClick(viewer, Qt.Key.Key_S)
    viewer.strip.picked.emit(3)
    assert viewer.field("Position") == "4 / 6"


def test_strip_follows_the_current_photo(qtbot, repo, photos):
    viewer, _ = _open(qtbot, repo)
    QTest.keyClick(viewer, Qt.Key.Key_S)
    viewer.show_next()
    assert viewer.strip.current_row() == 1


def test_strip_state_is_remembered(qtbot, repo, photos):
    viewer, _ = _open(qtbot, repo)
    QTest.keyClick(viewer, Qt.Key.Key_S)
    viewer.close()
    again, _ = _open(qtbot, repo)
    assert again.strip.isVisible()


def test_strip_asks_only_for_visible_thumbnails(qtbot, repo, make_item):
    for i in range(60):
        repo.upsert_many([make_item(f"b/{i:02d}.jpg")])
    ctx = _ctx(repo.db_path)
    strip = Filmstrip(ctx)
    qtbot.addWidget(strip)
    strip.resize(500, STRIP_HEIGHT)
    strip.show()
    rows = _rows(repo)
    strip.set_rows(repo.drive_root, rows)
    strip.set_current(0)
    qtbot.waitUntil(lambda: len(ctx.thumbs.requests) > 0)
    qtbot.wait(100)
    asked = set(ctx.thumbs.requests)
    assert 0 < len(asked) < 30 and rows[0].id in asked


def test_strip_shows_a_thumbnail_when_it_arrives(qtbot, repo, photos, tmp_path):
    ctx = _ctx(repo.db_path)
    strip = Filmstrip(ctx)
    qtbot.addWidget(strip)
    strip.resize(500, STRIP_HEIGHT)
    strip.show()
    strip.set_rows(repo.drive_root, photos)
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (60, 40)).save(thumb, "JPEG")
    assert not strip.has_thumbnail(photos[0].id)
    ctx.thumbs.ready.emit(str(repo.drive_root), photos[0].id, str(thumb))
    assert strip.has_thumbnail(photos[0].id)


def test_strip_ignores_other_drives(qtbot, repo, photos, tmp_path):
    ctx = _ctx(repo.db_path)
    strip = Filmstrip(ctx)
    qtbot.addWidget(strip)
    strip.set_rows(repo.drive_root, [LibraryRow(1, "x.jpg", "image", ".jpg", 0)])
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (60, 40)).save(thumb, "JPEG")
    ctx.thumbs.ready.emit(str(tmp_path / "other"), 1, str(thumb))
    assert not strip.has_thumbnail(1)
