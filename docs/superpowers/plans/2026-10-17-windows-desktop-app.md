# Smart Gallery Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an installable Windows desktop app (PySide6) with these
areas:
- **Library**: folder tree, filters, thumbnails, and a viewer.
- **Dashboard**: the Streamlit charts, ported.
- **Import**: import profiles across several galleries.
- **Actions**: auto-generated forms for every CLI command.
- **History**: past jobs.
- **People**: a placeholder.

It sits on top of the existing services, and long commands run as
cancellable background CLI jobs.

**Architecture:** The UI layer is thin. It reads catalogs read-only through
`GalleryRepository`, and every write runs as a `smart-gallery` CLI
subprocess, started through `QProcess` with `SG_PROGRESS=json`.

The code under `smart_gallery/gui/` is split by whether it needs Qt:
- **No PySide6 import:** `gui/__init__.py`, every `model.py`/`store.py`, and
  `gui/thumbs/cache.py`. These are unit-tested without a display.
- **Qt:** `view.py`, `app.py`, `context.py`, `jobs.py`, and
  `gui/thumbs/loader.py`. These get `pytest-qt` smoke tests.

Areas register in one list, `gui/areas.py`.

**Tech Stack:** Python 3.13, PySide6 (+ QtWebEngine), Pillow, Plotly,
pandas, SQLite, pytest + pytest-qt, PyInstaller (one-folder), Inno Setup 6.

**Spec:** `docs/superpowers/specs/2026-10-17-windows-desktop-app-design.md`

## Global Constraints

- Python `>=3.13`. Format with Black and lint with Ruff, **only the files you
  touch**. The tree as a whole is not Ruff/Black clean.
- The core CLI must import cleanly without the `gui` extra. Never import
  PySide6/Pillow at module level outside `smart_gallery/gui/`, and never in
  `gui/__init__.py`, any `model.py`/`store.py`, or `gui/thumbs/cache.py`.
- The GUI never writes to a catalog directly. It opens catalogs with
  `GalleryRepository.open(..., read_only=True)` (no `migrate()`). Writes
  happen only through CLI jobs.
- One job at a time.
- Business logic stays in `services/`, `db/`, and `organize/`.
- No per-command hand-written forms. Forms come from `cli.build_parser()`
  via `smart_gallery/forms.py`.
- App data dir: `%LOCALAPPDATA%\SmartGallery\`, overridable with the
  `SG_APP_DATA` env var (tests set it to `tmp_path`). Subprocess working
  directory, `logs/`, `history.jsonl`, `galleries.json`, `profiles/`, and
  `jobs/` all live there.
- Thumbnail cache: `<root>/.smart_gallery/thumbs/` (already excluded by
  `IGNORE_DIRS`). Never add columns to `media_items` for UI data.
- Keep the `onnxruntime` CPU override in `pyproject.toml` intact.
- No face logic or face schema changes. People is a placeholder.
- Commit messages: short imperative subject, ending with the trailer lines:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01CBh1KuG73SgJga3f3W9KMs
  ```

## Review Focus

1. **Unplugged gallery drive** (at startup, or while that gallery is
   selected). Expected: it shows greyed out and "Drive not connected". It
   can't be selected for import, and nothing raises. Tests are in Tasks 5
   and 11.
2. **Paths with spaces or non-ASCII characters** (`E:\Photos\Other cameras`,
   `Mamá`). Expected: argv reaches the CLI intact, output decodes as UTF-8,
   and history round-trips them. Tests are in Tasks 6 and 14.
3. **A job writing while the Library reads the same catalog** (WAL).
   Expected: read-only queries keep working during the job, and areas
   refresh on `catalog_updated` after it. Test in Task 11.
4. **Unreadable or odd media**: a corrupt JPEG, a RAW with no embedded
   preview, a video, or a file deleted since the last sync. Expected: no
   thumbnail and a placeholder tile, cached as a failure so it isn't retried
   every scroll, with no exception escaping the worker. Test in Task 9.
5. **Folder names containing SQL `LIKE` wildcards** (`100_CANON`, `50%`), or
   differing only in case. Expected: the folder filter matches exactly that
   folder, case-insensitively, like `relpath`. Test in Task 10.

---

### Task 0: Commit the in-progress baseline

The spec's precondition. The working tree holds uncommitted TUI/progress
work that later tasks build on.

**Files:** everything currently modified or untracked except `.superpowers/`,
`.mcp.json`, and the two `*_PLAN.md` files in the repo root.

- [ ] **Step 1:** Add `.superpowers/` to `.gitignore`.
- [ ] **Step 2:** Run `uv run pytest -m "not exiftool and not faces"`.
  Expected: all pass. If something fails, stop and report it; don't fix
  unrelated work.
- [ ] **Step 3:** Commit, with the subject `Add progress reporting and
  argparse-generated TUI forms`. The commit covers:
  - `.gitignore`, `README.md`, `CLAUDE.md`
  - `smart_gallery/`: the modified files, plus `progress.py`, `tui_forms.py`,
    and `tui_progress.py`
  - `tests/`: the new test files

---

### Task 1: Move the form model to `smart_gallery/forms.py`

`tui_forms.py` is already toolkit-neutral. Rename it and make the hidden set
a parameter so the GUI can hide more commands.

**Files:**
- Move: `smart_gallery/tui_forms.py` → `smart_gallery/forms.py` (`git mv`)
- Move: `tests/unit/test_tui_forms.py` → `tests/unit/test_forms.py`
- Modify: `smart_gallery/tui.py` (import path), `CLAUDE.md` (mentions of
  `tui_forms.py`/`tui_forms.LABELS`)

**Interfaces:**
- Produces:
  - `forms.command_forms(parser, hidden: Collection[str] = HIDDEN_COMMANDS) -> dict[str, CommandForm]`
  - `forms.HIDDEN_COMMANDS = {"ui", "gui"}` (TUI)
  - `forms.GUI_HIDDEN_COMMANDS = {"ui", "gui", "dashboard"}`
  - Unchanged: `Field`, `Section`, `CommandForm`, `Row`, `BuildResult`,
    `field_rows`, `default_values`, `build_argv`, `format_command`,
    `split_values`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_forms.py`. Fix
  its imports to `smart_gallery.forms` and add:

```python
def test_gui_hides_launchers():
    forms = command_forms(build_parser(), hidden=GUI_HIDDEN_COMMANDS)
    assert not {"ui", "gui", "dashboard"} & forms.keys()

def test_every_visible_command_has_a_form():
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    primary = {c.dest for c in sub._choices_actions}
    assert command_forms(parser, hidden=GUI_HIDDEN_COMMANDS).keys() == primary - GUI_HIDDEN_COMMANDS
