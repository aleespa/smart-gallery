import pytest
from PySide6.QtWidgets import QPushButton

from smart_gallery.gui import theme
from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.areas import AREAS
from smart_gallery.gui.segment import segment

pytestmark = pytest.mark.gui


def visible_primaries(widget):
    return [
        b
        for b in widget.findChildren(QPushButton)
        if b.objectName() == "primaryButton" and b.isVisibleTo(widget)
    ]


@pytest.mark.parametrize("area", [a.id for a in AREAS])
def test_at_most_one_primary_per_page(qtbot, area):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show_area(area)
    page = win._pages[area]
    header = win.header_controls.currentWidget()
    total = visible_primaries(page)
    if header is not None:
        total += [b for b in visible_primaries(header) if b not in total]
    assert len(total) <= 1


def test_danger_button_has_no_fill_until_hover():
    b = theme.danger(QPushButton("x"))
    assert b.objectName() == "dangerButton"
    assert "background: transparent" in b.styleSheet()


def test_checked_segment_is_not_a_fill():
    assert "palette(highlight)" not in theme.SEGMENT_STYLE


def test_segment_applies_the_style():
    box, _, _ = segment("a", "b")
    assert box.objectName() == "segment"
    assert box.styleSheet() == theme.SEGMENT_STYLE
