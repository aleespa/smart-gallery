"""Controls the filter panel is built from.

- ``ChipPicker`` picks values from a long list (cameras, lenses, extensions,
  people): what is picked shows as removable chips, and a search box offers
  the rest, most-used first, with how many photos have each.
- ``DateField`` wraps a date text box with a calendar button.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from PySide6.QtCore import QDate, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QCalendarWidget,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from smart_gallery.gui import theme

COUNT_ROLE = Qt.ItemDataRole.UserRole + 1
VISIBLE_OPTIONS = 6  # rows the search's list shows before it scrolls
ANY = "Any"
SELECTED = "{n} selected"
SEARCH = "Search {n} {noun}…"


def _accent_rgba(alpha: int) -> str:
    c = QColor(theme.ACCENT)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


PICKER_STYLE = f"""
QFrame#filterChip {{
    border: 1px solid {theme.ACCENT};
    border-radius: 11px;
    background: {_accent_rgba(46)};
}}
QFrame#filterChip QToolButton {{ border: none; background: transparent; }}
QPushButton#linkButton {{
    border: none;
    background: transparent;
    color: palette(highlight);
    padding: 0;
}}
QPushButton#linkButton:hover {{ text-decoration: underline; }}
"""


class FlowLayout(QLayout):
    """Lays its widgets out in rows, wrapping to the next row as needed."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def addWidget(self, widget: QWidget) -> None:
        self.addChildWidget(widget)
        self.addItem(QWidgetItem(widget))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, move: bool) -> int:
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y = rect.x(), y + row_height + self._spacing
                row_height = 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y()


class _Chip(QFrame):
    def __init__(self, text: str, on_remove, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("filterChip")
        row = QHBoxLayout(self)
        row.setContentsMargins(9, 1, 2, 1)
        row.setSpacing(2)
        self.label = QLabel(text)
        self.remove_btn = QToolButton()
        self.remove_btn.setText("×")
        self.remove_btn.setAccessibleName(f"Remove {text}")
        self.remove_btn.setToolTip(f"Remove {text}")
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_btn.clicked.connect(lambda: on_remove(text))
        row.addWidget(self.label)
        row.addWidget(self.remove_btn)


class _CountDelegate(QStyledItemDelegate):
    """Draws each option's photo count right-aligned and muted."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        count = index.data(COUNT_ROLE)
        if count is None:
            return
        painter.save()
        painter.setPen(option.palette.color(QPalette.ColorRole.PlaceholderText))
        box = option.rect.adjusted(0, 0, -8, 0)
        align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(box, align, f"{count:,}")
        painter.restore()


class ChipPicker(QWidget):
    """Picks any number of values from ``set_options``.

    ``changed`` is emitted when the owner picks or removes a value, never by
    ``set_selected`` or ``set_options``.
    """

    changed = Signal()

    def __init__(self, noun: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._noun = noun  # plural, for the search hint: "cameras"
        self._options: list[tuple[str, int]] = []
        self._selected: list[str] = []
        self.setStyleSheet(PICKER_STYLE)

        self.status = QLabel()
        muted = self.status.palette()
        muted.setColor(
            QPalette.ColorRole.WindowText,
            muted.color(QPalette.ColorRole.PlaceholderText),
        )
        self.status.setPalette(muted)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("linkButton")
        self.clear_btn.setFlat(True)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.status)
        header.addStretch(1)
        header.addWidget(self.clear_btn)

        self._chip_box = QWidget()
        self._chips = FlowLayout(self._chip_box)
        self._chip_widgets: dict[str, _Chip] = {}

        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self._model = QStandardItemModel(self)
        self.completer = QCompleter(self._model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setMaxVisibleItems(VISIBLE_OPTIONS)
        self.completer.setWidget(self.search)
        self.completer.popup().setItemDelegate(_CountDelegate(self.completer.popup()))
        self.completer.activated[str].connect(self._on_activated)
        self.search.textEdited.connect(self._show_matches)
        self.search.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self._chip_box)
        layout.addWidget(self.search)
        self._refresh()

    # ── values ──────────────────────────────────────────────────────────────
    def set_options(self, options: Iterable[tuple[str, int]]) -> None:
        self._options = [(str(v), int(n)) for v, n in options]
        self._refresh()

    def set_selected(self, values: Iterable[str]) -> None:
        self._selected = list(dict.fromkeys(values))
        self._refresh()

    def selected(self) -> tuple[str, ...]:
        return tuple(self._selected)

    def available(self) -> list[str]:
        """The options not picked yet, most-used first."""
        picked = set(self._selected)
        return [v for v, _ in self._options if v not in picked]

    def count_of(self, value: str) -> int | None:
        return dict(self._options).get(value)

    def chips(self) -> list[str]:
        return list(self._chip_widgets)

    def chip_button(self, value: str) -> QToolButton:
        return self._chip_widgets[value].remove_btn

    # ── edits ───────────────────────────────────────────────────────────────
    def pick(self, value: str) -> None:
        if value in self._selected:
            return
        self._selected.append(value)
        self._refresh()
        self.changed.emit()

    def remove(self, value: str) -> None:
        if value not in self._selected:
            return
        self._selected.remove(value)
        self._refresh()
        self.changed.emit()

    def clear(self) -> None:
        if not self._selected:
            return
        self._selected = []
        self._refresh()
        self.changed.emit()

    # ── internals ───────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        n = len(self._selected)
        self.status.setText(SELECTED.format(n=n) if n else ANY)
        self.clear_btn.setVisible(bool(n))
        self.search.setPlaceholderText(
            SEARCH.format(n=f"{len(self._options):,}", noun=self._noun)
        )
        self.search.setEnabled(bool(self._options))

        counts = dict(self._options)
        self._model.clear()
        for value in self.available():
            item = QStandardItem(value)
            item.setData(counts[value], COUNT_ROLE)
            item.setEditable(False)
            self._model.appendRow(item)

        for value in [v for v in self._chip_widgets if v not in self._selected]:
            chip = self._chip_widgets.pop(value)
            self._chips.removeWidget(chip)
            chip.deleteLater()
        for value in self._selected:
            if value not in self._chip_widgets:
                chip = _Chip(value, self.remove, self._chip_box)
                self._chip_widgets[value] = chip
                self._chips.addWidget(chip)
                chip.show()
        self._chip_box.setVisible(bool(self._selected))
        self._chips.invalidate()
        self.updateGeometry()

    def _show_matches(self, text: str = "") -> None:
        self.completer.setCompletionPrefix(text)
        if self._model.rowCount():
            self.completer.complete()

    def _on_activated(self, value: str) -> None:
        self.pick(value)
        self.search.clear()
        self.completer.popup().hide()

    def eventFilter(self, watched, event) -> bool:
        # Clicking into an empty search offers the most-used options at once.
        if watched is self.search and event.type() == event.Type.MouseButtonPress:
            if not self.search.text():
                self._show_matches("")
        return False


def calendar_icon(color: QColor, edge: int = 16) -> QIcon:
    pixmap = QPixmap(edge * 2, edge * 2)  # drawn at 2x for sharp scaling
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, 3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    s = edge * 2
    p.drawRoundedRect(int(s * 0.14), int(s * 0.22), int(s * 0.72), int(s * 0.64), 4, 4)
    p.drawLine(int(s * 0.14), int(s * 0.42), int(s * 0.86), int(s * 0.42))
    p.drawLine(int(s * 0.34), int(s * 0.12), int(s * 0.34), int(s * 0.3))
    p.drawLine(int(s * 0.66), int(s * 0.12), int(s * 0.66), int(s * 0.3))
    p.end()
    return QIcon(pixmap)


class CalendarPopup(QFrame):
    """A month calendar under a date box, with a jump to the first or last
    photo's month and a clear link."""

    chosen = Signal(QDate)
    cleared = Signal()

    def __init__(self, jump_text: str, jump_to: date | None, parent: QWidget):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(PICKER_STYLE)
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        # Weekends in the ordinary text colour, not Qt's red.
        plain = QTextCharFormat()
        plain.setForeground(self.palette().color(QPalette.ColorRole.Text))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.calendar.setWeekdayTextFormat(day, plain)
        # Made as children: showing a widget that has no parent yet opens it
        # as a window of its own, which took the focus from this popup and
        # so closed it the moment it opened.
        self.jump_btn = QPushButton(jump_text, self)
        self.clear_btn = QPushButton("Clear", self)
        for btn in (self.jump_btn, self.clear_btn):
            btn.setObjectName("linkButton")
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._jump_to = jump_to
        links = QHBoxLayout()
        links.addWidget(self.jump_btn)
        links.addStretch(1)
        links.addWidget(self.clear_btn)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.calendar)
        layout.addLayout(links)
        self.jump_btn.setVisible(jump_to is not None)

        self.calendar.clicked.connect(self._choose)
        self.calendar.activated.connect(self._choose)
        self.jump_btn.clicked.connect(self.jump)
        self.clear_btn.clicked.connect(self._clear)

    def jump(self) -> None:
        if self._jump_to is not None:
            self.calendar.setCurrentPage(self._jump_to.year, self._jump_to.month)

    def _choose(self, day: QDate) -> None:
        self.chosen.emit(day)
        self.close()

    def _clear(self) -> None:
        self.cleared.emit()
        self.close()