```

- [ ] **Step 2:** Run `uv run pytest tests/unit/test_forms.py -v`.
  Expected: FAIL (ImportError on `smart_gallery.forms`).
- [ ] **Step 3:** `git mv` both files, add the `hidden` parameter and the
  constants, and update the imports in `tui.py`, `tests/test_tui.py`, and
  `CLAUDE.md`.
- [ ] **Step 4:** Run `uv run pytest tests/unit tests/test_tui.py`.
  Expected: PASS.
- [ ] **Step 5:** Commit: `Move argparse form model to smart_gallery.forms`.

---

### Task 2: Cooperative cancellation in the CLI

**Files:**
- Modify: `smart_gallery/progress.py`, `smart_gallery/cli.py` (`main`)
- Test: `tests/unit/test_progress.py`, `tests/test_cli_progress.py`

**Interfaces:**
- Produces, in `progress.py`:
  - `CANCEL_ENV = "SG_CANCEL_FILE"`
  - `CANCELLED_EXIT = 130`
  - `class Cancelled(Exception)`
  - `class CancellableProgress`, `__init__(self, inner, stop_file: Path)`.
    `__call__(event)` raises `Cancelled` if `stop_file` exists, and
    otherwise forwards to `inner`. `close()` delegates.
  - `progress_reporter()` wraps its reporter in `CancellableProgress` when
    `CANCEL_ENV` is set.
- `cli.main` catches `Cancelled` (before the generic `Exception`), logs
  `Cancelled.` at WARNING, and exits `CANCELLED_EXIT`. The `Details:` line in
  `finally` must still print.

- [ ] **Step 1: Write the failing tests:**

```python
def test_cancellable_raises_once_stop_file_exists(tmp_path):
    seen = []
    cb = CancellableProgress(seen.append, tmp_path / "stop")
    cb(ProgressEvent("x", 1, 3))
    (tmp_path / "stop").touch()
    with pytest.raises(Cancelled):
        cb(ProgressEvent("x", 2, 3))
    assert [e.done for e in seen] == [1]

def test_reporter_is_cancellable_when_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv(CANCEL_ENV, str(tmp_path / "stop"))
    assert isinstance(progress_reporter(), CancellableProgress)
```

  In `tests/test_cli_progress.py`, add
  `test_main_exits_130_when_cancelled`. Monkeypatch
  `cli.sync_drive` to raise `Cancelled`, run `cli.main(["sync", str(drive)])`
  with the `repo` fixture, and assert `SystemExit.code == 130`.
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_progress.py tests/test_cli_progress.py -v`.
  Expected: FAIL (ImportError).
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add cooperative cancellation to CLI commands`.

---

### Task 3: Safe cancel in copy, init, and scan services

**Files:**
- Modify:
  - `smart_gallery/config.py` (`PARTIAL_SUFFIX`, `iter_files`)
  - `smart_gallery/organize/organize.py`
  - `smart_gallery/services/export.py`, `import_media.py`, `init_db.py`,
    `scan_faces.py`
- Test: `tests/test_import.py`, `tests/test_export.py`,
  `tests/test_progress_services.py`, `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `progress.Cancelled` (Task 2).
- Produces:
  - `config.PARTIAL_SUFFIX = ".sg-partial"`. `iter_files` skips files whose
    name ends with it.
  - `organize.copy_atomic(source: Path, destination: Path) -> None`. It
    `shutil.copy2`s to `destination.name + PARTIAL_SUFFIX` beside the
    destination, then `os.replace`s. On any exception it unlinks the partial
    file and re-raises. It replaces both `shutil.copy2` calls (organize
    `_place`, export `_export_one`).
  - `OrganizeReport.cancelled: bool = False`. `organize_items` catches
    `Cancelled` from the callback, sets it, and stops the loop.
  - `import_media`:
    - inserts `org.placed`, as now
    - then raises `Cancelled` if `org.cancelled`
  - `export_media`:
    - catches `Cancelled` in its loop
    - writes the manifest/portable db for what was placed
    - then re-raises
  - `init_drive`, on `Cancelled`:
    - closes the repo
    - deletes `gallery.db`, `gallery.db-wal`, and `gallery.db-shm`
    - re-raises

    A re-run then needs no `--overwrite`.
  - `scan_faces`: the trailing `if buffer: add_scan_results` moves into a
    `finally`, so scanned results are kept on cancel.

- [ ] **Step 1: Write the failing tests.** Use the existing
  monkeypatched-`analyze_paths` pattern from `tests/test_sync.py`.
  - `test_copy_atomic_leaves_no_partial_on_failure`: patch `shutil.copy2`
    to write half the bytes and raise `OSError`. Assert no destination file
    and no `*.sg-partial`.
  - `test_import_cancel_mid_copy_keeps_catalog_consistent`: 5 source
    files, with a callback that raises `Cancelled` on the 3rd COPYING event.
    Assert:
    - `Cancelled` propagates
    - exactly the copied files are in the catalog
    - no `*.sg-partial` exists

    Then re-run `import_media` with `on_exist="skip"`, without
    cancelling, and assert all 5 are in the catalog.
  - `test_export_cancel_writes_manifest_for_placed`: same shape for
    `export_media`.
  - `test_init_cancel_removes_catalog`: the callback raises on the first
    READING event. Assert `Cancelled`, `not resolve_db_path(drive).exists()`,
    and that a second `init_drive(drive)` succeeds without `overwrite`.
  - `test_iter_files_skips_partials`.
- [ ] **Step 2:** Run
  `uv run pytest tests/test_import.py tests/test_export.py tests/test_progress_services.py tests/unit/test_config.py -v`.
  Expected: the new tests FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the same command, then
  `uv run pytest -m "not exiftool and not faces"`. Expected: PASS.
- [ ] **Step 5:** Commit: `Make import, export and init safe to cancel`.

---

### Task 4: `gui` extra, `gui` command, and the window shell

**Files:**
- Modify: `pyproject.toml`, `smart_gallery/cli.py`, `README.md`,
  `CLAUDE.md`
- Create:
  - `smart_gallery/gui/__init__.py` (docstring only)
  - `gui/paths.py`, `gui/context.py`, `gui/areas.py`, `gui/app.py`
  - `gui/people/view.py`
  - `tests/gui/conftest.py`, `tests/gui/test_shell.py`

**Interfaces:**
- `pyproject.toml`:
  - extra `gui = ["PySide6>=6.8", "Pillow>=11"]`
  - dev group adds `"pytest-qt>=4.4"`
  - marker `gui: tests that need the optional 'gui' extra (PySide6)`
- `cli`: a `gui` subcommand ("Open the Windows desktop app.") whose handler
  lazy-imports `smart_gallery.gui.app.run`. Add it to `_HANDLERS`.
