# RAW → JPEG: keep only a camera JPEG for photos that won't be edited

Date: 2026-10-31. Area: desktop app (`smart_gallery/gui/storage/`), the
`convert-raw` service, and a new CLI job. Third list on the Storage page,
beside **RAW + JPEG pairs** and **Bursts**.

## Why

The user shoots RAW only (Canon EOS R8, CR3). Many shots will never be
edited, and for those the 19 MB RAW buys nothing over the JPEG the camera
would have saved. The user wants to look through a folder, pick those
photos, and keep a JPEG that looks **the same as if they had shot JPEG**,
dropping the RAW.

On the E: catalog (2026-10-31) there are 7,947 CR3s with no same-stem JPEG,
212 GB in all, almost all in `Photos/Canon/2026`. None has an `.xmp`
sidecar. Their rows carry 1,313 `user` faces, 727 `auto` faces of named
people and 1,723 `cluster` faces.

## Which converter (measured, the user's decision to rely on)

Tested 2026-10-31 on four R8 RAW+JPEG pairs the camera shot together
(`C:\Users\Alejandro\Projects\gallery-inspector\sample_pairs`), comparing
each method to the camera's JPEG:

| Method | Colour ΔE vs camera | PSNR vs camera | Size |
|---|---|---|---|
| CR3's embedded `JpgFromRaw` (today's `convert-raw`) | 0.3 | 47.8 dB, SSIM 0.99 | ~1 MB |
| darktable-cli, defaults | 28 | — (no lens/distortion match) | 7–10 MB |
| rawpy / LibRaw | 19 | — | 4–8 MB |
| either, colour-fitted to the camera | ~6 | — | 4–5 MB |

The embedded image **is** the camera's JPEG (its Picture Style, white
balance, ALO, lens correction), only saved at about quality 82 instead of
97. At 200% it cannot be told apart from the camera JPEG. RAW developers
(darktable, LibRaw, and by extension Lightroom) have their own look and
don't apply Canon's corrections the same way. Canon DPP 4 would match at
full quality but has no command line. **Decision: keep the embedded-JPEG
method.** No new dependency.

