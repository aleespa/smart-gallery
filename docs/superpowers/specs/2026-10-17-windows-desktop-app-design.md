# Smart Gallery desktop app (Windows): design

Date: 2026-10-17
Status: draft for owner approval

## Intent

Smart Gallery organizes a mixed pile of photos and videos from many devices
into a gallery laid out the way the owner wants, using each file's metadata.
Each gallery (drive root) has a SQLite catalog of that metadata, which makes
filtered exports fast. On top of that the tool can detect faces and let the
owner name them. It can also compare two catalogs and extract JPEG previews
from RAW files. Once a catalog exists, a dashboard analyzes it.

Today that is spread across three tools: a CLI, a Textual TUI (`ui`) that runs
CLI commands, and a Streamlit dashboard (`dashboard`). The owner wants **one
installed Windows desktop app that contains everything**. It covers every
action and the dashboard analysis. New in the app, the owner can **preview
the photos** and **explore the folder structure** the gallery was organized
into.

It is for the owner only, on their own PC (RTX 5060, Windows 11). It installs
from a `.exe` like a normal program. ExifTool may be a separately installed
prerequisite.

The owner's own success test is that the app fails if **it is not easy to
implement new features**. Every structural choice below serves that first.

## Scope: two projects, in this order

1. **This document: the desktop app.** Everything except the face-naming
   experience. The People area is a placeholder.
2. **Later, separate design: face classification redesign + the People
   area.** The owner wants to rework the face pipeline, possibly including
   detection:
   - The program analyzes each photo, detects every face, and clusters them.
   - The owner names the clusters.
   - Alternatively, the owner picks a few photos of one person, the program
     finds which cluster that is, and the owner names it.

   That project also decides whether manual corrections must survive later
   automated runs. Today `identify-faces`/`cluster-faces` reassign
   unassigned faces, and `--rebuild`/`--rescan`/`split-person`/`sync` can drop
   names or faces. This app must not paint that project into a corner. It
   builds the area slot and the thumbnail infrastructure that face crops will
   reuse, and it adds no face logic.

## Goals

1. A desktop window with a left navigation rail and five areas: **Library,
   Dashboard, People (placeholder), Actions, History**. A **Galleries** panel
   at the top of the rail lists every catalog the owner has created or opened.
2. **Library**: browse the open gallery by **folder tree** (its on-disk
   layout, e.g. `E:/Photos/Canon/2026/09`) or by **filters**, as thumbnails,
   with a viewer for a single photo.
3. **Dashboard**: the existing Streamlit charts, ported into the app.
4. **Actions**: every CLI command runnable from a form auto-generated from
   `cli.build_parser()`, on the same principle as `tui_forms.py`.
5. **Import from a source**: the owner selects a source (an SD card, USB
   stick, or folder), a saved **import profile** (e.g. "Canon SD card"), and
   one or more **target galleries**. The app runs the whole chain of imports
   as one job, replacing `custom/import_canon.ps1`/`import_phone.ps1`.
6. **Background jobs**: commands run without blocking the UI. A status bar at
   the bottom shows the running job's progress and a Cancel button in every
   area. **History** lists past jobs and their logs.
7. Packaged as a Windows installer (`Setup.exe`) with a Start-menu shortcut.
8. Easy to extend:
   - A new CLI option or command appears in Actions with no UI code.
   - A new area is one self-contained module registered in one place.

## Non-goals

- The face-naming UI and any change to face detection, clustering, or
  identification (project 2).
- Distribution to other people, code signing, auto-update.
- Removing or changing the CLI, the TUI, or the Streamlit dashboard. They keep
  working from the dev environment as they do now.
- Wizards or step-by-step flows for individual commands. The owner chose plain
  auto-generated forms. Import profiles are saved form values, not a wizard.
- Detecting when a drive is plugged in. The owner selects the source.
- Photo editing, tagging/ratings, or writing metadata back to files.
- macOS/Linux packaging.

## Anti-goals (a failure even if it works)

