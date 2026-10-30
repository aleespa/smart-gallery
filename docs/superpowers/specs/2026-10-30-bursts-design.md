# Bursts: shots taken within seconds of each other

Date: 2026-10-30. Area: desktop app (`smart_gallery/gui/storage/`), plus a
no-Qt grouping module. No new CLI command: removal reuses `recycle`.

## Why

When the user holds the shutter down, the camera produces runs of nearly
identical photos. Today they find these runs by eye in Windows Photos, look
at the shots in detail, and decide which ones to keep. The app should find
the runs and lay them out for that decision. It should not make the decision.

A read-only pass over the E: catalog (81,809 images, 2026-10-30) gave the
sizes below. A group is a run of shots from one camera with no more than the
gap between one shot and the next:

| Gap | Groups | Photos in them | Space beyond one shot per group |
|-----|--------|----------------|---------------------------------|
| 1 s | 7,055 | 16,176 | 60 GB |
| 2 s | 8,720 | 20,762 | 85 GB |
| 5 s | 11,933 | 31,405 | 154 GB |
| 10 s | 15,938 | 45,556 | 230 GB |

Most groups have 2–4 shots, and the largest has 54. Contact sheets of
random groups showed that at 2 s almost every group really is the same
scene. At 5–10 s most groups are retakes, but some are different photos.
Two things got in the way: chained runs (an intervalometer at 2 s
intervals made one 44 s, 26-shot group, and a tennis serve ran on into the
next rally), and Google Photos `-edited` copies (about 3,500, each with the
same timestamp as its original).

## Goals

1. Find bursts **from catalog metadata only** (capture time, camera,
   folder). Never compare image content. This is the user's decision,
   because it has to run fast over the whole catalog.
2. Show them in the **Storage** tab, scoped by the folder tree on the left
   exactly as the RAW + JPEG pairs list is.
3. List them **largest first**.
4. **List view first** (mockup A): each burst is a row; clicking it expands
   the burst into a grid of medium shots where each shot toggles keep /
   remove.
5. From an expanded burst, open a **review view** (mockup B): one shot
   large, the burst as a filmstrip, keyboard stepping and marking.
6. **Every shot starts as keep.** The user marks the ones to remove.
7. Marked shots go to the **Windows Recycle Bin** in one action and leave
   the catalog, through the existing `recycle` command.

Mockup: `2026-10-30-bursts-mockups/burst-review.html` (a brainstorm-server
fragment; it showed real photos served from E: that are not in the repo, so
the images will be missing when it's opened from here. Use it for layout).

## Non-goals

- Comparing pixels, hashes or embeddings, or ranking shots by sharpness or
  closed eyes. No suggestion of which to keep.
- Bulk rules ("keep the first of every burst", "keep 1 per group").
- Grouping across folders or across cameras.
- Videos, and images with no capture time (`taken_ts IS NULL`).
- Google Photos `-edited` twins. They are a different problem and can be
  their own feature later.
- A CLI or TUI way to review bursts.
- Saving marks between app sessions.

## Anti-goals (failures even if it works)

- **Removing every shot of a burst.** At least one shot of each group must
  stay keep. The UI refuses to mark the last kept shot.
- Removing a shot the user didn't mark, or one whose file changed since the
  list was built (`recycle` already skips size/mtime mismatches).
- Permanent deletion. Only the Recycle Bin, via `recycle`.
- Writing to the catalog from the GUI process.
- Freezing the window. Grouping runs on the same worker load as the tree
  and pairs. Thumbnails and large previews load off the GUI thread.
- A slow first view. Grouping over 100k rows must take well under a second.
  It is one SQL query plus a linear pass.

## What counts as a burst

A no-Qt module, `smart_gallery/storage/bursts.py`, beside `pairs.py`.

**Candidates:** catalog rows with `media_type = 'image'`, a non-NULL
`taken_ts`, not listed in `mirror_offloaded` (those files aren't on the
drive; tolerate the table being missing, as other readers do), and whose
name stem does not end in `-edited` (case-insensitive).

**One shot = one stem in one folder.** All candidate files with the same
stem in the same folder (case-insensitive) are **one shot**, whatever their
image formats (CR3 + JPG, but also JPG + HEIC or PNG), plus the stem's
`.xmp` sidecar if any. Marking that shot removes all of its files. *My
call:* otherwise every RAW+JPEG burst shows each frame twice as a fake
"+0 s" burst, and removing "one" copy would leave its twin.

- Only **candidates** join a shot. A same-stem file that isn't one (a video
  such as a Live Photo `.MOV`, or an image with no `taken_ts`) is never
  shown and never removed with the shot. The sidecar is the only exception,
  and it follows the rules in `pairs.py`. *My call.*
