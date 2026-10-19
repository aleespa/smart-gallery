from PIL import Image
from PySide6.QtCore import QObject, Qt, Signal
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
    assert view.stats.text().startswith("3 items")

    view.select_folder("a")
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/1.jpg", "a/sub/2.jpg"])

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
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/1.jpg", "a/2.jpg"])
    assert view.stack.currentWidget() is view.grid

    repo.upsert_many([make_item("a/3.jpg")])  # commits the open transaction
    ctx.catalog_updated.emit()
    qtbot.waitUntil(lambda: _relpaths(view) == ["a/1.jpg", "a/2.jpg", "a/3.jpg"])
    assert view.stack.currentWidget() is view.grid


def test_filters_change_reloads_rows(qtbot, repo, make_item):
    repo.upsert_many(
        [make_item("a.jpg", camera="Canon"), make_item("b.jpg", camera="Sony")]
    )
    view, ctx = _view(qtbot, repo.db_path)
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 2)
    qtbot.waitUntil(lambda: view.filters.cameras.count() == 2)

    view.filters.cameras.item(1).setCheckState(Qt.CheckState.Checked)
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


def test_video_tile_placeholder_and_no_request(qtbot, repo, make_item):
    repo.upsert_many(
        [make_item("v.mp4", media_type="video", ext=".mp4", duration_ms=83_000)]
    )
    view, ctx = _view(qtbot, repo.db_path)
    view.resize(900, 600)
    view.show()
    qtbot.waitUntil(lambda: len(view.grid_model.rows) == 1)
    qtbot.wait(300)
    assert ctx.thumbs.requests == []
    index = view.grid_model.index(0)
    assert not view.grid_model.data(index, Qt.ItemDataRole.DecorationRole).isNull()
    assert view.grid_model.data(index, Qt.ItemDataRole.DisplayRole) == "v.mp4"


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
    qtbot.waitUntil(lambda: _relpaths(view) == ["a.jpg", "b.jpg", "c.jpg"])
    assert not view.descending.isChecked()

    view.descending.setChecked(True)
    qtbot.waitUntil(lambda: _relpaths(view) == ["c.jpg", "b.jpg", "a.jpg"])

    # Picking a sort resets the direction to that sort's natural one.
    view.sort.setCurrentIndex(view.sort.findData("size"))
    assert view.descending.isChecked()
    qtbot.waitUntil(lambda: _relpaths(view) == ["b.jpg", "c.jpg", "a.jpg"])
    view.descending.setChecked(False)
    qtbot.waitUntil(lambda: _relpaths(view) == ["a.jpg", "c.jpg", "b.jpg"])

    view.sort.setCurrentIndex(view.sort.findData("name"))
    assert not view.descending.isChecked()


def test_sort_direction_button_matches_sort_box_height(qtbot, repo):
    view, _ = _view(qtbot, repo.db_path)
    view.resize(1200, 700)
    view.show()
    qtbot.waitExposed(view)
    assert view.descending.height() == view.sort.height()


def test_folder_tree_uses_a_compact_indent(qtbot, repo):
    from smart_gallery.gui.library.view import TREE_INDENT

    view, _ = _view(qtbot, repo.db_path)
    assert view.tree.indentation() == TREE_INDENT < 20
