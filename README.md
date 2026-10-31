# Smart Gallery

A DB-centric photo & video catalog. Each managed drive carries a single SQLite
database describing every media file on it. Imports update the catalog
incrementally, `sync` reconciles it with the live drive, and `export` copies
filtered subsets elsewhere — all without re-scanning the whole drive.

New to Smart Gallery? Start with the **[User Guide](docs/USER_GUIDE.md)**, a
walk-through of the desktop app for first-time users. This README is the
command-line reference. To understand exactly what a command does to your files and
catalog, and what it reports, see the
**[Technical Reference](docs/TECHNICAL_REFERENCE.md)**.

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
  init          Create a catalog on a drive and scan it.
  import        Copy media into the drive and update the catalog.
  sync          Reconcile the catalog with the live drive.
  compare       Compare two gallery catalogs.
  export        Copy a filtered subset out to another directory.
  convert-raw   Extract embedded JPEG previews from CR2/CR3 files.
  recycle       Move listed catalog files to the Recycle Bin and drop them from the catalog.
  rename        Give a gallery a new name; it is stored in the catalog on the drive.
  dashboard     Launch the Streamlit dashboard on a catalog.
  ui            Open the interactive terminal interface.
  gui           Open the Windows desktop app.
  report        Write an Excel report (and optional figures).
  find-faces    Scan new images for faces, then sort them into people (GPU).
  file-faces    Sort scanned faces into people, suggestions and new groups (no GPU).
  label-faces   Confirm, reject, move or ignore faces.
  scan-faces    Detect & embed faces in every catalogued image (GPU).
  identify-faces Identify faces using person-named sample directories.
  cluster-faces Sort faces into groups (wrapper over file-faces).
  people        List detected people and their photo counts.
  name-person   Set (or clear) the name of a person cluster.
  hide-person   Hide an unnamed group of strangers, or show it again.
  merge-persons Merge several person clusters into one.
  split-person  Re-cluster one impure person into tighter sub-clusters.
  delete-person Delete a person cluster (its faces become unassigned).
```

> **Face recognition / group by person** (optional `faces` extra, GPU) — `find-faces`
> detects and sorts faces into people; name the groups and fix mistakes on the
> desktop People page or with `label-faces`, then filter with `--people NAME`
> (`--people-match any|all` for several). Sample directories with
> `identify-faces` are an optional shortcut.
> See **[FACES.md](FACES.md)** for the full guide.

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

### Keeping two drives in step — `pair`, `sync-mirror`, `free-up`, `bring-back`

Keep a **working drive** (where you edit, say `E:/`) and one or more **mirror
drives** (the stable copy, say `D:/`) identical. Sync only flows working →
mirror; nothing on a mirror is ever deleted outright.

```bash
smart-gallery pair E:/ D:/                  # once; both catalogs must exist
smart-gallery sync-mirror E:/ --dry-run --plan-out plan.json   # preview
smart-gallery sync-mirror E:/ --expect-plan plan.json           # apply just that
smart-gallery free-up E:/ --date-end 2019-12-31 --dry-run --plan-out free.json
smart-gallery free-up E:/ --date-end 2019-12-31 --expect-plan free.json
smart-gallery bring-back E:/ --date-end 2019-12-31
```

- **`pair <working> <mirror>`** links the two catalogs (matched by catalog id,
  so a new drive letter is fine). A working drive may have several mirrors; with
  more than one, pass `--mirror D:/` to `sync-mirror`, `free-up` and `bring-back`.
- **`sync-mirror <drive>`** refreshes both catalogs, then copies new and edited
  files to the mirror, moves files you deleted or renamed on the working drive
  into the mirror's trash, and carries face data across. Files seen only on the
  mirror, edited there or on both, or deleted on the working drive but edited
  on the mirror, are your call: `--only-on-mirror
  later|keep-on-mirror|copy-back`, `--changed-on-mirror
  skip|take-working|keep-mirror`, `--conflicts skip|take-working|keep-mirror`
  (all default to leaving them; for a file deleted on the working drive,
  `take-working` trashes the mirror's copy and `keep-mirror` copies it back).
  `--dry-run` previews, `--plan-out FILE` writes the plan as JSON (each class
  with its files, plus `copy_bytes` and `trash_bytes`). `--expect-plan FILE`
  applies only if nothing would be trashed, overwritten, moved or decided that
  this preview did not list; otherwise it changes nothing and exits 1. A sync
  that would trash more than 5% of the mirror needs `--confirm-large-delete`.
  It refuses when either drive is missing or its catalog does not match the
  pairing. If you put a different file at the path of a freed-up one, the plan
  lists it under `offloaded_conflict` and leaves it alone: move it elsewhere
  to have it synced.
- **`free-up <drive>`** deletes files from the working drive once the mirror
  holds a verified copy (same size, and the same hash read from both files).
  Takes the usual filter flags and `--folder`. The catalog rows stay, shown
  greyed out in the app, and are not offered to `scan-faces`. `--plan-out
  FILE` writes what would be freed (and what is skipped, and why) as JSON;
  `--expect-plan FILE` deletes only the files that preview listed.
- **`bring-back <drive>`** copies such files from the mirror back.
- **`import`** also copies into every reachable mirror, from the fresh working
  copy. If a mirror is absent the import still completes and the next
  `sync-mirror` finishes the job. `--no-mirror` skips mirrors for one import.
- The mirror's trash is `<mirror>/.smart_gallery/trash/<run>/…`; runs older than
  30 days are purged by the next sync.
- Faces are scanned on the working drive only. Face and person commands
  (`scan-faces`, `label-faces`, `merge-persons`, …) refuse on a mirror; its face
  data is a copy that each sync refreshes.

In the desktop app the Library header has a **Sync options** button with the same
actions (sync with a preview dialog that lists the files of each kind, free
up, bring back, pair). Each sync or free-up runs exactly what its preview
showed (`--expect-plan`).

### `compare` — compare two catalogs

```bash
smart-gallery compare <gallery-a> <gallery-b>
                     [--match-by path|name|hash]
                     [--format text|json|csv] [--output FILE] [--details]
