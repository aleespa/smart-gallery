import pytest
from PIL import Image
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QPixmap

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.library.view import LibraryView
from smart_gallery.gui.library.viewer import MediaViewer


class FakeThumbs(QObject):
    ready = Signal(str, int, str)

    def __init__(self):
        super().__init__()
        self.requests = []
        self.cancels = 0
        self.log = []  # ("request", id) and ("cancel", None), in call order

    def request(self, root, media_id, mtime_ns, relpath, ext, media_type):
        self.requests.append((str(root), media_id))
        self.log.append(("request", media_id))

    def cancel_pending(self):
        self.cancels += 1
        self.log.append(("cancel", None))


def _view(qtbot, db_path, label="test"):
    ctx = AppContext()
    ctx.thumbs = FakeThumbs()
    ctx.set_gallery(Gallery("uuid", str(db_path), label))
    view = LibraryView(ctx)
    qtbot.addWidget(view)
    return view, ctx


def _relpaths(view):
    return [r.relpath for r in view.grid_model.rows]


def test_library_shows_rows_for_folder(qtbot, repo, make_item):
    repo.upsert_many(
        [
            make_item("a/1.jpg", date_taken="2026-01-01"),
            make_item("a/sub/2.jpg", date_taken="2026-01-02"),
            make_item("b/3.jpg", date_taken="2026-01-03"),
        ]
    )
    view, _ = _view(qtbot, repo.db_path)
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 3)
    assert view.summary_text.startswith("3 files")

    view.select_folder("a")
    # Newest first by default.
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/sub/2.jpg", "a/1.jpg"])

    view.subfolders.setChecked(False)
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/1.jpg"])
    assert view.summary_text.startswith("1 file")
    assert view.stack.currentWidget() is view.grid


def test_library_disconnected_drive_message(qtbot, tmp_path):
    missing = tmp_path / "gone" / ".smart_gallery" / "gallery.db"
    view, ctx = _view(qtbot, missing, label="Holiday")
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.message)
    assert view.message.text.text() == (
        "Drive not connected. Plug in Holiday to browse it."
    )
    assert view.grid_model.rows == []
    # Filter changes while disconnected do not raise either.
    ctx.set_filters(ctx.filters)
    ctx.catalog_updated.emit()
    qtbot.wait(50)
    assert view.stack.currentWidget() is view.message


def test_library_reads_while_writer_open(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1.jpg"), make_item("a/2.jpg")])
    repo.conn.execute("BEGIN IMMEDIATE")  # a job holds the write lock
    view, ctx = _view(qtbot, repo.db_path)

    view.reload()
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/2.jpg", "a/1.jpg"])
    assert view.stack.currentWidget() is view.grid

    repo.upsert_many([make_item("a/3.jpg")])  # commits the open transaction
    ctx.catalog_updated.emit()
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/3.jpg", "a/2.jpg", "a/1.jpg"])
    assert view.stack.currentWidget() is view.grid


def test_filters_change_reloads_rows(qtbot, repo, make_item):
    repo.upsert_many(
        [make_item("a.jpg", camera="Canon"), make_item("b.jpg", camera="Sony")]
    )
    view, ctx = _view(qtbot, repo.db_path)
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 2)
    qtbot.waitUntil(lambda: len(view.filters.cameras.available()) == 2)

    view.filters.cameras.pick("Sony")
    view.filters.apply()
    assert ctx.filters.cameras == ["Sony"]
    qtbot.waitUntil(lambda: _relpaths(view) == ["b.jpg"])


def test_thumbnails_requested_for_visible_rows_only(qtbot, repo, make_item, tmp_path):
    repo.upsert_many([make_item(f"a/{i:04}.jpg") for i in range(500)])
    view, ctx = _view(qtbot, repo.db_path)
    view.resize(900, 600)
    view.show()
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 500)
    qtbot.waitUntil(lambda: bool(ctx.thumbs.requests))
    requested = {media_id for _, media_id in ctx.thumbs.requests}
    assert 0 < len(requested) < 100
    first = view.grid_model.rows[0]
    assert first.id in requested
    assert {root for root, _ in ctx.thumbs.requests} == {str(repo.drive_root)}

    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (256, 128), "green").save(thumb)
    index = view.grid_model.index(0)
    placeholder = view.grid_model.data(index, Qt.ItemDataRole.DecorationRole)
    ctx.thumbs.ready.emit("Z:\\other", first.id, str(thumb))  # another gallery
    assert view.grid_model.data(index, Qt.ItemDataRole.DecorationRole) is placeholder
    ctx.thumbs.ready.emit(str(repo.drive_root), first.id, str(thumb))
    pixmap = view.grid_model.data(index, Qt.ItemDataRole.DecorationRole)
    assert isinstance(pixmap, QPixmap) and pixmap is not placeholder
    assert (pixmap.width(), pixmap.height()) == (256, 128)  # scaled at paint time