**CR2 is out of scope (the user's decision).** Rebel T6 CR2s keep a
full-size `PreviewImage` (no `JpgFromRaw`), at quality ~80. Against DPP 4
output it matched colour (ΔE 0.9–2.2) but was visibly softer at 100% and
noisier at ISO 3200 (PSNR 33–42 dB). Only 232 CR2s exist. The new list
shows CR3s only.

## Goals

1. A **RAW → JPEG** list on the Storage page: pick a folder in the tree,
   see that folder's convertible CR3s as a grid (mockup A), pick photos.
2. **Every photo starts unpicked.** The user picks the ones to convert.
3. One action converts the picked photos: each CR3 becomes a same-stem
   JPEG beside it (the embedded image plus all of the CR3's metadata), the
   JPEG enters the catalog **with the CR3's faces and person decisions**,
   and the CR3 goes to the Windows Recycle Bin and leaves the catalog.
4. The same job is a CLI command, so the GUI stays a thin layer (as
   `recycle --list` is for pairs and bursts).

Mockup: `2026-10-31-raw-to-jpeg-mockups/raw-to-jpeg-grid.html`, option A
(images were served from E: and are missing here; use it for layout).

## Non-goals

- CR2, DNG or other RAW formats. Rendering RAW with a developer.
- Choosing JPEG quality or size; re-encoding the embedded image (it would
  only lose more).
- Automatic picking or rules ("convert all of 2026").
- Writing to D: directly. D follows on the next mirror sync.
- Saving picks between app sessions.

## Anti-goals (failures even if it works)

- **Losing the photo:** a CR3 recycled without a valid JPEG in place
  (exists, non-empty, decodes, same pixel size as the CR3's `ImageSize`).
- **Losing People work:** the user's confirmed faces, rejections and named
  people silently dropped because the photo changed rows.
- Overwriting any existing file. Touching a CR3 the user didn't pick, or one
  whose size/mtime changed since the list was built.
- Permanent deletion. Only the Recycle Bin.
- Writing to the catalog from the GUI process.
- A JPEG with no date, wrong orientation, or no GPS: all metadata comes
  over (today's `_copy_metadata` does this).

## What can be converted

Catalog rows with extension `.cr3` (case-insensitive) that:

- have **no same-stem JPEG/HEIC** in the same folder (those are pairs; the
  pairs list already removes the RAW). *My call.*
- are not in `mirror_offloaded` (the file isn't on the drive; tolerate a
  missing table as other readers do);
- carry an embedded `JpgFromRaw` (checked at convert time; a CR3 without
  one fails and stays).

A CR3 with a same-stem `.xmp` sidecar is shown with an **edited** badge (its
edits won't be in the JPEG) but can still be picked. *My call:* none exist
today. When such a CR3 is converted, its `.xmp` goes to the Recycle Bin with
it.

## Where it lives

The Storage page's right-column switch becomes **RAW + JPEG pairs | Bursts |
RAW → JPEG** (`gui/segment.py`). `storage.json` `"right"` gains `"raw"`.
Tree, scope line ("In **Canon/2025/05** · 412 CR3s without a JPEG · 7.8 GB",
**Show all**), scoping to a folder and everything under it, and the root
meaning "all" work as for pairs and bursts.

## The grid (mockup A)

- A wrapping grid of thumbnails (`gui/thumbs/` cache and loader), each with
  a pick box, file name and size; the **edited** badge where it applies.
  Order: by `taken_ts`, then relpath. *My call.*
- **Click** toggles a pick. **Shift-click** picks the range from the last
  click. **Ctrl+A** picks every photo in scope; **Esc** clears picks in
  scope. **Double-click** (or Enter) opens the photo large, reusing what
  `gui/library/viewer.py:MediaViewer` does, with ← / → stepping through the
  grid's scope and Space toggling the pick; Esc returns.
- Footer: "**N picked** · X of CR3 → about Y of JPEG · frees ~Z", **Clear**,
  **Convert N and recycle the CR3s**. Y is an estimate (5% of X is close for
  the R8; the builder may use the embedded image's length from ExifTool if
  it is cheap). Totals cover picks in every folder, adding "(in 3 folders)"
  when they span beyond the scope, as pairs/bursts do.
- Empty: "No CR3s without a JPEG in this folder" / "… in this gallery".

Picks live in memory, are separate from pair choices and burst marks, and
are counted by the page's existing leave guard. After any reload a pick is
kept only if its CR3 still exists with the same size and mtime and is
still convertible; dropped picks are reported once on the scope line, as
for bursts.

## The job

Pressing the button asks once ("Convert 48 photos to JPEG and move their
CR3s (0.9 GB) to the Recycle Bin?"), writes a list file in the format
`services/recycle.py:write_list` uses (relpath, size, mtime of each CR3),
and runs one CLI job with progress. Name: the builder's choice, for example
`convert-raw --list PATH --replace` on the catalog. When it finishes the
page reloads from the catalog.

Per CR3, in this order, and **each photo committed on its own** so a cancel
or crash leaves catalog and disk consistent:

1. **Check** as `recycle` does: row, list and disk agree on size and mtime;
   regular file inside the drive root; skip with a reason otherwise. Skip
   if a same-stem `.jpg`/`.jpeg` already exists on disk.
2. **Write the JPEG** beside it as `<stem>.JPG` (upper-case to match the
   camera's own names) from `JpgFromRaw`, copy all metadata from the CR3
   (`_copy_metadata`), and set the JPEG's mtime to the CR3's. *My call:*
   keeps date sorting and mirror behaviour natural. Batching ExifTool calls
   across photos is expected (as `convert_cr3` does today).
3. **Validate** the JPEG: decodes, non-empty, and its stored pixel size
   (ignoring EXIF Orientation) equals the CR3's `ImageSize`. On the R8 the
   embedded `JpgFromRaw` is full size, 6000×4000 (measured 2026-10-31; it is
   ~1–2 MB only because it is compressed harder), so this is strict
   equality. On failure delete the new JPEG, leave the CR3, record the
   failure.
4. **Catalog, then recycle, then commit.** In one transaction: add the
   JPEG's `media_items` row (analysed by ExifTool as import/sync do, after
   its mtime is set), **move the CR3's faces to it** (below), delete the
   CR3's row. Then recycle the CR3 (and its `.xmp`, if any) with
   `recycle_bin.move_to_recycle_bin`. Commit only if that succeeds. If the
   recycle fails, roll back, delete the new JPEG and report the failure, so
   the CR3 is exactly as before. *My call:* this avoids a CR3 left on disk
   with no row, which the next `sync` would re-add and re-scan, duplicating
   its faces.

Cancel stops between photos. JPEGs written by a batched ExifTool step for
photos not yet committed are deleted, so a cancel leaves no stray JPEGs.

The report gives converted / skipped / failed counts, bytes freed, and
failure lines; the GUI shows them as for recycling. The per-file Recycle Bin
capacity check that `recycle` does applies here too. The confirm dialog also
names E:'s Recycle Bin limit and warns when the picks' total exceeds it
(Windows would silently purge the oldest items to make room). *My call:*
the user split the 57 GB pairs cleanup into batches for this reason.

### Faces move with the photo

The JPEG has exactly the embedded image's pixels, so every face can move to
the new row instead of being re-detected:

- `faces` rows (box, score, embedding, `person_id`, `cluster_id`, `state`,
  suggestion, match score) are re-pointed or copied to the JPEG's
  `media_id`; `face_rejections` and any per-media face bookkeeping
  (`face_scan_state` at the current `FACE_DET_VERSION`, cached face crops)
  follow, so the JPEG is not rescanned.
- **Box coordinates are rotated only when the scan saw unrotated pixels.**
  `analysis/faces.py:decode` reads a RAW's embedded preview with
  `cv2.imdecode`, which applies the preview's **own** EXIF Orientation if
  it has one. Corrected 2026-10-31 after checking real files: the R8's
  embedded JPEG carries Orientation (e.g. `IMG_6778.CR3`: Rotate 90 CW),
  so its faces were found in the **oriented** frame, and E:'s
  `face_scan_state` records 4000×6000 for 1,029 portrait CR3s. The repo's
  `test.CR3` fixture has a preview with no Orientation tag, so its faces
  would be in the stored frame. The new JPEG gets the CR3's Orientation
  (step 2) and is read by `cv2.imread`, which applies it. So:
  rotate boxes by the CR3's Orientation (3 / 6 / 8) only when the
  extracted preview has no Orientation tag of its own; otherwise leave
  them. When `face_scan_state` has `img_w/img_h`, they must equal the
  frame this rule implies; if not, the photo is not converted. The test:
  a rescan of the JPEG would find the same box.
- **Embeddings are kept as they are.** They come from landmark-aligned
  face crops of identical pixels, so rotating the frame does not change
  them in any way that matters. *My call:* recomputing would need the face
  model and the GPU in this job.
- If faces cannot be moved for a photo, that photo is not converted
  (CR3 stays, failure reported). Losing face decisions is an anti-goal.

### Mirror

The job runs on E: only. The next `sync-mirror` copies each JPEG to D: and
moves D's CR3 to D's trash, exactly as for any other change: a file new on
E and a file deleted on E. Only a CR3 that was also edited on D comes up as
`changed_on_mirror`, as today. A large conversion trips the existing >5%
confirmation on that sync. That is expected, and the README section should
say so. Offloaded CR3s are never offered, so no JPEG is created at a path
the mirror treats as freed up.

## Constraints

- `CLAUDE.md` / `AGENTS.md`: the no-Qt/Qt split (selection of convertible
  CR3s lives in a Qt-free module beside `storage/pairs.py` and
  `storage/bursts.py`, unit-tested), colours only from `colors.py` and
  palette roles, GUI tests headless in `tests/gui`, CLI options in an
  argument group with help text (`test_every_field_has_a_hint`).
- Refuses on a `mirror`-role catalog (faces are moved, and D must only
  change through sync), as face commands do via `ensure_not_mirror`.
- Load the convertible CR3s in the Storage page's existing worker load
  (`gui/storage/view.py:load_storage` → `StorageData`); no new query on
  the GUI thread.
- No schema change and no migration.
- Fix in passing: `convert_cr3` asks only for `JpgFromRaw`, so it fails on
  every CR2 today. Fall back to `PreviewImage` for the existing
  `convert-raw` / export path (the new list still offers CR3 only).
- Update `README.md`'s Storage section.

## Left to the builder

- The CLI command's name and flags, and whether it extends `convert-raw` or
  is new, as long as `convert-raw SOURCES` keeps working.
- How the grid is drawn (a `QListView` in icon mode with a delegate is the
  obvious fit) and how the large view is reused.
- How faces are moved (re-point rows vs copy then delete) and how ExifTool
  work is batched, within the per-photo commit rule.
- Tests: the selection rules (pairs excluded, offloaded excluded, edited
  badge), list file contents, the job's per-photo order (JPEG validated
  before the CR3 is touched; CR3 kept on any failure; never overwrites),
  faces and decisions surviving conversion including a rotated photo, the
  mirror-role refusal, picks dropped on reload.
