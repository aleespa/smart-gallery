from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.areas import AREAS


def test_every_area_is_a_tab(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    assert list(win.tabs) == [a.id for a in AREAS]
    assert [t.text() for t in win.tabs.values()] == [
        "Library",
        "Import",
        "Stats",
        "People",
        "Actions",
        "History",
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
    win.tabs["import"].click()
    assert win.current_area == "import"
    assert win.tabs["import"].isChecked()
    assert not win.tabs["library"].isChecked()
    win.tabs["import"].click()  # clicking the open tab keeps it open
    assert win.tabs["import"].isChecked()


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
    win.show_area("import")
    assert win.header_controls.currentWidget() is not library.header_controls


def test_navigate_signal_switches_area(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.ctx.navigate.emit("people")
    assert win.current_area == "people"
    assert win.tabs["people"].isChecked()


def test_people_view_lists_persons(qtbot, repo):
    from types import SimpleNamespace

    from smart_gallery.gui.context import AppContext
    from smart_gallery.gui.people.view import PeopleView
    from smart_gallery.models import Person

    repo.create_person(Person(name="Ana", face_count=3))
    ctx = AppContext()
    ctx.set_gallery(SimpleNamespace(db_path=str(repo.db_path)))
    view = PeopleView(ctx)
    qtbot.addWidget(view)
    assert view.persons.item(0).text() == "Ana (3 faces)"


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
