"""The small components pages are assembled from."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.colors import ERROR, KEEP, NEUTRAL
from smart_gallery.gui import icons, theme
from smart_gallery.gui.ui import metrics

PILL_STATUSES = {
    "ok": ("done", KEEP),
    "failed": ("failed", ERROR),
    "cancelled": ("cancelled", NEUTRAL),
}


def _muted(label: QLabel) -> QLabel:
    palette = label.palette()
    palette.setColor(
        QPalette.ColorRole.WindowText,
        palette.color(QPalette.ColorRole.PlaceholderText),
    )
    label.setPalette(palette)
    return label


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.title = QLabel()
        self.title.setFont(theme.serif_font(theme.HEADER_PT))
        self.subtitle = _muted(QLabel())
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        self.set_title(title)
        self.set_subtitle(subtitle)

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)
        self.subtitle.setHidden(not text)


class EmptyState(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(metrics.SPACE[1])
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = _muted(QLabel())
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.action = QPushButton()
        self._callback: Callable[[], None] | None = None
        self.action.clicked.connect(self._clicked)
        for w in (self.icon, self.text, self.hint):
            layout.addWidget(w)
        layout.addWidget(self.action, 0, Qt.AlignmentFlag.AlignCenter)
        for w in (self.icon, self.hint, self.action):
            w.hide()

    def _clicked(self) -> None:
        if self._callback is not None:
            self._callback()

    def show_text(
        self,
        text: str,
        hint: str = "",
        action_label: str | None = None,
        action: Callable[[], None] | None = None,
        icon: str | None = None,
    ) -> None:
        self.text.setText(text)
        self.hint.setText(hint)
        self.hint.setHidden(not hint)
        self._callback = action
        self.action.setText(action_label or "")
        self.action.setHidden(not action_label)
        if icon:
            color = self.palette().color(QPalette.ColorRole.PlaceholderText)
            self.icon.setPixmap(icons.icon(icon, color, 32).pixmap(32, 32))
        self.icon.setHidden(not icon)


class ActionBar(QWidget):
    """A status line on the left, buttons on the right, an optional note below."""

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(metrics.SPACE[1])
        self._left = QHBoxLayout()
        self._left.setSpacing(metrics.SPACE[1])
        self._right = QHBoxLayout()
        self._right.setSpacing(metrics.SPACE[1])
        self.status = QLabel()
        row.addLayout(self._left)
        row.addWidget(self.status, 1)
        row.addLayout(self._right)
        self.note = _muted(QLabel())
        self.note.setWordWrap(True)
        outer.addLayout(row)
        outer.addWidget(self.note)

    def add_left(self, widget: QWidget) -> None:
        self._left.addWidget(widget)

    def add_right(self, widget: QWidget) -> None:
        self._right.addWidget(widget)


class PanelFooter(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(metrics.FOOTER_HEIGHT)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(8, 0, 8, 0)

    def add(self, widget: QWidget) -> None:
        self._row.addWidget(widget)


class Disclosure(QWidget):
    """A text toggle with an arrow that shows or hides ``body``."""

    toggled = Signal(bool)

    def __init__(self, text: str, body: QWidget):
        super().__init__()
        self.body = body
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(metrics.SPACE[0])
        self.button = QToolButton()
        self.button.setText(text)
        self.button.setCheckable(True)
        self.button.setAutoRaise(True)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(body)
        body.setVisible(False)
        self.button.toggled.connect(self._toggled)

    @property
    def opened(self) -> bool:
        return self.button.isChecked()

    def _toggled(self, shown: bool) -> None:
        self.body.setVisible(shown)
        self.button.setArrowType(
            Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow
        )
        self.toggled.emit(shown)


def pill_style(status: str) -> tuple[str, str]:
    """(label, colour) for a history status; unknown ones show their own text."""
    return PILL_STATUSES.get(status, (status, NEUTRAL))


def status_pill(painter: QPainter, rect: QRectF, status: str) -> None:
    """A rounded pill: the status colour tinted, with the normal text colour."""
    label, color = pill_style(status)
    fill = QColor(color)
    fill.setAlpha(70)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(color))
    painter.setBrush(fill)
    r = QRectF(rect)
    painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)
    painter.setPen(painter.device().palette().color(QPalette.ColorRole.WindowText))
    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
    painter.restore()
