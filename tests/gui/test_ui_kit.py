import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from smart_gallery.colors import NEUTRAL
from smart_gallery.gui import theme
from smart_gallery.gui.segment import segment
from smart_gallery.gui.ui import metrics
from smart_gallery.gui.ui.widgets import (
    ActionBar,
    Disclosure,
    EmptyState,
    PanelFooter,
    SectionHeader,
    pill_style,
)

pytestmark = pytest.mark.gui


def test_empty_state_hides_action_without_label(qtbot):
    e = EmptyState()
    qtbot.addWidget(e)
    e.show_text("Nothing")
    assert e.action.isHidden()
    e.show_text("Nothing", action_label="Go", action=lambda: None)
    assert not e.action.isHidden()


def test_action_bar_keeps_right_order(qtbot):
    bar = ActionBar()
    qtbot.addWidget(bar)
    a, b = QPushButton("a"), QPushButton("b")
    bar.add_right(a)
    bar.add_right(b)
    bar.show()
    assert a.x() < b.x()


def test_disclosure_starts_closed(qtbot):
    body = QLabel("x")
    d = Disclosure("Command", body)
    qtbot.addWidget(d)
    assert not d.opened and body.isHidden()
    with qtbot.waitSignal(d.toggled):
        d.button.setChecked(True)
    assert d.opened and not body.isHidden() or body.isVisibleTo(d)


def test_section_header_and_footer(qtbot):
    h = SectionHeader("Title")
    qtbot.addWidget(h)
    assert h.subtitle.isHidden()
    h.set_subtitle("sub")
    assert not h.subtitle.isHidden()
    f = PanelFooter()
    qtbot.addWidget(f)
    assert (
        f.height() == metrics.FOOTER_HEIGHT
        or f.maximumHeight() == metrics.FOOTER_HEIGHT
    )


def test_unknown_status_pill_is_neutral():
    assert pill_style("weird") == ("weird", NEUTRAL)
    assert pill_style("ok")[0] == "done"


def test_group_box_is_a_card():
    assert "border-radius" in theme.CARD_STYLE
    assert "QGroupBox::title" in theme.CARD_STYLE


def _hint(button):
    button.ensurePolished()
    button.style().polish(button)
    return button.sizeHint()


@pytest.mark.parametrize("style", [theme.danger, theme.primary])
def test_button_size_stable_when_disabled(qtbot, style):
    b = style(QPushButton("Delete"))
    qtbot.addWidget(b)
    b.show()
    enabled = _hint(b)
    b.setEnabled(False)
    assert _hint(b) == enabled


def test_segment_size_stable_when_checked(qtbot):
    box, _group, (a, b) = segment("One", "Two")
    qtbot.addWidget(box)
    box.show()
    assert a.isChecked() and not b.isChecked()
    assert _hint(a) == _hint(b)


def test_action_bar_set_note_hides_when_empty(qtbot):
    bar = ActionBar()
    qtbot.addWidget(bar)
    bar.show()
    assert bar.note.isHidden()
    bar.set_note("Careful")
    assert not bar.note.isHidden() and bar.note.text() == "Careful"
    bar.set_note("")
    assert bar.note.isHidden()
