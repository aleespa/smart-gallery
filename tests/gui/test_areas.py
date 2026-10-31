from smart_gallery.gui.areas import AREAS


def test_stats_label():
    assert [a.label for a in AREAS][:4] == [
        "Library",
        "Import / Export",
        "Stats",
        "Storage",
    ]
    assert next(a for a in AREAS if a.label == "Stats").id == "dashboard"
    assert AREAS[3].id == "storage"


def test_advanced_is_the_last_tab():
    last = AREAS[-1]
    assert (last.id, last.label) == ("actions", "Advanced")
