from datetime import date

from PySide6.QtCore import QDate

from smart_gallery.gui.context import AppContext
from smart_gallery.gui.filter_widgets import ChipPicker, DateField
from smart_gallery.gui.filters_panel import FilterPanel
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.organize.filters import FilterOptions

CAMERAS = [("Canon EOS R8", 40), ("Nexus 5", 12), ("Sony A7", 3)]


def _picker(qtbot) -> ChipPicker:
    picker = ChipPicker("cameras")
    qtbot.addWidget(picker)
    picker.set_options(CAMERAS)
    return picker


def test_picker_offers_options_most_used_first_with_counts(qtbot):
    picker = _picker(qtbot)
    assert picker.available() == ["Canon EOS R8", "Nexus 5", "Sony A7"]
    assert picker.count_of("Nexus 5") == 12
    assert picker.search.placeholderText() == "Search 3 cameras…"
    assert picker.status.text() == "Any"
    assert not picker.clear_btn.isVisibleTo(picker)


def test_picking_turns_an_option_into_a_chip(qtbot):
    picker = _picker(qtbot)
    with qtbot.waitSignal(picker.changed):
        picker.pick("Nexus 5")
    assert picker.selected() == ("Nexus 5",)
    assert picker.chips() == ["Nexus 5"]
    assert picker.available() == ["Canon EOS R8", "Sony A7"]  # not offered twice
    assert picker.status.text() == "1 selected"
    assert picker.clear_btn.isVisibleTo(picker)


def test_typing_narrows_the_options(qtbot):
    picker = _picker(qtbot)
    picker.show()
    qtbot.keyClicks(picker.search, "son")
    completer = picker.completer
    assert completer.completionCount() == 1
    assert completer.currentCompletion() == "Sony A7"


def test_choosing_from_the_search_picks_and_clears_it(qtbot):
    picker = _picker(qtbot)
    picker.show()
    qtbot.keyClicks(picker.search, "nex")
    picker.completer.activated[str].emit("Nexus 5")
    qtbot.waitUntil(lambda: picker.search.text() == "")
    assert picker.selected() == ("Nexus 5",)


def test_chip_cross_and_clear_remove_selections(qtbot):
    picker = _picker(qtbot)
    picker.pick("Canon EOS R8")
    picker.pick("Sony A7")
    picker.chip_button("Canon EOS R8").click()
    assert picker.selected() == ("Sony A7",)
    with qtbot.waitSignal(picker.changed):
        picker.clear_btn.click()
    assert picker.selected() == ()
    assert picker.status.text() == "Any"


def test_set_selected_keeps_values_the_catalog_no_longer_has(qtbot):
    picker = _picker(qtbot)
    with qtbot.assertNotEmitted(picker.changed):
        picker.set_selected(["Gone camera", "Sony A7"])
    assert picker.chips() == ["Gone camera", "Sony A7"]
    picker.set_options(CAMERAS)  # a reload keeps them
    assert picker.selected() == ("Gone camera", "Sony A7")


def _panel(qtbot, repo, make_item):
    repo.upsert_many(
        [
            make_item("a.jpg", camera="Canon EOS R8", date_taken="2024-06-01"),
            make_item("b.jpg", camera="Canon EOS R8", date_taken="2024-07-09"),
            make_item("c.jpg", camera="Sony A7", date_taken="2025-02-03"),
        ]
    )
    ctx = AppContext()
    ctx.set_gallery(Gallery("uuid", str(repo.db_path), "test"))
    panel = FilterPanel(ctx)
    qtbot.addWidget(panel)
    qtbot.waitUntil(lambda: panel.cameras.available() != [])
    return panel, ctx


def test_panel_shows_catalog_choices_with_counts(qtbot, repo, make_item):
    panel, _ = _panel(qtbot, repo, make_item)
    assert panel.cameras.available() == ["Canon EOS R8", "Sony A7"]
    assert panel.cameras.count_of("Canon EOS R8") == 2


