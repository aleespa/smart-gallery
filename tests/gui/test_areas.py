import pytest

from smart_gallery.gui.areas import AREAS


@pytest.mark.xfail(strict=True, reason="Storage area arrives in Task 8")
def test_stats_label():
    assert [a.label for a in AREAS][:4] == ["Library", "Import", "Stats", "Storage"]
    assert next(a for a in AREAS if a.label == "Stats").id == "dashboard"