- `gui/paths.py` (no Qt):
  - `app_data_dir() -> Path`: `$SG_APP_DATA`, else
    `%LOCALAPPDATA%/SmartGallery`. Creates the directory.
  - `cli_command() -> list[str]`:
    - frozen: `[str(Path(sys.executable).with_name("smart-gallery.exe"))]`
    - otherwise: `[sys.executable, "-m", "smart_gallery.cli"]`
  - `is_frozen() -> bool`
  - `exiftool_available() -> bool`: `shutil.which(_get_exiftool_path())`,
    or the bundled file exists.
  - `faces_available() -> bool`: `importlib.util.find_spec("insightface")`
    is not None.
- `gui/context.py`, `class AppContext(QObject)`:
  - Signals:
    - `gallery_changed(object)`
    - `catalog_updated()`
    - `filters_changed(object)`
    - `navigate(str)`
    - `open_in_actions(object)`: payload is an argv list
  - Attributes: `galleries` (Task 5), `jobs` (Task 6), `thumbs` (Task 9),
    `current` (`Gallery | None`), `filters` (`FilterOptions`).
  - Methods:
    - `set_gallery(g)` and `set_filters(f)` emit the matching signals.
    - `open_repo()` is a context manager returning a read-only
      `GalleryRepository` for `current`. It raises `FileNotFoundError` if the
      drive isn't connected.

  Until Tasks 5, 6, and 9 land, those attributes may be `None`.
- `gui/areas.py`:
  - `@dataclass(frozen=True) class Area: id: str; label: str; icon: str; factory: Callable[[AppContext], QWidget]`
  - `AREAS: list[Area]`, in rail order: library, import, dashboard, people,
    actions, history.
  - Factories import their view module lazily. Each area not built yet uses
    `placeholder(label)`.
- `gui/app.py`:
  - `MainWindow(QMainWindow)`: the rail (`QListWidget`) on the left, with a
    slot at its top for the Galleries panel. A `QStackedWidget` builds each
    area lazily on first visit, and `ctx.navigate` switches the rail.
  - A banner across the top when `not exiftool_available()`: "ExifTool not
    found. Install it from https://exiftool.org to read metadata."
  - `run() -> None` creates the `QApplication`, sets the app name to
    "Smart Gallery", and shows the window.
- `gui/people/view.py`, `PeopleView(QWidget)`:
  - shows the text "Face naming arrives with the face redesign."
  - an "Open face commands" button emits `ctx.navigate("actions")`
  - a read-only list of `repo.list_persons()` (name or `Person <id>`, face
    count) when a gallery is open
- `tests/gui/conftest.py`:
  - `pytest.importorskip("PySide6")`
  - marks everything in the folder as `gui`
  - an autouse fixture setting `SG_APP_DATA` to `tmp_path`

- [ ] **Step 1: Write the failing tests:**
  - `tests/unit/test_cli_parser.py::test_gui_command_parses`.
  - `tests/unit/test_gui_paths.py`:
    - `test_app_data_dir_honors_env`
    - `test_cli_command_dev_mode`, which asserts
      `[sys.executable, "-m", "smart_gallery.cli"]`
  - `tests/unit/test_gui_paths.py::test_core_import_without_pyside`. In a
    subprocess, set `sys.modules["PySide6"] = None`, then import
    `smart_gallery.cli`, `smart_gallery.gui`, and `smart_gallery.gui.paths`.
    Expected: exit code 0.
  - `tests/gui/test_shell.py::test_window_lists_all_areas`, using `qtbot`:
    the rail labels equal `[a.label for a in AREAS]`, and selecting each one
    shows a widget without raising.
- [ ] **Step 2:** Run `uv sync --extra gui`, then
  `uv run pytest tests/unit/test_gui_paths.py tests/unit/test_cli_parser.py tests/gui -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it. Document `smart-gallery gui` in
  `README.md`'s command list, and add a short "Desktop app (`gui/`)" section
  to `CLAUDE.md` describing the no-Qt/Qt split and the area registry.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run
  `uv run smart-gallery gui` and check by eye that the window opens.
- [ ] **Step 5:** Commit: `Add desktop app shell and gui command`.

---

### Task 5: Galleries store and panel

**Files:**
- Create: `smart_gallery/gui/galleries/{__init__,store,view}.py`
- Test: `tests/unit/test_gallery_store.py`, `tests/gui/test_galleries_view.py`

**Interfaces:**
- `store.py` (no Qt):
  - `@dataclass(frozen=True) class Gallery: db_uuid: str; db_path: str; label: str`.
    It also has a property `root -> Path` (`drive_root_of(db_path)`).
  - `class GalleryStore(path: Path)`, persisted as JSON at
    `app_data_dir()/galleries.json`. Methods:
    - `all() -> list[Gallery]`
    - `add(target) -> Gallery`: opens `target` read-only and reads the
      `db_uuid` and `drive_label` meta (the label falls back to the root
      string). An existing `db_uuid` updates `db_path` instead of
      duplicating. This works for a drive remounted under a new letter.
    - `remove(db_uuid) -> None`
  - `is_connected(g) -> bool`: `Path(g.db_path).exists()`.
  - `locate(g, drives: Iterable[str]) -> Gallery | None`: tries
    `<drive>` + the path after `g.db_path`'s anchor, and returns the copy
    with the updated `db_path` whose `db_uuid` matches.
- `view.py`, `GalleriesPanel(QWidget)`, at the top of the rail:
  - a combo of galleries; disconnected ones are greyed out with
    " (not connected)"
  - selecting one calls `ctx.set_gallery`
  - "Add existing…" (folder dialog, then `store.add`)
  - "Create on drive…" (folder dialog, then a job: `JobSpec("init", (("init", path),))`,
    and once it finishes successfully, `store.add(path)`)
  - On startup, a disconnected gallery gets `locate` against the existing
    drive letters.
  - The last-selected `db_uuid` is remembered in `galleries.json`.

- [ ] **Step 1: Write the failing tests:**
  - `test_add_reads_uuid_and_label(repo)`
  - `test_add_same_catalog_twice_dedupes(repo)`
  - `test_remount_updates_path(tmp_path)`: copy the drive folder to a new
    location, `add` it, and assert one entry with the new `db_path`.
  - `test_disconnected_gallery_is_reported(tmp_path)`: delete the folder
    after `add`. Assert `is_connected` is False, and `all()` still lists it
    without raising (Review Focus 1).
  - GUI: `test_panel_greys_out_disconnected`.
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_gallery_store.py tests/gui/test_galleries_view.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it, and mount the panel in `MainWindow`'s rail
  slot. The "Create on drive…" button stays disabled until Task 6 provides
  `ctx.jobs`. Wire it in Task 6, Step 3.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add galleries list to the desktop app`.

---

### Task 6: Job runner, history store, and status bar

**Files:**
- Create: `smart_gallery/gui/history/store.py` (no Qt), `gui/jobs.py`,
  `gui/statusbar.py`, `tests/gui/fake_cli.py`
- Test: `tests/unit/test_history_store.py`, `tests/gui/test_jobs.py`