- Business logic in the UI. Catalog, filter, import, and organize logic stays
  in `services/`, `db/`, and `organize/`, and the UI calls them.
- Hand-written per-command forms, or a second hand-maintained list of options
  that drifts from the CLI.
- A UI that freezes during long operations (sync, import, scan-faces).
- A Cancel that leaves the gallery or catalog broken (see Jobs).
- UI code that can't be tested: the view-model layer must be testable without
  a display.

## Decisions

### The owner's decisions

- A desktop app that runs locally and installs from a `.exe`. ExifTool may be
  a separate prerequisite.
- **Layout A, "Library-first"**: left rail with Galleries at the top, then
  Library / Dashboard / People / Actions / History, and a job status bar at
  the bottom.
- The Library shows thumbnails and supports exploring the folder structure.
- Actions: **one auto-generated form per command**.
- Importing: the owner **selects a source**, then the target galleries, as
  the import scripts do. There is no plug-in detection.
- Faces are a separate project after this one. People is a placeholder here.
- Ease of adding features is the top priority.

### The assistant's calls (the owner said "all fine"; open to revision)

- **Toolkit: Qt for Python (PySide6)**, plus `QtWebEngine` for charts.
  - It is all Python, which matches the codebase.
  - Virtualized item views handle ~50k thumbnails.
  - Plotly figures render unchanged in a `QWebEngineView`.
- **Packaging: PyInstaller one-folder build, wrapped by an Inno Setup
  installer.** One-file mode is rejected. With Qt WebEngine and the GPU face
  stack, it would unpack gigabytes to a temp folder on every launch.
- **The GPU face stack is bundled**, so the existing face commands work from
  Actions. The app must still start if CUDA/onnxruntime-gpu fails to load. In
  that case the face commands show as unavailable, with the reason.
- **Two executables in one install folder**: `SmartGallery.exe` (windowed GUI)
  and `smart-gallery.exe` (console, `smart_gallery.cli:main`).
  - Jobs launch the console exe as a subprocess. In a frozen build there is
    no `python -m smart_gallery.cli`.
  - In development (not frozen), the app uses `sys.executable -m
    smart_gallery.cli`, as the TUI does.
