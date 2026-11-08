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
            assert palette.color(group, role).name() == theme.ACCENT_FILL.lower()
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
        assert highlight.name() == theme.ACCENT_FILL.lower()
    finally:
        qapp.setFont(font)
        qapp.setPalette(palette)


def test_apply_theme_is_always_dark(qapp):
    """The app is dark whatever the system is set to; where Qt cannot follow
    the request (offscreen, older Windows styles) it uses the same colours."""
    from PySide6.QtGui import QPalette

    from smart_gallery.gui import theme

    font, palette, style = qapp.font(), qapp.palette(), qapp.style().name()
    try:
        theme.apply_theme(qapp)
        p = qapp.palette()
        assert p.color(QPalette.ColorRole.Window).lightness() < 128
        assert p.color(QPalette.ColorRole.Base).lightness() < 128
        assert p.color(QPalette.ColorRole.WindowText).lightness() > 200
        assert p.color(QPalette.ColorRole.Highlight).name() == theme.ACCENT_FILL.lower()
    finally:
        qapp.setStyle(style)
        qapp.setFont(font)
        qapp.setPalette(palette)


def test_dark_palette_matches_windows_11_dark():
    from PySide6.QtGui import QPalette

    from smart_gallery.gui import theme

    p = theme.dark_palette()
    role = QPalette.ColorRole
    assert p.color(role.Window).name() == "#1e1e1e"
    assert p.color(role.Base).name() == "#2d2d2d"
    assert p.color(role.Button).name() == "#3c3c3c"
    assert p.color(role.Text).name() == "#ffffff"
    assert p.color(role.PlaceholderText).alpha() == 128
    disabled = p.color(QPalette.ColorGroup.Disabled, role.Text)
    assert disabled.name() == "#9d9d9d"


def test_main_actions_are_primary_buttons(qtbot, repo):
    """Each page's main action is white bold text on the accent fill."""
    from smart_gallery.gui import theme
    from smart_gallery.gui.context import AppContext
    from smart_gallery.gui.exports.view import ExportView
    from smart_gallery.gui.galleries.store import Gallery
    from smart_gallery.gui.history.view import HistoryView
    from smart_gallery.gui.imports.view import ImportView
    from smart_gallery.gui.library.view import LibraryView

    ctx = AppContext()
    ctx.set_gallery(Gallery("uuid", str(repo.db_path), "E"))
    library = LibraryView(ctx)
    views = [library, ExportView(ctx), ImportView(ctx), HistoryView(ctx)]
    for view in views:
        qtbot.addWidget(view)
    buttons = [
        library.import_btn,
        views[1].run_button,
        views[2].run_button,
        views[3].run_button,
    ]
    for button in buttons:
        assert button.objectName() == "primaryButton", button.text()
        assert button.styleSheet() == theme.PRIMARY_STYLE


def test_primary_button_reads_and_greys_out(qtbot):
    from PySide6.QtWidgets import QPushButton

    from smart_gallery.gui import theme

    button = theme.primary(QPushButton("Run"))
    qtbot.addWidget(button)
    button.resize(120, 36)
    edge = button.grab().toImage().pixelColor(6, 18)
    assert edge.name() == theme.ACCENT_FILL.lower()
    button.setEnabled(False)
    edge = button.grab().toImage().pixelColor(6, 18)
    assert edge.name() != theme.ACCENT_FILL.lower()