def test_scrolling_drops_queued_thumbnails_before_requesting_visible_ones(
    qtbot, repo, make_item
):
    repo.upsert_many([make_item(f"a/{i:04}.jpg") for i in range(500)])
    view, ctx = _view(qtbot, repo.db_path)
    view.resize(900, 600)
    view.show()
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 500)
    qtbot.waitUntil(lambda: bool(ctx.thumbs.requests))
    ctx.thumbs.log.clear()

    bar = view.grid.verticalScrollBar()
    bar.setValue(bar.maximum())  # fling to the end: the top rows are stale
    qtbot.waitUntil(lambda: ("cancel", None) in ctx.thumbs.log)
    qtbot.waitUntil(lambda: ctx.thumbs.log[-1][0] == "request")
    first_cancel = ctx.thumbs.log.index(("cancel", None))
    assert first_cancel == 0  # nothing requested before the queue was cleared
    last = view.grid_model.rows[-1].id
    assert ("request", last) in ctx.thumbs.log


def test_video_tile_requests_a_thumbnail_and_keeps_the_frame(
    qtbot, repo, make_item, tmp_path
):
    from smart_gallery.gui.library.tiles import DURATION_ROLE

    repo.upsert_many(
        [make_item("v.mp4", media_type="video", ext=".mp4", duration_ms=83_000)]
    )
    view, ctx = _view(qtbot, repo.db_path)
    view.resize(900, 600)
    view.show()
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 1)
    row = view.grid_model.rows[0]
    qtbot.waitUntil(lambda: [i for _, i in ctx.thumbs.requests] == [row.id])
    index = view.grid_model.index(0)
    placeholder = view.grid_model.data(index, Qt.ItemDataRole.DecorationRole)
    assert not placeholder.isNull()
    assert view.grid_model.data(index, Qt.ItemDataRole.DisplayRole) == "v.mp4"
    assert view.grid_model.data(index, DURATION_ROLE) == "1:23"

    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (256, 144), (0, 0, 255)).save(frame)
    ctx.thumbs.ready.emit(str(view._root), row.id, str(frame))
    tile = view.grid_model.data(index, Qt.ItemDataRole.DecorationRole).toImage()
    assert tile.cacheKey() != placeholder.toImage().cacheKey()
    # The frame as loaded: the delegate paints the play chip, not the model.
    centre = tile.pixelColor(tile.width() // 2, tile.height() // 2)
    assert centre.blue() > 200 and centre.red() < 60


def test_gallery_change_cancels_pending_thumbnails(qtbot, repo, make_item, tmp_path):
    repo.upsert_many([make_item("a.jpg")])
    view, ctx = _view(qtbot, repo.db_path)
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 1)
    ctx.set_gallery(Gallery("x", str(tmp_path / "none.db"), "Other"))
    assert ctx.thumbs.cancels == 1
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.message)
    assert view.grid_model.rows == []


def test_viewer_opens_on_double_click_and_steps(qtbot, repo, make_item, copy_image):
    copy_image(repo.drive_root, "test.JPG", "a/1.jpg")
    repo.upsert_many(
        [
            make_item("a/1.jpg", date_taken="2026-01-01", camera="Canon EOS R6"),
            make_item("a/2.mp4", media_type="video", ext=".mp4", date_taken=None),
        ]
    )
    view, _ = _view(qtbot, repo.db_path)
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 2)

    view.grid.doubleClicked.emit(view.grid_model.index(0))
    viewer = view.viewer
    assert isinstance(viewer, MediaViewer)
    qtbot.waitUntil(lambda: viewer.field("Camera") == "Canon EOS R6")
    qtbot.waitUntil(lambda: viewer.has_preview())
    assert viewer.field("Path") == "a/1.jpg"
    assert not viewer.prev_btn.isEnabled()

    viewer.next_btn.click()
    qtbot.waitUntil(lambda: viewer.field("Path") == "a/2.mp4")
    assert not viewer.next_btn.isEnabled()
    assert not viewer.has_preview()

    viewer.prev_btn.click()
    qtbot.waitUntil(lambda: viewer.field("Path") == "a/1.jpg")


