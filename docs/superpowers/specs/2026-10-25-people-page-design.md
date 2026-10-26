# People page: find photos by who is in them

Date: 2026-10-25. Area: desktop app (`smart_gallery/gui/people/`), the face
services (`services/scan_faces.py`, `services/cluster_faces.py`), the face
tables, and a few new CLI commands.

This replaces the placeholder `gui/people/view.py`. It is "project 2" in the
desktop-app roadmap (face classification redesign + People UI).

## Why

The user's library mixes photos with and without people. They want what
Google Photos gives: the computer finds the faces, groups them by person, the
user names the groups, and then picks one or more people to see only the
photos those people are in.

Mistakes will happen (two cousins in one group, one person split over three
groups). The user wants to reclassify them on the page, and wants the matching
to get better as they correct it.

## Decisions made with the user

- **Group first, name second.** The computer clusters all faces; the user
  names the groups it found. The sample-folder flow (`identify-faces
  --samples`) stays as a CLI shortcut but is not part of the page.
- **Layout B**: a people list on the left, one person's page on the right
  (`2026-10-25-people-page-mockups/people-layout.html`, option B).
- **Reclassify on the page**: a Photos | Faces switch on a person's page; in
  Faces, select face crops and move them (`reclassify.html` in the same
  folder). Merge and split from the left list.
- **Corrections teach the matcher.** Every face the user confirms or moves
  becomes a reference example for that person; every "Not this person" is
  remembered. User decisions are locked: no automatic run ever changes them.
- **New photos**: a very confident match is filed automatically; a close but
  unsure match waits as a suggestion ("Is this Alice? ✓ ✕"); no match goes to
  an unnamed group, or is hidden if it is a tiny or one-off face.
- **Local only, 8 GB NVIDIA card.**

The mockups are fragments for the brainstorm server and come without its
frame styles; open them for layout, not polish. Circles and coloured squares
stand in for face crops, gray squares for photos.

## Model choice (Claude's call, accepted by the user)

Keep InsightFace `buffalo_l` (SCRFD-10G detector + ArcFace R50, 512-d
embeddings). It is already verified on the user's RTX 5060 (~45 ms per 24 MP
photo, under 1 GB of VRAM), and it is close to the best open model that runs
locally. A newer model (`antelopev2`, AdaFace) would gain a little accuracy
for a lot of setup risk. "Getting better" comes from using the user's
corrections at match time, not from training: the embeddings stay fixed and
the reference set grows. Keep the model behind `FaceScanner` so a later
swap is a `FACE_DET_VERSION` bump plus a rescan.

## Goals

1. A **People** page (layout B) that lists named people and unnamed groups,
   shows a person's photos, and lets the user pick several people at once.
2. **Find faces** from the page: one job that scans new images and then files
   their faces (auto / suggest / unnamed), with the usual progress panel.
