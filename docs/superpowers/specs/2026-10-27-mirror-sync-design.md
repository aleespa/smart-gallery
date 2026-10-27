# Mirror sync between two drives: design

Date: 2026-10-27 (revised after review the same day). Status: approved, revised.

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
- A question answered once in the preview is not asked again on the next sync.

## Non-goals

- Two-way merge as a general feature. Files that exist only on D, or changed
  only on D, are surfaced and the user decides; nothing flows D -> E
  automatically except *Bring back* and an explicit "copy back" choice.
- Continuous or background sync. Sync runs when the user presses a button.
- Several working drives (changes made in two places and merged), or a cloud
  target. One working drive with several mirrors is supported by the schema
  and the commands (see "Several mirrors"), though the user has one today.
- A new top-level section/tab in the desktop app.

## Anti-goals (a failure even if it technically works)

- An unmounted, empty or wrong-letter E causing D's files to be removed.
- Deleting D's copy of a file the user only moved off E to save space.
- Face data on D diverging from E, being mixed with D's own older face rows,
  or media upserts wiping face data (existing rule: face data never lives on
  `media_items`).
- Recopying gigabytes because a file was renamed or moved.
- Reading the whole library on every sync, or on the first one.

## Current state (checked 2026-10-27 against the real catalogs)

| | E | D |
|---|---|---|
| media items | 91,664 | 91,707 |
| faces | 88,845 (ids 1-88,845) | 216 (ids 1-216) |
| persons | 2,305 | 0 |
| schema version | 6 | 5 |
| rows with `content_hash` | 0 | 0 |
| `db_uuid` | `23c2e08b...` | `a00793ac...` (distinct) |

91,663 relpaths are common. 1 is only on E (`photos/canon/2025/02/img_9039.cr2`),
44 are only on D (new `2026/10` photos and `metadata.xlsx`). 11 common files
differ in size (recent `2026/09` JPGs). 14,935 differ in `mtime_ns`, mostly by
1-2 s (exFAT rounding) or exactly 1 h (DST); 76,728 have equal size and mtime.
So **mtime cannot prove two drives differ**, though equal size and mtime is
strong evidence they match. `content_hash` exists on `media_items` but is never
filled in by any code today. D's face ids overlap E's, so face ids cannot be
reused on D without first clearing D's face tables.

## Design

### Pairing

Each catalog stores its role (`working` for E, `mirror` for D) and its peers:
each peer's `db_uuid` (so a drive-letter change survives) and a hint path. A
working catalog may have several mirrors; a mirror has exactly one working
peer. The sync buttons appear when the current gallery has a pairing.

The working catalog (E) also stores:

- **Baseline**, one per mirror: for each relpath that was identical on both
  drives at the end of the last successful sync with that mirror, its size and
  **each drive's own mtime** at that moment. "Changed since the last sync" is then decided within one drive
  (size or mtime differs from that drive's snapshot), where mtime is reliable,
  so unchanged files are never hashed. The baseline also tells "deleted on E"
  from "new on D". It lives only on E.
- **Offloaded set**: relpaths deliberately kept off E, each with the mirrors
  that hold a verified copy. Their rows stay in E's catalog (with faces and
  thumbnails), drawn greyed out.
- **Offload rules**: filters (file type, extension, date range) describing
  what new imports should send to D only.

### Sync to mirror (E -> D)

1. Refresh both catalogs with the existing stat-only `sync` walk, so changes
   made outside the app are seen. Abort if either root is missing or looks
   wrong (see Safeguards). E's refresh keeps offloaded rows whose file is
   missing, and clears the offloaded flag of a row whose file is present again.
2. Compare. The E side is E's catalog **minus offloaded rows**; offloaded rows
   are classified separately. Rows match by relpath (NOCASE).
3. Classify (W = on E and not offloaded, O = offloaded on E, M = on D,
   B = in baseline; "changed" = size or mtime differs from that drive's
   baseline snapshot):

| Case | Meaning | Action |
|---|---|---|
| B, not W, not O, M | deleted on E | move D's copy to D's trash |
| B, W, not M | deleted on D | copy E's file to D again |
| B, W, M, neither changed | in step | none |
| B, W, M, only E changed | edited on E | overwrite D (old copy to trash) |
| B, W, M, only D changed | edited on D | ask: take E's version or keep D's |
| B, W, M, both changed | edited on both | conflict: user chooses |
| not B, not W, not O, M | new on D | ask: copy back to E, keep on D only, or decide later |
| not B, W, not M | new on E | copy to D, preserving mtime |
| not B, W, M | first sight of a common file | equal size and mtime: in step; otherwise compare hashes: equal is in step, different is a conflict |
| O, M, D unchanged | offloaded, safe | none |
| O, M, D changed | offloaded file edited on D | report only (E has no file to compare) |
| O, not M, D listed as a holder | offloaded file missing on D: **a kept copy is gone** | report loudly; never act |
| O, not M, D not a holder | kept on another mirror | none |

   Any "changed" case first compares hashes; if E's and D's bytes are in fact
   equal (only a timestamp moved), it is in step and the baseline is refreshed.
4. A file deleted on E plus a new file on E with the same size and hash as
   D's copy of the deleted one is a **move**: rename on D, keep faces attached
   (they follow relpath).