def test_panel_applies_picked_values(qtbot, repo, make_item):
    panel, ctx = _panel(qtbot, repo, make_item)
    panel.cameras.pick("Sony A7")
    panel.apply()
    assert ctx.filters.cameras == ["Sony A7"]


def test_panel_follows_filters_set_elsewhere(qtbot, repo, make_item):
    panel, ctx = _panel(qtbot, repo, make_item)
    ctx.set_filters(FilterOptions(cameras=["Canon EOS R8"]))
    assert panel.cameras.chips() == ["Canon EOS R8"]


def test_calendar_pick_fills_the_date_and_applies(qtbot, repo, make_item):
    panel, ctx = _panel(qtbot, repo, make_item)
    field = panel.date_fields["date_from"]
    assert isinstance(field, DateField)
    popup = field.open_calendar()
    popup.calendar.clicked.emit(QDate(2024, 6, 15))
    assert panel.texts["date_from"].text() == "2024-06-15"
    assert not popup.isVisible()
    qtbot.waitUntil(lambda: ctx.filters.date_range is not None)
    assert ctx.filters.date_range[0] == date(2024, 6, 15)


def test_calendar_opens_on_the_typed_date_and_can_jump_and_clear(
    qtbot, repo, make_item
):
    panel, _ = _panel(qtbot, repo, make_item)
    panel.texts["date_to"].setText("2025-01-20")
    popup = panel.date_fields["date_to"].open_calendar()
    assert popup.calendar.selectedDate() == QDate(2025, 1, 20)
    popup.jump_btn.click()  # the To field jumps to the last photo's month
    assert (popup.calendar.yearShown(), popup.calendar.monthShown()) == (2025, 2)
    popup.clear_btn.click()
    assert panel.texts["date_to"].text() == ""
    from_popup = panel.date_fields["date_from"].open_calendar()
    assert from_popup.jump_btn.text() == "Jump to first photo"


def test_ranges_put_both_bounds_on_one_line(qtbot, repo, make_item):
    panel, _ = _panel(qtbot, repo, make_item)
    panel.resize(300, 900)
    panel.show()
    qtbot.waitExposed(panel)
    for low, high in (
        ("date_from", "date_to"),
        ("min_aperture", "max_aperture"),
        ("min_iso", "max_iso"),
        ("min_shutter", "max_shutter"),
    ):
        a, b = panel.texts[low], panel.texts[high]
        top = lambda w: w.mapTo(panel, w.rect().topLeft()).y()  # noqa: E731
        assert abs(top(a) - top(b)) <= 2, (low, high)
        assert (
            a.mapTo(panel, a.rect().topLeft()).x()
            < b.mapTo(panel, b.rect().topLeft()).x()
        )


def test_invalid_range_text_still_reports_an_error(qtbot, repo, make_item):
    panel, _ = _panel(qtbot, repo, make_item)
    panel.texts["min_iso"].setText("abc")
    panel.apply()
    assert panel.errors.isVisibleTo(panel)
    assert "ISO from" in panel.errors.text()
    assert panel.texts["min_iso"].toolTip()


def test_type_checkboxes_sit_side_by_side(qtbot, repo, make_item):
    panel, _ = _panel(qtbot, repo, make_item)
    panel.resize(300, 900)
    panel.show()
    qtbot.waitExposed(panel)
    boxes = [panel.filetypes[v] for v in ("image", "video", "other")]
    tops = {b.mapTo(panel, b.rect().topLeft()).y() for b in boxes}
    lefts = [b.mapTo(panel, b.rect().topLeft()).x() for b in boxes]
    assert max(tops) - min(tops) <= 2
    assert lefts == sorted(lefts) and len(set(lefts)) == 3


def _shown(qtbot, repo, make_item):
    from smart_gallery.gui.filters_panel import PANEL_WIDTH

    panel, ctx = _panel(qtbot, repo, make_item)
    panel.resize(PANEL_WIDTH, 1000)
    panel.show()
    qtbot.waitExposed(panel)
    return panel, ctx


