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
  `tui_forms.py`:
  - each argparse argument group becomes a form section
  - each option's `help=` text becomes its on-screen hint
  - an option's type, `choices`, and `nargs` pick the widget
  - store_true/store_false pairs and mutually exclusive groups each become one
    control

  So when you add or change a CLI option, put it in the right argument group
  and give it help text (`test_every_field_has_a_hint` enforces the help text).
  Only display labels live in `tui_forms.LABELS`/`HINTS`.

### Optional extras are imported lazily

The core CLI must import cleanly without the extras:

- **`faces`**: `insightface`, `onnxruntime`, `cv2`, `rawpy`, `sklearn`, and
  `hdbscan` are imported inside `analysis/faces.py:FaceScanner` and inside the
  functions in `services/cluster_faces.py`.
- **`figures`**: imported inside the `report --figures` handler.

numpy is a core dependency and may be imported at module level.

### Face pipeline

1. **`scan-faces`** is resumable. Images already scanned at the current
   `config.FACE_DET_VERSION` are skipped; bump that constant when the model
   changes. Image decoding runs on a CPU thread pool and feeds one thread that
   owns the GPU.
2. **`identify-faces`** builds a per-person `reference_centroid` from the sample
   directories and assigns faces using a cosine threshold plus a margin over the
   runner-up.
3. **`cluster-faces`** (HDBSCAN/DBSCAN) is the optional unsupervised path.

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
