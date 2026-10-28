"""Dialogs for the Mirror button: sync preview, free up / bring back, pairing.

They only collect choices and turn them into CLI flags; the menu runs the
commands as jobs.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.gui import theme
from smart_gallery.storage.tree import format_bytes

DEFAULT_MIRROR_LABEL = "the mirror"

# (CLI value, label); "{d}" is the mirror's label. The first "Only on" choice
# shown is the default: keep the mirror's extra files where they are.
ONLY_ON_CHOICES = (
    ("keep-on-mirror", "Keep on {d} only"),
    ("copy-back", "Copy back to this drive"),
    ("later", "Decide later"),
)
CHANGED_ON_CHOICES = (
    ("skip", "Leave them for now"),
    ("take-working", "Overwrite with this drive's version"),
    ("keep-mirror", "Keep {d}'s version"),
)
CONFLICT_CHOICES = (
    ("skip", "Leave them for now"),
    ("take-working", "Keep this drive's version"),
    ("keep-mirror", "Keep {d}'s version"),
)

MISSING_WARNING = (
    "{n} freed-up file(s) are missing from {d}. They were kept only there, so "
    "those copies may be gone. Check {d} before syncing."
)
CONFLICT_WARNING = (
    "{n} freed-up file(s) kept only on {d} now have a different file at the "
    "same place on this drive: {names}. Those new files are not synced; move "
    "them to another folder to sync them."
)
LARGE_DELETE = "Yes, move this many files on {d} to its trash"
NOTHING_TO_DO = "{d} is already in step. Nothing to copy or change."
FILE_TYPES = (("image", "Photos"), ("video", "Videos"), ("other", "Other files"))


def _n(plan: dict, name: str) -> int:
    counts = plan.get("counts") or {}
    if name in counts:
        return int(counts[name])
    return len(plan.get(name) or [])


def _files(n: int) -> str:
    return f"{n:,} file" if n == 1 else f"{n:,} files"


def _combo(choices, mirror_label: str) -> QComboBox:
    combo = QComboBox()
    for value, label in choices:
        combo.addItem(label.format(d=mirror_label), value)
    return combo


class SyncPreviewDialog(QDialog):
    """What a sync would do, with a choice for each kind of difference."""

    def __init__(
        self, plan: dict, parent=None, mirror_label: str = DEFAULT_MIRROR_LABEL
    ):
        super().__init__(parent)
        self.plan = plan
        d = mirror_label
        self.setWindowTitle(f"Sync to {d}")

        lines = []
        copy_new, overwrite = _n(plan, "copy_new"), _n(plan, "overwrite")
        if copy_new or overwrite:
            size = format_bytes(int(plan.get("copy_bytes") or 0))
            lines.append(
                f"Copy {_files(copy_new)} and update {_files(overwrite)} ({size})"
            )
        moves = _n(plan, "moves")
        if moves:
            lines.append(f"Move or rename {_files(moves)} on {d}")
        adopt = _n(plan, "adopt")
        if adopt:
            lines.append(f"Record {_files(adopt)} that already match")
        changed = _n(plan, "offloaded_changed")
        if changed:
            lines.append(f"Leave {_files(changed)} kept only on {d} that changed there")
        trash = _n(plan, "trash")
        if trash:
            size = format_bytes(int(plan.get("mirror_trash_bytes") or 0))
            lines.append(f"<b>Move {_files(trash)} on {d} to its trash</b> ({size})")
        free = plan.get("mirror_free_bytes")
        if free is not None:
            lines.append(f"Free space on {d}: {format_bytes(int(free))}")
        self.summary = QLabel("<br>".join(lines) or NOTHING_TO_DO.format(d=d))
        self.summary.setWordWrap(True)

        missing = _n(plan, "offloaded_missing")
        self.missing_warning = QLabel(MISSING_WARNING.format(n=missing, d=d))
        self.missing_warning.setWordWrap(True)
        self.missing_warning.setStyleSheet(theme.WARNING_BANNER_STYLE)
        self.missing_warning.setVisible(bool(missing))

        clash = list(plan.get("offloaded_conflict") or [])
        names = ", ".join(clash[:5]) + (", …" if len(clash) > 5 else "")
        self.conflict_warning = QLabel(
            CONFLICT_WARNING.format(n=len(clash), d=d, names=names)
        )
        self.conflict_warning.setWordWrap(True)
        self.conflict_warning.setStyleSheet(theme.WARNING_BANNER_STYLE)
        self.conflict_warning.setVisible(bool(clash))

        self.only_on = _combo(ONLY_ON_CHOICES, d)
        self.changed_on = _combo(CHANGED_ON_CHOICES, d)
        self.conflicts = _combo(CONFLICT_CHOICES, d)
        form = QFormLayout()
        for combo, name, label in (
            (self.only_on, "only_on_mirror", f"Only on {d}"),
            (self.changed_on, "changed_on_mirror", f"Changed on {d}"),
            (self.conflicts, "conflicts", "Changed on both"),
        ):
            n = _n(plan, name)
            form.addRow(f"{label} ({n:,})", combo)
            combo.setEnabled(bool(n))

        self.confirm = QCheckBox(LARGE_DELETE.format(d=d))
        self.confirm.setVisible(bool(plan.get("large_delete")))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Sync")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.confirm.toggled.connect(self._update_ok)
        self._update_ok()

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.missing_warning)
        layout.addWidget(self.conflict_warning)
        layout.addLayout(form)
        layout.addWidget(self.confirm)
        layout.addWidget(buttons)

    def summary_text(self) -> str:
        return self.summary.text().replace("<b>", "").replace("</b>", "")

    def _update_ok(self, *_args) -> None:
        needs_confirm = bool(self.plan.get("large_delete"))
        self.ok_button.setEnabled(not needs_confirm or self.confirm.isChecked())

    def flags(self) -> list[str]:
        out = [
            "--only-on-mirror",
            self.only_on.currentData(),
            "--changed-on-mirror",
            self.changed_on.currentData(),
            "--conflicts",
            self.conflicts.currentData(),
        ]
        if self.plan.get("large_delete") and self.confirm.isChecked():
            out.append("--confirm-large-delete")
        return out


class FreeUpDialog(QDialog):
    """Which files to free up (or bring back): types, a date range, a folder.

    ``mirrors`` is ``[(label, db_path), ...]``; with more than one, a picker
    adds ``--mirror``. ``argv()`` returns the flags after ``DRIVE``.
    """

    def __init__(
        self,
        parent=None,
        mirrors: Sequence[tuple[str, str]] = (),
        title: str = "Free up space",
        ok_text: str = "Preview",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout()

        self.mirror: QComboBox | None = None
        if len(mirrors) > 1:
            self.mirror = QComboBox()
            for label, db_path in mirrors:
                self.mirror.addItem(label, db_path)
            form.addRow("Mirror", self.mirror)

        self.types: dict[str, QCheckBox] = {}
        row = QWidget()
        types_layout = QHBoxLayout(row)
        types_layout.setContentsMargins(0, 0, 0, 0)
        for value, label in FILE_TYPES:
            box = QCheckBox(label)
            box.setChecked(value == "image")
            self.types[value] = box
            types_layout.addWidget(box)
        types_layout.addStretch(1)
        form.addRow("File types", row)

        self.use_dates = QCheckBox("Only files taken between")
        today = QDate.currentDate()
        self.date_start = QDateEdit(today.addYears(-1))
        self.date_end = QDateEdit(today)
        for edit in (self.date_start, self.date_end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setEnabled(False)
            self.use_dates.toggled.connect(edit.setEnabled)
        dates = QWidget()
        dates_layout = QHBoxLayout(dates)
        dates_layout.setContentsMargins(0, 0, 0, 0)
        dates_layout.addWidget(self.use_dates)
        dates_layout.addWidget(self.date_start)
        dates_layout.addWidget(QLabel("and"))
        dates_layout.addWidget(self.date_end)
        form.addRow("Dates", dates)

        self.folder = QLineEdit()
        self.folder.setPlaceholderText("A folder on the drive, e.g. 2019/Trips")
        form.addRow("Folder", self.folder)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def argv(self) -> list[str]:
        out: list[str] = []
        if self.mirror is not None:
            out += ["--mirror", self.mirror.currentData()]
        types = [value for value, box in self.types.items() if box.isChecked()]
        if types:
            out += ["--file-types", *types]
        if self.use_dates.isChecked():
            out += ["--date-start", self.date_start.date().toString("yyyy-MM-dd")]
            out += ["--date-end", self.date_end.date().toString("yyyy-MM-dd")]
        folder = self.folder.text().strip()
        if folder:
            out += ["--folder", folder]
        return out


class PairDialog(QDialog):
    """Pick the gallery that becomes this gallery's mirror."""

    def __init__(self, galleries, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pair with another gallery")
        self._galleries = list(galleries)
        self.picker = QComboBox()
        for gallery in self._galleries:
            self.picker.addItem(gallery.label or str(gallery.root))
        note = QLabel(
            "The gallery you pick becomes a mirror: a copy of this one that "
            "Sync keeps in step."
        )
        note.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Pair")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.picker)
        layout.addWidget(buttons)

    def selected(self):
        i = self.picker.currentIndex()
        return self._galleries[i] if 0 <= i < len(self._galleries) else None
