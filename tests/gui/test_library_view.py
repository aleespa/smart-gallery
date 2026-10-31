from PIL import Image
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QPixmap

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.library.view import LibraryView
from smart_gallery.gui.library.viewer import FIT_SCREEN_MAX, MediaViewer


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
    assert view.stats.text().startswith("3 items")

    view.select_folder("a")
    # Newest first by default.
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/sub/2.jpg", "a/1.jpg"])

    view.subfolders.setChecked(False)
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/1.jpg"])
    assert view.stats.text().startswith("1 item")
    assert view.stack.currentWidget() is view.grid


def test_library_disconnected_drive_message(qtbot, tmp_path):
    missing = tmp_path / "gone" / ".smart_gallery" / "gallery.db"
    view, ctx = _view(qtbot, missing, label="Holiday")
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.message)
    assert view.message.text() == ("Drive not connected. Plug in Holiday to browse it.")
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
    assert pixmap.width() <= view.grid.iconSize().width()


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


def test_video_tile_requests_a_thumbnail_and_badges_it(
    qtbot, repo, make_item, tmp_path
):
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

    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (256, 144), (0, 0, 255)).save(frame)
    ctx.thumbs.ready.emit(str(view._root), row.id, str(frame))
    tile = view.grid_model.data(index, Qt.ItemDataRole.DecorationRole).toImage()
    assert tile.cacheKey() != placeholder.toImage().cacheKey()
    # The frame shows at the edge; the centre carries the light play badge.
    edge = tile.pixelColor(8, tile.height() // 2)
    centre = tile.pixelColor(tile.width() // 2 + 4, tile.height() // 2)
    assert edge.blue() > 200 and edge.red() < 60
    assert centre.red() > 150 and centre.green() > 150


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
    assert view.descending.isChecked()

    view.descending.setChecked(False)
    qtbot.waitUntil(lambda: _relpaths(view) == ["a.jpg", "b.jpg", "c.jpg"])

    # Picking a sort resets the direction to that sort's natural one.
    view.sort.setCurrentIndex(view.sort.findData("size"))
    assert view.descending.isChecked()
    qtbot.waitUntil(lambda: _relpaths(view) == ["b.jpg", "c.jpg", "a.jpg"])
    view.descending.setChecked(False)
    qtbot.waitUntil(lambda: _relpaths(view) == ["a.jpg", "c.jpg", "b.jpg"])

    view.sort.setCurrentIndex(view.sort.findData("name"))
    assert not view.descending.isChecked()


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
    from smart_gallery.gui.library.grid import HEADER_HEIGHT

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
    assert first.top() >= HEADER_HEIGHT
    assert second.top() >= first.bottom() + HEADER_HEIGHT  # own heading between
    assert not grid.indexAt(first.topLeft() - QPoint(0, HEADER_HEIGHT)).isValid()
    assert grid.indexAt(first.center()).row() == 0
    assert grid.indexAt(second.center()).row() == 1
    assert list(grid.visible_rows()) == [0, 1]


def test_clicking_a_heading_collapses_its_group(qtbot, repo, make_item):
    from smart_gallery.gui.library.grid import HEADER_HEIGHT

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


def test_folder_tree_indent_clears_the_selection_bar(qtbot, repo):
    from smart_gallery.gui.library.view import TREE_INDENT

    view, _ = _view(qtbot, repo.db_path)
    # Windows 11 draws a selection bar at the row's left edge; under 20 px the
    # expand arrow sits on top of it.
    assert view.tree.indentation() == TREE_INDENT >= 20


def test_fitted_size_follows_the_image_shape():
    from PySide6.QtCore import QSize

    from smart_gallery.gui.library.viewer import fitted_size

    chrome, screen = QSize(360, 60), QSize(1920, 1040)
    # 3:2 landscape at a 600px tall photo area: 900 wide, plus the chrome.
    assert fitted_size(QSize(3000, 2000), 600, chrome, screen) == QSize(1260, 660)
    # Portrait 2:3 keeps the height and narrows.
    assert fitted_size(QSize(2000, 3000), 600, chrome, screen) == QSize(760, 660)
    # A panorama is capped by the screen width and gets shorter instead.
    pano = fitted_size(QSize(8000, 1000), 600, chrome, screen)
    assert pano.width() <= screen.width() and pano.height() < 660
    # An area taller than the screen allows is capped by the screen height.
    tall = fitted_size(QSize(1000, 1000), 2000, chrome, screen)
    assert tall.height() <= screen.height()
    assert tall.width() - chrome.width() == tall.height() - chrome.height()


def _photo(drive, rel, size):
    path = drive / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (90, 120, 160)).save(path, "JPEG")


def _area_ratio(viewer):
    return viewer.image.width() / viewer.image.height()


def test_viewer_window_matches_each_photo_shape(qtbot, repo, make_item):
    drive = repo.drive_root
    _photo(drive, "a/portrait.jpg", (600, 800))
    _photo(drive, "a/wide.jpg", (900, 600))
    repo.upsert_many([make_item("a/portrait.jpg"), make_item("a/wide.jpg")])
    view, _ = _view(qtbot, repo.db_path)
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 2)
    rows = view.grid_model.rows
    start = next(i for i, r in enumerate(rows) if r.relpath == "a/portrait.jpg")

    view.grid.doubleClicked.emit(view.grid_model.index(start))
    viewer = view.viewer
    qtbot.waitUntil(viewer.has_preview)
    qtbot.waitUntil(lambda: abs(_area_ratio(viewer) - 600 / 800) < 0.05)
    height = viewer.image.height()

    wide = next(i for i, r in enumerate(rows) if r.relpath == "a/wide.jpg")
    viewer.show_index(wide)
    qtbot.waitUntil(lambda: abs(_area_ratio(viewer) - 900 / 600) < 0.05)
    # Same height and wider, unless that would not fit the screen: then the
    # window is as wide as allowed and the photo area shorter.
    screen = viewer.screen().availableGeometry().width()
    capped = viewer.width() >= int(screen * FIT_SCREEN_MAX) - 2
    assert abs(viewer.image.height() - height) <= 2 or capped
    assert viewer.image.width() > viewer.image.height()


def test_filters_hide_behind_a_strip_and_come_back(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    view.show()
    assert view.filters.isVisible() and not view.filters_strip.isVisible()
    view.hide_filters_btn.click()
    assert not view.filters.isVisible() and view.filters_strip.isVisible()
    view.filters_strip.click()
    assert view.filters.isVisible() and not view.filters_strip.isVisible()


def test_sync_options_sit_right_of_sort(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    layout = view.header_controls.layout()
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert widgets.index(view.sort) < widgets.index(view.mirror_btn)
    assert view.mirror_btn.text() == "Sync options"
