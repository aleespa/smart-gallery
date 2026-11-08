"""Final-review fixes for the UI consistency pass."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QPushButton

from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.areas import AREAS
from smart_gallery.gui.filters_panel import FilterPanel
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.history.view import format_start
from smart_gallery.gui.ui.widgets import EmptyState


def _in_segment(button):
    parent = button.parentWidget()
    while parent is not None:
        if parent.objectName() == "segment":
            return True
        parent = parent.parentWidget()
    return False


def test_every_checkable_button_is_in_a_segment(qtbot, repo):
    win = MainWindow()
    qtbot.addWidget(win)
    win.ctx.set_gallery(Gallery("uuid", str(repo.db_path), "Archive"))
    stray = []
    for area in AREAS:
        win.show_area(area.id)
        for b in win.stack.currentWidget().findChildren(QPushButton):
            if b.isCheckable() and not _in_segment(b):
                stray.append((area.id, b.text()))
    assert stray == []


def test_import_says_why_run_is_off(qtbot, tmp_path):
    from tests.gui.test_imports_view import _view

    view, _ctx, _g = _view(qtbot, tmp_path)
    view.set_source(str(tmp_path / "card"))  # no gallery ticked
    assert view.footer.note.isVisible()
    assert view.footer.note.text()


def test_filter_panel_never_shown_as_window(qtbot, repo):
    shown = []

    class Spy(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Show and obj.isWindow():
                shown.append(obj)
            return False

    spy = Spy()
    original = FilterPanel.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        self.installEventFilter(spy)

    FilterPanel.__init__ = init
    try:
        from tests.gui.test_dashboard_view import _view as dash
        from tests.gui.test_library_view import _view as lib

        lib(qtbot, repo.db_path)
        dash(qtbot, repo.db_path)
    finally:
        FilterPanel.__init__ = original
    assert shown == []


def test_rename_is_a_gallery_command():
    from smart_gallery.gui.actions.model import COMMAND_GROUPS

    assert COMMAND_GROUPS["Catalog"]["rename"] == "Rename gallery"


def test_empty_state_text_fits_one_line_and_selectable(qtbot):
    e = EmptyState()
    qtbot.addWidget(e)
    e.resize(900, 500)
    e.show()
    e.show_text("Select a job to see its log")
    qtbot.wait(20)
    need = e.text.fontMetrics().horizontalAdvance("Select a job to see its log")
    assert e.text.width() >= need
    assert e.text.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse


def test_bottom_row_is_a_panel_footer(qtbot, repo):
    from smart_gallery.gui.ui.widgets import PanelFooter
    from tests.gui.test_library_view import _view

    view, _ = _view(qtbot, repo.db_path)
    assert isinstance(view.filters.bottom_footer, PanelFooter)
    assert view.filters.bottom_row.count() >= 1


def test_clicking_panel_title_toggles_collapse(qtbot, repo):
    from tests.gui.test_dashboard_view import _view

    view, _ = _view(qtbot, repo.db_path)
    panel = next(iter(view.panels.values()))
    view.show()
    before = panel.collapsed
    qtbot.mouseClick(panel.header.title, Qt.MouseButton.LeftButton)
    assert panel.collapsed != before
    qtbot.mouseClick(panel.header.title, Qt.MouseButton.LeftButton)
    assert panel.collapsed == before


def test_format_start_none():
    assert format_start(None) == ""
