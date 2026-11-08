"""The box shown when an undo fails: offers to forget that step."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

UNDO_FAILED = "Could not undo.\n\n{error}"
FORGET = "Forget this undo step"


def ask_forget(parent: QWidget, title: str, error: str) -> bool:
    """Show why the undo failed; True when the user chose to forget the step."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(UNDO_FAILED.format(error=error))
    forget = box.addButton(FORGET, QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    return box.clickedButton() is forget
