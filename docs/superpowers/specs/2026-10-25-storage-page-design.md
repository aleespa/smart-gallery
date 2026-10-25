# Storage page: where the space is, and RAW + JPEG pairs

Date: 2026-10-25. Area: desktop app (`smart_gallery/gui/`), plus one CLI
command and its service.

This document replaces `2026-10-25-dashboard-storage-panel-design.md` and
its plan, which were never built. The Storage tree from that design moves
out of the Dashboard onto a page of its own, next to a new RAW + JPEG pairs
feature. The other Dashboard changes from that design still stand.

## Why

The user wants to get space back on a gallery drive. Their workflow, in
order:

1. Find the folder holding the most bytes.
2. See which file type fills it.
3. Look in it for files to remove. In particular, look for shots kept twice,
   as a RAW and as a JPEG. They used to shoot RAW + JPEG on a Canon R8, and
   `convert-raw` can now rebuild a JPEG from a RAW with its metadata.

Today the Dashboard counts files and draws charts about them, but it never
shows where the bytes are. Nothing finds the shots stored twice.

## Goals

1. Rename the **Dashboard** tab to **Stats**.
2. Add a **Storage** tab right after Stats, with two columns:
   - left: **Where the space is**, the storage tree (folder ▸ type sizes);
   - right: **RAW + JPEG pairs**, a list of shots stored as both formats in
     the same folder, where the user chooses which copy to keep.
3. Clicking a folder in the tree narrows the pairs list to that folder and
   everything under it.
4. The copies the user chose not to keep go to the **Windows Recycle Bin**,
   all in one action, and leave the catalog.
5. On Stats: remove the Images / Videos / Others data tables, and make every
   panel collapsible with its state remembered.

The approved mockup is
`2026-10-25-storage-page-mockups/storage-page.html`, next to this document.
The choice of layout B, a list of pairs, is in `pair-review-layout.html` in
the same folder. The mockups are fragments for the brainstorm server and
come without its frame styles, so open them for layout, not polish.

## Non-goals

- Pairs across folders. A pair is always two files in the same folder.
- Duplicates of the same format (two JPEGs of one shot), or edited exports
  under another name (`IMG_0001-Edit.jpg`).
- Deciding for the user. There is no "keep RAW for all" or any other bulk
  rule. The user goes through the pairs one by one.
- Permanent deletion, or moving files to a holding folder.
- A Library folder filter. The tree still opens folders in Explorer, not in
  the Library.
- A treemap or sunburst chart (options A and C in the first mockups).
- A CLI or TUI way to review pairs. Reviewing happens only in the desktop
  app. The CLI only gains the command that carries out the user's choices
  (see below).

## Anti-goals (failures even if it works)

- **Deleting a file permanently.** If the Recycle Bin can't take a file,
  that file is left where it is and reported. It is never deleted for good.
- **Removing either copy of a pair the user didn't decide on**, or the copy
  they chose to keep.
- **Removing a file whose contents changed since the list was built.** If
  its size or mtime on disk differs from the catalog, skip it and report it.
- Scanning folders to find pairs. Pairs come from the catalog. Only the
  picture check reads files.
- Freezing the window. Building the tree, building the pairs list, and
  checking pictures all run off the GUI thread.
- Writing to the catalog from the GUI process. Every write goes through a
  CLI job (`gui/jobs.py`), like every other write the app makes.

## Page layout