5. Show a preview (counts, sizes, the list per class). Apply only after the
   user confirms. Record each finished file in the baseline as it completes,
   so an interrupted sync keeps its progress.
6. Copy faces: after files are in step, transfer face data from E to D (see
   Faces).

"Keep on D only" turns the D-only file into an offloaded row in E's catalog
(built from D's row), so it shows greyed out on E and is not asked about
again. "Decide later" leaves it as is and it appears in the next preview.

### Faces

Faces are scanned only on E (`scan-faces` / `find-faces`). D's face data is a
derived copy:

- On the first transfer, D's face tables (`faces`, `persons`,
  `face_scan_state`, `face_rejections`, `face_carryover`) are emptied, and D's
  catalog records that its face data comes from E's `db_uuid`.
- After that, face and person ids are kept identical to E's (D never creates
  faces), so later transfers diff by id and only move what changed;
  embeddings are copied only for new faces. `media_id`s are remapped through
  relpath.
- Face scanning, filing and label/person commands refuse to run on a catalog
  whose role is `mirror`, so D can never create ids that clash with E's.
- The transfer only writes face tables; D's `media_items` upsert path is
  untouched. Face rows for a deleted/moved file follow the normal FK cascade.
- If D's own refresh invalidates faces of a file changed on D, the next
  transfer restores E's rows for it.

### Import to both

The Import form gets a "Mirror" option, on by default when a pairing exists
and D is reachable. The card is read once per file for E: files are placed on
E (unless they match an offload rule) at E's relpath, which is authoritative,
then copied to D from E's fresh copy (faster than re-reading the card). Files
matching an offload rule go from the card straight to D and become offloaded
rows on E. Both catalogs are updated and finished files join the baseline. If
D is absent, full, or already has a different file at that relpath, E
completes and the file stays out of the baseline, so the next sync finishes
or flags it; the report says "D pending". Face scanning afterwards runs on E
only.

### Partial E: offload rules, Free up space, Bring back

- **Free up space on E...** The user picks file types / date range / folders.
  The app shows size freed and E's free space after. Before deleting anything
  on E it requires: D holds a verified copy (same size and hash), and the
  images' faces are scanned on E and transferred to D. Then it deletes from E,
  marks the rows offloaded, and adds the filter (not folders) to the offload
  rules. This never touches D.
- **Offloaded rows stay in E's catalog** with no file on disk, keeping their
  faces, people and thumbnails, greyed out in E's library (user decision).
- **Bring back to E...** copies a selection from D to E, clears the offloaded
  flag, and removes a matching offload rule.
- Both drives get a free-space check before copying; D's check counts the
  space its trash already uses.

### Several mirrors

The user has one mirror today; the design keeps the door open for more
without a later migration:

- Peers, baselines and offloaded holders are stored per mirror (above).
- Sync, Free up and Bring back work on one pair at a time. With more than one
  mirror, the command names which one; the Mirror menu shows one "Sync to
  <label>" entry per mirror.
- Free up requires a verified copy on the chosen mirror and records it as the
  holder. Bring back copies from any reachable holder.
- Import copies to every reachable mirror; each one that fails counts as
  pending for that mirror.
- Each mirror records E as its face source, so face transfer is per pair.

### Safeguards

- Refuse to sync if E or D root is not mounted, or its catalog `db_uuid` does
  not match the pairing, or E's walk finds no files.
- Extra explicit confirmation if deletes on D exceed 5% of D's files.
- "Delete" on D always means move to `<D>/.smart_gallery/trash`, purged after
  30 days. Never the Recycle Bin, never permanent in the same step.
- First run is a reconcile: files with equal size and mtime on both drives
  (76,728 today) are accepted as identical without hashing; the ~14,946 that
  differ in size or mtime are hashed on both drives. The user accepted that
  this run is slow. The 1 / 44 / 11 cases above appear in its preview.
- The desktop app opens catalogs read-only (no migration), so anything that
  reads the new tables must cope with a catalog that does not have them yet.

### Where it lives (suggestions to match the codebase)

- New package `smart_gallery/mirror/` (types, hashing, planner, face
  transfer, peer lookup) and service `services/mirror.py` with `*Report`
  dataclasses; progress via `ProgressEvent` steps.
- CLI: `pair`, `sync-mirror` (preview / apply, choice flags), `free-up`,
  `bring-back`, and a mirror switch on `import`. Options go in the proper
  argparse groups with `help=` text, so the generated forms work.
- Schema v7: peers, baseline (keyed by mirror and relpath) and offloaded
  (keyed by relpath and holding mirror) tables; no `media_items` column, so
  the lock-step contracts and filter parity are untouched.
- GUI: one "Mirror" menu button in the Library header (no new area). On E:
  sync, free up, bring back. On D: a disabled entry saying to open the working
  drive to sync. On an unpaired gallery: "Pair with another gallery...". The
  preview is a dialog; offloaded items render greyed in the grid.

## Decided by the user

- E is the working copy, D the stable mirror; sync is manual, via buttons.
- Faces scanned once, on E, then carried to D.
- Changes made outside the app (File Explorer) must be picked up.
- Offloaded photos stay visible, greyed out, in E's library.
- The slow first reconcile is acceptable.

## Left to the builder

- Hash algorithm, trash retention (30 days proposed), delete guard (5%
  proposed).
- Button placement within the Library header and the preview-dialog layout.
- Exact wording in the preview.