def test_sort_direction_toggle(qtbot, repo, make_item):
    repo.upsert_many(
        [
            make_item("a.jpg", date_taken="2026-01-01", size_bytes=10),
            make_item("b.jpg", date_taken="2026-02-01", size_bytes=30),
            make_item("c.jpg", date_taken="2026-03-01", size_bytes=20),
        ]
    )
    view, _ = _view(qtbot, repo.db_path)
    # The newest photos come first by default.
    qtbot.waitUntil(lambda: _relpaths(view) == ["c.jpg", "b.jpg", "a.jpg"])
    assert view._descending
    assert view.descending.isCheckable() is False

    view.descending.click()
    qtbot.waitUntil(lambda: _relpaths(view) == ["a.jpg", "b.jpg", "c.jpg"])

    # Picking a sort resets the direction to that sort's natural one.
    view.sort.setCurrentIndex(view.sort.findData("size"))
    assert view._descending
    qtbot.waitUntil(lambda: _relpaths(view) == ["b.jpg", "c.jpg", "a.jpg"])
    view.descending.click()
    qtbot.waitUntil(lambda: _relpaths(view) == ["a.jpg", "c.jpg", "b.jpg"])

    view.sort.setCurrentIndex(view.sort.findData("name"))
    assert not view._descending
    assert view.descending.toolTip() == "A to Z"

    view.descending.click()
    view.descending.click()
    assert not view._descending


def _labels(view):
    return [(g.label, g.count) for g in view.grid.groups()]


def test_grid_groups_follow_the_sort(qtbot, repo, make_item):
    repo.upsert_many(
        [
            make_item("x/a.jpg", date_taken="2025-12-24", size_bytes=2 << 20),
            make_item("x/b.jpg", date_taken="2026-03-01", size_bytes=500),
            make_item("y/c.jpg", date_taken="2026-03-15", size_bytes=200 << 20),
            make_item("d.jpg", date_taken=None, size_bytes=700),
        ]
    )
    view, _ = _view(qtbot, repo.db_path)
    view.resize(900, 600)
    view.show()
    qtbot.waitUntil(
        lambda: _labels(view)
        == [("March 2026", 2), ("December 2025", 1), ("No date", 1)]
    )

    view.sort.setCurrentIndex(view.sort.findData("name"))
    qtbot.waitUntil(lambda: _labels(view) == [("Top folder", 1), ("x", 2), ("y", 1)])

    view.sort.setCurrentIndex(view.sort.findData("size"))
    qtbot.waitUntil(
        lambda: _labels(view)
        == [("100 MB to 1 GB", 1), ("1 to 10 MB", 1), ("Under 1 MB", 2)]
    )


def test_grid_headings_take_space_and_are_not_tiles(qtbot, repo, make_item):
    from smart_gallery.gui.library.grid import HEADER_HEIGHT, SIDE_PAD

    repo.upsert_many(
        [
            make_item("a.jpg", date_taken="2026-03-01"),
            make_item("b.jpg", date_taken="2026-02-01"),
        ]
    )
    view, _ = _view(qtbot, repo.db_path)
    view.resize(900, 600)
    view.show()
    qtbot.waitUntil(lambda: len(view.grid.groups()) == 2)
    grid = view.grid
    first = grid.visualRect(view.grid_model.index(0))
    second = grid.visualRect(view.grid_model.index(1))
    assert first.top() == HEADER_HEIGHT
    assert first.x() == second.x() == SIDE_PAD
    assert second.top() >= first.bottom() + HEADER_HEIGHT  # own heading between
    assert not grid.indexAt(first.topLeft() - QPoint(0, HEADER_HEIGHT)).isValid()
    assert grid.indexAt(first.center()).row() == 0
    assert grid.indexAt(second.center()).row() == 1
    assert list(grid.visible_rows()) == [0, 1]


def test_clicking_a_heading_collapses_its_group(qtbot, repo, make_item):
    from smart_gallery.gui.library.grid import HEADER_HEIGHT, SIDE_PAD

    repo.upsert_many(
        [
            make_item("a.jpg", date_taken="2026-03-02"),
            make_item("b.jpg", date_taken="2026-03-01"),
            make_item("c.jpg", date_taken="2026-02-01"),
        ]
    )
    view, ctx = _view(qtbot, repo.db_path)
    view.resize(900, 600)
    view.show()
    qtbot.waitUntil(lambda: len(view.grid.groups()) == 2)
    grid, model = view.grid, view.grid_model
    heading = QPoint(20, HEADER_HEIGHT // 2)
    assert grid.heading_at(heading) == 0

    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=heading)
    assert grid.is_collapsed("March 2026")
    assert grid.isIndexHidden(model.index(0))
    assert grid.visualRect(model.index(0)).isNull()
    # February's heading moves up under March's, and its tile follows it.
    assert grid.heading_at(QPoint(20, HEADER_HEIGHT + 5)) == 1
    assert grid.visualRect(model.index(2)).top() == 2 * HEADER_HEIGHT
    assert grid.visualRect(model.index(2)).x() == SIDE_PAD
    assert grid.visible_rows() == [2]
    assert grid.indexAt(grid.visualRect(model.index(2)).center()).row() == 2

    # Keys skip the collapsed group.
    grid.setCurrentIndex(model.index(2))
    grid.setFocus()
    qtbot.keyClick(grid, Qt.Key.Key_Home)
    assert grid.currentIndex().row() == 2

    # A reload (filters, a sync) keeps it collapsed; a new sort does not.
    ctx.catalog_updated.emit()
    qtbot.wait(100)
    assert grid.is_collapsed("March 2026")
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=heading)
    assert not grid.is_collapsed("March 2026")
    assert grid.visible_rows() == [0, 1, 2]

    grid.collapse_all()
    assert grid.visible_rows() == []
    view.sort.setCurrentIndex(view.sort.findData("size"))
    assert not grid.is_collapsed("March 2026")