**Interfaces:**
- `history/store.py`:
  - `@dataclass class JobRecord`, with these fields:
    - `title: str`
    - `argvs: list[list[str]]`
    - `started_at: str` and `ended_at: str` (ISO)
    - `status: Literal["ok", "failed", "cancelled"]`
    - `exit_code: int | None`
    - `summary: str`
    - `log_path: str | None`
    - `output_tail: list[str]` (the last 200 lines)
  - `class HistoryStore(path: Path)`, stored at
    `app_data_dir()/history.jsonl`. Methods:
    - `append(record)`
    - `load() -> list[JobRecord]`, newest first. It skips lines that fail
      to parse.
- `jobs.py`:
  - `@dataclass(frozen=True) class JobSpec: title: str; argvs: tuple[tuple[str, ...], ...]; writes_catalog: bool = True`
  - `class JobRunner(QObject)`:
    - Constructor:
      `__init__(self, history: HistoryStore, workdir: Path, program: list[str] | None = None, grace_ms: int = 10_000)`.
      `program` defaults to `cli_command()`, and tests pass `fake_cli.py`.
    - Signals: `started(object)`, `step(int, int)` (1-based index and
      count), `progress(object)` (a `ProgressEvent`), `output(str)`,
      `finished(object)` (a `JobRecord`).
    - `running: bool`
    - `start(spec) -> bool`: False if a job is already running.
    - `cancel() -> None`
  - Process environment: `os.environ` plus `PYTHONIOENCODING=utf-8`,
    `SG_PROGRESS=json`, and `SG_CANCEL_FILE=<workdir>/jobs/<uuid>.stop`.
    The working directory is `workdir`, and stdout/stderr are merged.
  - Each line goes through `ProgressEvent.from_line`, giving a `progress`
    signal or an `output` signal.
  - `summary` is the last output line that doesn't start with `Details:`.
    `log_path` is parsed from the `Details: <path>` line the CLI prints.
  - A sequence runs its argvs in order and stops at the first non-zero exit.
    The status is:
    - `cancelled` if the exit was `CANCELLED_EXIT`, or a cancel was
      requested
    - `failed` for any other non-zero exit
    - `ok` otherwise
  - `cancel()`:
    1. Touches the stop file.
    2. Starts a `grace_ms` timer.
    3. When the timer fires, runs `taskkill /PID <pid> /T /F` on Windows
       (`QProcess.kill()` elsewhere), which also kills ExifTool children.

    No later step of the sequence starts.
  - On `finished`, the runner appends the record to history. If
    `writes_catalog` is set, `AppContext` emits `catalog_updated`.
- `statusbar.py`, `JobStatusBar(QWidget)`, along the window bottom:
  - the title
  - "step i of n" when n > 1
  - a `QProgressBar` (busy when `total == 0`)
  - outcomes rendered `copied 10 · skipped 2 · failed 0`
  - a Cancel button
  - after a job ends, "Last job: <status>" for 10 s
- `fake_cli.py` accepts these args:
  - `progress N` (N events, total N)
  - `echo TEXT`
  - `fail`, which exits 1
  - `wait`, which loops until `$SG_CANCEL_FILE` exists, then exits 130
  - `hang`, which ignores the stop file

  It prints `Details: <path>` last.

- [ ] **Step 1: Write the failing tests:**
  - `test_history_roundtrip_and_skips_corrupt_lines`: include a title and
    argv `"E:\\Photos\\Other cameras"` and `Mamá` (Review Focus 2).
  - `test_runner_emits_progress_and_records_ok(qtbot)`
  - `test_sequence_stops_at_first_failure(qtbot)`: argvs `progress 2`,
    `fail`, `progress 2`. Expected: two steps ran, status `failed`.
  - `test_cancel_cooperative(qtbot)`: `wait`, then `cancel()`. Expected:
    status `cancelled` within 5 s.
  - `test_cancel_kills_after_grace(qtbot)`: `hang` with `grace_ms=500`.
    Expected: status `cancelled`.
  - `test_second_start_rejected_while_running(qtbot)`
  - `test_unicode_argv_reaches_cli(qtbot)`: `echo Mamá` comes out of
    `output` unchanged.
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_history_store.py tests/gui/test_jobs.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it. Attach the runner and status bar in
  `MainWindow`, and enable "Create on drive…" from Task 5.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add background job runner with safe cancel`.

---

### Task 7: Actions area

**Files:**
- Create: `smart_gallery/gui/actions/{__init__,model,fields,view}.py`
- Test: `tests/unit/test_actions_model.py`, `tests/gui/test_actions_view.py`

**Interfaces:**
- `model.py` (no Qt):
  - `gui_forms() -> dict[str, CommandForm]`:
    - `command_forms(build_parser(), hidden=GUI_HIDDEN_COMMANDS)`
    - when `is_frozen()`, also removes the `figures` field from `report`
  - `initial_values(form, gallery_root: str | None) -> dict` is
    `default_values` with `drive` set to the root when the form has that
    field.
  - `values_from_argv(form, argv: list[str]) -> dict`: parses with
    `build_parser()` and converts the namespace into widget values (the same
    value types as `default_values`).
  - `unavailable_reason(command) -> str | None`. It returns "Face features
    aren't installed in this build" when `not faces_available()` and the
    command is in
    `{"scan-faces", "identify-faces", "cluster-faces", "split-person"}`.
- `fields.py`, `make_editor(field: Field) -> FieldEditor`. `FieldEditor` is
  a `QWidget` with `value()`, `set_value(v)`, and a `changed` signal. Values
  use the same types as `forms.default_values`. One widget per `Field.kind`:
  - `text`, `list`, `int`, `int_list`, `float`, `date`: `QLineEdit` with
    the TUI's `PLACEHOLDERS` text
  - `path`: line edit + "Browse…" (folder dialog). For `report.output` and
    `compare.output` it uses a save-file dialog instead.
  - `path_list`: line edit + "Add folder…", which appends a quoted path
  - `choice`, `mode`: `QComboBox`, with a blank entry for none/default
  - `multi`: a checkable list
  - `ordered`: a checkable list with Up/Down buttons
  - `switch`: `QCheckBox`
- `view.py`, `ActionsView(QWidget)`:
  - on the left, the command list in CLI order
  - on the right, the selected form:
    - one `QGroupBox` per `Section`, with `collapsed` sections folded
    - rows from `field_rows`, with a hint label under each
    - a live command preview (`format_command`)
    - per-field error text from `BuildResult.errors`/`missing`
    - an "Extra options" line edit
    - "Copy as CLI command" and "Run" buttons
  - Run calls `ctx.jobs.start(JobSpec(form.name, (tuple(result.argv),), writes_catalog=form.name not in READ_ONLY_COMMANDS))`,
    where `READ_ONLY_COMMANDS = {"export", "report", "people", "compare", "convert-raw"}`.
  - Run is disabled while a job runs or when `unavailable_reason` returns a
    reason (shown as a banner).
  - It handles `ctx.open_in_actions(argv)`: select the command, then
    `values_from_argv`.

- [ ] **Step 1: Write the failing tests:**
  - `test_values_from_argv_roundtrip`: for argvs `["sync", "E:/"]`, the
    Canon import argv from `custom/import_canon.ps1`, and
    `["export", "E:/", "D:/out", "--cameras", "Canon EOS R6", "--min-iso", "400"]`,
    check that `parse_args(build_argv(form, values_from_argv(form, argv)).argv)`
    equals `parse_args(argv)`.
  - `test_initial_values_prefills_drive`
  - `test_face_commands_unavailable_without_insightface` (monkeypatch
    `faces_available`)
  - GUI:
    - `test_every_field_kind_has_an_editor`: for every form and field,
      `make_editor(f).set_value(v)` followed by `.value() == v`, with
      `v = default_values(form)[f.key]`.
    - `test_run_starts_job_with_built_argv`, with a fake `ctx.jobs` that
      records `start` calls.
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_actions_model.py tests/gui/test_actions_view.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it and register the area.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add Actions area with generated command forms`.

