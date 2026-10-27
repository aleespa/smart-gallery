# Mirror sync between two drives: design

Date: 2026-10-27. Status: draft for approval.

## Intent

The user keeps one photo/video library on two drives with identical folder
structure: **E** (external, fast, where they work) and **D** (local, slow, the
stable copy). Today they keep them in step by hand. They want Smart Gallery to:

1. Push changes made on E to D with a few buttons (no new section in the UI).
2. Run face recognition once, on E, and carry the result to D's catalog.
3. Import from an SD card into both libraries in one pass.
4. Carry deletions from E to D, including ones made in File Explorer.
5. Let E hold only part of the library (some years, or no videos) while D
   holds everything.

E is the working copy, D is the mirror. D must never lose a file by accident.

## Goals

- One-direction sync E -> D that is correct whether a change was made in the
  app or outside it (File Explorer, other tools).
- Every destructive step on D is previewed first and recoverable (D's trash).
- Faces/people scanned once on E appear on D without a GPU run on D.
- Import from a card writes to both drives, and degrades safely if D is absent.
- E can be a deliberate subset of D; "not kept on E" is never mistaken for
  "deleted".

## Non-goals

- Two-way merge as a general feature. Files that exist only on D are surfaced
  and the user decides; nothing flows D -> E automatically except *Bring back*.
- Continuous or background sync. Sync runs when the user presses a button.
- More than two drives, or a cloud target.
- A new top-level section/tab in the desktop app.

## Anti-goals (a failure even if it technically works)

- An unmounted, empty or wrong-letter E causing D's files to be removed.
- Deleting D's copy of a file the user only moved off E to save space.
- Face data on D diverging from E, or media upserts wiping face data
  (existing rule: face data never lives on `media_items`).
- Recopying gigabytes because a file was renamed or moved.

## Current state (checked 2026-10-27 against the real catalogs)

| | E | D |
|---|---|---|
| media items | 91,664 | 91,707 |
| faces | 88,845 | 216 |
| persons | 2,305 | 0 |

91,663 relpaths are common. 1 is only on E (`photos/canon/2025/02/img_9039.cr2`),
44 are only on D (new `2026/10` photos and `metadata.xlsx`). 11 common files
differ in size (recent `2026/09` JPGs). 14,935 differ in `mtime_ns`, mostly by
1-2 s (exFAT rounding) or exactly 1 h (DST), so **mtime cannot be compared
across drives**; size + `content_hash` can. `content_hash` already exists on
`media_items`; how many rows have it populated is not yet checked.

## Design

### Pairing

Each catalog stores its peer: the other drive's identity (not its letter, so a
remount survives) and a role (`working` for E, `mirror` for D). The sync
buttons appear only when the current gallery has a pairing.

The pairing also stores:

- **Baseline**: the relpaths (with size and content hash) that were identical
  on both drives at the end of the last successful sync. Needed to tell "deleted
  on E" from "new on D".
- **Keep-rule** (E only): what E is meant to hold, expressed with the existing
  `FilterOptions` (year, media type, folder). Default: everything.

### Sync to mirror (E -> D)

1. Refresh both catalogs with the existing stat-only `sync` walk, so changes
   made outside the app are seen. Abort if either root is missing or looks
   wrong (see Safeguards).
2. Compare E and D by relpath (NOCASE), then size, then `content_hash` when
   sizes match but identity is in doubt. Mtime is ignored across drives.
   Missing hashes are computed on demand, only for candidates.
3. Classify each relpath:

| In baseline | On E | On D | Meaning | Action |
|---|---|---|---|---|
| yes | gone, in keep-rule, not offloaded | yes | deleted on E | move D's copy to D's trash |
| yes | gone, offloaded | yes | moved off E on purpose | none |
| no | no | yes | new on D | ask: copy back to E, or leave |
| no | yes | no | new on E | copy to D, preserving mtime |
| yes | changed | unchanged | edited on E | overwrite D (old copy to trash) |
| yes | changed | changed | edited on both | conflict: user chooses |
| no | yes | yes, same hash | already in step | add to baseline |
| no | yes | yes, different | divergent | conflict: user chooses |

4. A delete on E plus an add with the same hash is a **move**: rename on D,
   keep faces attached (they follow relpath).
5. Show a preview (counts, sizes, the list per class). Apply only after the
   user confirms. Update the baseline after a successful apply.