```
┌ Storage ── E: · 1.82 TB in 48,210 files ─────────────────────────────┐
│ ┌ Where the space is  [Folder▸Type|Type▸Folder] ┐ ┌ RAW + JPEG pairs  Checking… 412/656 ┐
│ │ Name          Size   Share of parent   Files │ │ In Canon/2026-02 · 656 pairs  Show all │
│ │ ▾ E:        1.82 TB  ██████████ 100%  48,210 │ │ [▣▣] IMG_0001  CR3 24 MB · JPG 9.8 MB  │
│ │   ▸ Drone    912 GB  █████ 49%         1,204 │ │            (RAW) (JPEG) (Both)         │
│ │   ▾ Canon    745 GB  ████ 40%         31,880 │ │ [▣▣] IMG_0007 ⚠ look different  …      │
│ │     ▾ 2026-02 …      (selected)              │ │ …                                      │
│ └──────────────────────────────────────────────┘ │ 2 chosen · frees 33.1 MB               │
│                                                  │ [Clear choices] [Move 2 files to Recycle Bin] │
│                                                  └────────────────────────────────────────┘
```

- **Tabs.** `gui/areas.py` lists, in order: Library, Import, **Stats**,
  **Storage**, People, Actions, History. The Stats area keeps its key
  (`"dashboard"`) and its `DashboardView`, and only its label changes, so
  saved window state and tests that look up the key keep working. Storage
  is a new area keyed `"storage"`. *My call:* its icon is a generic
  drive or disk glyph from whatever icon set `areas.py` already uses.
- **Header.** The page header reads "Storage" and has a caption with the
  gallery's root name and totals, for example "E: · 1.82 TB in 48,210
  files".
- **Two columns**, side by side, splitting the width equally. *My call:* a
  `QSplitter` so the user can drag the divider. Each column scrolls on its
  own.
- **Filters.** *My call:* the Storage page ignores the Library / Stats
  filters and always shows the whole gallery, because it is about what is
  on the disk. Unlike Stats, it has no header filter controls.

## Left column: Where the space is

This is the tree from the replaced design, with the same data and
behaviour. Only its home has changed.

### Data

- Built from the catalog's `relpath`, `ext`, `size_bytes`, in a no-Qt
  module (`smart_gallery/dashboard/storage.py` or a new
  `smart_gallery/storage/` package; builder's choice). It runs on a worker
  thread.
- **Folder.** The POSIX parent of `relpath`. The root node is the gallery
  root. Files directly at the root appear as type rows under the root.
  Folder names that differ only in case are one folder, named after the
  first spelling seen, because `relpath` is `COLLATE NOCASE` and Windows
  treats them as one.
- **Type.** The extension, upper-cased without its dot (`CR3`, `JPG`).
  Extensions in different cases merge. A missing extension shows as
  `(none)`. `.jpg` and `.jpeg` stay separate rows.
- Each node holds total bytes and file count, and a folder's totals include
  all its descendants. A NULL `size_bytes` counts as a file of 0 bytes.

### Two groupings

A two-way switch above the tree:

- **Folder ▸ Type** (default). A folder shows its subfolders, then one row
  per type for its own files. Each group is sorted biggest first, with the
  name as the tiebreak.
- **Type ▸ Folder.** The top level is the types. Under each type is the
  folder tree, limited to that type.

Switching rebuilds the Qt model from data already computed, without
another query. The choice is remembered (see Persistence).

### Columns

| Name | Size | Share of parent | Files |
|------|------|-----------------|-------|

- **Name:** the folder name with an expand arrow, or, for a type row, a
  colour chip and the type.
- **Size:** 1024-based B / KB / MB / GB / TB with three significant
  figures, right-aligned, tabular figures.
- **Share of parent:** a bar plus a percentage of the parent's bytes (the
  root is 100%). Folder bars use `palette(highlight)`. Type bars use the
  type's colour.
- **Files:** the count with thousands separators, right-aligned.
- **Type colours:** `colors.categorical()` ranked by each type's total
  bytes, so the biggest type gets the accent. Ranks ≥
  `colors.CATEGORY_COUNT` get `colors.NEUTRAL`. A type keeps its colour in
  both groupings.
- The root opens expanded one level. Sort on raw bytes through the model's
  sort role, never on formatted text.

### Interaction

