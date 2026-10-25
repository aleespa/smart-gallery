# Dashboard: Storage panel, collapsible panels, no data tables

Date: 2026-10-25. Area: desktop app, `smart_gallery/gui/dashboard/`.

## Why

The user wants to see what is filling a drive. The questions are, in order:
which folder holds the most bytes, and which file type is filling it. Then
they open that folder to look for files to delete, or for shots kept as both
a RAW and a JPEG. Today the Dashboard counts files and draws charts about
them, but it never shows where the bytes are.

While working on the Dashboard, two more changes were asked for: remove the
data tables at the bottom, and make every panel collapsible.

## Goals

1. Add a **Storage** panel as the last panel, below the Photo Map. It holds
   a tree view of the catalog's sizes by folder and by file type (layout B
   from the brainstorm mockups).
2. Remove the Images / Videos / Others table tabs from the Dashboard.
3. Let every Dashboard panel collapse to its title bar.

## Non-goals

- **Finding RAW + JPEG pairs.** The user wants this as a feature of its own
  later. The panel shows no "pairs" rows. Do not design anything for it here.
- **A Library "folder" filter.** The tree opens folders in Explorer, not in
  the Library. That means no new `FilterOptions` field and no change to the
  filter-parity contracts.
- Deleting or moving files from the panel.
- A treemap or sunburst chart. These were options A and C in the mockups,
  and the user did not pick them.

## Anti-goals (failures even if it works)

- Re-scanning files or running ExifTool. Every number comes from the catalog
  (`relpath`, `size_bytes`).
- Freezing the GUI. The tree is computed on the worker thread inside
  `load_dashboard`, like every other chart.
- A file type showing in different colours in different places in the tree.

## The Storage panel

### Data

It is computed in `load_dashboard` from the same filtered DataFrame the
other panels use, and stored on `DashboardData` as a new field. The panel
follows the Dashboard's filters, like every other panel.

- **Folder.** The POSIX parent directories of `relpath`. The root node is
  the gallery root itself. Files directly at the root appear as type rows
  under the root node.
- **Type.** The file extension, upper-cased without its dot (`CR3`, `JPG`,
  `MOV`). Files with no extension show as `(none)`. *My call:* `.jpg` and
  `.jpeg` stay separate rows. The tree shows what is on disk.
- Each node holds its total bytes and its file count. A folder's totals
  include all of its descendants.

The tree should be a plain-data structure in a no-Qt module, so it can be
unit-tested without Qt. A good home is `smart_gallery/dashboard/data.py` or
a new `smart_gallery/dashboard/storage.py`.

### Two groupings

A two-way switch sits in the panel, above the tree:

- **Folder ▸ Type** (the default). Opening a folder shows its subfolders,
  then one row per file type, for that folder's own files only. Each child
  group is sorted biggest first.
- **Type ▸ Folder.** The top level is the file types. Under each type is the
  folder tree, limited to the files of that type, again biggest first.

Switching only rebuilds the Qt model from data that is already computed. It
does not run another query. *My call:* the chosen grouping is remembered
along with the collapse state (see below).

### Columns

| Name | Size | Share of parent | Files |
|------|------|-----------------|-------|

- **Name.** The folder name, with an expand arrow. For a type row, a colour
  chip and the type.
- **Size.** Human-readable, with the unit picked automatically
  (KB / MB / GB / TB, 1024-based to match the existing `size (MB)` column).
  Right-aligned, with tabular figures.
- **Share of parent.** A horizontal bar plus a percentage of the parent
  node's bytes. The root's share is 100%. Folder bars use the accent
  (`palette(highlight)`). Each type's bar uses that type's colour.
- **Files.** The count, with thousands separators, right-aligned.

**Type colours.** Every extension gets a colour from
`colors.categorical()`, ranked by its total bytes in the whole result, so
the biggest type gets the accent. A type keeps that colour everywhere in
the tree and in both groupings. Any type past `CATEGORY_COUNT` takes
`colors.NEUTRAL`.

The panel title is followed by a one-line caption with the totals, for
example "1.82 TB in 48,210 files".

**Opening state.** The root is expanded one level. *My call:* the tree has a
fixed height, about the map's 500 px, and its own scrollbar, so a deep tree
doesn't make the dashboard page longer.

### Opening in Explorer

- Double-click a **folder** row, or right-click it and choose
  **Open in Explorer**, to open that folder.
- Do the same on a **type** row to open the folder that row belongs to. In
  Type ▸ Folder mode, a type's top-level row has no single folder, so
  "Open in Explorer" is disabled there.
- The absolute path is the gallery root plus the node's relative path. Reuse
  or generalize `gui/library/viewer.py:show_in_explorer`. That function
  selects a file, so it needs a sibling function that opens a folder.
- If the folder doesn't exist (the drive was unplugged, or the folder was
  removed after the last sync), do nothing harmful. *My call:* show the
  message in the Storage panel's caption line, not in a dialog. (The app has
  no Qt status bar for messages; its bottom bar belongs to jobs.)

## Removing the data tables

Delete the `QTabWidget` (Images / Videos / Others), its `QTableView`s,
`DataFrameModel`, and the vertical `QSplitter` between the charts and the
tables. The tiles row and the scrolling panel column then fill the whole
height. Remove `DashboardData.split` if nothing else reads it.
`load_dashboard` still splits the frame internally for the metrics and
charts. Update `tests/gui/test_dashboard_view.py` to match.

## Collapsible panels

- Every `Panel` title becomes clickable, with a ▾ / ▸ chevron before the
  name. Clicking the title or the chevron hides or shows the body, leaving
  just the title bar. This applies to File Types, Camera Settings, Timeline,
  Video, Photo Map and Storage.
- A collapsed panel keeps its body, which is still replaced on each new
  result as today. It simply stays hidden.
- The rule "hide a panel with no data" still applies and wins: an empty
  panel is hidden whether it is collapsed or not.
- **Persistence (my call).** The collapse state of each panel, keyed by
  panel title, and the Storage grouping are saved to
  `app_data_dir() / "dashboard.json"`. This follows the JSON-in-app-data
  pattern of `galleries.json`. The state is global, not per gallery. A file
  that is missing or unreadable means everything starts expanded with
  Folder ▸ Type grouping.

## Constraints

- Follow `CLAUDE.md` / `AGENTS.md`. All colours come from
  `smart_gallery/colors.py` or palette roles, with no hard-coded hex values
  in the view. Keep the no-Qt/Qt split: the tree data is no-Qt, the
  widgets are Qt.
- Read-only. The Dashboard never migrates the catalog or writes to it.
- Run Ruff and Black on the touched files only.

## Tests

- Unit (no Qt): building the tree from a small frame, covering folder
  totals, type rows, root-level files, extensionless files, the
  Type ▸ Folder regrouping, biggest-first ordering, and the type colour
  ranking.
- GUI (`tests/gui`, offscreen): the Storage panel appears last, after the
  map. The tables are gone. A panel collapses and expands. The collapse
  state round-trips through `dashboard.json`. The grouping toggle rebuilds
  the model.

## Left to the builder

- Whether to use `QTreeView` with a custom model or `QTreeWidget`. Whichever
  is used, sort with the model's sort role on raw bytes, never on the
  formatted text.
- How to draw the share bar (an item delegate is the natural fit) and its
  exact sizes.
- The widget for the grouping switch, as long as it uses the existing theme
  (`palette(highlight)` for the checked state).
