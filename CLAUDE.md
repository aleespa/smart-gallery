# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` holds the style, commit/PR, and security conventions, and
`README.md`/`FACES.md` are the user-facing CLI guides. Read them before changing
CLI flags or face behavior. This file covers commands and the architecture you
would otherwise have to piece together from several modules.

## Commands

```bash
uv sync                                   # core + dev deps (add --extra faces / --extra figures)
uv run pytest                             # full suite
uv run pytest tests/unit                  # fast unit tests
uv run pytest tests/unit/test_where_parity.py::test_sql_matches_inmemory   # one test
uv run pytest -m "not exiftool and not faces"   # skip tests that need ExifTool / the faces extra
uv run pytest -m exiftool                 # ExifTool integration (needs exiftool on PATH)
uv run ruff check <files>
uv run black <files>
uv run smart-gallery <command> --help
```

The repository as a whole is not currently Ruff/Black clean. Format and lint
the files you touch instead of reformatting the whole tree in an unrelated
change.

Standalone helpers in `custom/` are not part of the package. Run them directly,
for example `uv run custom/timelapse.py ...` or `./custom/import_canon.ps1`.

## Architecture

### The catalog is the product

Each managed "drive root" (any directory; `E:/` in practice) owns one SQLite
catalog at `<root>/.smart_gallery/gallery.db`. Commands take a drive, any path
on it, or a direct `.db` path, and `config.resolve_db_path` normalizes them.
`media_items.relpath` is a POSIX path relative to the root, unique, and
`COLLATE NOCASE`. The root is derived from where the DB file lives
(`config.drive_root_of`) instead of being stored, so a catalog survives being
remounted under another drive letter. Change detection uses `size_bytes` +
`mtime_ns` only.

All DB access goes through `db/repository.py:GalleryRepository`. Use it as a
context manager via `open(target, read_only=..., create=...)` or `create(...)`.
`_connect` enables `PRAGMA foreign_keys=ON`. Face cleanup depends on that
setting through FK cascades.

### Lock-step contracts enforced by tests

Several definitions exist in two places on purpose, and tests fail if they
drift apart:

- **Media schema.** The `models.MediaItem` fields and `models.DB_COLUMNS` must
  match `CREATE_MEDIA_ITEMS` in `db/schema.py`, in the same order
  (`tests/unit/test_models.py`). To add a media column, update the dataclass,
  `DB_COLUMNS`, the `CREATE TABLE`, and a `migrate()` step. If the value comes
  from EXIF, also update the mapping in `MediaItem.from_exiftool` and the
  ExifTool tag lists in `analysis/extract.py`.
- **Face schema.** `FACE_COLUMNS`/`PERSON_COLUMNS` in `models.py` must match
  `CREATE_FACES`/`CREATE_PERSONS` (`tests/unit/test_faces.py`).
- **Filter parity.** `organize/filters.py:matches()` (the in-memory predicate)
  and `db/where.py:build_where()` (the SQL compiler) must select the same rows
  (`tests/unit/test_where_parity.py`). Any new `FilterOptions` field must be
  implemented in both places and added to the parity `QUERIES`. It also needs
  an entry in `is_query_empty`, in `cli._filter_parser`, and in
  `cli.build_filter_query`. The exceptions are `people`/`person_ids`. They are
  SQL-only (they join `faces`/`persons`) and are deliberately absent from
  `matches()`.
- **Schema versioning.** `config.SCHEMA_VERSION` is the target version.
  `db/schema.py:migrate()` runs on every **read-write** open, so it must stay
  idempotent; add an `if current < N:` block for each new version. Read-only
  opens (export, report, people, compare, dashboard) never migrate.

### Why face data is never stored on `media_items`

The media upsert (`ON CONFLICT(relpath) DO UPDATE`) rewrites every media column
on each sync or import. Faces, persons, and scan bookkeeping are therefore kept
in separate tables (`faces`, `persons`, `face_scan_state`) keyed by
`media_items.id`:

- Deleting a media row cascades to its faces.
- Deleting a person sets `faces.person_id` to NULL.
- `sync` calls `invalidate_faces_by_relpath` for files whose size or mtime
  changed.

