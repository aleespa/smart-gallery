# Library justified grid and stored rotation

Date: 2026-11-09
Mock-up (approved): https://claude.ai/artifact/HC2CMygNBEiFjA7S5kyEgT

## Intent

The Library grid today shows fixed 170×190 cells: each thumbnail is fitted
into a 150 px square with its file name underneath. The user wants it to look
like Google Photos: no file names, photos edge to edge in rows, each photo
keeping its own shape. The approved mock-up is the reference for the look.

A justified layout needs each photo's displayed shape before its thumbnail
loads, or rows would jump around as thumbnails arrive. The catalog stores
`width`/`height`, but they come from ExifTool's `ImageWidth`/`ImageHeight`,
which ignore EXIF Orientation: a portrait photo from a camera reads as
landscape. The user asked that rotation be stored in the catalog (a schema
change) so rotated photos get the right shape.

## Goals

1. Library tiles show no file names.
2. Justified rows: photos in a row share one height; each full row is scaled
   to span the grid width exactly; the last row of a group keeps the target
   height and starts at the left. 4 px gaps between tiles and rows.
3. Every photo's tile has its upright shape, rotated photos and phone videos
   included, from catalog data alone.
4. Existing catalogs gain rotation without a re-`init` and without losing
   faces or any other data.
5. A photo-size control sets the target row height.

## Non-goals

- Multi-select (the hover check circle is a visual hint only; selection stays
  single).
- Day headings, a date scrubber, or other new grouping.
- Changes to the photo viewer, filmstrip, or Name/Size sort headings.
- Showing rotation in `report`/`export` output.

## Anti-goals (a failure even if it works)

- Rows that re-flow as thumbnails load. Shapes come from the catalog only.
- Scrolling or resizing noticeably slower than today on a 50k+ item gallery.
- The rotation backfill touching any column other than `orientation`, or
  invalidating faces.

## Look (from the mock-up)

- Month (or folder / size band) headings unchanged: serif title, count,
  collapse chevron, click to toggle, right-click Collapse all / Expand all.
  New: the heading of the group at the top of the view stays pinned at the
  top while that group scrolls under it; clicking the pinned heading
  toggles that group.
- Tiles: thumbnail fills the tile (centre crop when the tile's aspect differs
  from the thumbnail's, which only happens after clamping, below). No frame,
  no padding beyond the 4 px gap. Grid side padding 12 px.
- Hover: thumbnail slightly brighter; an empty white check circle top-left.
- Selected: thumbnail scaled to 90% inside the tile, 2 px `theme.ACCENT`
  ring, check circle filled with `palette(highlight)` (ACCENT_FILL) and a
  white tick.
- Video: chip top-right, play glyph + duration (`BADGE_BACK`/`BADGE_TEXT`),
  replacing today's centre circle.
- Offloaded: faded placeholder as today, plus an "On D:" chip bottom-left
  using the holder's drive label (the `holders` map already exists).
- Tooltip: file name, date taken, size; plus "On <label> only" when
  offloaded.
- Photo size: a slider in `header_controls`, left of "Sort by", with small
  and large square glyphs at its ends; tooltip "Photo size (Ctrl + scroll
  wheel)". Range 110–340 px row height, default 190. Ctrl + wheel over the
  grid steps it by 20. Remembered in the Library state file
  (`library.json`, key `row_height`).

## Catalog: stored rotation (schema v9)

- New column `media_items.orientation INTEGER`, EXIF Orientation semantics
  (1–8). `NULL` means "not read yet"; a file read with no tag gets 1.
- Photos: ExifTool `-Orientation` (with `-n`). Videos: `-Rotation` (degrees,
  `-n`), mapped 0→1, 90→6, 180→3, 270→8.
- Add the tag args to `EXIFTOOL_IMAGE_TAG_ARGS` / `EXIFTOOL_VIDEO_TAG_ARGS`
  and the mapping in `MediaItem.from_exiftool`.
- Lock-step per CLAUDE.md: `MediaItem` field, `DB_COLUMNS`,
  `CREATE_MEDIA_ITEMS` (appended last, matching where `ALTER TABLE ADD
  COLUMN` puts it), `SCHEMA_VERSION = 9`, and an idempotent `if current < 9:`
  step in `migrate()` that adds the column if missing.
- A pure helper (Qt-free, e.g. `models.display_size(width, height,
  orientation)`) returns the upright size: swaps for orientations 5–8.
- `width`/`height` keep their current meaning (as stored in the file).

### Filling in existing catalogs

- **`sync`**: after its diff, for rows that are `image`/`video`, present on
  the drive (not offloaded), not being re-analysed this run, and
  `orientation IS NULL`, read only the rotation tag with ExifTool (batched,
  `-fast2`, same worker pool settings) and `UPDATE` only `orientation`. It is
  its own counted progress step, "Reading rotation". A file ExifTool could
  not read stays `NULL` and is retried next sync. `--dry-run` reports how
  many would be read. First sync after upgrading reads every photo once.
