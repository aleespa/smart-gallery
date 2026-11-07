"""The Studio photo viewer window."""

import pytest
from PIL import Image
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtTest import QTest

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.library.model import LibraryRow
from smart_gallery.gui.thumbs.cache import thumb_path
from smart_gallery.gui.viewer import previews as previews_mod
from smart_gallery.gui.viewer.window import (
    DISCONNECTED,
    NO_PREVIEW,
    MediaViewer,
)

_BLOB = b"\x00" * 8


class FakeThumbs(QObject):
    ready = Signal(str, int, str)

    def __init__(self):
        super().__init__()
        self.requests = []

    def request(self, root, media_id, mtime_ns, relpath, ext, media_type):
        self.requests.append(media_id)

    def cancel_pending(self):
        raise AssertionError("the viewer must not cancel the Library's thumbnails")


@pytest.fixture(autouse=True)
def _app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_APP_DATA", str(tmp_path / "appdata"))


def _ctx(db_path):
    ctx = AppContext()
    ctx.thumbs = FakeThumbs()
    ctx.set_gallery(Gallery("uuid", str(db_path), "test"))
    return ctx


def _photo(repo, make_item, rel, size=(900, 600), **kw):
    path = repo.drive_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (90, 120, 160)).save(path, "JPEG")
    repo.upsert_many([make_item(rel, width=size[0], height=size[1], **kw)])


def _rows(repo):
    return [
        LibraryRow(r["id"], r["relpath"], r["media_type"], r["ext"], r["mtime_ns"])
        for r in repo.conn.execute(
            "SELECT id, relpath, media_type, ext, mtime_ns FROM media_items ORDER BY relpath"
        )
    ]


def _open(qtbot, repo, rows=None, index=0, **kw):
    ctx = kw.pop("ctx", None) or _ctx(repo.db_path)
    viewer = MediaViewer(ctx, repo.drive_root, rows or _rows(repo), index, **kw)
    qtbot.addWidget(viewer)
    viewer.show()
    qtbot.waitExposed(viewer)
    return viewer, ctx


def _spots(viewer):
    widgets = (viewer.prev_btn, viewer.next_btn, viewer.info_btn, viewer.full_btn)
    return [w.mapTo(viewer, QPoint()) for w in widgets] + [viewer.size()]