class DateField(QWidget):
    """``edit`` (an ISO date or empty) with a calendar button inside it.

    ``picked`` is emitted when the calendar sets or clears the date; typing
    goes through ``edit``'s own signals as before.
    """

    picked = Signal()

    def __init__(self, edit: QLineEdit, jump_text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.edit = edit
        self.jump_text = jump_text
        self.jump_to: date | None = None  # the first or last photo's date
        self._popup: CalendarPopup | None = None
        color = edit.palette().color(QPalette.ColorRole.Text)
        action = edit.addAction(
            calendar_icon(color), QLineEdit.ActionPosition.TrailingPosition
        )
        action.setToolTip("Pick a date")
        action.triggered.connect(self.open_calendar)
        self.calendar_action = action
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)

    def open_calendar(self) -> CalendarPopup:
        if self._popup is not None:
            self._popup.deleteLater()  # one calendar per box, not one per click
        popup = CalendarPopup(self.jump_text, self.jump_to, self)
        typed = self._typed()
        if typed is not None:
            popup.calendar.setSelectedDate(QDate(typed.year, typed.month, typed.day))
        elif self.jump_to is not None:
            popup.jump()
        popup.chosen.connect(self._set_date)
        popup.cleared.connect(self._clear)
        popup.adjustSize()
        popup.move(self.edit.mapToGlobal(self.edit.rect().bottomLeft()))
        popup.show()
        self._popup = popup
        return popup

    def _typed(self) -> date | None:
        try:
            return date.fromisoformat(self.edit.text().strip())
        except ValueError:
            return None

    def _set_date(self, day: QDate) -> None:
        self.edit.setText(day.toString(Qt.DateFormat.ISODate))
        self.picked.emit()

    def _clear(self) -> None:
        if self.edit.text():
            self.edit.clear()
            self.picked.emit()