Keep new derived or ML data out of `media_items` for the same reason.

### Data flow per command

`cli.py` is a thin argparse layer. `_HANDLERS` maps each command name, aliases
included (`convert-cr3`→`convert-raw`, `tui`→`ui`), to a `_handle_*` function.
The handler opens a repository and calls a service from `services/`; those are
re-exported in `services/__init__.py`. Each service returns a `*Report`
dataclass. `main()` turns `FileNotFoundError`, `FileExistsError`, and
`ValueError` into a logged error and exit code 1.

Progress and logging:
- **Progress events.** Long-running services take a
  `progress_callback(ProgressEvent)` (`progress.py`). Each event carries a
  named step (Finding files / Reading metadata / Copying files / Scanning
  faces), `done`/`total`, and, for copy steps, copied/skipped/failed outcomes.
- **Where the events go.** `cli` picks a reporter with
  `progress_reporter()`. In a plain terminal that's a tqdm bar with counts.
  When the TUI runs a command it sets `SG_PROGRESS=json`, so events are
  written as `@sg-progress {...}` lines on stdout, and the TUI turns them
  into its progress panel (`tui_progress.py`).
- **Log levels.** `cli._configure_logging` logs INFO and above to the
  terminal. For the long-running commands in `DETAIL_LOG_COMMANDS`, it also
  writes everything (including per-file DEBUG lines such as "Copied: …") to
  `./logs/<command>-<timestamp>.log`.

- **Metadata extraction** (`analysis/extract.py:analyze_paths`) runs ExifTool in
  batch JSON mode (`-n`) across a thread pool. Batch size and worker count
  scale with file count and can be tuned with `SG_EXIFTOOL_MAX_WORKERS` /
  `SG_EXIFTOOL_BATCH_SIZE`. Non-media files are recorded from `stat` only.
  Passing `drive_root` anchors items (sets `relpath`) for files already on the
  drive.
- **init / sync** walk with `config.iter_files`, which skips `IGNORE_DIRS`,
  including the `.smart_gallery` folder itself. `sync` diffs a stat-only walk
  against `repo.index_for_sync()` and runs ExifTool only on added or changed
  files. It applies deletes and upserts in one transaction (`apply_sync`).
- **import** analyzes the *source* files with no `drive_root`, filters them in
  memory with `matches()` because they aren't in the DB yet, and places them
  with `organize/organize.py:organize_items`. That step only copies, never
  moves. `MediaItem.relocate` re-anchors each item to its destination before
  the insert.
- **export / report / dashboard** filter in SQL via `build_where` on a
  read-only repository and never re-analyze files. Export reuses the same
  placement helpers (`target_dir_for`, `_unique_destination`) or mirrors
  relpaths.
- **dashboard** runs `streamlit run dashboard/app.py` as a subprocess, passing
  the DB path in the `SG_DB_PATH` environment variable.
- **ui** (`tui.py`, Textual) runs commands as `python -m smart_gallery.cli ...`
  subprocesses. Its forms are generated from `cli.build_parser()` by
  `forms.py` (shared with the desktop app):
  - each argparse argument group becomes a form section
  - each option's `help=` text becomes its on-screen hint
  - an option's type, `choices`, and `nargs` pick the widget
  - store_true/store_false pairs and mutually exclusive groups each become one
    control

  So when you add or change a CLI option, put it in the right argument group
  and give it help text (`test_every_field_has_a_hint` enforces the help text).
  Only display labels live in `forms.LABELS`/`HINTS`.

### Optional extras are imported lazily

The core CLI must import cleanly without the extras:

- **`faces`**: `insightface`, `onnxruntime`, `cv2`, `rawpy`, `sklearn`, and
  `hdbscan` are imported inside `analysis/faces.py:FaceScanner` and inside the
  functions in `services/cluster_faces.py`.
- **`figures`**: imported inside the `report --figures` handler.

numpy is a core dependency and may be imported at module level.

### Desktop app (`gui/`)