6. Copy faces: after files are in step, transfer the face tables for each
   relpath from E to D (see Faces).

### Faces

Faces are scanned only on E (`scan-faces` / `find-faces`). Sync copies, per
relpath, rows of `faces`, `persons`, `face_scan_state`, `face_rejections`,
`face_carryover` and embeddings from E's catalog into D's, remapping
`media_items.id` and person ids. D's catalog is a derived copy: user
corrections made on E are the source of truth. The transfer only writes face
tables; D's `media_items` upsert path is untouched. Today D has 216 faces and
0 persons; the first sync replaces those with E's data (shown in the
preview). Face rows for a deleted/moved file follow the normal FK cascade.

### Import to both

The Import form gets a checkbox "Also copy to mirror", ticked when a pairing
exists. The card is read once. Files are placed on E (if they match the
keep-rule; otherwise recorded as offloaded) and on D at the same relpath, and
both catalogs are updated. If D is absent or full, E completes and the items
are left out of the baseline so the next sync finishes them; the report says
"D pending". Face scanning afterwards runs on E only.

### Partial E: keep-rule, Free up space, Bring back

- **Free up space on E...** The user picks years/types/folders. The app shows
  size freed and E's free space after. Before deleting anything on E it
  requires: D holds a verified copy (hash), the files' faces are scanned and
  synced to D. Then it deletes from E, sets `offloaded` on E's rows, and
  updates the keep-rule. This never touches D.
- **Offloaded rows stay in E's catalog** with no file on disk, keeping their
  faces, people and thumbnails. They show greyed out in E's library (user
  decision). The pre-sync refresh of E must not drop them.
- **Bring back to E...** copies a year/folder from D to E, clears
  `offloaded`, and adjusts the keep-rule.
- Import and sync honour the keep-rule as in the tables above.
- Both drives get a free-space check before copying; warn if it will not fit.

### Safeguards

- Refuse to sync if E or D root is not mounted, or its `.smart_gallery` catalog
  identity does not match the pairing, or a walk finds no files.
- Extra explicit confirmation if deletes on D exceed 5% of D's files
  (builder's/designer's call; make it a constant).
- "Delete" on D always means move to `<D>/.smart_gallery/trash`, purged after N
  days (builder to pick N; suggest 30). Never the Recycle Bin, never permanent
  in the same step.
- First run is a reconcile: hash all common files whose size or mtime differ,
  resolve the 1 / 44 / 11 cases above in the preview. The user accepted that
  this run is slow because it reads both drives.

### Where it lives (suggestions to match the codebase)

- New service `services/mirror.py` with `*Report` dataclasses, re-exported in
  `services/__init__.py`; progress via `ProgressEvent` steps (compare, copy,
  faces).
- CLI: `sync-mirror` (flags `--dry-run`/apply, `--yes`), `free-up`,
  `bring-back`, and an import option for mirroring. Options go in the proper
  argparse groups with `help=` text, so the generated forms work
  (`test_every_field_has_a_hint`).
- Schema: pairing/baseline tables and an `offloaded` marker. If `offloaded` is
  a `media_items` column, update `models.MediaItem`, `DB_COLUMNS`,
  `CREATE_MEDIA_ITEMS`, add a `migrate()` step and bump `SCHEMA_VERSION`. If
  it needs a filter field, implement it in both `matches()` and `build_where()`
  with parity tests. A separate table avoids the lock-step columns but the
  media upsert must still not reset it; the builder decides.
- GUI: buttons only, no new area. "Sync to D (n changes)" and "Free up space
  on E..." / "Bring back to E..." on the Library header or gallery controls;
  the preview is a dialog; offloaded items render greyed in the grid.

## Decided by the user

- E is the working copy, D the stable mirror; sync is manual, via buttons.
- Faces scanned once, on E, then carried to D.
- Changes made outside the app (File Explorer) must be picked up.
- Offloaded photos stay visible, greyed out, in E's library.
- The slow first reconcile is acceptable.

## Left to the builder

- Storage shape of pairing, baseline and `offloaded`; trash retention N.
- Exact hashing strategy and thresholds (the 5% guard is a proposal).
- Button placement and preview-dialog layout within the existing Library UI.
- Conflict-resolution UI for "edited on both" and "divergent".
- How to detect drive identity robustly across letter changes.

## Open items

- Confirm how many rows have `content_hash` populated on each drive.
- Decide the trash purge period.
