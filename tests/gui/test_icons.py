from PySide6.QtGui import QColor

from smart_gallery.gui import icons


def test_every_icon_renders(qtbot):
    for name in icons.NAMES:
        pixmap = icons.icon(name, QColor("white"), 18).pixmap(18, 18)
        assert not pixmap.isNull(), name


def test_shared_names_exist(qtbot):
    for name in ("sort-up", "sort-down", "refresh", "undo", "browse"):
        assert name in icons.NAMES
