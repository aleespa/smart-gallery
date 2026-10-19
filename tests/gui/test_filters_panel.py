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