```

Each gallery can be a drive root or a direct `gallery.db` path. The default
`path` mode matches catalog-relative paths; `name` matches unique filenames;
`hash` matches unique stored content hashes. Name or hash duplicates are
reported as ambiguous. Hash mode requires at least one stored hash in each
catalog, and records without hashes are shown as unmatched. The current CLI
does not populate content hashes, so hash mode is useful only when catalogs
already contain them. Matched rows are marked changed when any other stored
media field differs. A basic summary is printed by default; `--details` adds
records in text mode. JSON includes summary counts; CSV contains comparison
records. Use `--output` to save any format. Comparison opens both databases
read-only; unlike `sync`, it does not inspect or reconcile the live files.

```bash
smart-gallery compare E:/ F:/ --match-by name --details
smart-gallery compare E:/.smart_gallery/gallery.db F:/ --format csv --output diff.csv
```

### `export` — copy a filtered subset elsewhere

```bash
smart-gallery export --from <drive> --to <dir>
                [--structure Year Month Model Lens | --mirror]
                [--no-by-media-type] [--on-exist rename|skip]
                [--no-manifest] [--portable-db] [--jpeg-only] [--dry-run]
                [filter flags]
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
| `--jpeg-only` | off | Photos as JPEG only: JPEGs and videos are copied, CR2/CR3 files are converted to JPEG (their embedded full-size image with the RAW's metadata, as `convert-raw` makes) and everything else is left out. A RAW with a same-named JPEG in its folder is left out too, since that JPEG is exported. Needs ExifTool. |
| `--dry-run` | off | List what would be copied without copying. |

Reads metadata from the catalog — the drive is **not** re-analyzed. Example —
export every Canon shot from 2026 into a dated tree:

```bash
smart-gallery export --from E:/ --to D:/Selects \
    --cameras "Canon EOS R6" --date-start 2026-01-01 --structure Year Month

# Or copy a subset preserving the drive's structure, plus a portable catalog:
smart-gallery export --from E:/ --to D:/Mirror --file-types image --mirror --portable-db

# Or share 2025's photos of one person as JPEGs, RAWs converted:
smart-gallery export --from E:/ --to F:/Share --people "Ana" \
    --date-start 2025-01-01 --date-end 2025-12-31 --jpeg-only
```

The desktop app's **Import / Export** page has an **Export** tab that builds
this command: its own filters (people included), the destination, the folder
layout and the JPEG-only switch.

### `convert-raw` — extract JPEG previews from Canon RAW files

```bash
smart-gallery convert-raw <sources…>
```

Each source can be a CR2/CR3 file or a directory. Directories are searched
recursively. ExifTool extracts the embedded `JpgFromRaw` image and writes a
same-named `.jpg` beside each RAW file. This is typically the full-resolution
embedded JPEG; it may still be more compressed than the separate JPEG produced
by the camera's RAW+JPEG mode. The embedded image has no EXIF of its own, so
the RAW's metadata (orientation, capture date, camera, lens, exposure, GPS) is
copied into each new JPEG; vertical shots display upright and galleries sort
them by capture date. If a same-stem `.jpg` or `.jpeg` already
exists, that RAW file is skipped. The previous `convert-cr3` command name
remains available as an alias.

```bash
smart-gallery convert-raw F:/DCIM
smart-gallery convert-raw F:/DCIM/IMG_0001.CR3
```

### `recycle` — move catalog files to the Recycle Bin

```bash
smart-gallery recycle <drive> --list <file>
```

Moves each listed file to the Windows Recycle Bin and removes its row from the
catalog. The desktop app's Storage page writes the list and runs this command
when you choose which copy of a RAW + JPEG pair to keep.

The list is a UTF-8 text file with one line per file: the catalog relpath, its
size in bytes and its modification time in nanoseconds, separated by tabs
(`relpath<TAB>size<TAB>mtime_ns`). Blank lines are ignored.

| Arg | Meaning |
|---|---|
| `<drive>` | Drive whose catalog lists the files. |
| `--list FILE` | required. The list of files to recycle. Deleted after the run (kept only when the drive is refused), so the same list can't be replayed. |

It never deletes a file permanently:

- On a drive without a Recycle Bin (anything but a fixed local drive on
  Windows, or one whose Recycle Bin is turned off in its properties or by
  policy), it refuses before touching anything.
- A file is skipped, and kept, when it has no catalog row, is missing, or its
  size or modification time on disk or in the catalog differs from the list.
- If the Recycle Bin can't take a file, the file stays where it is, keeps its
  catalog row, and is reported as failed. So does a file larger than the
  drive's Recycle Bin size limit.
- Each recycled file's row is removed as soon as the file is gone, so a
  cancelled run leaves the catalog matching the drive.

Files moved this way can be restored from the Recycle Bin; run `sync`
afterwards to put restored files back in the catalog. The list file is deleted
once a run has started, so running the same job again from the app's History
fails with a missing-list error instead of recycling restored files a second
time.

```bash
smart-gallery recycle E:/ --list recycle.txt
```

### Storage page: pairs, bursts and RAW → JPEG

The desktop app's **Storage** page shows where the space goes (the size tree on
the left) and, on the right, three lists you switch between: **RAW + JPEG pairs**,
**Bursts** and **RAW → JPEG**. Picking a folder in the tree scopes all three.

