"""A window around one job form (the Library's Import and Export).

It is modeless and kept between openings, so a form keeps what was typed in
it. Once the form starts its job (its ``ran`` signal) the window closes; the
job's progress shows in the status bar like any other.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QPushButton, QVBoxLayout, QWidget

from smart_gallery.gui import theme

DEFAULT_SIZE = (860, 800)


class FormWindow(QDialog):
    def __init__(
        self,
        title: str,
        form: QWidget,
        parent: QWidget | None = None,
        *,
        size: tuple[int, int] = DEFAULT_SIZE,
    ):
        super().__init__(parent)
        self.form = form
        self.setWindowTitle(title)
        self.setModal(False)
        self.setStyleSheet(theme.CARD_STYLE)
        self.resize(*size)
        layout = QVBoxLayout(self)
        layout.addWidget(form)
        form.ran.connect(self.accept)
        # In a dialog every push button turns "auto default", so Enter in a
        # text box would press the first one (Browse…). A form runs only from
        # its own Run button.
        for button in form.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def present(self) -> None:
        """Show the window, or bring it forward when it is already open."""
        self.show()
        self.raise_()
        self.activateWindow()
