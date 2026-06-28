# Smart Gallery

A DB-centric photo & video catalog. Each managed drive carries a single SQLite
database describing every media file on it. Imports update the catalog
incrementally, `sync` reconciles it with the live drive, and `export` copies
filtered subsets elsewhere — all without re-scanning the whole drive.

## Requirements

- Python ≥ 3.13
- [ExifTool](https://exiftool.org/) on `PATH` (the metadata engine)
- Managed with [uv](https://docs.astral.sh/uv/)

```bash
uv sync
```

## Concepts

- A **drive root** is any directory you point at (`E:/` in normal use). Its
  catalog lives at `<root>/.smart_gallery/gallery.db`.
- The catalog is a single SQLite table `media_items` (one row per file), keyed by
  the file's path relative to the drive root. Change-detection uses size + mtime.
- `MediaItem` (`smart_gallery/models.py`) is the single source of truth for the
  schema and the ExifTool→column mapping.

## CLI guide

The installed entry point is `smart-gallery` (run it via `uv run smart-gallery …`
inside this repo, or just `smart-gallery …` once the package is on your `PATH`).

```
smart-gallery <command> [options]

Commands:
  init        Create a catalog on a drive and scan it.
  import      Copy media into the drive and update the catalog.
  sync        Reconcile the catalog with the live drive.
  export      Copy a filtered subset out to another directory.
  dashboard   Launch the Streamlit dashboard on a catalog.
  report      Write an Excel report (and optional figures).
```

A `<drive>` argument is any directory you treat as a root — `E:/`, a subfolder,
or a direct path to a `gallery.db`. The catalog always resolves to
`<drive>/.smart_gallery/gallery.db`.

### Shared filter flags

`import`, `export` and `report` accept the same query flags. Combine freely; all
active flags are AND-ed together. Any photo-only flag (camera/lens/aperture/iso/
shutter) implicitly restricts the selection to images.

| Flag | Type | Meaning |
|------|------|---------|
| `--file-types {image,video,other} …` | list | Keep only these media types. |
| `--extensions .jpg .mp4 …` | list | Keep only these extensions (a leading `.` is added if you omit it). |
| `--date-start YYYY-MM-DD` | date | Taken on or after this date. |
| `--date-end YYYY-MM-DD` | date | Taken on or before this date. |
| `--cameras "Canon EOS R6" …` | list | Match these camera models (exact, case-sensitive). |
| `--lenses "RF24-70mm" …` | list | Match these lens models. |
| `--min-aperture` / `--max-aperture` | float | Aperture (f-number) range. |
| `--min-iso` / `--max-iso` | int | ISO range. |
| `--min-shutter-speed` / `--max-shutter-speed` | str | Shutter range, e.g. `1/250` or `2s`. |

Rows missing the relevant value are excluded by a range or set filter (e.g. a
video has no aperture, so `--min-aperture 4` drops it).

### `init` — create and populate a catalog

```bash
smart-gallery init <drive> [--label NAME] [--hashing] [--overwrite]
```

| Option | Meaning |
|--------|---------|
| `<drive>` | Drive/root to catalog, e.g. `E:/`. |
| `--label NAME` | Human label stored in the catalog's metadata. |
| `--hashing` | Record a content-hash column (reserved for future dedup; off by default). |
| `--overwrite` | Re-create the catalog if one already exists (otherwise the command errors). |

Walks the whole drive and indexes every file. Run this **once per drive** before
importing. Example:

```bash
smart-gallery init E:/ --label Archive
```

### `import` — copy media in and update the catalog

```bash
smart-gallery import <sources…> --to <drive> [--output DIR]
                [--structure Year Month Model Lens] [--by-media-type | --no-by-media-type]
                [--on-exist rename|skip] [--dry-run] [filter flags]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `<sources…>` | — | One or more source files/folders (SD card, dump folder). |
| `--to <drive>` | required | Target drive whose catalog gets updated. |
| `--output DIR` | drive root | Subfolder on the drive to place files under (e.g. `E:/Photos/Canon`). |
| `--structure …` | `Year Month` | Folder hierarchy, any of `Year Month Model Lens`. |
| `--by-media-type` / `--no-by-media-type` | on | Put images under `Photos/` and videos under `Videos/`. |
| `--on-exist rename\|skip` | `rename` | On a name clash: append `_1, _2…` or skip the copy. |
| `--dry-run` | off | Plan the placement without copying or writing to the DB. |

Only the imported files are analyzed (ExifTool runs once, over just them), then
inserted into the catalog. Example — pull JPG/CR3 photos off an SD card:

```bash
smart-gallery import F:/ --to E:/ --output E:/Photos/Canon \
    --extensions .cr3 .jpg --structure Year Month --on-exist skip
```

### `sync` — reconcile the catalog with the drive

```bash
smart-gallery sync <drive> [--dry-run]
```

| Option | Meaning |
|--------|---------|
| `<drive>` | Drive to reconcile. |
| `--dry-run` | Report the add/update/delete plan without changing the DB. |

Does a cheap path-and-stat walk, diffs it against the catalog, then **prunes rows
whose files are gone** and **analyzes only new or changed files** (detected by
size + mtime). Unchanged files are never re-read. Prints a summary like
`+12 ~3 -5 =40100` (added / updated / deleted / unchanged).

### `export` — copy a filtered subset elsewhere

```bash
smart-gallery export --from <drive> --to <dir>
                [--structure Year Month Model Lens | --mirror]
                [--no-by-media-type] [--on-exist rename|skip]
                [--no-manifest] [--portable-db] [--dry-run] [filter flags]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--from <drive>` | required | Source drive (opened read-only). |
| `--to <dir>` | required | Destination directory. |
| `--structure …` | `Year Month` | Reorganize exports into this folder hierarchy. |
| `--mirror` | off | Preserve each file's path relative to the source drive instead of reorganizing. |
| `--no-by-media-type` | (media split on) | Don't split into `Photos/`/`Videos/`. |
| `--on-exist rename\|skip` | `rename` | Clash handling at the destination. |
| `--no-manifest` | (manifest on) | Skip writing `_smart_gallery_manifest.csv`. |
| `--portable-db` | off | Also write a self-contained catalog at the destination (browsable/dashboardable on its own). |
| `--dry-run` | off | List what would be copied without copying. |

Reads metadata from the catalog — the drive is **not** re-analyzed. Example —
export every Canon shot from 2026 into a dated tree:

```bash
smart-gallery export --from E:/ --to D:/Selects \
    --cameras "Canon EOS R6" --date-start 2026-01-01 --structure Year Month

# Or copy a subset preserving the drive's structure, plus a portable catalog:
smart-gallery export --from E:/ --to D:/Mirror --file-types image --mirror --portable-db
```

### `dashboard` — explore the catalog

```bash
smart-gallery dashboard <drive>
```

Launches Streamlit and opens it on the drive's catalog (read-only, queried
directly from SQLite). The view refreshes automatically after an import or sync.
You can also point the in-app sidebar at any other drive/`gallery.db`.

### `report` — Excel report (and optional figures)

```bash
smart-gallery report <drive> --to <file.xlsx> [--figures] [filter flags]
```

| Option | Meaning |
|--------|---------|
| `<drive>` | Drive to report on (opened read-only). |
| `--to <file.xlsx>` | Output workbook (3 sheets: images/videos/others, with clickable file links). |
| `--figures` | Also render static PNG charts into a `Figures/` folder next to the workbook (needs the `figures` extra: `uv sync --extra figures`). |

The filter flags scope the report, e.g. `--file-types image --date-start 2026-01-01`.

### Exit behavior

Commands exit non-zero on error (missing catalog, a catalog that already exists
on `init` without `--overwrite`, ExifTool not found, etc.) and log the reason.
Run `smart-gallery <command> --help` for the authoritative flag list.

## Per-device import recipes

`custom/import_canon.py` and `custom/import_phone.py` are thin declarative
configs over `custom/recipe.py`. Edit the paths/extensions and run:

```bash
python -m custom.import_canon
```

## Layout

```
smart_gallery/
  config.py        # path resolution, extensions, scan walk
  models.py        # MediaItem — schema source of truth
  analysis/        # ExifTool extraction -> MediaItem
  organize/        # FilterOptions + in-memory predicate + placement engine
  db/              # SQLite schema, repository, FilterOptions->SQL
  services/        # init, import, sync, export
  reporting/       # optional Excel report + figures
  dashboard/app.py # Streamlit, reads the DB directly
custom/            # per-device import recipes
tests/             # unit + integration (ExifTool-marked)
```
