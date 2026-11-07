"""The viewer stage: the photo fitted on a dark backdrop, fixed arrows, zoom,
pan, face boxes and messages."""

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from smart_gallery import colors
from smart_gallery.gui.viewer import icons
from smart_gallery.gui.viewer.stage import (
    ARROW_INSET,
    MAX_ZOOM,
    Box,
    PhotoStage,
    fit_rect,
)


def _img(w, h, color="white"):
    image = QImage(w, h, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


@pytest.fixture
def stage(qtbot):
    s = PhotoStage()
    qtbot.addWidget(s)
    s.resize(1000, 700)
    s.show()
    qtbot.waitExposed(s)
    return s


def test_fit_rect_keeps_shape_centres_and_never_upscales():
    assert fit_rect(QSize(3000, 2000), QSize(1048, 748)) == QRect(24, 41, 1000, 666)
    assert fit_rect(QSize(50, 50), QSize(1000, 800)).size() == QSize(50, 50)
    pano = fit_rect(QSize(8000, 1000), QSize(1048, 748))
    assert (pano.width(), pano.height()) == (1000, 125)
    assert fit_rect(QSize(0, 0), QSize(100, 100)).isEmpty()


def test_arrows_do_not_move_between_shapes(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    before = (stage.prev_btn.geometry(), stage.next_btn.geometry())
    stage.set_image(_img(2000, 3000), sharp=True)
    assert (stage.prev_btn.geometry(), stage.next_btn.geometry()) == before
    assert stage.next_btn.geometry().right() == 1000 - ARROW_INSET - 1
    assert stage.prev_btn.geometry().left() == ARROW_INSET
    assert abs(stage.prev_btn.geometry().center().y() - 350) <= 1


def test_arrows_disabled_at_the_ends(stage):
    stage.set_nav(False, True)
    assert not stage.prev_btn.isEnabled() and stage.next_btn.isEnabled()
    assert stage.prev_btn.isVisible()  # dimmed, not hidden


def test_arrow_buttons_take_no_focus(stage):
    assert stage.prev_btn.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_arrows_emit_navigation(stage, qtbot):
    stage.set_nav(True, True)
    with qtbot.waitSignal(stage.next):
        stage.next_btn.click()
    with qtbot.waitSignal(stage.previous):
        stage.prev_btn.click()


def test_toggle_zoom_shows_100_percent_and_back(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    fit = stage.image_rect()
    assert fit.width() < 1000
    stage.toggle_zoom()
    assert stage.is_zoomed() and stage.image_rect().width() == pytest.approx(3000)
    stage.reset_zoom()
    assert not stage.is_zoomed() and stage.image_rect() == fit


def test_zoom_keeps_the_point_under_the_cursor(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    fit = stage.image_rect()
    anchor = QPointF(fit.left() + fit.width() * 0.5, fit.top() + fit.height() * 0.4)
    stage.toggle_zoom(anchor)
    zoomed = stage.image_rect()
    # The image point under the anchor: 50 % across, 40 % down, before and after.
    assert (anchor.x() - zoomed.left()) / zoomed.width() == pytest.approx(0.5, abs=0.01)
    assert (anchor.y() - zoomed.top()) / zoomed.height() == pytest.approx(0.4, abs=0.01)


def test_zoomed_image_still_covers_the_stage(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    stage.toggle_zoom(QPointF(1, 1))  # a corner: no empty band may show
    rect = stage.image_rect()
    assert rect.left() <= 0 and rect.top() <= 0
    assert rect.right() >= 999 and rect.bottom() >= 699


def test_small_image_is_not_zoomable_past_its_size_at_fit(stage):
    stage.set_image(_img(50, 50), sharp=True)
    assert stage.image_rect().size() == QSize(50, 50)


def test_zoom_by_steps_is_capped(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    centre = QPointF(500, 350)
    stage.zoom_by(40, centre)
    assert stage.image_rect().width() == pytest.approx(3000 * MAX_ZOOM)
    stage.zoom_by(-80, centre)
    assert not stage.is_zoomed()


def test_set_image_keep_view_scales_the_zoom(stage):
    stage.set_image(_img(1500, 1000), sharp=True)
    stage.toggle_zoom(QPointF(500, 350))
    before = stage.image_rect()
    stage.set_image(_img(3000, 2000), sharp=True, keep_view=True)  # sharper copy
    assert stage.image_rect() == before


def test_new_image_resets_zoom(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    stage.toggle_zoom()
    stage.set_image(_img(2000, 3000), sharp=True)
    assert not stage.is_zoomed()


def _wheel(widget, dy, modifiers=Qt.KeyboardModifier.NoModifier):
    pos = QPointF(500, 350)
    event = QWheelEvent(
        pos,
        QPointF(widget.mapToGlobal(pos.toPoint())),
        QPoint(0, 0),
        QPoint(0, dy),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_wheel_steps_photos(stage, qtbot):
    seen = []
    stage.next.connect(lambda: seen.append("next"))
    stage.previous.connect(lambda: seen.append("prev"))
    _wheel(stage, -120)
    _wheel(stage, 120)
    assert seen == ["next", "prev"]


def test_ctrl_wheel_zooms_instead(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    seen = []
    stage.next.connect(lambda: seen.append("next"))
    _wheel(stage, 120, Qt.KeyboardModifier.ControlModifier)
    assert stage.is_zoomed() and seen == []


def test_double_click_toggles_zoom(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    QTest.mouseDClick(stage, Qt.MouseButton.LeftButton, pos=QPoint(500, 350))
    assert stage.is_zoomed()


def test_drag_pans_when_zoomed(stage):
    stage.set_image(_img(3000, 2000), sharp=True)
    stage.toggle_zoom(QPointF(500, 350))
    before = stage.image_rect()
    QTest.mousePress(stage, Qt.MouseButton.LeftButton, pos=QPoint(500, 350))
    QTest.mouseMove(stage, QPoint(400, 300))
    QTest.mouseRelease(stage, Qt.MouseButton.LeftButton, pos=QPoint(400, 300))
    after = stage.image_rect()
    assert after.left() == pytest.approx(before.left() - 100)
    assert after.top() == pytest.approx(before.top() - 50)


def _pixel(stage, x, y):
    return stage.grab().toImage().pixelColor(x, y)


def test_boxes_drawn_only_on_the_sharp_image(stage):
    # 400x300 sits unscaled at (300, 200); the box covers (400, 300)-(500, 400).
    stage.set_boxes([Box(QRectF(100, 100, 100, 100))])
    stage.set_image(_img(400, 300), sharp=False)
    assert _pixel(stage, 450, 300) == QColor("white")
    stage.set_image(_img(400, 300), sharp=True)
    edge = _pixel(stage, 450, 300)
    assert edge != QColor("white") and edge.red() > edge.green()
    assert _pixel(stage, 450, 350) == QColor("white")  # inside the box


def test_stage_backdrop_colour(stage):
    stage.set_image(_img(400, 300), sharp=True)
    assert _pixel(stage, 5, 5) == QColor(colors.VIEWER_STAGE)


def test_message_replaces_the_image(stage):
    stage.set_image(_img(400, 300), sharp=True)
    stage.set_message("No preview for this file.")
    assert stage.image_rect().isEmpty()
    assert stage.message() == "No preview for this file."
    stage.set_image(_img(400, 300), sharp=True)
    assert stage.message() == ""


def test_spinner_only_after_a_moment(stage, qtbot):
    stage.set_loading(True)
    assert not stage.spinner_visible()
    qtbot.waitUntil(stage.spinner_visible, timeout=1000)
    stage.set_image(_img(40, 30), sharp=True)
    assert not stage.spinner_visible()


def test_strip_tab(stage, qtbot):
    stage.set_strip_handle(True)
    assert stage.strip_tab.isVisible()
    with qtbot.waitSignal(stage.strip_toggle):
        stage.strip_tab.click()
    stage.set_strip_handle(False)
    assert not stage.strip_tab.isVisible()


def test_icons_paint(qtbot):
    for name in icons.NAMES:
        assert not icons.icon(name, QColor("white")).isNull()
    with pytest.raises(KeyError):
        icons.icon("nope", QColor("white"))
