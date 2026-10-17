"""Import area: pick a source, a saved profile and target galleries, then run."""

from __future__ import annotations

from PySide6.QtCore import QDir, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.forms import format_command
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.galleries.store import is_connected
from smart_gallery.gui.imports.editor import ProfileEditor
from smart_gallery.gui.imports.model import build_import_job, list_removable_drives
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.import_profiles import (
    ImportProfile,
    delete_profile,
    load_profiles,
    save_profile,
    seed_starter_profiles,
)

PICK_AGAIN = (
    "Pick the source and target galleries again — drive letters may have changed."
)


class ImportView(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._seeded = False
        self._profiles: list[ImportProfile] = []
        self._galleries = []

        self.source_combo = QComboBox()
        self.source_combo.setEditable(True)
        self.source_combo.setMinimumWidth(260)
        self.browse_button = QPushButton("Browse folder…")
        self.refresh_button = QPushButton("Refresh")
        self.profile_combo = QComboBox()
        self.new_button = QPushButton("New")
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        self.gallery_list = QListWidget()
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.dry_run_box = QCheckBox("Dry run (plan only, copy nothing)")
        self.run_button = QPushButton("Run import")
        self.note = QLabel()
        self.note.setWordWrap(True)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source"))
        source_row.addWidget(self.source_combo, 1)
        source_row.addWidget(self.browse_button)
        source_row.addWidget(self.refresh_button)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile"))
        profile_row.addWidget(self.profile_combo, 1)
        profile_row.addWidget(self.new_button)
        profile_row.addWidget(self.edit_button)
        profile_row.addWidget(self.delete_button)
        layout = QVBoxLayout(self)
        layout.addLayout(source_row)
        layout.addLayout(profile_row)
        layout.addWidget(QLabel("Import into"))
        layout.addWidget(self.gallery_list, 1)
        layout.addWidget(QLabel("Commands"))
        layout.addWidget(self.preview, 2)
        layout.addWidget(self.dry_run_box)
        layout.addWidget(self.note)
        layout.addWidget(self.run_button)

        self.browse_button.clicked.connect(self._browse)
        self.refresh_button.clicked.connect(self.refresh_sources)
        self.source_combo.currentTextChanged.connect(lambda _t: self._update())
        self.profile_combo.currentIndexChanged.connect(lambda _i: self._update())
        self.gallery_list.itemChanged.connect(lambda _i: self._update())
        self.new_button.clicked.connect(lambda: self._edit(None))
        self.edit_button.clicked.connect(lambda: self._edit(self._profile()))
        self.delete_button.clicked.connect(self._delete)
        self.dry_run_box.toggled.connect(lambda _on: self._update())
        self.run_button.clicked.connect(self._run)
        ctx.gallery_changed.connect(lambda _g: self.refresh_galleries())
        ctx.open_in_import.connect(self.select_profile)
        if ctx.jobs is not None:
            ctx.jobs.started.connect(lambda _s: self._update())
            ctx.jobs.finished.connect(lambda _r: self._update())
        self.refresh_sources()
        self.refresh_galleries()
        self._update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load_profiles_once()
        self.refresh_galleries()

    def _load_profiles_once(self) -> None:
        if not self._seeded:
            self._seeded = True
            seed_starter_profiles(self._profiles_dir())
            self.reload_profiles()

    def select_profile(self, name: str | None) -> None:
        """Preselect the profile ``name`` when it still exists (History's "Run
        again"); the source and targets are left for the owner to pick."""
        self._load_profiles_once()
        if name and any(p.name == name for p in self._profiles):
            self.profile_combo.setCurrentText(name)
        self.note.setText(PICK_AGAIN)

    def _profiles_dir(self):
        return app_data_dir() / "profiles"

    def reload_profiles(self, select: str | None = None) -> None:
        current = self._profile()
        keep = select or (current.name if current else None)
        self._profiles = load_profiles(self._profiles_dir())
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems([p.name for p in self._profiles])
        if keep:
            self.profile_combo.setCurrentText(keep)
        self.profile_combo.blockSignals(False)
        self._update()

    def refresh_sources(self) -> None:
        keep = self.source_combo.currentText()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItems(list_removable_drives())
        self.source_combo.setEditText(keep)
        self.source_combo.blockSignals(False)
        self._update()

    def refresh_galleries(self) -> None:
        ticked = {g.db_uuid for g in self.selected_galleries()}
        store = self.ctx.galleries
        self._galleries = store.all() if store is not None else []
        self.gallery_list.blockSignals(True)
        self.gallery_list.clear()
        for gallery in self._galleries:
            connected = is_connected(gallery)
            item = QListWidgetItem(
                gallery.label if connected else f"{gallery.label} (not connected)"
            )
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            if not connected:
                flags &= ~Qt.ItemFlag.ItemIsEnabled
            item.setFlags(flags)
            on = connected and gallery.db_uuid in ticked
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.gallery_list.addItem(item)
        self.gallery_list.blockSignals(False)
        self._update()

    def set_source(self, path: str) -> None:
        self.source_combo.setEditText(path)

    def source(self) -> str:
        return self.source_combo.currentText().strip()

    def selected_galleries(self) -> list:
        return [
            gallery
            for row, gallery in enumerate(self._galleries)
            if self.gallery_list.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _profile(self) -> ImportProfile | None:
        row = self.profile_combo.currentIndex()
        return self._profiles[row] if 0 <= row < len(self._profiles) else None

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose source folder")
        if chosen:
            self.set_source(QDir.toNativeSeparators(chosen))

    def _edit(self, profile: ImportProfile | None) -> None:
        dialog = ProfileEditor(profile, self)
        if not dialog.exec():
            return
        edited = dialog.profile()
        renamed = profile is None or profile.name != edited.name
        if renamed and any(p.name == edited.name for p in self._profiles):
            QMessageBox.warning(
                self,
                "Profile exists",
                f"A profile named “{edited.name}” already exists.",
            )
            return
        try:
            save_profile(self._profiles_dir(), edited)
            if profile is not None and renamed:
                delete_profile(self._profiles_dir(), profile.name)
        except OSError as error:
            QMessageBox.warning(self, "Could not save profile", str(error))
        self.reload_profiles(select=edited.name)

    def _delete(self) -> None:
        profile = self._profile()
        if profile is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete profile",
            f"Delete the profile “{profile.name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                delete_profile(self._profiles_dir(), profile.name)
            except OSError as error:
                QMessageBox.warning(self, "Could not delete profile", str(error))
            self.reload_profiles()

    def _spec(self):
        profile = self._profile()
        if profile is None:
            raise ValueError("Choose or create an import profile.")
        return build_import_job(
            profile,
            self.source(),
            self.selected_galleries(),
            dry_run=self.dry_run_box.isChecked(),
        )

    def _update(self) -> None:
        jobs = self.ctx.jobs
        has_profile = self._profile() is not None
        self.edit_button.setEnabled(has_profile)
        self.delete_button.setEnabled(has_profile)
        try:
            spec = self._spec()
        except ValueError as error:
            self.preview.setPlainText(str(error))
            self.run_button.setEnabled(False)
            return
        lines = [format_command(list(argv)) for argv in spec.argvs]
        self.preview.setPlainText("\n".join(lines))
        self.run_button.setEnabled(jobs is not None and not jobs.running)

    def _run(self) -> None:
        if self.ctx.jobs is None:
            return
        try:
            spec = self._spec()
        except ValueError as error:
            self.preview.setPlainText(str(error))
            return
        self.note.clear()
        self.ctx.jobs.start(spec)