---

### Task 8: History area

**Files:**
- Create: `smart_gallery/gui/history/view.py`
- Test: `tests/gui/test_history_view.py`

**Interfaces:**
- Consumes: `HistoryStore`, `JobRecord` (Task 6), `ctx.open_in_actions`
  (Task 4).
- `HistoryView(QWidget)`:
  - The list shows the newest job first, as `title`, start time, duration,
    `status`, and `summary`. It refreshes on `jobs.finished`.
  - Selecting a job shows the contents of `log_path` if that file exists,
    else `output_tail`.
  - "Run again":
    - a single argv emits `ctx.open_in_actions(argv)` and
      `ctx.navigate("actions")`
    - a sequence asks for confirmation (`QMessageBox`), then calls
      `ctx.jobs.start` with the same `JobSpec`

- [ ] **Step 1: Write the failing tests:**
  - `test_history_lists_newest_first`
  - `test_run_again_single_opens_actions`
  - `test_missing_log_falls_back_to_tail`
- [ ] **Step 2:** Run `uv run pytest tests/gui/test_history_view.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it and register the area.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add History area`.

---

### Task 9: Thumbnail cache and loader

**Files:**
- Create:
  - `smart_gallery/gui/thumbs/__init__.py`
  - `gui/thumbs/cache.py` (no Qt; imports Pillow lazily inside functions)
  - `gui/thumbs/loader.py`
- Test: `tests/unit/test_thumb_cache.py` (`pytest.importorskip("PIL")`),
  `tests/gui/test_thumb_loader.py`

**Interfaces:**
- `cache.py`:
  - `THUMB_EDGE = 256`
  - `thumb_path(root: Path, media_id: int, mtime_ns: int | None) -> Path`
    returns
    `root/.smart_gallery/thumbs/<media_id // 1000>/<media_id>-<mtime_ns or 0>.jpg`.
  - `ensure_thumbnail(root, media_id, mtime_ns, abspath: Path, ext: str, media_type: str) -> Path | None`:
    - It returns the cached path if one exists.
    - It returns None if `<same stem>.none` exists (a recorded failure).
    - Video: None, written as a `.none` marker.
    - RAW extensions (`.cr2 .cr3 .nef .arw .dng .raf .rw2 .orf`): it pipes
      ExifTool `-b -PreviewImage`, then `-JpgFromRaw`, then
      `-ThumbnailImage` (via `_get_exiftool_path()`) into Pillow.
    - Everything else opens directly in Pillow, with
      `draft("RGB", (THUMB_EDGE, THUMB_EDGE))` for JPEGs.
    - It applies `ImageOps.exif_transpose`, shrinks to fit `THUMB_EDGE`, and
      saves as JPEG at quality 85, writing a temp file and then
      `os.replace`.
    - On any failure it writes the `.none` marker and returns None. It
      never raises.
- `loader.py`, `ThumbnailLoader(QObject)`:
  - Signal: `ready(int, str)` (media id, plus the path, or "" for none).
  - `request(root: Path, media_id: int, mtime_ns: int | None, relpath: str, ext: str, media_type: str) -> None`.
    It resolves `abspath` with `config.to_abspath(relpath, root)`, dedupes
    in-flight ids, and runs `ensure_thumbnail` on a private `QThreadPool`
    with at most 4 threads.
  - `cancel_pending() -> None` clears queued requests. The Library calls it
    on scroll and folder change, so visible items win.
  - Set it as `ctx.thumbs`.

- [ ] **Step 1: Write the failing tests:**
  - `test_thumbnail_jpeg(copy_image, drive)`: use a JPEG from
    `tests/resources/images/`. Assert that the longest edge is 256 or less,
    and that the file sits under `thumbs/`.
  - `test_thumbnail_cached_second_call` (patch `PIL.Image.open` so a second
    call would fail)
  - `test_changed_mtime_regenerates`
  - `test_corrupt_file_records_failure_once` (Review Focus 4): write
    garbage bytes as `.jpg`. The first call returns None and creates
    `.none`; the second call returns None without opening the file.
  - `test_missing_file_returns_none`
  - `test_video_returns_none`
  - `@pytest.mark.exiftool test_raw_preview(copy_image, drive)` with a
    fixture CR3
  - GUI: `test_loader_emits_ready(qtbot)`
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_thumb_cache.py tests/gui/test_thumb_loader.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the same command, plus `-m exiftool` for the RAW test.
  Expected: PASS.
- [ ] **Step 5:** Commit: `Add on-drive thumbnail cache`.

---

### Task 10: Library query layer

**Files:**
- Create: `smart_gallery/gui/library/{__init__,model}.py`
- Modify: `smart_gallery/db/repository.py` (add `get_by_id`)
- Test: `tests/unit/test_library_model.py`, `tests/test_repository.py`

