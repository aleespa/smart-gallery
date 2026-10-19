from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QWidget

from smart_gallery.gui.app import MainWindow
from smart_gallery.gui.brand import GRADIENT, BrandLabel, letter_colours


def test_letter_colours_run_along_sunset():
    colours = letter_colours(12)
    assert len(colours) == 12
    assert colours[0].name() == GRADIENT[0].name()
    assert colours[-1].name() == GRADIENT[1].name()
    assert letter_colours(1) == [GRADIENT[0]]
    assert letter_colours(0) == []


def _render(label):
    image = QImage(label.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0))
    # Only the label's own painting, over black.
    label.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return image


def _pixels(image, x0, x1):
    return [
        image.pixelColor(x, y)
        for x in range(x0, x1)
        for y in range(image.height())
        if image.pixelColor(x, y) != QColor(0, 0, 0)
    ]


def test_brand_letters_start_coral_and_end_orange(qtbot):
    label = BrandLabel()
    qtbot.addWidget(label)
    label.resize(label.sizeHint())
    image = _render(label)
    third = image.width() // 3
    start = _pixels(image, 0, third)
    end = _pixels(image, image.width() - third, image.width())
    assert start and end
    # Coral is far redder than it is green; orange is greener than it is blue.
    assert max(c.red() - c.green() for c in start) > 100
    assert max(c.green() - c.blue() for c in end) > 40
    assert max(c.green() - c.blue() for c in start) < 20


def test_window_shows_the_brand(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.brand.text() == "Smart Gallery"
    assert win.brand.accessibleName() == "Smart Gallery"


def test_brand_uses_the_larger_heading_size(qtbot):
    from smart_gallery.gui import theme

    label = BrandLabel()
    qtbot.addWidget(label)
    assert label.font().pointSizeF() == theme.BRAND_PT
    assert theme.BRAND_PT > theme.TAB_PT


def test_accent_palette_shares_the_sunset_accent():
    from PySide6.QtGui import QPalette

    from smart_gallery.gui import theme

    palette = theme.accent_palette(QPalette())
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role in (QPalette.ColorRole.Highlight, QPalette.ColorRole.Accent):
            assert palette.color(group, role).name() == theme.ACCENT.lower()
        text = palette.color(group, QPalette.ColorRole.HighlightedText)
        assert text.name() == theme.ON_ACCENT.lower()
    assert theme.ACCENT == GRADIENT[0].name().upper()


def test_apply_theme_sets_font_and_accent(qapp):
    from PySide6.QtGui import QPalette

    from smart_gallery.gui import theme

    font, palette = qapp.font(), qapp.palette()
    try:
        theme.apply_theme(qapp)
        assert qapp.font().families()[0] == theme.SANS[0]
        highlight = qapp.palette().color(QPalette.ColorRole.Highlight)
        assert highlight.name() == theme.ACCENT.lower()
    finally:
        qapp.setFont(font)
        qapp.setPalette(palette)