- **The bundled CLI only needs to serve the app's jobs.** These are hidden or
  disabled in the frozen build and stay available through `uv run`:
  - `dashboard` (Streamlit), `ui`/`tui` (Textual), and `gui`
  - `report --figures` (the `figures` extra isn't bundled)

  Freezing Streamlit and geopandas is fragile, and the app already replaces
  them.
- A new optional extra, `gui` (`PySide6`; `pytest-qt` in dev), and a new CLI
  command `gui` that launches the app from the dev environment. The core CLI
  must keep importing cleanly without the extra (the same lazy-import rule as
  `faces`/`figures`).
- **Safe cancel** (see Jobs) instead of killing the process mid-copy.
- **Precondition:** commit the uncommitted TUI/progress/form work first. That
  covers `tui_forms.py`, `progress.py`, `tui_progress.py`, the `cli.py`
  argument groups, and their tests. Step 1 builds on it.

## Architecture

### Package layout (proposed)

```
smart_gallery/
  forms.py              # extracted from tui_forms.py: toolkit-neutral
                        # CommandForm/Field/Section/build_argv (TUI keeps working)
  import_profiles.py    # ImportProfile model + load/save + expand to argv list
  gui/
    __init__.py
    app.py              # QApplication, main window, rail, status bar
    areas.py            # AREA registry: [(id, label, icon, factory)]
    context.py          # AppContext: galleries, current catalog, job runner,
                        # thumbnail cache, filter state, signals
    jobs.py             # JobRunner: QProcess, @sg-progress parsing, sequences,
                        # cancel, history persistence
    thumbs.py           # thumbnail cache + async loader
    filters_panel.py    # widget that edits a FilterOptions
    library/  dashboard/  people/  actions/  imports/  history/
                        # one package per area: view-model (no Qt widgets)
                        # + view (widgets)
```

**Extension seam.** An area is one package exposing a factory. The factory
receives the `AppContext` and returns a `QWidget`. Adding an area takes one
package plus one line in `areas.py`. Areas don't import each other. They
communicate through `AppContext` signals ("gallery changed", "catalog
updated", "job finished").

**View-model rule.** Each area is split in two:
- a plain-Python view-model that queries the repository, holds state, and
  exposes data and commands
- a thin Qt view

View-models are tested with plain pytest and no display. Views get a few
`pytest-qt` smoke tests.

### Shared form model (Actions)

`tui_forms.py` already turns argparse into `CommandForm`/`Field`/`Section`
and back into argv (`build_argv`). Move that into a toolkit-neutral
`forms.py` used by both the TUI and the GUI, so there is one source of truth.
The GUI maps each `Field.kind` to a Qt widget:
- path picker with Browse…
- choice combo
- ordered multi-choice (for `structure`)
- number, date, and switch
- radio group for mutually exclusive options

`test_every_field_has_a_hint` keeps covering both UIs.

Each form has two buttons: "Copy as CLI command" (`format_command`) and Run.
`drive` fields are pre-filled with the current gallery's root.
`HIDDEN_COMMANDS` grows per the packaging decision.

### Galleries

- The panel at the top of the rail lists every catalog the owner has created
  (via `init` from the app) or opened. It shows each one's label/root and item
  count. The list is remembered in `QSettings` under `%LOCALAPPDATA%`.
- A gallery whose drive isn't connected shows greyed out.
- "Add existing…" accepts a drive, folder, or `.db`, normalized by
  `config.resolve_db_path`. "Create on drive…" runs `init` as a job.
- Selecting a gallery makes it current for Library, Dashboard, and Actions.
- A gallery is identified by its `db_uuid` meta value, so a drive remounted
  under another letter is recognized as the same gallery.
- Browsing opens catalogs **read-only**, so the GUI never triggers `migrate()`
  behind the owner's back. Writes happen only through jobs.

### Library

- **Two ways to browse, side by side:**
  - **Folder tree**: the gallery's directory structure, built from
    `media_items.relpath`. Each node shows its item count. Selecting a folder
    shows its items, with an option to include subfolders.
  - **Filter panel**: edits a `FilterOptions` (date range, cameras, lenses,
    file type, extensions, aperture/ISO/shutter ranges, people). Pick-list
    values come from `repo.distinct_values` / `date_bounds` / `list_persons`.

  Folder and filters combine: a folder plus filters shows the matching items
  under that folder. A folder (relpath prefix) condition needs a new filter.
  It can live in the Library's own query (a `relpath LIKE prefix || '/%'`
  clause added next to `build_where`). Promoting it to a `FilterOptions` field
  would trigger the full filter-parity contract (`matches()`, `build_where`,
  `is_query_empty`, `_filter_parser`, `build_filter_query`, parity
  `QUERIES`). That would be worth it only if `export` should accept it too.
  The builder may do either. The query results must match what `export` with
  the same filters selects.
- **The grid**: a `QListView` in icon mode, backed by a model that holds only
  `(id, relpath, media_type, mtime_ns)` per row. Thumbnails load lazily for
  visible items. The header shows the item count and total size
  (`total_size_bytes`). Sort by date taken (the default), name, or size.
- **The viewer**: double-click opens a large preview, the metadata fields,
  previous/next navigation, and "Show in Explorer".

### Thumbnails

- The cache lives on the gallery drive at
  `<root>/.smart_gallery/thumbs/`. That folder is already excluded from walks
  by `IGNORE_DIRS`, so the cache travels with the catalog.
  - Key: media id plus `mtime_ns`, so a changed file regenerates.
  - Format: JPEG, longest edge ~256 px.
- Generated on a background thread pool, visible items first:
  - JPEG/PNG: Qt's image reader (or Pillow), using the embedded EXIF
    thumbnail when it is large enough.
  - RAW (CR2/CR3/…): the embedded preview via ExifTool `-b
    -PreviewImage`/`-JpgFromRaw`, the same approach as `convert-raw`. Use
    `rawpy` if it's present.
  - Video: a generic tile with the duration. There is no ffmpeg dependency
    in v1.
- The cache is derived data and stays out of `media_items`, consistent with
  the rule in CLAUDE.md. Its loader must be reusable later for face crops.

### Dashboard

- Move `dashboard/app.py`'s figure code into functions that take DataFrames
  and return Plotly figures, in a module the Streamlit app also imports. The
  two then stay identical, and the Streamlit app shrinks to layout code.
- The GUI renders each figure in a `QWebEngineView`, with plotly.js loaded
  from a bundled local file so it works offline. The photo map's tiles still
  need internet; without it the map background is blank.
- The content matches today's dashboard:
  - the metrics row
  - file-type, camera, and lens donuts
  - the settings histograms
  - the monthly timeline by camera
  - video minutes per month
  - the photo map
  - the data tables, as Qt table views
- The dashboard uses the **same filter panel and filter state as the
  Library**.

### People (placeholder)

A placeholder page that says face naming arrives with the face redesign. It
links to Actions for the existing face commands. It may show the current
`list_persons` as a read-only list. No face logic or schema change is part of
this project.

### Import from a source

This replaces the `custom/import_*.ps1` scripts.

- An **import profile** is saved settings for one kind of source. It holds:
  - a name (e.g. "Canon SD card", "Pixel dump")
  - a list of **rules**. Each rule has a file type (image or video),
    extensions, and a destination folder relative to the gallery root (e.g.
    `Photos/Canon`, `Videos`). Rules may also set structure,
    by-media-type, on-exist, and the other `import` options.
- **Running an import**:
  1. The owner selects a **source**: a drive or folder via a picker, with
     removable drives listed for convenience. There is no auto-detection.
  2. The owner selects a **profile**.
  3. The owner ticks one or more **target galleries** from the Galleries
     list. Only connected ones are selectable.
  4. Run.
- The profile expands to one `import` argv per (gallery × rule). This is
  the same chain the scripts run, e.g. `import F:\ --to E:\ --output
  E:\Photos\Canon --file-types image --extensions .cr3 .jpg
  --no-by-media-type --structure Year Month --on-exist skip`.
- The chain runs as **one job sequence**. It stops at the first failure, as
  the scripts do, and the status bar shows "step 2 of 4".
- The profile editor reuses the auto-generated `import` form fields for each
  rule, so a new `import` option is automatically available in profiles.
- Profiles are stored as JSON in `%LOCALAPPDATA%\SmartGallery\profiles\`.
  They are toolkit-neutral (`import_profiles.py`) and unit-tested. The two
  current scripts' configurations ship as starter profiles.

### Jobs

- `JobRunner` starts the CLI as a `QProcess` with `SG_PROGRESS=json`. It
  reads `@sg-progress {...}` lines into `ProgressEvent`s, reusing the TUI's
  parsing. Other output goes to the job's live log.
- A job is one argv or a **sequence** of argvs (import profiles).
- **One job at a time**, matching the TUI. This avoids concurrent writers on
  a catalog.
- **Safe cancel.** Cancel must never leave a truncated file or a half-written
  catalog:
  - **Cooperative stop.** The app signals the CLI to stop (for example with
    a stop file whose path is passed in an environment variable, polled via
    the progress callback between files). Services finish the current file
    and exit cleanly. `sync`'s single transaction and `scan-faces`'
    resumability already fit this.
  - **No partial copies.** `import` (organize) and `export` copy to a
    temporary name and rename when the copy completes. A killed copy then
    never looks like an existing file to a later `--on-exist skip`.
  - **`init` cancelled.** A half-created catalog is removed, or marked so a
    re-run doesn't need `--overwrite`.
  - **Last resort.** After a timeout, kill the whole process tree, including
    ExifTool children.
- Working directory: `%LOCALAPPDATA%\SmartGallery\`. Detailed logs
  (`logs/<command>-<timestamp>.log`) therefore land in a fixed place.
- History is a JSON-lines file in the same folder. Each entry records:
  - the command and its argv(s)
  - start and end times
  - the exit code
  - the last summary line
  - the log path
- When a job that writes to a catalog finishes, `AppContext` emits "catalog
  updated" and open areas refresh.

### History

A list of past jobs, newest first: command or profile, when, duration,
result, and the summary line. Selecting one shows its log. "Run again"
re-opens the Actions form, or the import, pre-filled.

## Packaging

- **`packaging/smart_gallery.spec`** (PyInstaller): two executables sharing
  one collected folder. It includes:
  - the Qt WebEngine resources
  - the local plotly.js
  - the face stack: onnxruntime-gpu, the DLLs from the nvidia cuDNN/CUDA
    wheels, and insightface

  It must keep the `onnxruntime` CPU exclusion (see `pyproject.toml` and
  `FACES.md`), and the `preload_dlls()` path in `analysis/faces.py` must find
  the bundled DLLs.
- **InsightFace model files** are bundled if present at build time.
  Otherwise the existing download-on-first-use behavior applies.
- **`packaging/installer.iss`** (Inno Setup) installs to
  `%LOCALAPPDATA%\Programs\SmartGallery` without admin rights. It adds a
  Start-menu shortcut and an uninstaller.
- **First launch** checks for ExifTool on PATH or a configured path. If it is
  missing, a banner shows the download link. Browsing existing galleries
  still works; only metadata-reading actions need ExifTool.
- **`packaging/build.ps1`** runs `uv sync --extra faces --extra gui`, then
  PyInstaller, then Inno Setup. The output is
  `dist/SmartGallery-Setup-<version>.exe`.

## Testing

- **View-models and `import_profiles.py`**: plain pytest with the existing
  fixtures (`repo`, `make_item`, `drive_file`).
- **Shared form model**: the `test_tui_forms.py` tests move or extend. One
  test asserts that every non-hidden CLI command produces a form.
- **Folder tree / folder filter**: tests that the folder query agrees with
  relpaths, and with `build_where` when combined with filters.
- **JobRunner**: tested against a tiny fake CLI script that emits
  `@sg-progress` lines, covering sequences that stop on failure and cancel.
- **Safe cancel**:
  - service tests that a stop request between files leaves no partial
    destination file
  - temp-then-rename in organize/export, with regression coverage per
    AGENTS.md
- **Thumbnail cache**: keying and invalidation, and the RAW fallback using
  `tests/resources/images/`. The ExifTool path is marked `exiftool`.
- **Views**: a few `pytest-qt` smoke tests (the window opens, areas switch,
  a form builds). They are marked `gui` and skipped when PySide6 is absent.
- **Packaging** is checked by hand: install, launch, add a gallery, browse
  the folder tree, run sync, and run an import profile.

## Build order

1. Commit the current work in progress, then extract the shared form model.
   The TUI stays unchanged and the tests stay green.
2. Window shell, area registry, Galleries panel, JobRunner + status bar,
   Actions, and History. This already replaces the TUI.
3. Safe cancel in the services (stop signal, temp-then-rename copies).
4. Thumbnail cache, then the Library (folder tree, filter panel, viewer).
5. Dashboard port (the shared figure module).
6. Import profiles and the "Import from a source" flow.
7. Packaging (spec, installer, build script).

Each step is usable on its own.

## Left to the builder

- Icons, colors, spacing, and light/dark theme (follow the Windows theme).
- Thumbnail size, thread counts, and cache eviction (none needed in v1).
- The widget for each `Field.kind`, as long as every kind is covered.
- Whether the viewer is a dialog or a panel inside the Library.
- Keyboard shortcuts.
- Whether the folder condition is Library-local or a `FilterOptions` field
  (see Library).
- The exact cooperative-stop mechanism, as long as it meets the safe-cancel
  rules.
- Where the import flow lives: its own rail entry, or an "Import from
  source" entry at the top of Actions.