- **Single click on a folder row** selects it and scopes the pairs list to
  that folder and its subfolders (see the right column). Clicking a type row
  scopes to the folder that row belongs to. In Type ▸ Folder mode, a type's
  top-level row has no single folder, so it scopes to the whole gallery.
- **Double-click** (or right-click → **Open in Explorer**) opens the folder
  in Explorer, as in the replaced design. Add an `open_folder(path)` next to
  `gui/library/viewer.py:show_in_explorer`. If the folder doesn't exist, it
  launches nothing and shows "Folder not found: <path>" in the page caption.

## Right column: RAW + JPEG pairs

### What counts as a pair

Two catalog rows in the **same folder** (`directory_rel`, compared
case-insensitively) with the **same file name stem** (compared
case-insensitively), where one is a **RAW** (`.cr2`, `.cr3`) and the other
a **JPEG** (`.jpg`, `.jpeg`).

- The RAW extensions are the ones `convert-raw` can extract a preview from.
  *My call:* keep them in one constant so another RAW type can be added
  later.
- If one stem has more than one RAW or more than one JPEG (for example both
  `IMG_1.JPG` and `IMG_1.jpeg`), *my call:* pair each RAW with each JPEG.
  Each resulting pair is its own row. This is rare, and it keeps every file
  visible. Because one file can then be in two rows, choices resolve
  **per file: keep wins**. A file is removed only if at least one row
  removes it and no row keeps it. ("Both" keeps both files.) The footer
  totals, the confirmation count and the list file all count each distinct
  file once.
- **XMP sidecars follow their RAW (the user's decision).** A catalog row in
  the same folder with the same stem and extension `.xmp` (case-insensitive)
  is the RAW's sidecar, for example Lightroom's saved edits. When the RAW is
  removed, its sidecar is removed with it. When the RAW is kept, the sidecar
  stays. *My call:* if a stem has more than one RAW (`.CR2` and `.CR3`), the
  sidecar goes only when every one of those RAWs is removed. The row shows
  "CR3 24 MB + XMP" when a sidecar exists. The footer totals, the
  confirmation count and the list file include sidecars.
- Pairs are found with one SQL query on the read-only catalog (a self-join
  of `media_items` on folder and stem). No folder is scanned. With an index
  or not, this should take well under a second for 100k rows. If it
  doesn't, an index on `(directory_rel, name)` may be added through a
  `migrate()` step.

### The picture check

Each pair in the selected folder is checked to see whether the two files
show the same picture. Same name does not guarantee it: the camera counter rolls over, a second
camera can use the same names, or the JPEG can be an edited Lightroom export
saved over the camera JPEG.

- **How:** take the RAW's embedded preview (the same image `convert-raw`
  extracts; `PreviewImage` or `JpgFromRaw`, whichever is faster to get and
  big enough), and the JPEG itself. Decode both at small size (Pillow's
  JPEG draft mode makes this cheap), convert to grayscale, ignore the EXIF
  orientation, since both images' pixels are in the sensor's orientation,
  and compare. A different aspect ratio means **different**. Otherwise use a
  simple distance (mean absolute difference on a small thumbnail, or a
  perceptual hash). Choose the threshold so that:
  - the camera's own JPEG next to its RAW counts as a **match**, even at
    different JPEG quality, picture style, or Auto Lighting Optimizer;
  - a crop, a strong edit, or a different shot counts as **different**.
- **Outcome per pair:** `match`, `different` (the row shows "⚠ look
  different"), or `unreadable` (a file is missing or has no preview; the row
  shows "⚠ couldn't compare"). A pair is choosable whatever its outcome. The
  warning informs the choice and blocks nothing.
- **Speed:** checking must not start one ExifTool process per pair. Batch
  the preview extraction (`-w` into a temp or cache folder, or one
  `-stay_open` process). Aim for a fifth of a second per pair or better on
  the user's machine.
