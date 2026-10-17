from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.areas import AREAS


def test_window_lists_all_areas(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    rail = win.rail
    assert [rail.item(i).text() for i in range(rail.count())] == [
        a.label for a in AREAS
    ]
    for i in range(rail.count()):
        rail.setCurrentRow(i)
        assert win.stack.currentWidget() is not None


def test_navigate_signal_switches_area(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.ctx.navigate.emit("people")
    assert win.rail.currentItem().text() == "People"


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
