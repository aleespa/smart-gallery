"""A segmented switch: checkable buttons side by side, exactly one checked."""

from __future__ import annotations

from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget


def segment(*labels: str) -> tuple[QWidget, QButtonGroup, list[QPushButton]]:
    """Checkable buttons side by side, exactly one checked (the first)."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    group = QButtonGroup(box)
    group.setExclusive(True)
    buttons = []
    for label in labels:
        button = QPushButton(label)
        button.setCheckable(True)
        group.addButton(button)
        layout.addWidget(button)
        buttons.append(button)
    buttons[0].setChecked(True)
    return box, group, buttons