- **Only the selected folder.** Pictures are checked only for the pairs in
  the folder selected in the tree (that folder and every folder under it).
  With no folder selected, the root selected, or after Show all, no check
  runs at all, and the column header reads "Select a folder to check its
  pictures" while some pairs have no result. Selecting another folder
  cancels the running check and starts one for that folder's pairs that
  have no result yet. A reload of the same gallery that keeps the selected
  folder checks it again the same way.
- **In the background:** the list shows at once. Rows covered by the
  running check read "checking…"; other rows without a result show
  nothing. The checks run off the GUI thread and fill in rows as they
  finish. The column header shows "Checking pictures… n / N". The user can
  choose before checking is done.
- **Remembered results show everywhere.** When the pairs load, the
  remembered results (see below) are read off the GUI thread for every
  pair, in or out of the selected folder, without running ExifTool or
  opening any picture.
- **When the check fails** (ExifTool missing, the drive pulled, the cache
  folder unwritable), the column header reads "Couldn't check pictures:
  <reason>", and the rows of that run that got no result show "⚠ couldn't
  compare". Those are not remembered, so the next check tries them again.
- **Remembered between visits (my call, changed from the conversation).**
  In the conversation, the plan was to store results in the catalog in a
  table of their own. That would make the GUI write to the catalog outside
  a CLI job, which the app never does. Results are therefore cached outside
  the catalog instead, in the app data folder (`gui/paths.py:app_data_dir()`),
  per gallery, like the thumbnail cache. Each result is keyed by both files'
  `relpath`, `size_bytes` and `mtime_ns`. If either file changes, its key
  no longer matches and the pair is checked again. No schema change, no
  migration, and no sync hook are needed. A lost or corrupt cache only
  means checking again.

### The list

- Grouped by folder, with a small uppercase folder heading. Folders are
  ordered like the tree in Folder ▸ Type mode (biggest first). Within a
  folder, pairs are ordered by name.
- **Scope line** above the list: "In **Canon/2026-02 Valentines** · 656
  pairs" with a **Show all N pairs** link that clears the scope. With no
  folder selected (the start state), it reads "All folders · N pairs". The
  scope is that folder and every folder under it. Selecting the root in the
  tree is the same as Show all.
- **Each row:**
  - small previews of both copies, RAW first. Reuse the app's thumbnail
    cache and loader (`gui/thumbs/`), which already handles RAW previews;
  - the stem, then "CR3 24 MB · JPG 9.8 MB" (type labels and
    `format_bytes`, as in the tree);
  - the check status: nothing for a match, "⚠ look different", "⚠ couldn't
    compare", "checking…" while a check covering the row runs, or nothing
    when no check covers it;
  - three exclusive choice buttons: **RAW**, **JPEG**, **Both**. RAW means
    keep the RAW and remove the JPEG, and so on. No button is selected at
    first (undecided). Clicking the selected button again returns the pair
    to undecided.
- **Clicking the row** (outside the buttons) opens the two copies large,
  side by side, for a closer look. *My call:* reuse the Library's viewer if
  it can show two images; otherwise use a simple dialog with both images
  and the same three buttons. The builder chooses.
- An empty list says so: "No RAW + JPEG pairs in this folder" or "… in this
  gallery".

### Choices and the one button

- Choices add up. Nothing touches the disk until the user presses the
  button.
- The **footer** shows "N chosen · frees X" (X is the total size of the
  copies that would be removed; Both frees nothing), a **Clear choices**
  button, and **Move N files to Recycle Bin**. That button is disabled when
  no file would be removed. "Undecided pairs are left alone" sits under the
  totals.
- The totals and the button cover **all** choices in the gallery, not just
  the visible scope. *My call:* this way, scoping to another folder never
  hides choices already made. The footer says "(in 3 folders)" when the
  choices span more than the current scope.