**Bursts.** A burst is a run of shots from the same folder and camera, each at
most the *gap* after the one before. The gap is set at the top of the list,
from 1 to 10 s (2 s by default). Whatever the gap, one burst spans at most
10 s from its first shot to its last; a longer run is split into several bursts.
Expand a burst to see its shots, and mark the ones to remove; at least one shot
of each burst always stays. **Review** opens a burst full size so you can step
through it and mark or keep each shot (Esc goes back). Nothing happens until you
confirm: marked shots (with their RAW, JPEG and sidecar files) go to the
Recycle Bin through `recycle`, and can be restored from there. Marks you have
not applied are lost if you leave the page, after one question.

**RAW → JPEG.** The third list offers every CR3 that has no JPEG beside it (CR2
is not offered, and neither are CR3s freed up to a mirror). Pick photos, then
convert: each one gets the camera's own embedded JPEG, with all its metadata
copied. It was measured to look the same as the camera's JPEG at about a quarter
of its file size (roughly 5% of the CR3). Faces, named people and rejections
move with the photo. Each photo is converted on its own, and its CR3 goes to the
Recycle Bin only after the JPEG has been checked. The JPEG is written and
checked in `.smart_gallery/staging` first and moved beside its CR3 only as that
photo is saved, so a stopped or killed run leaves no stray JPEGs (the next run
clears the staging folder). The confirmation names the drive's Recycle Bin
limit when it is known, and warns when the picks are more than it holds. From
a terminal:

```bash
smart-gallery raw-to-jpeg E:/ --list picks.txt
```

The next mirror sync copies the new JPEGs to the mirror and moves the mirror's
CR3s to its trash. Expect the large-delete confirmation after a big conversion.