**Interfaces:**
- `GalleryRepository.get_by_id(media_id: int) -> Optional[MediaItem]`.
- `model.py` (no Qt):
  - `@dataclass(frozen=True) class FolderNode: name: str; path: str; count: int; children: tuple["FolderNode", ...]`.
    The root has `path == ""`, `count` is recursive, and children are sorted
    case-insensitively. Build it from one `SELECT relpath` query.
  - `folder_tree(repo) -> FolderNode`
  - `@dataclass(frozen=True) class LibraryQuery: folder: str = ""; recursive: bool = True; filters: FilterOptions | None = None; sort: Literal["date", "name", "size"] = "date"`
  - `@dataclass(frozen=True) class LibraryRow: id: int; relpath: str; media_type: str; ext: str; mtime_ns: int | None`
  - `folder_clause(folder: str, recursive: bool) -> tuple[str, list]`. It
    uses `relpath LIKE ? ESCAPE '\'` on `folder + "/%"`, with `\`, `%`, and
    `_` escaped. Non-recursive adds `relpath NOT LIKE <folder>/%/%`. The
    root folder non-recursive gives `relpath NOT LIKE '%/%'`.
  - `library_rows(repo, q) -> list[LibraryRow]`: `build_where(q.filters)`
    AND `folder_clause`.
  - `library_stats(repo, q) -> tuple[int, int]` (count, total bytes), with
    the same WHERE.

  The folder condition stays local to the Library; it isn't a
  `FilterOptions` field. (The spec leaves this to the builder; my call.)

- [ ] **Step 1: Write the failing tests:**
  - `test_rows_match_export_selection`: for each `FilterOptions` in
    `tests/unit/test_where_parity.py::QUERIES`, compare relpaths from
    `library_rows(repo, LibraryQuery(filters=q))` with
    `repo.query_relpaths(q)`.
  - `test_folder_tree_counts`: relpaths `a/x.jpg`, `a/b/y.jpg`, and
    `z.jpg`. Expected: root count 3, `a` count 2, `a/b` count 1.
  - `test_folder_recursive_vs_direct`
  - `test_folder_with_like_wildcards` (Review Focus 5): folders
    `DCIM/100_CANON` and `DCIM/100XCANON`. Selecting `DCIM/100_CANON`
    returns only its items, and so does `dcim/100_canon`.
  - `test_get_by_id` in `tests/test_repository.py`.
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_library_model.py tests/test_repository.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add library folder and filter queries`.

---

### Task 11: Filter panel and Library view

**Files:**
- Create:
  - `smart_gallery/gui/filters_model.py` (no Qt), `gui/filters_panel.py`
  - `gui/library/view.py`, `gui/library/viewer.py`
- Test: `tests/unit/test_filters_model.py`, `tests/gui/test_library_view.py`

**Interfaces:**
- `filters_model.py`:
  - `@dataclass class FilterValues`, with these fields:
    - `date_from: str = ""` and `date_to: str = ""`
    - `cameras: tuple[str, ...] = ()` and `lenses: tuple[str, ...] = ()`
    - `filetypes: tuple[str, ...] = ()` and `extensions: tuple[str, ...] = ()`
    - `min_aperture: str = ""` and `max_aperture: str = ""`
    - `min_iso: str = ""` and `max_iso: str = ""`
    - `min_shutter: str = ""` and `max_shutter: str = ""`
    - `people: tuple[str, ...] = ()`
  - `to_filter_options(v: FilterValues) -> tuple[FilterOptions | None, dict[str, str]]`.
    It returns the options and per-field errors. Blank values give `None`,
    so `is_query_empty` holds.
  - `filter_choices(repo) -> dict[str, list[str]]`. It is built from
    `distinct_values("camera" | "lens" | "ext")`, `date_bounds`, and
    `list_persons` names, and covers `people` only when the face tables have
    rows.
- `FilterPanel(QWidget)`:
  - edits a `FilterValues`
  - on Apply or a 300 ms debounce, calls `ctx.set_filters(...)`
  - reloads its choices on `gallery_changed` and `catalog_updated`
  - Dashboard reuses the same panel class, and both areas read
    `ctx.filters`.
- `LibraryView(QWidget)`, laid out left to right:
  1. a folder `QTreeView` from `folder_tree`, with an "Include subfolders"
     checkbox
  2. the `FilterPanel` (collapsible)
  3. the grid: a `QListView` in `IconMode`, with a
     `QAbstractListModel` over `list[LibraryRow]`. Its `DecorationRole`
     returns the cached pixmap, or a placeholder (a generic photo, or a
     video icon with the duration).

  It also has:
  - a header showing the `library_stats` count and size, plus a sort combo
  - requests to `ctx.thumbs.request` from the visible rows only, both on
    `rowsInserted` and on a scroll-debounce timer
  - queries that run on a `QThreadPool` worker, with results posted back
    to the UI thread
  - a reload on `gallery_changed`, `filters_changed`, and
    `catalog_updated`
  - when `ctx.open_repo()` raises `FileNotFoundError`: the message "Drive
    not connected. Plug in <label> to browse it.", and nothing raises
    (Review Focus 1)
- `viewer.py`, `MediaViewer(QDialog)`:
  - a large preview: Pillow at up to 2048 px for JPEG/PNG, otherwise the
    RAW preview path from `cache.py`, scaled to the window
  - a metadata form from `repo.get_by_id`
  - Previous/Next across the current rows, plus "Show in Explorer"
    (`explorer /select,<abspath>`)
  - It opens on double-click.

- [ ] **Step 1: Write the failing tests:**
  - `test_blank_values_are_empty_query`
  - `test_values_convert_to_filter_options`: a date range, cameras, ISO
    `100`–`1600`, and shutter `1/1000`–`2s` compare equal to the
    `FilterOptions` the CLI's `build_filter_query` gives for the matching
    flags.
  - `test_bad_iso_reports_error`
  - GUI:
    - `test_library_shows_rows_for_folder(qtbot, repo, make_item)`
    - `test_library_disconnected_drive_message(qtbot)`
    - `test_library_reads_while_writer_open(qtbot, repo, make_item)`
      (Review Focus 3). Hold an open read-write `GalleryRepository` with an
      uncommitted `BEGIN IMMEDIATE` transaction on the same catalog. Reload
      the Library and assert it lists the committed rows without an error.
      Then commit an `upsert_many` of a new item, emit `catalog_updated`,
      and assert the new row appears.
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_filters_model.py tests/gui/test_library_view.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it and register the area.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run
  `uv run smart-gallery gui` on a real catalog (E:/). Check by eye:
  - the folder tree browses
  - thumbnails fill in while scrolling
  - the viewer opens
  - nothing freezes
- [ ] **Step 5:** Commit: `Add Library area with folder tree, filters and thumbnails`.

---

### Task 12: Shared dashboard figures

**Files:**
- Create: `smart_gallery/dashboard/figures.py`
- Modify: `smart_gallery/dashboard/app.py`, which keeps only Streamlit
  layout and its own sidebar filters and calls `figures.*`
- Test: `tests/unit/test_dashboard_figures.py`

**Interfaces** (all in `figures.py`, no Streamlit import):
- `load_catalog_df(db_path, filters: FilterOptions | None = None) -> pd.DataFrame`:
  `query_df` with `date_taken` parsed via `pd.to_datetime(errors="coerce")`.
- `split_media(df) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]`:
  images, videos, others.
- `metrics(fi, fv, fo) -> list[tuple[str, str]]`. The labels are exactly
  "Total Files", "Images (filtered)", "Videos", "Total Size", and "Video
  Length", and the values are formatted as in today's app (`f"{n:,}"`,
  `"{mb:,.0f} MB"`, `"{h}h {m}m {s}s"`).