- **Choices live in memory only (the user's decision).** They are not saved.
  When any are pending, both closing the app and switching to another
  gallery first ask "You have N unapplied choices. Discard them?", and
  answering no cancels the close or the switch. Today
  `MainWindow.closeEvent` only asks about running jobs, and
  `AppContext.set_gallery` can't be cancelled. Both need a hook the Storage
  page can veto, for example a `can_leave() -> bool` check that `MainWindow`
  and the gallery switcher call before they act.
- Pressing the button asks for one confirmation ("Move 48 files (1.1 GB) to
  the Recycle Bin?") and then starts a job (below). The rows stay as they
  are while the job runs. When it finishes, the page reloads from the
  catalog (`catalog_updated`). Removed pairs disappear, and choices for
  pairs that failed remain, so the user can retry.

## Carrying out the choices: a CLI command

All writes go through a CLI job, so the GUI hands the removal to a new
command:

```
smart-gallery recycle <drive-or-db> --list <file>
```

- `--list` is a UTF-8 text file with one entry per line:
  `relpath<TAB>size_bytes<TAB>mtime_ns`, the values the GUI saw in the
  catalog. The GUI writes it to a temp file and passes its path, so any
  number of files fits.
- For each entry, the service (`services/recycle.py`, returning a
  `RecycleReport` with `recycled`, `skipped`, `failures`, `freed_bytes`):
  1. resolves `root / relpath`;
  2. **skips** it if the catalog row is gone, the file is missing, or its
     size or mtime on disk differs from the list (anti-goal: changed
     files);
  3. moves it to the Recycle Bin;
  4. on success, deletes its catalog row (`delete_by_relpaths`; faces
     cascade).
- **Recycle Bin safety (anti-goal: never permanent).** On Windows, before
  moving anything, check that the drive supports the Recycle Bin (a fixed
  drive per `GetDriveTypeW`. The user's galleries D: and E: are both fixed
  and have a `$Recycle.Bin`). If it doesn't, the command fails with a clear
  message and touches nothing. Use the shell's recycle operation with undo
  allowed (`IFileOperation` or `SHFileOperationW` with `FOF_ALLOWUNDO`),
  with no UI prompts and errors reported back per file, never "delete if it
  can't recycle". Off Windows, the command refuses to run. The app is
  Windows-only, and permanent deletion is not acceptable. A small
  dependency such as `send2trash` is acceptable if the builder confirms it
  never falls back to permanent deletion.
- Deletes are committed per file, or in small batches, so a cancelled or
  interrupted job leaves the catalog matching the disk.
- The command emits progress events (`progress.py`: step "Moving files to
  Recycle Bin", `done`/`total`, with recycled/skipped/failed outcomes) and
  honours the job stop file, like other long commands. Add it to
  `DETAIL_LOG_COMMANDS` so each file gets a log line.
- CLI rules from `CLAUDE.md` apply. Add it to `_HANDLERS`, give every option
  help text in the right argument group (forms are generated from the
  parser), and add a README section. The command is generic: it recycles
  any listed catalog files. The pairs feature is its only caller for now.
  *My call:* hide it from the Actions area's command list, because it needs
  a list file, unless the generated form handles a file path fine (it does
  for other path options). The builder decides.

## Stats tab (from the replaced design)

- **Label:** "Stats". The key, the view class and the module stay as they
  are.
- **No data tables:** delete the Images / Videos / Others `QTabWidget`, its
  `QTableView`s, `DataFrameModel`, and the charts/tables splitter. Remove
  `DashboardData.split` if nothing else reads it. Update
  `tests/gui/test_dashboard_view.py`.
- **Collapsible panels:** every `Panel` title becomes a flat `QToolButton`
  with a ▾ / ▸ chevron, and clicking it hides or shows the body. This
  applies to File Types, Camera Settings, Timeline, Video and Photo Map. A
  collapsed panel's body is still replaced on each new result and stays
  hidden. "Hide a panel with no data" still wins.
- **There is no Storage panel on Stats.** It is now the Storage page.

## Persistence

`app_data_dir()` holds two small JSON files, following the `galleries.json`
pattern. Writes are atomic (`.tmp` then `os.replace`), and a missing or
invalid file means defaults:

- `dashboard.json` (Stats): `{"collapsed": [panel titles]}`.
- `storage.json` (Storage): `{"grouping": "folder" | "type", "split": [left,
  right]}` (the splitter sizes; *my call*).

The picture-check cache is separate (see above).

## Constraints

- Follow `CLAUDE.md` / `AGENTS.md`. Colours come only from
  `smart_gallery/colors.py` or palette roles; `tests/unit/test_colors.py`
  rejects hex literals elsewhere. Keep the no-Qt/Qt split: tree data, pair
  finding, the picture comparison, the state stores and the recycle service
  are no-Qt and unit-testable.
- `gui/paths.py` stays Qt-free.
- The GUI opens the catalog read-only only. No migration from the GUI.
- Pillow is in the `gui` extra, so the picture check may use it. The core
  CLI must still import cleanly without it, and the `recycle` command
  doesn't need it.
- Run Ruff and Black on touched files only. Commit messages follow the repo
  style.

## Tests

- **Unit (no Qt):**
  - the storage tree (all cases from the replaced plan's Task 1: totals,
    ordering, root files, extensionless files, case merging, NULL sizes,
    Type ▸ Folder, colour ranking);
  - pair finding: same folder only; case-insensitive stem, folder and
    extension; CR2 and CR3; `.jpeg`; one stem with two JPEGs; no pair across
    folders; no RAW+RAW or JPEG+JPEG;
  - resolving choices into files to remove: keep wins for a file shared by
    two rows; each file is counted once; the sidecar goes with a removed
    RAW, stays with a kept one, and stays while any RAW with its stem is
    kept;
  - scoping pairs to a folder and its subfolders;
  - the picture check, marked `exiftool`: the R8 fixture's preview against
    a re-encoded copy of itself at a lower quality is a **match**; against
    a cropped copy, a strongly edited copy (inverted or heavily tinted), and
    `test.JPG` (another shot) it is **different**; a RAW with no preview is
    **unreadable**;
  - the cache: a hit when keys match, and a miss after either file's size
    or mtime changes;
  - the recycle service, with the recycle call monkeypatched: it recycles
    and drops rows; it skips changed, missing or uncatalogued files; it
    refuses on an unsupported drive and touches nothing; a recycle failure
    keeps the row;
  - state stores: round trip, and defaults on bad files.
- **GUI (`tests/gui`, offscreen):**
  - tab order and labels (Stats, Storage);
  - Stats has no tables, and panels collapse and remember it;
  - Storage shows both columns, and clicking a folder scopes the pairs list
    and updates the scope line; Show all clears it;
  - choosing updates the footer totals, clicking the selected button again
    clears it, and Clear choices clears all;
  - with pending choices, closing or switching gallery asks first, and
    answering no keeps the app open on the same gallery;
  - the button builds the right list file (only removed copies, with
    size/mtime) and starts a `recycle` job (monkeypatch the job runner);
  - the grouping and splitter are remembered.
- **By hand:** run `uv run smart-gallery gui` on a real gallery, choose a
  few pairs, move them, and confirm they are in the Recycle Bin and can be
  restored.

## Left to the builder

- Module layout for the no-Qt parts. A `smart_gallery/storage/` package
  (tree, pairs, compare, cache) is a natural fit.
- The exact comparison metric, thumbnail size and threshold, as long as the
  test cases above hold and real camera pairs match.
- How the preview extraction is batched, and the cache's file format.
- `QTreeView` + model versus `QTreeWidget` for the tree, and the list widget
  for pairs (a `QListView` with a delegate scales best to thousands of rows).
- The share-bar delegate and the choice-button styling, using
  `palette(highlight)` for the selected state.
- The large side-by-side view.
