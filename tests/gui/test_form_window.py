from PySide6.QtCore import Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from smart_gallery.gui.form_window import FormWindow
from smart_gallery.gui.ui.widgets import ActionBar, SectionHeader


class Form(QWidget):
    ran = Signal()

    def __init__(self):
        super().__init__()
        self.heading = SectionHeader("T", "sub")
        self.footer = ActionBar()
        self.scroll = QScrollArea()
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.scroll)
        layout.addWidget(self.footer)


def test_heading_and_footer_stay_outside_the_scroll_area(qtbot):
    form = Form()
    window = FormWindow("W", form)
    qtbot.addWidget(window)
    window.show()
    assert not form.scroll.isAncestorOf(form.heading)
    assert not form.scroll.isAncestorOf(form.footer)
    assert form.heading.y() < form.scroll.y() < form.footer.y()