- A shot is placed by its **earliest** `taken_ts`. Its camera is the
  `camera` of its first file by relpath, and if its files disagree, the
  first non-NULL value in that order. *My call:* in-camera RAW and JPEG
  always agree, so this only matters for odd copies.
- The badge lists the shot's formats, upper-cased and joined with "+"
  ("CR3+JPG").

**Grouping.** Sort the shots by (folder casefolded, camera, `taken_ts`,
relpath). Walk them in that order. A shot joins the current group when all
of these hold:

- same folder (`directory_rel`, case-insensitive). *My call:* this makes
  folder scoping exact, and it keeps the same photo copied into two folders
  from being treated as a burst;
- same `camera` (NULL counts as its own camera value);
- `taken_ts` minus the previous shot's `taken_ts` is between 0 and the
  **gap**;
- `taken_ts` minus the group's first shot's `taken_ts` is at most the
  **span cap**, 10 s. *My call:* this splits intervalometer runs and
  serve-then-rally sequences. A shot that would break the cap starts a new
  group. The cap should be one constant so it can be changed.

Groups with one shot are dropped.

**Gap:** default **2 s** (the user's decision). The user can change it from
1 to 10 s, in whole seconds, with a spin box above the list. The value is
remembered in `storage.json` as `"burst_gap"`. Changing it regroups from the
data already loaded, without a new query. Changing it while marks exist asks
"Discard N marks?" first, because the groups the marks belong to change.

**Timestamps are whole seconds** (`taken_ts` is epoch seconds; sub-second
EXIF is not stored). Shots in the same second are ordered by relpath, which
follows the camera counter. Each shot shows its offset from the first shot
("+0 s", "+1 s").

**Order:** largest first, by shot count, then total bytes, then earliest
`taken_ts`.

## Where it lives

The right column of the Storage page gets a two-way switch at its top,
**RAW + JPEG pairs | Bursts** (`gui/segment.py:segment`). *My call:* this keeps the
page's tree + list layout, and the tree's selection scopes whichever list
is showing. The last-shown list is remembered in `storage.json` as
`"right": "pairs" | "bursts"`. The default is pairs, so the existing page
stays as it is.

The tree, the scope line ("In **Canon/2025** · 412 bursts · 1,180 photos ·
9.6 GB", with **Show all**), scoping to a folder and everything under it,
and the root meaning "all" all work as they do for pairs. A burst is in
scope when its folder is.

## List view

Each burst is a row:

- a thumbnail strip of its shots, as many as fit (`gui/thumbs/` cache and
  loader, which already handle CR2/CR3 previews);
- "**19 shots · 1 s**" (count, then last offset), the folder, the total
  size (`format_bytes`);
- its state: "not decided" until a shot is marked, then "keeping 3 of 19"
  (green) and "removing 16 · 120 MB".

**Clicking a row** expands it in place, and clicking it again collapses it.
*My call:* one burst is expanded at a time. The expanded area is a wrapping
grid of medium thumbnails (about 150 px wide). Each one shows its file
name, its offset, a **keep** (green) or **remove** (red, image dimmed) tag,
and a "CR3+JPG" badge when the shot has both files. **Clicking a shot**
toggles it. Toggling the last kept shot is refused, and the tag briefly
shows "keep at least one".

The expanded area has a **Review** button, which opens the review view on
its first shot. Double-clicking a shot, or pressing Enter on the row, opens
it on that shot.

Empty list: "No bursts in this folder" or "… in this gallery".

## Review view

It fills the Storage page in place of the tree and list (the user asked to
"expand" into it). *My call:* the page swaps to it in its existing
`QStackedWidget`. It can also be a large dialog if that turns out simpler,
and the builder chooses. Either way it shares the marks with the list.

- Header: "Burst 3 of 412 · 19 shots · 1 s · Canon/2025/05", "keeping 3",
  **◀ Previous group**, **Next group ▶**, **Back to list**.
- One shot large, fit to the area. **Space** toggles 100% zoom (pan by
  dragging). CR2/CR3 show their embedded preview, which is large enough to
  judge focus. *My call:* reuse what `gui/library/viewer.py:MediaViewer`
  and `gui/thumbs/` already do to decode; the builder decides how much.
- Under it: file name, offset, shutter, aperture, ISO (from the catalog),
  and **K Keep** / **X Remove** buttons showing the current state.
- A filmstrip of the burst, with the current shot outlined, removed shots
  dimmed, and a green or red dot on each.
- Keys: ← / → step through shots; K keep; X remove (refused on the last
  kept shot, as in the list); Enter or Page Down next group; Page Up
  previous group; Esc back to the list, with that group expanded and
  scrolled into view.
- Previous and Next follow the list's current scope and order.

## Removing

- The **footer**, as for pairs: "N marked · frees X", **Clear marks**,
  **Move N files to Recycle Bin**. N counts files (a RAW+JPEG shot plus its
  sidecar is up to 3), and X is their bytes. Untouched bursts are left
  alone. The totals cover marks in every folder, not only the visible
  scope, and add "(in 3 folders)" when they span beyond it.
- Pressing the button asks once ("Move 48 files (1.1 GB) to the Recycle
  Bin?"), writes the list file (`services/recycle.py:write_list`) and runs
  `recycle --list` as a job, as `pairs_view` does today. When it finishes,
  the page reloads from the catalog. Removed shots disappear, and marks on
  shots that failed stay so the user can retry.
- **Marks after any reload (my call).** Every reload regroups: the burst
  job finishing, a pairs recycle, or a sync or import elsewhere. Groups can
  split or merge when a shot disappears, so after regrouping:
  1. a mark is kept only if its shot still exists with exactly the same
     files (relpath, size and mtime unchanged), as pair choices are;
  2. a mark on a shot that is no longer in any burst is dropped, so a mark
     is never sent to the Bin unless it is visible;
  3. if a group comes out with every shot marked, all marks in that group
     are dropped, so at least one shot always stays;
  4. if any marks were dropped, the scope line says so once ("12 marks
     cleared because their bursts changed").

  Reloads only ever drop marks. They never add one.
- **Marks and pair choices are separate (my call).** Each list has its own
  footer and its own button, which applies only its own marks. After either
  one runs, the reload drops the other list's marks for files that are now
  gone.
- **Marks live in memory only**, as pair choices do. The page's existing
  leave guard (`ctx.add_leave_guard`, `pairs_view.confirm_leave`) also
  counts burst marks: "You have N unapplied choices. Discard them?"
  Switching between the Pairs and Bursts lists does not discard anything.

## Constraints

- Follow `CLAUDE.md` / `AGENTS.md`: the no-Qt/Qt split (`storage/bursts.py`
  has no Qt and is unit-tested), colours only from `colors.py` and palette
  roles (green/red for keep/remove from the existing semantic colours, or
  added there), and GUI tests in `tests/gui`, headless.
- Load bursts in the same worker call as the tree and pairs
  (`gui/storage/view.py:load_storage` → `StorageData`). Store each shot's
  fields there so regrouping on a gap change needs no query.
- No schema change and no migration.
- Update `README.md`'s Storage section.

## Left to the builder

- Whether the list view is a `QListView` with a delegate (as `pairs_view`
  is) or something else, and how the expanded grid is drawn.
- How the review view decodes and caches large previews.
- Whether marks are kept as a set of file ids or of shot keys, as long as
  the leave guard, the footer and the list file agree.
- Tests: grouping rules (gap, span cap, camera, folder, same-second order,
  `-edited`, offloaded rows, RAW+JPEG as one shot, NULL camera), the
  last-kept-shot rule, the regroup-discards-marks prompt, and the list file
  contents.