If the app or the command is killed in the instant between a CR3 going to the
Recycle Bin and the catalog being saved, that CR3's faces still belong to its
catalog row. Restore the CR3 from the Recycle Bin and delete its new JPEG
before you run `sync` (a sync first would drop the CR3's row and its faces),
then convert it again.

### `rename` — name a gallery

```bash
smart-gallery rename <drive> "<name>"
```

Stores the gallery's name in its catalog (the same name `init --label` sets),
so it follows the drive to any computer and shows in the CLI and the app.
Surrounding and repeated spaces are dropped; a blank name, or one over 80
characters, is refused. In the desktop app, use **Rename gallery…** in the
gallery menu. Until two galleries have different names, that menu shows each
one's drive after the name, for example `Archive (D:)`.

```bash
smart-gallery rename D:/ "Archive 2026"
```

### `dashboard` — explore the catalog

```bash
smart-gallery dashboard <drive>
```

Launches Streamlit and opens it on the drive's catalog (read-only, queried
directly from SQLite). The view refreshes automatically after an import or sync.
You can also point the in-app sidebar at any other drive/`gallery.db`.

### `ui` — interactive terminal interface

```bash
smart-gallery ui
```

Opens a Textual interface listing the commands. Selecting one builds a form
with a control for every option: required values first, then foldable sections
(for example Layout, Filters, Extras) with a one-line hint under each option.
Switches, dropdowns, and checklists cover the flags with fixed choices; for
`--structure`, the order you tick the levels is the folder order. A preview
above the form shows the exact `smart-gallery …` command, updated as you type.
Invalid values are flagged next to their field, and Run waits until required
values are filled in.

While a command runs, a Progress panel shows each step (finding files, reading
metadata, copying, scanning faces) with live counts, elapsed time and an
estimate of what's left. Copy steps (`import`, `export`) show one bar split
into copied / skipped / failed. The Output pane keeps the summary and any
errors.

The drive you enter carries over to the next command, and each command keeps
its values while you switch between them. `F1` shows the command's full
`--help`; `Esc` resets the form to the defaults. A "More CLI options" box
appends anything else as typed. Paths containing spaces can be entered as-is.

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

### Progress and log files

Long-running commands (`init`, `import`, `sync`, `export`, `scan-faces`) show a
progress bar per step with counts; copy steps add copied / skipped / failed
counts. The terminal shows the summary and errors. Every per-file detail (each
copied or skipped file) goes to a log file for the run,
`logs/<command>-<date>_<time>.log` under the current directory, whose path is
printed at the end. `export` no longer stops at the first file it can't read or
write: such files are counted as failed and logged.

## Installing the desktop app

The Windows desktop app (`smart-gallery gui`) can be built into a per-user
installer, so it runs without uv or Python.

Prerequisites:

- [ExifTool](https://exiftool.org/) on `PATH`. The installed app uses it the
  same way the CLI does.
- [Inno Setup 6](https://jrsoftware.org/isdl.php), to build the installer.
  `build.ps1` finds `ISCC.exe` on `PATH` or in its default install folder.

Build from the repository root:

```powershell
./packaging/build.ps1
```

The script runs `uv sync --extra faces --extra gui`, freezes the app with
PyInstaller into `dist/SmartGallery/` (`SmartGallery.exe` for the window,
`smart-gallery.exe` for the CLI), and packages that folder as
`dist/SmartGallery-Setup-<version>.exe`. If `uv sync` fails because a file is in
use, close any running `smart-gallery` windows first.

The setup exe installs to `%LOCALAPPDATA%\Programs\SmartGallery` without admin
rights, adds a Start-menu shortcut, and registers an uninstaller.

Face recognition is bundled only when the faces stack is installed (via
`uv sync --extra faces`) at build time. InsightFace models are not bundled; the
app loads them from `~/.insightface/models`. The installed CLI does not include
the Streamlit `dashboard` or the Textual `ui`; use the app's Stats page
instead.

## Per-device import recipes

`custom/import_canon.ps1` and `custom/import_phone.ps1` are thin PowerShell
wrappers over `smart-gallery import`. Each holds a small device config (source,
per-drive photo/video targets, extensions) at the top; edit those and run:

```powershell
./custom/import_canon.ps1
```

## Layout

```
smart_gallery/
  config.py        # path resolution, extensions, scan walk
  models.py        # MediaItem (+ Face/Person) — schema source of truth
  analysis/        # ExifTool extraction -> MediaItem; faces.py (InsightFace, GPU)
  organize/        # FilterOptions + in-memory predicate + placement engine
  db/              # SQLite schema (+ migrate), repository, FilterOptions->SQL
  services/        # init, import, sync, export, scan_faces, cluster_faces
  reporting/       # optional Excel report + figures
  dashboard/app.py # Streamlit, reads the DB directly
custom/            # per-device import wrappers (PowerShell over the CLI)
tests/             # unit + integration (ExifTool-marked)
```