- `file_type_donut(fi, fv, fo)`, `top_donut(df, column: str)`,
  `settings_histograms(fi)`, `timeline_by_camera(fi)`,
  `video_minutes(fv)`, and `photo_map(fi)`. Each returns a `go.Figure` or
  `None` when there's no data, and keeps today's colors (`STACKED_COLORS`)
  and layout values.
- `parse_shutter(s) -> float | None` (moved from `app.py`).

- [ ] **Step 1: Write the failing tests:**
  - `test_figures_none_on_empty` (every builder with empty frames)
  - `test_metrics_labels_and_values`, using 2 images of 1 MB each and a
    90 s video. Expected: "Total Files" is "3", and "Video Length" is
    "0h 1m 30s".
  - `test_parse_shutter`: `"1/250s"` gives 0.004, `"2"` gives 2.0, and
    `"x"` gives None.
  - `test_timeline_groups_top_cameras_into_other`, with 11 cameras. The
    trace names include "Other".
- [ ] **Step 2:** Run `uv run pytest tests/unit/test_dashboard_figures.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it, and switch `app.py` over to the new
  functions.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run
  `uv run smart-gallery dashboard E:/` and check by eye that the charts are
  unchanged.
- [ ] **Step 5:** Commit: `Extract dashboard figures into a shared module`.

---

### Task 13: Dashboard area

**Files:**
- Create: `smart_gallery/gui/dashboard/{__init__,model,view}.py`
- Test: `tests/unit/test_dashboard_page.py`, `tests/gui/test_dashboard_view.py`

**Interfaces:**
- `model.py` (no Qt):
  - `build_sections(df) -> list[tuple[str, go.Figure]]`, in today's order:
    1. File Types, Cameras, Lenses
    2. Camera Settings Distribution
    3. Photography Timeline
    4. Video Recording Summary
    5. Photo Map

    Sections whose figure is None are dropped.
  - `write_dashboard(out_dir: Path, metrics, sections) -> Path`:
    - writes `plotly.min.js` once, from `plotly.offline.get_plotlyjs()`,
      when it's missing
    - writes `index.html`, which has the metrics row and one
      `fig.to_html(full_html=False, include_plotlyjs=False)` div per section
    - references `<script src="plotly.min.js">`

    It loads from a file instead of `setHtml`, because `setHtml` is capped
    at 2 MB and plotly.js is bigger.
- `view.py`, `DashboardView(QWidget)`:
  - a `FilterPanel` (Task 11)
  - a `QWebEngineView` loading
    `QUrl.fromLocalFile(app_data_dir()/"dashboard"/"index.html")`
  - a `QTabWidget` with Images/Videos/Others tables (`QTableView` over a
    `DataFrameModel(QAbstractTableModel)`)
  - The data loads via `load_catalog_df(db_path, ctx.filters)` on a worker
    thread, and reloads on `gallery_changed`, `filters_changed`, and
    `catalog_updated`.
  - It shows a "Drive not connected" message like the Library's.

- [ ] **Step 1: Write the failing tests:**
  - `test_write_dashboard_inlines_one_div_per_section(tmp_path)`: the
    `plotly-graph-div` count equals the number of sections, and the
    `<script src="plotly.min.js">` tag appears once.
  - `test_plotly_js_written_once` (compare mtimes across two calls)
  - GUI: `test_dashboard_loads_for_repo(qtbot, repo, make_item)`
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_dashboard_page.py tests/gui/test_dashboard_view.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it and register the area.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then check by eye on
  E:/ that the charts render offline and that the filters change both the
  Library and the Dashboard.
- [ ] **Step 5:** Commit: `Add Dashboard area`.

---

### Task 14: Import profiles

**Files:**
- Create: `smart_gallery/import_profiles.py`
- Test: `tests/unit/test_import_profiles.py`

**Interfaces:**
- `@dataclass class ImportRule`, with these fields:
  - `file_type: Literal["image", "video"]`
  - `extensions: list[str]`
  - `output: str`: relative to the gallery root, POSIX, e.g. `"Photos/Canon"`
  - `options: dict[str, object]`: extra `import` form values keyed by
    `Field.key`, e.g. `{"by_media_type": False, "structure": ("Year", "Month"), "on_exist": "skip"}`
- `@dataclass class ImportProfile: name: str; rules: list[ImportRule]`
- `load_profiles(dirpath: Path) -> list[ImportProfile]`: sorted by name,
  skipping files that fail to parse.
- `save_profile(dirpath, profile) -> Path`: writes a slugged `.json`
  filename.
- `delete_profile(dirpath, name) -> None`
- `seed_starter_profiles(dirpath) -> None`: writes `starter_profiles()`
  only if the directory has no `.json` files.
- `starter_profiles() -> list[ImportProfile]`. These are "Canon SD card"
  and "Pixel dump", with rules equal to `custom/import_canon.ps1` and
  `import_phone.ps1`:
  - photos: extensions `.cr3 .jpg` into `Photos/Canon` or
    `Photos/Other cameras`
  - videos: extension `.mp4` into `Videos`
  - all rules: `by_media_type` False, `structure` Year Month, `on_exist`
    skip
- `expand(profile, source: str, gallery_roots: Sequence[str]) -> list[list[str]]`:
  - The order is for each rule, then for each root, matching the scripts.
  - It builds each argv with `forms.build_argv` on the `import` form. The
    values are:
    - `sources` = the quoted source
    - `drive` = the root
    - `output` = `str(PureWindowsPath(root) / output)`
    - `file_types` = `(file_type,)`
    - `extensions`
    - `**options`
  - It raises `ValueError` on any `BuildResult` error.

- [ ] **Step 1: Write the failing tests:**
  - `test_canon_profile_matches_script`: from
    `expand(canon, "F:\\", ["E:\\", "D:\\"])`, expect 4 argvs. In order,
    their parsed namespaces (via `build_parser().parse_args`) have
    `output`s `E:\Photos\Canon`, `D:\Photos\Canon`, `E:\Videos`, and
    `D:\Videos`; `file_types` `["image"]` ×2 then `["video"]` ×2;
    `by_media_type` False; `structure` `["Year", "Month"]`; and `on_exist`
    `"skip"`.
  - `test_source_and_output_with_spaces` (Review Focus 2): source
    `C:\Users\A\Pictures\Pixel 03.08.2026` and output
    `Photos/Other cameras` both survive `parse_args` intact.
  - `test_save_load_roundtrip_and_seed_only_once(tmp_path)`
  - `test_unknown_option_key_raises`
- [ ] **Step 2:** Run `uv run pytest tests/unit/test_import_profiles.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add import profiles replacing the import scripts`.

---

### Task 15: Import area

**Files:**
- Create: `smart_gallery/gui/imports/{__init__,model,view,editor}.py`
- Test: `tests/unit/test_imports_model.py`, `tests/gui/test_imports_view.py`

**Interfaces:**
- `model.py` (no Qt):
  - `list_removable_drives() -> list[str]`. On win32 it lists `A:\`–`Z:\`
    where `ctypes.windll.kernel32.GetDriveTypeW(d) == 2` (removable);
    elsewhere it returns `[]`.
  - `build_import_job(profile, source, galleries: Sequence[Gallery]) -> JobSpec`
    returns `JobSpec(f"Import · {profile.name}", tuple(map(tuple, expand(profile, source, [str(g.root) for g in galleries]))))`.
    It raises `ValueError` when `source` is empty, when there are no
    galleries, or when any gallery is disconnected.
- `view.py`, `ImportView(QWidget)`:
  - a source combo listing the removable drives, a "Browse folder…"
    button, and a Refresh button
  - a profile combo with New/Edit/Delete
  - target gallery checkboxes from `ctx.galleries`, where disconnected
    galleries are disabled with " (not connected)"
  - a read-only preview listing `format_command` for every argv
  - Run calls `ctx.jobs.start(build_import_job(...))`
  - On first show, it calls `seed_starter_profiles(app_data_dir()/"profiles")`.
- `editor.py`, `ProfileEditor(QDialog)`:
  - the profile name
  - a rule list with Add/Remove
  - per rule: `file_type`, `extensions`, `output`, and the remaining
    `import` form fields from `gui_forms()["import"]`, excluding `sources`,
    `drive`, `output`, `file_types`, `extensions`, and `dry_run`, rendered
    with `actions.fields.make_editor`
  - Only values that differ from the form defaults are stored in
    `options`. A new `import` option therefore shows up automatically.

- [ ] **Step 1: Write the failing tests:**
  - `test_build_import_job_rejects_disconnected_gallery`
  - `test_build_import_job_title_and_steps`
  - GUI:
    - `test_preview_lists_commands(qtbot)`
    - `test_disconnected_gallery_checkbox_disabled(qtbot)`
- [ ] **Step 2:** Run
  `uv run pytest tests/unit/test_imports_model.py tests/gui/test_imports_view.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it and register the area.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then by eye, run
  the "Canon SD card" profile with `dry_run` set as an extra option on a
  test folder.