def test_controls_stay_put_between_shapes(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1-portrait.jpg", (600, 900))
    _photo(repo, make_item, "a/2-wide.jpg", (1800, 600))
    viewer, _ = _open(qtbot, repo)
    qtbot.waitUntil(viewer.has_preview)
    before = _spots(viewer)
    viewer.show_next()
    qtbot.waitUntil(lambda: viewer.field("Path") == "a/2-wide.jpg")
    qtbot.waitUntil(viewer.has_preview)
    assert _spots(viewer) == before


def test_fields_position_and_ends(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg", camera="Canon EOS R6", date_taken="2026-06-14")
    _photo(repo, make_item, "a/2.jpg")
    viewer, _ = _open(qtbot, repo)
    qtbot.waitUntil(lambda: viewer.field("Camera") == "Canon EOS R6")
    assert viewer.field("Path") == "a/1.jpg"
    assert viewer.field("Position") == "1 / 2"
    assert viewer.field("Taken") == "14 June 2026"
    assert viewer.position_label.text() == "1 / 2"
    assert not viewer.prev_btn.isEnabled() and viewer.next_btn.isEnabled()
    viewer.show_next()
    assert not viewer.next_btn.isEnabled()
    viewer.show_next()  # at the end: stays
    assert viewer.field("Position") == "2 / 2"


def test_key_burst_ends_on_last_photo(qtbot, repo, make_item):
    for i in range(10):
        _photo(repo, make_item, f"a/{i:02d}.jpg", (300 + i, 200))
    viewer, _ = _open(qtbot, repo)
    for _ in range(9):
        QTest.keyClick(viewer, Qt.Key.Key_Right)
    assert viewer.field("Position") == "10 / 10"
    qtbot.waitUntil(viewer.has_preview)
    qtbot.wait(100)  # late results for earlier photos must not replace it
    assert viewer.stage.image_size().width() == 309
    assert viewer.field("Path") == "a/09.jpg"


def test_home_end_keys(qtbot, repo, make_item):
    for i in range(3):
        _photo(repo, make_item, f"a/{i}.jpg")
    viewer, _ = _open(qtbot, repo, index=1)
    QTest.keyClick(viewer, Qt.Key.Key_End)
    assert viewer.field("Position") == "3 / 3"
    QTest.keyClick(viewer, Qt.Key.Key_Home)
    assert viewer.field("Position") == "1 / 3"


def test_missing_file_shows_message_and_keeps_nav(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1-gone.jpg")])
    _photo(repo, make_item, "a/2.jpg")
    viewer, _ = _open(qtbot, repo)
    qtbot.waitUntil(lambda: viewer.stage.message() == NO_PREVIEW)
    viewer.show_next()
    qtbot.waitUntil(viewer.has_preview)


def test_disconnected_drive_message(qtbot, repo, make_item, tmp_path):
    _photo(repo, make_item, "a/1.jpg")
    rows = _rows(repo)
    gone = tmp_path / "unplugged"
    ctx = _ctx(gone / ".smart_gallery" / "gallery.db")
    viewer = MediaViewer(ctx, gone, rows, 0)
    qtbot.addWidget(viewer)
    viewer.show()
    qtbot.waitUntil(lambda: viewer.stage.message() == DISCONNECTED)


def test_offloaded_row_message(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1.jpg"), make_item("a/2.jpg")])
    rows = [
        LibraryRow(r.id, r.relpath, r.media_type, r.ext, r.mtime_ns, offloaded=True)
        for r in _rows(repo)
    ]
    viewer, _ = _open(
        qtbot,
        repo,
        rows,
        holder=lambda row: "Backup D" if row.relpath == "a/1.jpg" else None,
    )
    assert viewer.stage.message() == "Kept on Backup D only."
    viewer.show_next()
    assert viewer.stage.message() == "Kept on a mirror drive only."


def test_closes_on_gallery_change(qtbot, repo, make_item, tmp_path):
    _photo(repo, make_item, "a/1.jpg")
    viewer, ctx = _open(qtbot, repo)
    ctx.set_gallery(Gallery("x", str(tmp_path / "other.db"), "Other"))
    assert not viewer.isVisible()


def test_info_toggle_is_remembered(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg")
    viewer, _ = _open(qtbot, repo)
    assert viewer.info_panel.isVisible() and viewer.info_btn.isChecked()
    stage_width = viewer.stage.width()
    QTest.keyClick(viewer, Qt.Key.Key_I)
    assert not viewer.info_panel.isVisible() and not viewer.info_btn.isChecked()
    qtbot.waitUntil(lambda: viewer.stage.width() > stage_width)
    assert (
        viewer.prev_btn.mapTo(viewer, QPoint()).x()
        == viewer.stage.mapTo(viewer, QPoint()).x() + 12
    )
    viewer.close()
    again, _ = _open(qtbot, repo)
    assert not again.info_panel.isVisible()


def test_window_size_is_remembered(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg")
    viewer, _ = _open(qtbot, repo)
    viewer.resize(700, 500)
    qtbot.waitUntil(lambda: viewer.width() == 700)
    viewer.close()
    again, _ = _open(qtbot, repo)
    assert (again.width(), again.height()) == (700, 500)


def test_esc_leaves_full_screen_then_closes(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg")
    viewer, _ = _open(qtbot, repo)
    QTest.keyClick(viewer, Qt.Key.Key_F)
    qtbot.waitUntil(viewer.isFullScreen)
    QTest.keyClick(viewer, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not viewer.isFullScreen())
    assert viewer.isVisible()
    QTest.keyClick(viewer, Qt.Key.Key_Escape)
    assert not viewer.isVisible()


def test_thumbnail_shown_before_the_preview(qtbot, repo, make_item, monkeypatch):
    _photo(repo, make_item, "a/1.jpg")
    row = _rows(repo)[0]
    thumb = thumb_path(repo.drive_root, row.id, row.mtime_ns, row.ext)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (256, 171)).save(thumb, "JPEG")
    monkeypatch.setattr(previews_mod.PreviewLoader, "request", lambda *a, **k: None)
    viewer, _ = _open(qtbot, repo)
    assert viewer.stage.image_size().width() == 256
    assert not viewer.has_preview()


def test_thumbnail_asked_for_when_not_cached(qtbot, repo, make_item, monkeypatch):
    _photo(repo, make_item, "a/1.jpg")
    monkeypatch.setattr(previews_mod.PreviewLoader, "request", lambda *a, **k: None)
    viewer, ctx = _open(qtbot, repo)
    row = viewer.row()
    assert ctx.thumbs.requests == [row.id]
    path = repo.drive_root / "t.jpg"
    Image.new("RGB", (200, 100)).save(path, "JPEG")
    ctx.thumbs.ready.emit(str(repo.drive_root), row.id, str(path))
    assert viewer.stage.image_size().width() == 200


def test_video_shows_thumbnail_play_glyph_and_open(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg")
    repo.upsert_many([make_item("a/2.mp4", media_type="video", ext=".mp4")])
    viewer, _ = _open(qtbot, repo)
    assert viewer.open_btn.isHidden()
    viewer.show_next()
    assert not viewer.open_btn.isHidden()
    assert viewer.stage.message() == NO_PREVIEW  # no thumbnail cached yet
    assert not viewer.has_preview()


def test_zoom_loads_a_sharper_preview(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg", (3000, 2000))
    viewer, _ = _open(qtbot, repo)
    qtbot.waitUntil(viewer.has_preview)
    assert viewer.stage.image_size().width() == previews_mod.PREVIEW_EDGE
    QTest.keyClick(viewer, Qt.Key.Key_Z)
    assert viewer.stage.is_zoomed()
    qtbot.waitUntil(lambda: viewer.stage.image_size().width() == 3000)
    assert viewer.stage.is_zoomed()


def test_face_boxes_button(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg", (900, 600))
    _photo(repo, make_item, "a/2.jpg")
    media = repo.conn.execute(
        "SELECT id FROM media_items WHERE relpath='a/1.jpg'"
    ).fetchone()[0]
    ana = repo.conn.execute("INSERT INTO persons (name) VALUES ('Ana')").lastrowid
    repo.conn.execute(
        "INSERT INTO faces (media_id, bbox_x, bbox_y, bbox_w, bbox_h, embedding,"
        " person_id, state) VALUES (?, 90, 60, 90, 60, ?, ?, 'user')",
        (media, _BLOB, ana),
    )
    repo.conn.execute(
        "INSERT INTO face_scan_state (media_id, det_version, n_faces, img_w, img_h)"
        " VALUES (?, 'v', 1, 900, 600)",
        (media,),
    )
    repo.conn.commit()
    viewer, _ = _open(qtbot, repo)
    qtbot.waitUntil(lambda: not viewer.faces_btn.isHidden())
    qtbot.waitUntil(viewer.has_preview)
    assert viewer.boxes() == []
    viewer.faces_btn.click()
    (box,) = viewer.boxes()
    assert box.label == "Ana" and box.rect.width() == pytest.approx(90)
    viewer.show_next()  # a photo without faces hides the button
    qtbot.waitUntil(lambda: viewer.field("Path") == "a/2.jpg")
    qtbot.waitUntil(lambda: viewer.faces_btn.isHidden())


def test_info_panel_shows_sections(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg", camera="Canon EOS R6", date_taken="2026-06-14")
    viewer, _ = _open(qtbot, repo)
    qtbot.waitUntil(lambda: "Canon EOS R6" in viewer.info_text())
    text = viewer.info_text()
    assert "14 June 2026" in text and "EXPOSURE" in text and "a/1.jpg" in text


def test_info_panel_shows_only_the_current_photo(qtbot, repo, make_item):
    from PySide6.QtWidgets import QLabel

    _photo(repo, make_item, "a/1.jpg", camera="Canon EOS R6")
    _photo(repo, make_item, "a/2.jpg", camera="Nikon Z6")
    viewer, _ = _open(qtbot, repo)
    qtbot.waitUntil(lambda: "Canon EOS R6" in viewer.info_text())
    viewer.show_next()
    qtbot.waitUntil(lambda: "Nikon Z6" in viewer.info_text())
    shown = [
        label.text()
        for label in viewer.info_body.findChildren(QLabel)
        if label.isVisibleTo(viewer.info_body)
    ]
    assert "a/1.jpg" not in shown and "Canon EOS R6" not in shown


def test_holding_right_does_not_queue_every_photo(qtbot, repo, make_item, monkeypatch):
    """Landing after a burst, the shown photo's preview is not stuck behind a
    decode for every photo passed on the way."""
    import time

    real = previews_mod.load_preview

    def slow(*args, **kwargs):
        time.sleep(0.05)
        return real(*args, **kwargs)

    monkeypatch.setattr(previews_mod, "load_preview", slow)
    for i in range(30):
        _photo(repo, make_item, f"a/{i:02d}.jpg", (300, 200))
    viewer, _ = _open(qtbot, repo)
    for _ in range(29):
        QTest.keyClick(viewer, Qt.Key.Key_Right)
    started = time.monotonic()
    qtbot.waitUntil(viewer.has_preview, timeout=5000)
    assert time.monotonic() - started < 0.6  # ~80 queued decodes take ~2 s


def test_reopens_on_the_monitor_it_was_left_on(
    qtbot, repo, make_item, monkeypatch, tmp_path
):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication

    from smart_gallery.gui import paths
    from smart_gallery.gui.viewer.state import STATE_FILE, ViewerState, save_state

    class SecondScreen:
        def availableGeometry(self):  # noqa: N802 (Qt API)
            return QRect(1920, 0, 1920, 1080)

    second = SecondScreen()
    monkeypatch.setattr(
        QGuiApplication,
        "screenAt",
        staticmethod(lambda point: second if point.x() >= 1920 else None),
    )
    save_state(paths.app_data_dir() / STATE_FILE, ViewerState((2000, 100, 700, 500)))
    _photo(repo, make_item, "a/1.jpg")
    ctx = _ctx(repo.db_path)
    viewer = MediaViewer(ctx, repo.drive_root, _rows(repo), 0)
    qtbot.addWidget(viewer)
    assert (viewer.x(), viewer.y(), viewer.width(), viewer.height()) == (
        2000,
        100,
        700,
        500,
    )


def test_viewer_has_the_apps_own_title_bar(qtbot, repo, make_item):
    """Like the main window: no system frame; the top bar moves the window
    and carries minimize, maximize and close."""
    from PySide6.QtWidgets import QToolButton

    from smart_gallery.gui.titlebar import TitleBar

    _photo(repo, make_item, "a/1.jpg")
    viewer, _ = _open(qtbot, repo)
    assert viewer.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert isinstance(viewer.top_bar, TitleBar)
    assert viewer.top_bar.isAncestorOf(viewer.window_buttons)
    tips = [b.toolTip() for b in viewer.top_bar.findChildren(QToolButton)]
    assert tips.count("Close") == 1  # one close button, the window's own
    viewer.window_buttons.maximize_btn.click()
    qtbot.waitUntil(viewer.isMaximized)
    assert viewer.frame._margin == 0  # no resize border while maximized
    viewer.window_buttons.maximize_btn.click()
    qtbot.waitUntil(lambda: not viewer.isMaximized())
    assert viewer.frame._margin > 0
    viewer.window_buttons.close_btn.click()
    assert not viewer.isVisible()


def _centre_x(widget, viewer):
    return widget.mapTo(viewer, QPoint(widget.width() // 2, 0)).x()


def test_tools_are_centred_in_the_top_bar(qtbot, repo, make_item):
    _photo(repo, make_item, "a/1.jpg")
    viewer, _ = _open(qtbot, repo)
    viewer.resize(1100, 700)
    qtbot.waitUntil(lambda: viewer.width() == 1100)
    assert abs(_centre_x(viewer.tools, viewer) - _centre_x(viewer.top_bar, viewer)) <= 2
    QTest.keyClick(viewer, Qt.Key.Key_I)  # the info panel does not shift them
    assert abs(_centre_x(viewer.tools, viewer) - _centre_x(viewer.top_bar, viewer)) <= 2
    assert viewer.tools.isAncestorOf(viewer.zoom_btn)
    assert viewer.tools.isAncestorOf(viewer.info_btn)


def test_position_follows_the_name_and_long_names_are_cut(qtbot, repo, make_item):
    _photo(repo, make_item, "a/short.jpg")
    long_name = "a/" + "very_long_file_name_" * 12 + ".jpg"
    _photo(repo, make_item, long_name)
    viewer, _ = _open(qtbot, repo)
    viewer.resize(1100, 700)
    qtbot.waitUntil(lambda: viewer.width() == 1100)
    while viewer.field("Path") != "a/short.jpg":
        viewer.show_next()
    label = viewer.title_label
    text_end = label.geometry().left() + label.fontMetrics().horizontalAdvance(
        label.text()
    )
    gap = viewer.position_label.geometry().left() - text_end
    assert 0 < gap <= 16  # right after the name's text, not off by the tools
    viewer.show_index(
        next(i for i, r in enumerate(viewer.rows) if r.relpath == long_name)
    )
    qtbot.wait(20)
    tools_left = viewer.tools.mapTo(viewer, QPoint()).x()
    assert (
        viewer.position_label.mapTo(viewer, QPoint()).x()
        + viewer.position_label.width()
        <= tools_left
    )
    assert abs(_centre_x(viewer.tools, viewer) - _centre_x(viewer.top_bar, viewer)) <= 2
