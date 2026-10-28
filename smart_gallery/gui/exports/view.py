"""Export page: copy a filtered part of the open gallery to a folder.

The left column is a filter panel with its own filters (not the Library's;
"Use the Library's filters" copies those in). The right column picks the
destination and the new folder made in it (by default "Export <date> <time>"),
how files are sorted into folders, and whether the export is
JPEG only (CR2/CR3 converted, videos kept, other files left out). It runs
the ``export`` command as a job; the gallery is only read. The command itself
sits in a section that starts collapsed.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.forms import format_command
from smart_gallery.gui import theme
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.exports.model import (
    LEVEL_LABELS,
    LEVELS,
    ExportSettings,
    ExportSummary,
    build_export_job,
    default_folder_name,
    example_path,
    export_summary,
)
from smart_gallery.gui.filters_model import from_filter_options
from smart_gallery.gui.filters_panel import FilterPanel
from smart_gallery.gui.galleries.store import is_connected
from smart_gallery.gui.paths import app_data_dir, exiftool_available
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.gui.state_store import load_json, save_json
from smart_gallery.storage.tree import format_bytes

STATE_FILE = "export.json"  # the last run's ExportSettings, but not NOT_SAVED
NOT_SAVED = ("dry_run", "folder")  # a folder name is for one export
CLOCK_MS = 20_000  # keeps the default folder name's time current
LEVEL_SLOTS = 4
NO_LEVEL = "—"
FROM = "Exporting from {label}"
NO_GALLERY = "Open a gallery to export from it."
DISCONNECTED = "{label} is not connected."
USE_LIBRARY = "Use the Library's filters"
COUNTING = "Counting…"
MATCHED = "{n:,} files match ({size})."
MATCHED_JPEG = (
    "{n:,} files match: {as_is:,} JPEGs and videos copied, {raws:,} RAW files "
    "converted to JPEG, {left_out:,} other files left out."
)
FIX_FILTERS = "Fix the filters marked in red."
EXAMPLE = "For example: {path}"
JPEG_ONLY = "JPEG only: convert CR2/CR3 photos to JPEG (videos are kept)"
JPEG_HINT = (
    "Each RAW photo becomes the full-size JPEG stored inside it, with its "
    "date, camera and orientation. A RAW with a JPEG of the same name beside "
    "it is left out, since that JPEG is exported. Videos are copied as they "
    "are; other files (PNG, documents…) are left out."
)
COMMAND = "Command"
NO_EXIFTOOL = "Needs ExifTool, which is not installed."
ON_EXIST = (("rename", "Keep both (add _1, _2…)"), ("skip", "Skip it"))


class ExportView(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._summary: ExportSummary | None = None
        self._counter = RepoQuery(ctx, self)

        self.filters = FilterPanel(ctx, shared=False)
        self.use_library = QPushButton(USE_LIBRARY)

        self.source = QLabel()
        self.source.setFont(theme.serif_font(theme.HEADER_PT))
        self.summary = QLabel()
        self.summary.setWordWrap(True)

        self.dest = QLineEdit()
        self.dest.setPlaceholderText("A folder, e.g. on a USB stick")
        self.browse_button = QPushButton("Browse…")
        self.open_button = QPushButton("Open folder")
        self.folder = QLineEdit()  # empty: the placeholder's dated name
        self.folder.setToolTip("A new folder made inside the destination")
        self._clock = QTimer(self)
        self._clock.setInterval(CLOCK_MS)

        self.sort_radio = QRadioButton("Sort into folders by")
        self.keep_radio = QRadioButton("Keep the gallery's own folders")
        self.levels: list[QComboBox] = []
        for _ in range(LEVEL_SLOTS):
            combo = QComboBox()
            combo.addItem(NO_LEVEL, None)
            for level in LEVELS:
                combo.addItem(LEVEL_LABELS[level], level)
            self.levels.append(combo)
        self.by_media_type = QCheckBox("Put photos and videos in separate folders")
        self.example = QLabel()
        self.on_exist = QComboBox()
        for value, label in ON_EXIST:
            self.on_exist.addItem(label, value)

        self.jpeg_only = QCheckBox(JPEG_ONLY)
        self.jpeg_hint = QLabel(JPEG_HINT)
        self.jpeg_hint.setWordWrap(True)
        self.manifest = QCheckBox("Write a list of the exported files (CSV)")
        self.portable_db = QCheckBox("Also write a catalog the app can open there")

        self.command_toggle = QToolButton()
        self.command_toggle.setText(COMMAND)
        self.command_toggle.setCheckable(True)
        self.command_toggle.setAutoRaise(True)
        self.command_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.command_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(90)
        self.preview.setVisible(False)
        self.problem = QLabel()  # why Run is off, with the command collapsed
        self.problem.setWordWrap(True)
        self.dry_run = QCheckBox("Dry run (plan only, copy nothing)")
        self.run_button = QPushButton("Run export")

        self._build_layout()
        self._load_settings()
        if not exiftool_available():
            self.jpeg_only.setChecked(False)
            self.jpeg_only.setEnabled(False)
            self.jpeg_hint.setText(NO_EXIFTOOL)

        self.filters.changed.connect(lambda _f: self._recount())
        self.use_library.clicked.connect(self.use_library_filters)
        self.browse_button.clicked.connect(self._browse)
        self.open_button.clicked.connect(self._open_dest)
        self.dest.textChanged.connect(lambda _t: self._update())
        self.folder.textChanged.connect(lambda _t: self._update())
        self._clock.timeout.connect(self._update)
        self._clock.start()
        for widget in (self.sort_radio, self.keep_radio, self.by_media_type):
            widget.toggled.connect(lambda _on: self._update())
        for box in (self.jpeg_only, self.manifest, self.portable_db, self.dry_run):
            box.toggled.connect(lambda _on: self._update())
        for combo in (*self.levels, self.on_exist):
            combo.currentIndexChanged.connect(lambda _i: self._update())
        self.run_button.clicked.connect(self._run)
        self.command_toggle.toggled.connect(self._show_command)
        self._counter.finished.connect(self._set_summary)
        self._counter.failed.connect(lambda _e: self._set_summary(None))
        ctx.gallery_changed.connect(lambda _g: self._recount())
        ctx.catalog_updated.connect(self._recount)
        if ctx.jobs is not None:
            ctx.jobs.started.connect(lambda _s: self._update())
            ctx.jobs.finished.connect(lambda _r: self._update())
        self._recount()

    def _build_layout(self) -> None:
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.use_library)
        left.addWidget(self.filters, 1)

        dest_box = QGroupBox("Destination")
        dest_layout = QVBoxLayout(dest_box)
        dest_row = QHBoxLayout()
        dest_row.addWidget(self.dest, 1)
        dest_row.addWidget(self.browse_button)
        dest_row.addWidget(self.open_button)
        dest_layout.addLayout(dest_row)
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder name"))
        folder_row.addWidget(self.folder, 1)
        dest_layout.addLayout(folder_row)

        folders = QGroupBox("Folders")
        folders_layout = QVBoxLayout(folders)
        levels_row = QHBoxLayout()
        levels_row.addWidget(self.sort_radio)
        for combo in self.levels:
            levels_row.addWidget(combo)
        levels_row.addStretch(1)
        folders_layout.addLayout(levels_row)
        folders_layout.addWidget(self.by_media_type)
        folders_layout.addWidget(self.keep_radio)
        folders_layout.addWidget(self.example)
        exists_row = QHBoxLayout()
        exists_row.addWidget(QLabel("If a file is already there"))
        exists_row.addWidget(self.on_exist)
        exists_row.addStretch(1)
        folders_layout.addLayout(exists_row)

        format_box = QGroupBox("Format")
        format_layout = QVBoxLayout(format_box)
        format_layout.addWidget(self.jpeg_only)
        format_layout.addWidget(self.jpeg_hint)

        extras = QGroupBox("Extras")
        extras_layout = QVBoxLayout(extras)
        extras_layout.addWidget(self.manifest)
        extras_layout.addWidget(self.portable_db)

        form = QWidget()
        right = QVBoxLayout(form)
        right.addWidget(self.source)
        right.addWidget(self.summary)
        right.addWidget(dest_box)
        right.addWidget(folders)
        right.addWidget(format_box)
        right.addWidget(extras)
        right.addWidget(self.command_toggle)
        right.addWidget(self.preview)
        right.addWidget(self.dry_run)
        right.addWidget(self.problem)
        right.addWidget(self.run_button)
        right.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)

        layout = QHBoxLayout(self)
        layout.addLayout(left)
        layout.addWidget(scroll, 1)

    # ── settings ────────────────────────────────────────────────────────────
    def settings(self) -> ExportSettings:
        structure: list[str] = []
        for combo in self.levels:
            level = combo.currentData()
            if level and level not in structure:
                structure.append(level)
        return ExportSettings(
            dest=self.dest.text(),
            folder=self.folder.text(),
            keep_folders=self.keep_radio.isChecked(),
            structure=structure,
            by_media_type=self.by_media_type.isChecked(),
            on_exist=self.on_exist.currentData(),
            jpeg_only=self.jpeg_only.isChecked(),
            manifest=self.manifest.isChecked(),
            portable_db=self.portable_db.isChecked(),
            dry_run=self.dry_run.isChecked(),
        )

    def set_settings(self, s: ExportSettings) -> None:
        self.dest.setText(s.dest)
        self.folder.setText(s.folder)
        (self.keep_radio if s.keep_folders else self.sort_radio).setChecked(True)
        for i, combo in enumerate(self.levels):
            level = s.structure[i] if i < len(s.structure) else None
            combo.setCurrentIndex(max(0, combo.findData(level)))
        self.by_media_type.setChecked(s.by_media_type)
        self.on_exist.setCurrentIndex(max(0, self.on_exist.findData(s.on_exist)))
        self.jpeg_only.setChecked(s.jpeg_only and self.jpeg_only.isEnabled())
        self.manifest.setChecked(s.manifest)
        self.portable_db.setChecked(s.portable_db)
        self.dry_run.setChecked(s.dry_run)
        self._update()

    def _state_path(self) -> Path:
        return app_data_dir() / STATE_FILE

    def _load_settings(self) -> None:
        data = load_json(self._state_path())
        settings = ExportSettings()
        for name in vars(settings):
            if name not in NOT_SAVED and name in data:
                if isinstance(data[name], type(getattr(settings, name))):
                    setattr(settings, name, data[name])
        settings.structure = [lv for lv in settings.structure if lv in LEVELS]
        self.set_settings(settings)

    def _save_settings(self) -> None:
        data = vars(self.settings()).copy()
        for name in NOT_SAVED:
            data.pop(name)
        try:
            save_json(self._state_path(), data)
        except OSError:
            pass  # remembering the choices is a convenience

    # ── filters and summary ─────────────────────────────────────────────────
    def use_library_filters(self) -> None:
        self.filters.set_values(from_filter_options(self.ctx.filters))
        self.filters.apply()

    def _recount(self, *_args) -> None:
        self._summary = None
        gallery = self.ctx.current
        if gallery is None or not is_connected(gallery) or not self.filters.valid:
            self._counter.cancel()
        else:
            options = self.filters.options
            self._counter.run(lambda repo: export_summary(repo, options))
        self._update()

    def _set_summary(self, summary: ExportSummary | None) -> None:
        self._summary = summary
        self._update()

    def _summary_text(self, settings: ExportSettings) -> str:
        if not self.filters.valid:
            return FIX_FILTERS
        s = self._summary
        if s is None:
            return COUNTING if self.ctx.current is not None else ""
        if settings.jpeg_only:
            return MATCHED_JPEG.format(
                n=s.matched, as_is=s.as_is, raws=s.raws, left_out=s.left_out
            )
        return MATCHED.format(n=s.matched, size=format_bytes(s.size_bytes))

    # ── state ───────────────────────────────────────────────────────────────
    def _update(self) -> None:
        gallery = self.ctx.current
        if gallery is None:
            self.source.setText(NO_GALLERY)
        elif not is_connected(gallery):
            self.source.setText(DISCONNECTED.format(label=gallery.label))
        else:
            self.source.setText(FROM.format(label=gallery.label))
        settings = self.settings()
        self.folder.setPlaceholderText(default_folder_name())
        self.summary.setText(self._summary_text(settings))
        for combo in self.levels:
            combo.setEnabled(not settings.keep_folders)
        self.by_media_type.setEnabled(not settings.keep_folders)
        self.example.setText(EXAMPLE.format(path=example_path(settings)))
        self.open_button.setEnabled(Path(settings.dest.strip() or ".").is_dir())
        try:
            spec = build_export_job(gallery, settings, self.filters.options)
        except ValueError as error:
            self.preview.setPlainText(str(error))
            self.problem.setText(str(error))
            self.problem.setVisible(True)
            self.run_button.setEnabled(False)
            return
        self.preview.setPlainText(format_command(list(spec.argvs[0])))
        self.problem.setVisible(False)
        jobs = self.ctx.jobs
        ready = self.filters.valid and jobs is not None and not jobs.running
        self.run_button.setEnabled(ready)

    def _show_command(self, shown: bool) -> None:
        self.preview.setVisible(shown)
        arrow = Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow
        self.command_toggle.setArrowType(arrow)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the destination folder", self.dest.text()
        )
        if chosen:
            self.dest.setText(QDir.toNativeSeparators(chosen))

    def _open_dest(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.dest.text().strip()))

    def _run(self) -> None:
        self.filters.apply()  # an edit still waiting out the pause counts
        if self.ctx.jobs is None or not self.filters.valid:
            return
        try:
            spec = build_export_job(
                self.ctx.current, self.settings(), self.filters.options
            )
        except ValueError as error:
            self.preview.setPlainText(str(error))
            return
        self._save_settings()
        self.ctx.jobs.start(spec)