- **Mirror catalogs (D)**: their rows are copies of E's. During
  `sync-mirror` apply, a D row whose `orientation` is `NULL` takes E's value
  for the same relpath when the baseline says the two files are the same.
  `sync` run directly on D also fills them.
- **Offloaded rows on E** (file absent) stay `NULL` until the file comes back;
  they fall back to the unrotated shape. Acceptable: they are faded
  placeholders.
- **Read-only opens** never migrate, so the Library query must tolerate a
  catalog without the column (check `PRAGMA table_info`, treat as `NULL`).

## Grid

- `LibraryRow` gains `width`, `height`, `orientation`;
  `library_rows` selects them.
- Aspect per tile: upright `display_size` ratio, clamped to 0.4–3.0
  (panoramas get a centre-cropped wide tile). Missing or zero size: 3:2 for
  photos, 16:9 for videos, 1:1 for anything else.
- Layout: a pure, Qt-free justify function (own module, unit tested) takes
  aspects, width, target height and gap and returns rows of
  (height, [tile widths]); the last tile of a full row absorbs rounding so
  rows end flush. The grid keeps a precomputed table per group (row tops,
  tile x/width) rebuilt on resize, model change, collapse and size change;
  painting, hit testing, selection and `visible_rows()` look rows up by
  bisect so they still touch only rows in view.
- Keyboard: Left/Right in reading order (across groups, skipping collapsed
  ones, as today); Up/Down to the tile in the row above/below whose centre is
  nearest horizontally; PageUp/PageDown by a viewport height; Home/End.
- Thumbnails: the model keeps source thumbnails and scales to the tile size
  at paint time, caching scaled pixmaps by (key, size) so scrolling stays
  smooth. Thumbnail requests stay lazy (visible rows only, debounced).
- `DisplayRole` text is no longer drawn on tiles.

## Thumbnail size (user decision: raise to 512 px)

- `gui/thumbs/cache.py:THUMB_EDGE` goes from 256 to 512 (long side), so tiles
  stay sharp at every slider size and on high-DPI screens.
- Thumbnails already on the drives must be remade: give the new files a name
  suffix (the same pattern as `RAW_THUMB_SUFFIX`), so an old 256 px file is
  not reused and is removed when its replacement is written. Rebuilding
  happens on demand as rows come into view; no bulk job.
- Every other thumbnail user (person page, Storage views, viewer filmstrip)
  gets the larger file and scales it down as today. Face crops are separate
  and unchanged.
- Costs about 4× the thumbnail disk space under `.smart_gallery/thumbs`.

## Person page (user decision: same layout)

- `gui/people/person_view.py` uses the same justified grid, look and photo
  size control as the Library. One photo size value is shared by both pages.
- `GroupedGrid` therefore changes its layout for both callers; no fixed-cell
  mode needs to survive.

## Left to the builder

- Exact painting code and pixmap cache sizing.
- Where the shared photo size value is stored.
- Updating `CLAUDE.md` (schema v9 note), `docs/TECHNICAL_REFERENCE.md`, and
  the Library section of `docs/USER_GUIDE.md` (screenshots per the existing
  screenshot conventions).

## Tests

- `test_models.py` lock-step passes with the new column.
- `migrate()` from v8 adds the column; running it twice is harmless.
- `from_exiftool`: photo Orientation 6 stored as 6; missing tag → 1; video
  Rotation 90 → 6.
- `display_size` for all eight orientations.
- Justify function: rows fill the width exactly, last row keeps the target
  height, a single very wide photo, empty input, clamping.
- `sync` backfill (monkeypatched ExifTool): updates only `orientation`,
  skips offloaded and freshly analysed rows, leaves faces untouched, leaves
  `NULL` on read failure, dry run counts.
- `sync-mirror` fills D's `NULL` orientation from E for unchanged files.
- Library query on a v8 catalog opened read-only.
- GUI (`tests/gui`): hit testing and `visualRect` agree; keyboard
  navigation; collapse; slider changes row height and is remembered; the
  person page shows the same layout.
- Thumbnails: a new thumbnail is 512 px on its long side; an old-name
  256 px file is not reused.

## Calls made without the user (check these)

- One `orientation` column with EXIF 1–8 meaning, video rotation mapped onto
  it, rather than separate photo/video columns or stored display sizes.
- The backfill runs inside `sync` (first sync after upgrading reads every
  photo once) and `sync-mirror` copies values to D; no new command.
- Aspect clamped to 0.4–3.0, with centre crop beyond it.
- Pinned group heading while scrolling.
- Slider range 110–340 px, default 190, shared by Library and person page.