- [ ] **Step 5:** Commit: `Add Import area with profiles and multiple targets`.

---

### Task 16: Packaging

**Files:**
- Create:
  - `packaging/entry_gui.py` (`from smart_gallery.gui.app import run; run()`)
  - `packaging/entry_cli.py` (`from smart_gallery.cli import main; main()`)
  - `packaging/smart_gallery.spec`, `packaging/installer.iss`,
    `packaging/build.ps1`
- Modify: `README.md` (an "Installing the desktop app" section),
  `.gitignore` (`build/`, `dist/`)

**Decisions:**
- **`smart_gallery.spec`**: two `Analysis` objects, merged into one
  `COLLECT` named `SmartGallery`:
  - `entry_gui.py` builds `SmartGallery.exe` with `console=False`
  - `entry_cli.py` builds `smart-gallery.exe` with `console=True`

  It collects:
  - `collect_all("PySide6")` for the WebEngine resources
  - plotly package data
  - `collect_all("insightface")`, `onnxruntime`, and the `nvidia` wheels'
    `bin/`/`lib/` DLLs as `binaries`

  It excludes `streamlit`, `textual`, `matplotlib`, `geopandas`, and
  `onnxruntime` (the CPU wheel).
- **InsightFace models** are not bundled; the build uses
  `~/.insightface/models`, where this PC already has them. This deviates
  from the spec ("bundle if present") and is flagged to the owner.
- **`installer.iss`**:
  - `DefaultDirName={localappdata}\Programs\SmartGallery`
  - `PrivilegesRequired=lowest`
  - a Start-menu shortcut to `SmartGallery.exe`
  - an uninstaller
  - `OutputBaseFilename=SmartGallery-Setup-{#Version}`, where the version
    comes from `pyproject.toml`
- **`build.ps1`**:
  1. `uv sync --extra faces --extra gui`
  2. `uv run pyinstaller packaging/smart_gallery.spec --noconfirm`
  3. `iscc packaging/installer.iss` (with the Inno Setup 6 path)
  4. Print the output path. Fail on any non-zero exit.

- [ ] **Step 1:** Write the entry scripts and the spec. Run
  `uv run pyinstaller packaging/smart_gallery.spec --noconfirm`. Expected:
  `dist/SmartGallery/` contains both exes.
- [ ] **Step 2:** Run `dist\SmartGallery\smart-gallery.exe --help` and
  `dist\SmartGallery\smart-gallery.exe sync E:\ --dry-run`. Expected: help
  text, and a sync report without errors.
- [ ] **Step 3:** Run `dist\SmartGallery\smart-gallery.exe scan-faces E:\ --limit 5`.
  Expected: the log line shows the CUDA provider.
  - If `preload_dlls()` can't find the CUDA/cuDNN DLLs, add
    `os.add_dll_directory` calls for the bundled `nvidia/*/bin` folders
    under `sys._MEIPASS` in `analysis/faces.py`, guarded by
    `getattr(sys, "frozen", False)`.
  - Re-run until the provider is CUDA.
- [ ] **Step 4:** Write `installer.iss` and `build.ps1`. Run
  `./packaging/build.ps1`. Expected:
  `dist/SmartGallery-Setup-0.1.0.exe`.
- [ ] **Step 5:** Install the setup exe and check by hand:
  1. The Start-menu shortcut opens the app.
  2. Add E:/.
  3. Browse the folder tree.
  4. Run Sync from Actions.
  5. Cancel a sync mid-run, and confirm no `*.sg-partial` files or
     catalog errors.
  6. Run the "Canon SD card" profile with dry-run against a test folder.
  7. The Dashboard renders with the network off.
  8. Uninstall.
- [ ] **Step 6:** Commit: `Add Windows packaging for the desktop app`.

---

## Self-review notes

- **Spec coverage:**

  | Spec item | Task |
  |---|---|
  | layout/rail | 4 |
  | Galleries | 5 |
  | Jobs/status bar/safe cancel | 2, 3, 6 |
  | History | 8 |
  | Actions + hidden commands + face availability | 1, 7 |
  | Library tree/filters/grid/viewer | 10, 11 |
  | Thumbnails | 9 |
  | Dashboard | 12, 13 |
  | Import profiles | 14, 15 |
  | People placeholder | 4 |
  | ExifTool banner | 4 |
  | Packaging | 16 |
  | WIP precondition | 0 |

- **Calls made here, within the spec's "left to the builder" list:**
  - Import has its own rail entry.
  - The folder condition is Library-local.
  - The viewer is a dialog.
  - Cooperative stop uses a stop file, checked in the progress callback.
    Commands without progress events (compare, report, identify, cluster)
    fall back to the kill after `grace_ms`. They don't copy files.
  - Galleries persist in `galleries.json` rather than `QSettings`, because
    the file is testable and sits in the same app-data folder.
