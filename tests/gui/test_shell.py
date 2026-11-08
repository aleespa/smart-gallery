import dataclasses

from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.areas import AREAS


def test_every_area_is_a_tab(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    assert list(win.tabs) == [a.id for a in AREAS]
    assert [t.text() for t in win.tabs.values()] == [
        "Library",
        "Stats",
        "Storage",
        "People",
        "History",
        "Advanced",
    ]


def test_every_area_opens_with_a_header(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.current_area == "library"
    assert win.tabs["library"].isChecked()
    for area in AREAS:
        win.show_area(area.id)
        assert win.stack.currentWidget() is not None
        assert win.title.text() == area.label
        assert win.windowTitle() == "Smart Gallery"


def test_tab_click_switches_area(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.tabs["people"].click()
    assert win.current_area == "people"
    assert win.tabs["people"].isChecked()
    assert not win.tabs["library"].isChecked()
    win.tabs["people"].click()  # clicking the open tab keeps it open
    assert win.tabs["people"].isChecked()


def test_header_names_the_open_gallery(qtbot, repo):
    from smart_gallery.gui.galleries.store import Gallery

    win = MainWindow()
    qtbot.addWidget(win)
    win.ctx.set_gallery(Gallery("uuid", str(repo.db_path), "Archive"))
    assert win.eyebrow.text() == "Archive"
    win.ctx.set_gallery(None)
    assert win.eyebrow.text() == "No gallery open"


def test_page_header_shows_the_library_controls(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    library = win.stack.currentWidget()
    assert win.header_controls.currentWidget() is library.header_controls
    win.show_area("people")
    assert win.header_controls.currentWidget() is not library.header_controls


def test_navigate_signal_switches_area(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.ctx.navigate.emit("people")
    assert win.current_area == "people"
    assert win.tabs["people"].isChecked()


def test_window_provides_thumbnail_loader(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.ctx.thumbs is not None


def test_window_holds_web_content_before_it_is_shown(qtbot):
    """A first web view (the Dashboard map) used to recreate the shown window,
    which flashed. The headless platform never recreates windows, so check
    the cause instead: a visible web view is there before the first show."""
    from PySide6.QtWebEngineWidgets import QWebEngineView

    win = MainWindow()
    qtbot.addWidget(win)
    views = win.findChildren(QWebEngineView)
    assert any(not v.isHidden() for v in views)


def test_every_page_fills_its_container_flush(qtbot):
    from smart_gallery.gui.app import HEADER_MARGIN

    win = MainWindow()
    qtbot.addWidget(win)
    assert win.stack_container.layout().contentsMargins().left() == HEADER_MARGIN
    for area in AREAS:
        win.show_area(area.id)
        m = win._pages[area.id].layout().contentsMargins()
        assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0), area.id


def test_summary_shows_only_for_the_open_page_and_clears(qtbot, repo, monkeypatch):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget

    from smart_gallery.gui import areas
    from smart_gallery.gui.galleries.store import Gallery

    class Page(QWidget):
        summary_changed = Signal(str)

    page = None

    def factory(ctx):
        nonlocal page
        page = Page()
        return page

    old = areas.AREAS[0]
    patched = [dataclasses.replace(old, factory=factory), *areas.AREAS[1:]]
    monkeypatch.setattr("smart_gallery.gui.app.AREAS", patched)
    win = MainWindow()
    qtbot.addWidget(win)
    page.summary_changed.emit("3 files · 1 KB")
    assert win.summary.text() == "3 files · 1 KB"
    win.show_area("people")
    assert win.summary.text() == ""
    win.show_area(old.id)
    assert win.summary.text() == "3 files · 1 KB"
    win.ctx.set_gallery(Gallery("uuid", str(repo.db_path), "Archive"))
    assert win.summary.text() == ""