def test_type_boxes_fit_the_default_width(qtbot, repo, make_item):
    panel, _ = _shown(qtbot, repo, make_item)
    group = panel.filetypes["other"].parentWidget()
    last = panel.filetypes["other"]
    needed = last.sizeHint().width()
    assert last.width() >= needed  # not squeezed
    assert last.geometry().right() < group.contentsRect().right()


def test_there_is_no_apply_button(qtbot, repo, make_item):
    from PySide6.QtWidgets import QPushButton

    panel, _ = _panel(qtbot, repo, make_item)
    texts = [b.text() for b in panel.findChildren(QPushButton)]
    assert "Apply" not in texts


def test_clear_is_pinned_above_the_scrolling_filters(qtbot, repo, make_item):
    panel, ctx = _shown(qtbot, repo, make_item)
    button = panel.clear_btn
    assert not panel.scroll.isAncestorOf(button)  # never scrolls out of view
    above = panel.scroll.mapTo(panel, panel.scroll.rect().topLeft()).y()
    assert button.mapTo(panel, button.rect().bottomLeft()).y() <= above

    panel.cameras.pick("Sony A7")
    panel.apply()
    assert ctx.filters.cameras == ["Sony A7"]
    button.click()
    assert panel.cameras.selected() == ()
    assert not ctx.filters.cameras


def test_clear_button_uses_the_accent(qtbot, repo, make_item):
    from PySide6.QtGui import QColor

    from smart_gallery.gui import theme

    panel, _ = _shown(qtbot, repo, make_item)
    image = panel.clear_btn.grab().toImage()
    edge = image.pixelColor(6, image.height() // 2)
    accent = QColor(theme.ACCENT)
    assert abs(edge.red() - accent.red()) < 12
    assert abs(edge.green() - accent.green()) < 12
    assert abs(edge.blue() - accent.blue()) < 12


def test_clicking_the_calendar_button_opens_a_calendar_that_stays(
    qtbot, repo, make_item
):
    """With a gallery open, the Jump button was shown before it had a parent,
    so it opened as a window of its own and the calendar closed at once."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QToolButton

    from smart_gallery.gui.filter_widgets import CalendarPopup

    panel, _ = _shown(qtbot, repo, make_item)
    edit = panel.texts["date_from"]
    button = next(b for b in edit.findChildren(QToolButton) if b.isVisible())
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    popup = panel.date_fields["date_from"]._popup
    assert isinstance(popup, CalendarPopup)
    assert panel.date_fields["date_from"].jump_to is not None  # a gallery is open
    qtbot.wait(200)
    assert popup.isVisible()
    assert popup.jump_btn.isVisible() and popup.jump_btn.window() is popup
    stray = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
    assert all(not w.inherits("QPushButton") for w in stray)
    popup.calendar.clicked.emit(QDate(2024, 7, 9))
    assert edit.text() == "2024-07-09"
    assert not popup.isVisible()


def test_people_match_switch_roundtrip(qtbot, repo, make_item):
    from smart_gallery.gui.filters_model import FilterValues, to_filter_options

    panel, _ = _panel(qtbot, repo, make_item)
    panel.set_values(FilterValues(people=("Ann",)))
    assert not panel.people_match.isEnabled()
    panel.set_values(FilterValues(people=("Ann", "Bo"), people_match="all"))
    assert panel.people_match.isEnabled()
    options, _ = to_filter_options(panel.values())
    assert options.people_match == "all"
    assert options.people == ["Ann", "Bo"]
    panel.set_values(FilterValues(people=("Ann", "Bo")))
    assert panel.values().people_match == "any"


def test_people_come_before_cameras(qtbot, repo, make_item):
    panel, _ = _shown(qtbot, repo, make_item)
    layout = panel.scroll.widget().layout()
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order.index(panel.people_box) < order.index(panel._boxes["cameras"])