3. **Name** a group; naming it with an existing person's name offers a merge.
4. **Reclassify**: confirm, reject ("Not Alice"), move to another or a new
   person, ignore (not a face / don't care); merge people; split a person.
5. **Learning matcher**: matching uses the user's confirmed faces as
   examples and respects their rejections. User decisions are never undone.
6. **Show in Library**: opens the Library with the selected people as its
   people filter.

## Non-goals

- Training or fine-tuning a model.
- Naming people from sample folders on the page (the CLI keeps it).
- Face search from an arbitrary photo ("who is this?").
- Pets, objects, or scene tags.
- Running the face scan automatically during Import or Sync. Sync keeps never
  touching the GPU; it already reports how many images are pending a scan.
- Video faces.
- Cloud services or downloading anything beyond the existing model files.

## Anti-goals (failures even if it works)

- **An automatic run changing something the user decided**: moving a
  confirmed face, re-suggesting a face for a person the user rejected it
  from, renaming a person, or deleting a named person.
- **A people list full of strangers**: background faces, crowds, posters.
  Tiny, low-score and one-off faces stay out of the list by default.
- **Freezing the window**: loading embeddings, crops and photo grids all run
  off the GUI thread.
- **Writing to the catalog from the GUI process.** Every write goes through a
  CLI job (`gui/jobs.py`), as in the rest of the app.
- **Losing corrections** on a rescan, an edit to the file, a model swap, a
  schema migration, or a re-cluster (see "Carrying corrections over").
- **Requiring the faces extra to open the app.** Without it the page explains
  what to install and stays read-only over any face data already present.

## Page layout

```
┌ People ─────────────────────────────────── [Find new faces] ┐
│ ┌ list ───────────────┐ ┌ person ─────────────────────────────────────┐
│ │ Search…              │ │ (cover) Alice   842 photos · 2009–2026        │
│ │ NAMED                │ │         [Rename] [Show in Library]  Photos|Faces│
│ │ (o) Alice      842   │ │ Is this Alice?  12 suggestions   [Review]     │
│ │ (o) Bob        310   │ │ ▣ ▣ ▣ ▣ ▣ ▣                                   │
│ │ (o) Mum      1,204   │ │ ▣ ▣ ▣ ▣ ▣ ▣   (photo or face grid)            │
│ │ TO NAME (40)         │ │ …                                             │
│ │ (o) Unnamed     96   │ │                                               │
│ │ (o) Unnamed     41   │ │                                               │
│ │ Show hidden faces    │ │                                               │
│ └──────────────────────┘ └───────────────────────────────────────────────┘
```

The page header (the shared one every area has) gets **Find new faces** in
its `header_controls`, plus a status line such as "1,204 images not scanned
yet" when there are some.

### Left list

- Two sections: **Named** (sorted by photo count, largest first) and
  **To name** (unnamed groups, largest first). Each row: circular cover face
  crop, name, photo count. A small badge on a named person shows how many
  suggestions are waiting.
- A search box filters by name.
- Ctrl/Shift+click selects several people. With several selected the right
  side shows the photos they are in (see "Several people") instead of one
  person's page.
- Drag one row onto another, or select several and choose **Merge**
  (context menu), to merge. The merged person keeps the target's name; if
  both are named and the names differ, ask which to keep.
- Context menu: Rename, Merge, Split, Hide (an unnamed group: hide it as
  "strangers"), Delete (named person: their faces become unassigned, user
  rejections pointing at them go away).
- **Show hidden faces** at the bottom opens the hidden pile (tiny, low-score,
  one-off and ignored faces) as a face grid, so the user can rescue one by
  moving it to a person.

### Right side: one person

- Header: cover crop, name (click or Rename to edit), photo count, year
  range, **Show in Library**, and the **Photos | Faces** switch.
- **Suggestions strip** when the person has unconfirmed suggestions: "Is
  this Alice? 12 suggestions [Review]". Review shows the suggested face crops
  one row at a time with ✓ / ✕ per face and "✓ all" / "✕ all" for the row.
- **Photos** (default): a thumbnail grid of every photo with at least one
  face assigned to the person, newest first, reusing the Library's grid and
  thumbnail loader. Double-click opens the Library viewer.
- **Faces**: a grid of face crops assigned to the person, sorted **least sure
  first** by default (other sort: newest). Multi-select, then a bar with
  **Not <name>**, **Move to… ▾** (people list + "New person…"), **Ignore**.
  Faces the user has confirmed are marked (a small tick) and listed last.
- For an unnamed group the header shows an inline "Add a name" field instead
  of the name.

### Right side: several people

A photo grid of photos that contain **all** the selected people
(intersection), with a switch to **any** of them (union), and a count.
**Show in Library** passes the same people and the same switch. (Claude's
call: "all" is the default because that is what Google Photos does when you
pick two people.)

**Library people filter gains all/any (user's decision).** Today
`db/where.py` only selects photos with any of the named people. Add a
`FilterOptions` field for the match mode (any = union, the current
behaviour and the default for existing queries; all = intersection) and a
visible any/all switch next to the people choice in the filter panel. Follow
the filter-parity rules in CLAUDE.md: `is_query_empty`, `cli._filter_parser`
(e.g. `--people-match any|all` on export and report), `cli.build_filter_query`,
and the parity tests. Like `people`, it is SQL-only and stays out of
`matches()`. Apply it to `person_ids` as well.

**Unnamed groups in Show in Library (Claude's call).** Show in Library is
enabled only when every selected row is a named person; the Library panel
filters by name and could not show an unnamed group. The several-people grid
on the People page still works with unnamed groups.

### Empty and missing states

- No face data: an explanation and **Find faces**.
- Faces extra not installed (`gui/paths.py:faces_available()` is false): an
  explanation with the install command; Find faces is disabled.
- Scan done but nothing clustered yet: Find faces still runs the filing step.
- Drive not connected: the shared "gallery not connected" state.

## The face model behind the page

### Face states

Each face ends up in exactly one of:

| state | meaning | who sets it |
|---|---|---|
| `user` | assigned to a person by the user (confirm, move, name a group) | user only |
| `auto` | assigned automatically, very confident | the filing step |
| `suggested` | best guess shown for review, not assigned | the filing step |
| `cluster` | in an unnamed group | the filing step |
| `hidden` | too small, low score, or a one-off; not shown in lists | the filing step |
| `ignored` | the user said "not a face / don't care" | user only |

Naming an unnamed group turns its faces into `auto` for that person, not
`user`: the user named the group without looking at every face. (Claude's
call. The user's ✓ on a face, or moving it, is what makes it `user`.)

Rejections ("Not Alice") are separate records `(face_id, person_id)`. A face
can carry several. A rejected face becomes `cluster` or `hidden` again and is
never assigned or suggested to that person.

Storage: new columns on `faces` (state, suggested person, match score) and a
new `face_rejections` table, with a schema-version bump and a `migrate()`
step that maps existing data: faces of a **named** person become `auto`,
faces of an unnamed `persons` row become `cluster` (keeping that row as the
group), and faces with no person stay in the pool for the next filing run. Keep everything
out of `media_items` (see CLAUDE.md). Update `FACE_COLUMNS` and the parity
tests together with the `CREATE TABLE`.

### Filing faces (the "learning" step)

Run after every scan, by **Find new faces** and by a CLI command. For every
face that is not `user` or `ignored`:

1. **Quality gate.** Faces under a minimum size (short side of the box, in
   original pixels) or under a detection score go to `hidden`. Starting
   values are the builder's call, tuned on real photos; expose them as
   constants.
2. **Match against people.** Score the face against each named person using
   that person's `user` faces as examples (best k-nearest similarity), plus
   their sample reference centroid if they have one, plus the centroid of
   their `auto` faces as a fallback for people with no `user` faces yet.
   Drop any person the face was rejected from. Also drop a person when the
   face is closer to that person's rejected faces than to its examples.
3. **Decide.** Best score ≥ **auto threshold** and ahead of the runner-up by
   the margin → `auto`. Between the **suggest threshold** and the auto
   threshold → `suggested` for that person. Below → step 4. Starting values
   from the existing defaults (`DEFAULT_MATCH_THRESH = 0.5`, margin `0.05`);
   the builder sets the two thresholds and documents them.
4. **Group the rest.** Cluster the remaining faces (HDBSCAN, existing code)
   together with the existing unnamed groups so groups stay stable between
   runs. Groups below a minimum size become `hidden`.

**Sticky assignments (Claude's call).** A later run never demotes an `auto`
face to `suggested`, `cluster` or `hidden`, and never re-applies the quality
gate to it. It moves an `auto` face only when another person beats its
current person by the margin *and* passes the auto threshold. So a group the
user just named can grow but never shrinks on its own. A `user` face never
moves.

**Unnamed groups keep their identity (Claude's call).** An unnamed group is
a `persons` row with `name` NULL, as today. Existing groups keep their id;
step 2 also scores leftover faces against unnamed groups (same thresholds),
so new faces join them. HDBSCAN runs only on what is still left and only
creates new groups; it never re-forms, merges or splits existing ones. Only
the user merges or splits groups.

**Hidden groups (Claude's call).** "Hide" on an unnamed group is a user
decision: the `persons` row gets a hidden flag and stays out of the list;
its faces get state `ignored`. Step 2 scores leftover faces against hidden
groups too, and a match goes to the hidden pile quietly instead of creating
a new stranger group. "Show hidden faces" lists hidden groups so one can be
un-hidden.

Re-running the step with no new faces and no new corrections changes
nothing.

### What each correction does

| action | effect |
|---|---|
| ✓ on a suggestion | face → `user` for that person |
| ✕ on a suggestion | rejection recorded; face back to the pool |
| Not <name> | rejection recorded; face back to the pool |
| Move to <person> | face → `user` for that person (and a rejection for the old one) |
| Move to New person… | creates a named person; face → `user` for it |
| Ignore | face → `ignored` |
| Name a group | sets the person's name; its faces stay `auto` |
| Merge | faces move to the target; rejections of either are kept for the target, except where a face is `user` for either one (the user's assignment wins and that rejection is dropped) |
| Split | re-clusters the person's non-`user` faces; the sub-group closest to the person's `user` faces (or the largest, if there are none) stays; the others become new unnamed groups, and each moved face gets a rejection against the original person so the next run does not pull it back |

After a batch of corrections, the page re-runs the filing step for the
affected people **without a rescan** (embeddings are already stored), so the
suggestions update. This can be a quick job; it does not need the GPU.

## CLI

Every write from the page is a CLI job. New commands (names are the
builder's call; keep them in the right argument groups with help text so the
TUI and the Actions forms pick them up):

- `file-faces <drive>`: the filing step above.
- `find-faces <drive>`: `scan-faces` then `file-faces`, one job with both
  progress steps. **Find new faces** runs this.
- `label-faces <drive> --faces ID... (--to PERSON_ID | --new-person NAME |
  --not PERSON_ID | --ignore)`: the corrections.
- Existing `name-person`, `merge-persons`, `split-person`, `delete-person`
  stay and gain the rejection and state rules above.

`cluster-faces` and `identify-faces` keep working and must respect `user`
and `ignored` faces and rejections. `cluster-faces --rebuild` must not touch
`user` faces or named people. Update `FACES.md` and the README for the new
workflow (scan → file → name → correct) with the sample folders as an
optional shortcut.

## Carrying corrections over a rescan (user's decision)

A file's faces are deleted and rescanned when its size or mtime changes
(`sync` → `invalidate_faces_by_relpath`), and every face is rescanned after a
`FACE_DET_VERSION` bump. Both must keep the user's decisions:

- Before deleting a file's faces, keep its decisions (state `user` or
  `ignored`, the person, and its rejections) with the old box, keyed by
  `media_id`. Use a separate carry-over table so it survives the delete and
  is not touched by the media upsert.
- When the file is scanned again, match each old face to the new detection
  with the highest box overlap (IoU, in original-image pixels; boxes are
  rescaled if the image size changed). Above an overlap threshold, copy the
  state, person and rejections to the new face. Old faces with no match are
  dropped and counted in the job report.
- Remove carried-over rows once they are applied or once the media row is
  gone (FK cascade).
- A model swap uses the same path; the overlap threshold may need to be
  looser across models (builder's call, documented).

Tests: a metadata-only edit plus sync plus scan keeps a `user` face, an
`ignored` face and a rejection; a cropped image that moves the face
drops it and reports it.

## Face crops

The page needs face crops, and the 256 px media thumbnails are too small for
faces in group shots. Cache crops beside the thumbnails, e.g.
`<root>/.smart_gallery/faces/<id // 1000>/<face_id>.jpg`, with some margin
around the box, about 160 px. Write them during the scan (the decoded image
is already in memory) and generate them lazily off the GUI thread for faces
scanned before this change. Boxes are in original-image pixels, so a lazy
crop from a RAW's embedded preview must scale the box.

## Constraints

- Core install must import without the faces extra; the page itself needs
  only numpy and stored data. GPU work happens only in the CLI job.
- Read-only opens never migrate; the page reads through `ctx.open_repo()`.
- `JobRunner` runs one job at a time. Corrections made while a scan runs must
  either queue or be refused with a clear message (builder's call).
- Corrections should feel quick: batch the user's clicks into one job where
  possible, and update the page optimistically, then refresh when the job
  ends.
- GUI tests in `tests/gui`, offscreen; face-logic tests with synthetic
  embeddings, no GPU (as in `tests/unit/test_faces.py`).

## Tests (minimum)

- Filing step: quality gate, auto / suggested / cluster decisions with
  synthetic embeddings; `user` and `ignored` faces untouched; rejections
  respected; idempotent rerun; a new `user` example pulls a former
  suggestion to `auto`.
- Each correction in the table above, including merge and split keeping
  `user` faces and rejections.
- Migration from the current schema keeps assignments and names.
- `cluster-faces --rebuild` and `identify-faces --reassign` leave `user`
  faces alone.
- People page: list sections and counts, single-person view, several-people
  all/any, Faces view selection and actions issue the right CLI job, empty
  states, faces-extra-missing state.

## Left to the builder

- Exact thresholds, the size and score gates, and k for nearest examples.
- Command and option names, crop size and margin, sort details.
- How the suggestion review looks beyond "rows of crops with ✓ / ✕".
- Whether rejections also reshape the clustering step (nice to have).
- Visual polish, within the existing theme (`palette(highlight)`, serif
  headings).