def test_sort_direction_button_matches_sort_box_height(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    view.resize(1200, 700)
    view.show()
    qtbot.waitExposed(view)
    assert view.descending.height() == view.sort.height()
    assert view.sort.height() == view.import_btn.height()


def test_folder_tree_indent_clears_the_selection_bar(qtbot, repo):
    from smart_gallery.gui.library.view import TREE_INDENT

    view, _ = _view(qtbot, repo.db_path)
    # Windows 11 draws a selection bar at the row's left edge; under 20 px the
    # expand arrow sits on top of it.
    assert view.tree.indentation() == TREE_INDENT >= 20


def test_filters_hide_behind_a_strip_and_come_back(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    view.show()
    assert view.filters.isVisible() and not view.filters_strip.isVisible()
    view.collapsible.hide_btn.click()
    assert not view.filters.isVisible() and view.filters_strip.isVisible()
    view.filters_strip.click()
    assert view.filters.isVisible() and not view.filters_strip.isVisible()


def test_folder_tree_hides_behind_a_strip_and_comes_back(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    view.show()
    assert view.tree_panel.isVisible() and not view.tree_strip.isVisible()
    view.tree_collapsible.hide_btn.click()
    assert not view.tree_panel.isVisible() and view.tree_strip.isVisible()
    assert view.filters.isVisible()  # the filters stay as they were
    view.tree_strip.click()
    assert view.tree_panel.isVisible() and not view.tree_strip.isVisible()


def test_folder_tree_strip_sits_where_the_tree_was(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    splitter = view.tree_panel.parentWidget()
    order = [splitter.widget(i) for i in range(splitter.count())]
    assert order.index(view.tree_strip) == order.index(view.tree_panel) - 1
    assert order.index(view.tree_panel) < order.index(view.filters)


def test_folder_tree_shown_persists_beside_filters(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    view.collapsible.hide_btn.click()
    view.tree_collapsible.hide_btn.click()
    again, _ = _view(qtbot, repo.db_path)
    assert not again.tree_collapsible.shown
    assert not again.collapsible.shown  # each panel keeps its own key
    again.tree_collapsible.set_shown(True)
    third, _ = _view(qtbot, repo.db_path)
    assert third.tree_collapsible.shown and not third.collapsible.shown


def test_sync_options_sit_right_of_sort(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    layout = view.header_controls.layout()
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert widgets.index(view.sort) < widgets.index(view.mirror_btn)
    assert view.mirror_btn.text() == "Sync options"


def test_library_summary_signal_header_and_footer(qtbot, repo, make_item, tmp_path):
    from smart_gallery.gui.ui.widgets import EmptyState, PanelFooter

    repo.upsert_many([make_item("a/1.jpg", date_taken="2026-01-01")])
    ctx = AppContext()
    ctx.thumbs = FakeThumbs()
    ctx.set_gallery(Gallery("uuid", str(repo.db_path), "test"))
    view = LibraryView(ctx)
    qtbot.addWidget(view)
    with qtbot.waitSignal(
        view.summary_changed, check_params_cb=lambda t: t.startswith("1 file ·")
    ):
        view.reload()
    layout = view.header_controls.layout()
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert view.import_btn is widgets[-1]
    assert not hasattr(view, "stats")
    assert isinstance(view.message, EmptyState)
    assert isinstance(view.subfolders.parentWidget(), PanelFooter)


def test_library_summary_cleared_when_drive_missing(qtbot, tmp_path):
    missing = tmp_path / "gone" / ".smart_gallery" / "gallery.db"
    view, _ = _view(qtbot, missing, label="Holiday")
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.message)
    assert view.summary_text == ""


def test_library_filters_shown_persists(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    view.collapsible.hide_btn.click()
    again, _ = _view(qtbot, repo.db_path)
    assert not again.collapsible.shown


def test_library_summary_cleared_after_failure_and_gallery_change(
    qtbot, repo, make_item
):
    repo.upsert_many([make_item("a/1.jpg", date_taken="2026-01-01")])
    view, ctx = _view(qtbot, repo.db_path)
    emitted = []
    view.summary_changed.connect(emitted.append)
    qtbot.waitUntil(lambda: view.summary_text.startswith("1 file"))
    view.reload()
    qtbot.waitUntil(lambda: emitted and emitted[-1].startswith("1 file"))
    view._on_failed(RuntimeError("boom"))
    assert view.summary_text == "" and emitted[-1] == ""
    view.reload()
    qtbot.waitUntil(lambda: emitted[-1].startswith("1 file"))
    ctx.set_gallery(None)
    assert view.summary_text == "" and emitted[-1] == ""


# ── tile look and model roles ───────────────────────────────────────────────


def _row(i, name, **kwargs):
    from smart_gallery.gui.library.model import LibraryRow

    values = dict(
        media_type="image",
        ext="." + name.rsplit(".", 1)[-1],
        mtime_ns=1,
        date_taken="2026-03-01",
        size_bytes=3_250_000,
        width=3000,
        height=2000,
        orientation=1,
    )
    values.update(kwargs)
    return LibraryRow(i, name, **values)


def _tile_grid(qtbot, rows, holders=None, delegate=None, size=(800, 500)):
    """A shown Library grid of ``rows`` painted by ``delegate``."""
    from PySide6.QtWidgets import QFrame

    from smart_gallery.gui.library.grid import GroupedGrid
    from smart_gallery.gui.library.model import group_rows
    from smart_gallery.gui.library.view import LibraryGridModel, attach_tiles

    grid = GroupedGrid()
    qtbot.addWidget(grid)
    grid.setFrameShape(QFrame.Shape.NoFrame)
    model = LibraryGridModel(grid)
    grid.setModel(model)
    attach_tiles(grid, model, delegate)
    grid.set_groups(group_rows(rows, "date"))
    model.set_rows(rows, holders)
    grid.resize(*size)
    grid.show()
    qtbot.waitExposed(grid)
    return grid, model


def _recording_delegate():
    from smart_gallery.gui.library.tiles import PhotoTileDelegate

    class Recording(PhotoTileDelegate):
        def __init__(self):
            super().__init__()
            self.texts = []
            self.chips = []  # (text, top_right) per chip painted

        def _draw_text(self, painter, rect, flags, text):
            self.texts.append(text)
            super()._draw_text(painter, rect, flags, text)

        def _paint_chip(self, painter, image, text, top_right, play=False):
            self.chips.append((text, top_right))
            super()._paint_chip(painter, image, text, top_right, play)

    return Recording()


def test_tiles_draw_no_file_name(qtbot, monkeypatch):
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QStyledItemDelegate

    delegate = _recording_delegate()
    grid, model = _tile_grid(qtbot, [_row(1, "a.jpg")], delegate=delegate)
    drawn, styled = [], []
    draw_text = QPainter.drawText

    def recording_draw_text(painter, *args):
        drawn.append(args)
        return draw_text(painter, *args)

    monkeypatch.setattr(QPainter, "drawText", recording_draw_text)
    # Qt's own item painting (which draws DisplayRole) must never run.
    monkeypatch.setattr(QStyledItemDelegate, "paint", lambda *args: styled.append(args))
    delegate.texts.clear()
    grid.grab()
    assert delegate.texts == [] and styled == []
    assert drawn  # the heading went through the patch
    assert not any("a.jpg" in str(arg) for args in drawn for arg in args)
    assert model.data(model.index(0), Qt.ItemDataRole.DisplayRole) == "a.jpg"


def test_video_tile_has_duration_role(qtbot):
    from smart_gallery.gui.library.tiles import DURATION_ROLE

    rows = [
        _row(1, "v.mp4", media_type="video", duration_ms=42_000),
        _row(2, "a.jpg"),
    ]
    delegate = _recording_delegate()
    grid, model = _tile_grid(qtbot, rows, delegate=delegate)
    assert model.data(model.index(0), DURATION_ROLE) == "0:42"
    assert model.data(model.index(1), DURATION_ROLE) is None
    delegate.texts.clear()
    delegate.chips.clear()
    grid.grab()
    assert delegate.texts == ["0:42"]  # the chip, once; no names
    assert delegate.chips == [("0:42", True)]  # the photo has none


def test_video_of_unknown_length_keeps_its_play_chip(qtbot, tmp_path):
    from smart_gallery.gui.library.tiles import DURATION_ROLE

    frame = tmp_path / "f.jpg"
    Image.new("RGB", (512, 288), (0, 0, 255)).save(frame)
    rows = [
        _row(1, "v.mp4", media_type="video", duration_ms=None),
        _row(2, "a.jpg"),
    ]
    delegate = _recording_delegate()
    grid, model = _tile_grid(qtbot, rows, delegate=delegate)
    model.set_thumbnail(1, str(frame))
    assert model.data(model.index(0), DURATION_ROLE) == ""
    assert model.data(model.index(1), DURATION_ROLE) is None
    delegate.texts.clear()
    delegate.chips.clear()
    grid.grab()
    assert delegate.chips == [("", True)]  # play glyph only, top-right
    assert delegate.texts == []
    rect = grid.visualRect(model.index(0))
    image = grid.viewport().grab().toImage()
    # Just inside the chip's right edge the frame is darkened by the chip.
    chip = image.pixelColor(rect.right() - 8, rect.top() + 11)
    frame_colour = image.pixelColor(rect.center().x(), rect.bottom() - 10)
    assert frame_colour.blue() > 200
    assert chip.blue() < 160


def test_tiles_answer_their_aspect_as_soon_as_rows_are_set(qtbot):
    from smart_gallery.gui.library.grid import ASPECT_ROLE

    rows = [_row(1, "a.jpg"), _row(2, "p.jpg", orientation=6)]
    _grid, model = _tile_grid(qtbot, rows)
    assert model.data(model.index(0), ASPECT_ROLE) == pytest.approx(1.5)
    assert model.data(model.index(1), ASPECT_ROLE) == pytest.approx(2 / 3)


def test_tooltip_has_name_date_size(qtbot):
    from smart_gallery.format import format_size

    rows = [_row(1, "a/b/a.jpg"), _row(2, "n.jpg", date_taken=None)]
    _grid, model = _tile_grid(qtbot, rows)
    tip = model.data(model.index(0), Qt.ItemDataRole.ToolTipRole)
    assert tip == f"a.jpg\n1 Mar 2026 · {format_size(3_250_000)}"
    undated = model.data(model.index(1), Qt.ItemDataRole.ToolTipRole)
    assert undated.splitlines()[1].startswith("No date · ")


def test_offloaded_tooltip_and_role(qtbot):
    from smart_gallery.gui.library.tiles import OFFLOADED_ROLE

    rows = [_row(1, "a.jpg", offloaded=True), _row(2, "b.jpg")]
    delegate = _recording_delegate()
    grid, model = _tile_grid(qtbot, rows, {1: "Drive D"}, delegate=delegate)
    assert model.data(model.index(0), OFFLOADED_ROLE) == "Drive D"
    assert model.data(model.index(1), OFFLOADED_ROLE) is None
    tip = model.data(model.index(0), Qt.ItemDataRole.ToolTipRole)
    assert tip.startswith("a.jpg\n") and tip.endswith("\nOn Drive D only")
    delegate.texts.clear()
    grid.grab()
    assert delegate.texts == ["On Drive D"]


def test_thumbnail_kept_full_size(qtbot, tmp_path):
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (512, 341), "green").save(thumb)
    _grid, model = _tile_grid(qtbot, [_row(1, "a.jpg")])
    assert model.set_thumbnail(1, str(thumb))
    pixmap = model.data(model.index(0), Qt.ItemDataRole.DecorationRole)
    assert (pixmap.width(), pixmap.height()) == (512, 341)


def test_video_thumbnail_has_no_badge_burned_in(qtbot, tmp_path):
    frame = tmp_path / "f.jpg"
    Image.new("RGB", (512, 288), (0, 0, 255)).save(frame)
    rows = [_row(1, "v.mp4", media_type="video", duration_ms=42_000)]
    _grid, model = _tile_grid(qtbot, rows)
    model.set_thumbnail(1, str(frame))
    image = model.data(model.index(0), Qt.ItemDataRole.DecorationRole).toImage()
    centre = image.pixelColor(image.width() // 2, image.height() // 2)
    assert centre.blue() > 200 and centre.red() < 60


def test_pixmap_cache_holds_a_full_screen_of_small_tiles(qtbot, tmp_path):
    from smart_gallery.gui.library.view import PIXMAP_CACHE_BYTES, LibraryGridModel

    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (512, 341), "green").save(thumb)
    model = LibraryGridModel()
    model.set_rows([_row(i, f"{i}.jpg") for i in range(1, 321)])
    for i in range(1, 251):
        model.set_thumbnail(i, str(thumb))
    assert all(not model.needs_thumbnail(r) for r in model.rows[:250])
    # Past the byte cap, the least recently shown go first.
    for i in range(251, 321):
        model.set_thumbnail(i, str(thumb))
    assert 250 * 512 * 341 * 4 <= PIXMAP_CACHE_BYTES < 320 * 512 * 341 * 4
    assert model.needs_thumbnail(model.rows[0])
    assert not model.needs_thumbnail(model.rows[-1])


def test_thumbnail_fills_its_tile(qtbot, tmp_path):
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (512, 512), (0, 200, 0)).save(thumb)  # square into 3:2
    grid, model = _tile_grid(qtbot, [_row(1, "a.jpg")])
    model.set_thumbnail(1, str(thumb))
    rect = grid.visualRect(model.index(0))
    image = grid.viewport().grab().toImage()
    corners = ((rect.left() + 1, rect.top() + 1), (rect.right() - 1, rect.bottom() - 1))
    for x, y in corners:
        colour = image.pixelColor(x, y)
        assert colour.green() > 150 and colour.red() < 60, (x, y)


def test_selected_tile_is_inset_in_an_accent_ring(qtbot, tmp_path):
    from PySide6.QtGui import QColor

    from smart_gallery.gui import theme

    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (512, 341), (0, 200, 0)).save(thumb)
    grid, model = _tile_grid(qtbot, [_row(1, "a.jpg")])
    model.set_thumbnail(1, str(thumb))
    grid.setCurrentIndex(model.index(0))  # single selection: selects it
    rect = grid.visualRect(model.index(0))
    image = grid.viewport().grab().toImage()
    x = rect.center().x()
    ring = image.pixelColor(x, rect.top())
    accent = QColor(theme.ACCENT)
    assert abs(ring.red() - accent.red()) < 30 and ring.green() < 150
    # Between the ring and the 90% image: the grid's background, as in the
    # side padding.
    background = image.pixelColor(2, rect.center().y())
    assert image.pixelColor(x, rect.top() + 4) == background
    assert image.pixelColor(x, rect.top() + int(rect.height() * 0.1)).green() > 150


def test_gallery_change_drops_the_delegates_scaled_tiles(qtbot, tmp_path):
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (512, 341), "green").save(thumb)
    grid, model = _tile_grid(qtbot, [_row(1, "a.jpg")])
    model.set_thumbnail(1, str(thumb))
    grid.grab()
    delegate = grid.itemDelegate()
    assert delegate._cache
    model.forget_thumbnails()
    assert not delegate._cache and delegate._cache_bytes == 0


def test_delegate_scales_once_per_size_and_scale(qtbot):
    from PySide6.QtCore import QSize

    from smart_gallery.gui.library.tiles import PhotoTileDelegate

    delegate = PhotoTileDelegate()
    source = QPixmap(512, 341)
    source.fill(Qt.GlobalColor.green)
    first = delegate._scaled(source, QSize(150, 100), 1.0)
    assert delegate._scaled(source, QSize(150, 100), 1.0) is first
    assert (first.width(), first.height()) == (150, 100)
    hidpi = delegate._scaled(source, QSize(150, 100), 2.0)
    assert hidpi is not first and (hidpi.width(), hidpi.height()) == (300, 200)
    assert hidpi.devicePixelRatio() == 2.0
    bigger = delegate._scaled(source, QSize(300, 200), 1.0)
    assert bigger is not first


def _saved_state():
    from smart_gallery.gui.paths import app_data_dir
    from smart_gallery.gui.state_store import load_json

    return load_json(app_data_dir() / "library.json")


def test_slider_sets_row_height_and_is_remembered(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    view.collapsible.hide_btn.click()  # writes filters_shown
    slider = view.size_slider.slider
    assert (slider.minimum(), slider.maximum()) == (110, 340)
    slider.setValue(250)
    assert view.grid.row_height() == 250
    qtbot.waitUntil(lambda: _saved_state().get("row_height") == 250)
    state = _saved_state()
    assert state["row_height"] == 250
    assert state["filters_shown"] is False


def test_ctrl_wheel_moves_slider(qtbot, repo):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent

    view, _ = _view(qtbot, repo.db_path)
    view.show()
    start = view.size_slider.slider.value()
    pos = QPointF(10, 10)
    event = QWheelEvent(
        pos,
        pos,
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    view.grid.wheelEvent(event)
    assert view.size_slider.slider.value() == view.grid.row_height() > start
    qtbot.waitUntil(lambda: _saved_state().get("row_height") == view.grid.row_height())


def test_bad_saved_value_falls_back(tmp_path):
    from smart_gallery.gui.library import photo_size
    from smart_gallery.gui.paths import app_data_dir
    from smart_gallery.gui.state_store import save_json

    path = app_data_dir() / "library.json"
    save_json(path, {"row_height": "huge"})
    assert photo_size.load_row_height() == 190
    save_json(path, {"row_height": 9999})
    assert photo_size.load_row_height() == 340
    save_json(path, {"row_height": True})
    assert photo_size.load_row_height() == 190


def test_size_change_requests_thumbnails_again(qtbot, repo, make_item, tmp_path):
    view, ctx = _view(qtbot, repo.db_path)
    called = []
    view._request_visible = lambda: called.append(1)
    view._thumb_timer.timeout.disconnect()
    view._thumb_timer.timeout.connect(view._request_visible)
    view.size_slider.slider.setValue(300)
    qtbot.waitUntil(lambda: bool(called))


def test_drag_writes_once(qtbot, repo, monkeypatch):
    from smart_gallery.gui.library import photo_size

    writes = []
    monkeypatch.setattr(photo_size, "save_row_height", writes.append)
    view, _ = _view(qtbot, repo.db_path)
    for px in (150, 200, 250, 300):
        view.size_slider.slider.setValue(px)
    qtbot.waitUntil(lambda: bool(writes))
    assert writes == [300]


def _portrait_model(tmp_path, n):
    from smart_gallery.gui.library.view import LibraryGridModel

    thumb = tmp_path / "p.jpg"
    Image.new("RGB", (341, 512), "green").save(thumb)
    model = LibraryGridModel()
    model.set_rows([_row(i, f"{i}.jpg", width=2000, height=3000) for i in range(n)])
    return model, str(thumb)


def test_pixmap_cache_never_drops_a_tile_on_screen(qtbot, tmp_path):
    from smart_gallery.gui.library.view import PIXMAP_CACHE_BYTES

    model, thumb = _portrait_model(tmp_path, 400)
    model.set_visible_rows(range(320))  # a 4K screen of the smallest tiles
    assert 320 * 341 * 512 * 4 > PIXMAP_CACHE_BYTES
    for i in range(320, 400):  # off screen, shown earlier
        model.set_thumbnail(i, thumb)
    for i in range(320):
        model.set_thumbnail(i, thumb)
    assert not any(model.needs_thumbnail(r) for r in model.rows[:320])
    # Over the budget, everything off screen went first.
    assert all(model.needs_thumbnail(r) for r in model.rows[320:])


def test_pixmap_cache_keeps_its_budget_for_tiles_off_screen(qtbot, tmp_path):
    from smart_gallery.gui.library.view import PIXMAP_CACHE_BYTES

    model, thumb = _portrait_model(tmp_path, 400)
    model.set_visible_rows(range(100))
    for i in range(100, 400):  # oldest first: 100, 101, ...
        model.set_thumbnail(i, thumb)
    for i in range(100):
        model.set_thumbnail(i, thumb)
    tile = 341 * 512 * 4
    assert model._pixmap_bytes <= PIXMAP_CACHE_BYTES
    assert not any(model.needs_thumbnail(r) for r in model.rows[:100])
    assert model.needs_thumbnail(model.rows[100])  # the oldest off screen
    assert not model.needs_thumbnail(model.rows[399])
    # Scrolled away: the old screen may go, but the budget holds again.
    model.set_visible_rows([])
    assert model._pixmap_bytes <= PIXMAP_CACHE_BYTES
    model.set_visible_rows(range(320))
    for i in range(320):
        model.set_thumbnail(i, thumb)
    assert not any(model.needs_thumbnail(r) for r in model.rows[:320])
    assert model._pixmap_bytes <= max(PIXMAP_CACHE_BYTES, 320 * tile)


def test_library_tells_the_model_which_rows_are_on_screen(qtbot, repo, make_item):
    repo.upsert_many([make_item(f"a/{i:04}.jpg") for i in range(500)])
    view, ctx = _view(qtbot, repo.db_path)
    view.resize(900, 600)
    view.show()
    qtbot.waitUntil(lambda: bool(ctx.thumbs.requests))
    visible = view.grid.visible_rows()
    keys = {view.grid_model._key(view.grid_model.rows[i]) for i in visible}
    assert keys and view.grid_model._on_screen == keys


def test_size_save_is_flushed_on_quit(qtbot, repo, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from smart_gallery.gui.library import photo_size

    writes = []
    monkeypatch.setattr(photo_size, "save_row_height", writes.append)
    view, _ = _view(qtbot, repo.db_path)
    QApplication.instance().aboutToQuit.emit()
    assert writes == []  # nothing changed: nothing to write
    view.size_slider.slider.setValue(250)
    assert writes == []  # still waiting out the drag
    QApplication.instance().aboutToQuit.emit()
    assert writes == [250]
    assert not view.size_slider._save_timer.isActive()