`smart-gallery gui` opens a PySide6 window and needs the `gui` extra. The CLI
imports `smart_gallery.gui.app` lazily, so the core install never loads Qt.

- **No-Qt/Qt split.** `gui/paths.py` (app-data dir, how to launch the CLI,
  ExifTool/faces availability) imports nothing from Qt, and
  `test_core_import_without_pyside` enforces that. Everything else may import
  PySide6.
- **`AppContext`** (`gui/context.py`) is the signal hub every area shares:
  `gallery_changed`, `catalog_updated`, `filters_changed`, `navigate`,
  `open_in_actions`, `open_in_import`. It also holds the current gallery and filters, and
  `open_repo()` gives a read-only repository for the current gallery.
- **Area registry.** `gui/areas.py:AREAS` lists the areas in order. Each
  `Area.factory(ctx)` imports its view module lazily and returns a QWidget.
  `MainWindow` builds each area on its first visit and shows each as a tab in
  the top bar. Every page sits under a header that names it; a page that exposes a `header_controls`
  widget gets those controls on the header's right (Library's sort and
  filters).
- **No system frame.** `MainWindow` is frameless: its top bar is a
  `gui/titlebar.py:TitleBar` (drag to move, double-click to maximize) with
  `WindowButtons` at the right, and a `ResizeFrame` margin resizes it. Moving
  and resizing use `startSystemMove`/`startSystemResize`, so snapping works.
- **Theme** lives in `gui/theme.py`: Segoe UI Variable Text for controls,
  Georgia (`serif_font`) for headings only, and one accent (`ACCENT`, the
  first Sunset stop) set as the palette's Highlight/Accent so every
  selection, checked box and the open tab share it. Style sheets should use
  `palette(highlight)` rather than a hard-coded colour. The brand name
  (`gui/brand.py`) runs along the same `SUNSET` stops.
- **GUI tests** live in `tests/gui` (marker `gui`, headless via
  `QT_QPA_PLATFORM=offscreen`) and are skipped when PySide6 is missing.

### Face pipeline

1. **`scan-faces`** is resumable. Images already scanned at the current
   `config.FACE_DET_VERSION` are skipped; bump that constant when the model
   changes. Image decoding runs on a CPU thread pool and feeds one thread that
   owns the GPU.
2. **`file-faces`** (no GPU) runs `analysis/filing.py` (pure numpy; thresholds
   in `FilingConfig`) and applies its decisions: each face not decided by the
   user becomes `auto`, `suggested`, `cluster`, `hidden` or stays `new`.
   `find-faces` is scan then file in one job; the People page's "Find new
   faces" runs it.
3. **`label-faces`**, `name-person`, `merge-persons`, `split-person`,
   `hide-person` and `delete-person` are the user's corrections.
   `models.USER_STATES` (`user`, `ignored`) are locked: filing, `cluster-faces`,
   `identify-faces` and rescans never change them. Rejections live in
   `face_rejections`. `auto`/`cluster` assignments are sticky.
4. **Carry-over.** Before `invalidate_faces_by_relpath` (or a rescan) deletes a
   file's faces, user/ignored decisions and rejections go to `face_carryover`;
   the next scan copies them to the new face with box IoU >= 0.5. A photo that
   fails to decode keeps its faces and pending decisions.
5. **`identify-faces --samples`** (sample-directory `reference_centroid`s) is an
   optional shortcut. **`cluster-faces`** delegates to `file-faces`; `--rebuild`
   keeps named people and user faces, and the old clustering knobs are ignored.
Embeddings are 512-d, L2-normalized, and stored as float32 BLOBs via
`models.embedding_to_blob`/`blob_to_embedding`.

### Tests

`tests/conftest.py` provides these fixtures:

- `drive`: a temp directory used as the drive root
- `repo`: a fresh catalog on `drive`
- `make_item`: builds `MediaItem` objects with image defaults
- `drive_file` / `copy_image`: create real files on the drive

Workflow tests avoid ExifTool by monkeypatching `analyze_paths` in the service
module (see `tests/test_sync.py`). Only tests marked `exiftool` call the
binary, and the RAW/JPEG fixtures live in `tests/resources/images/`.
