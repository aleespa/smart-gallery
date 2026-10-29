# Face recognition — grouping photos by person

smart_gallery detects faces in your catalogued photos, sorts them into people (you name the
groups and correct mistakes; sample photos are an optional shortcut), and lets
you filter/browse by person. Faces are
stored **in the same per-drive `gallery.db`**, linked to existing `media_items`
rows — nothing in your media catalog changes.

It runs on the GPU (NVIDIA, via InsightFace) and is **verified working on an RTX
5060 / Blackwell**. A 90k-photo library scans in roughly **15–40 minutes**
(decode/disk-bound, not GPU-bound).

---

## 1. One-time setup

### Install the optional `faces` extra
```bash
uv sync --extra faces
```
This pulls InsightFace, `onnxruntime-gpu` (CUDA 13 build), the NVIDIA cuDNN/cuBLAS
runtime wheels, OpenCV, scikit-learn/HDBSCAN and rawpy. The core CLI does **not**
need any of this — it is lazy-loaded only by the face commands.

### GPU prerequisites (NVIDIA)
* Recent NVIDIA driver (R570+ for RTX 50-series). CUDA 12.8+/13 runtime.
* The cuDNN/cuBLAS DLLs ship in the `nvidia-*` wheels above and are loaded
  automatically (`onnxruntime.preload_dlls()` in `analysis/faces.py`).
* **Do not** also `pip install onnxruntime` (the CPU build) — it shadows the GPU
  build and silently forces CPU mode. The project already excludes it via
  `[tool.uv] override-dependencies`; just don't add it back.

### Verify the GPU is actually used
The first thing the scan (`find-faces` / `scan-faces`) logs is the bound execution provider:
```
Face model ready — execution provider: CUDAExecutionProvider
```
If you instead see `CPUExecutionProvider`, it will warn loudly — fix the CUDA
setup before scanning a big library (CPU is many times slower). To hard-fail
instead of falling back, set `SG_FACES_REQUIRE_GPU=1`.

---

## 2. The workflow

Assuming your photos already live in a catalog (`smart-gallery init <drive>` /
`sync`), point a drive letter or folder at each step. Examples use `E:/`. The
desktop app's **People** page does every step below; the commands are what it
runs for you.

**1. Find faces.** In the app press **Find new faces**; on the command line:

```bash
# Scan every image not yet scanned (GPU, resumable), then sort the faces.
uv run smart-gallery find-faces E:/
```

This is `scan-faces` followed by `file-faces`. The sorting step (`file-faces`,
no GPU) puts each face in a person, a suggestion, or a new unnamed group.
`file-faces --no-group` only matches faces to existing people and groups and
forms no new ones.

**2. Name the groups.** Unnamed groups are people the program found but you
have not named. Name one on the People page, or:

```bash
uv run smart-gallery people E:/                       # list people and groups
uv run smart-gallery name-person E:/ 7 Alice
uv run smart-gallery name-person E:/ 9 Alice --merge  # 9 is Alice too
uv run smart-gallery hide-person E:/ 12               # a group of strangers
uv run smart-gallery hide-person E:/ 12 --unhide
```

Naming a group keeps its faces as `auto`, not confirmed by you. Each person
lists its 3 best photos as clickable links (OSC-8); `--samples N` changes that.

**3. Fix what is wrong.** On a person's page the **Faces** view shows every
face, least sure first; confirm, move, say "not this person" or ignore. The
suggestions strip lets you accept or reject close matches. The same
corrections from the command line:

```bash
uv run smart-gallery label-faces E:/ --faces 101 102 --confirm 7
uv run smart-gallery label-faces E:/ --faces 101 --to 9          # move
uv run smart-gallery label-faces E:/ --faces 55 --new-person Carol
uv run smart-gallery label-faces E:/ --faces 55 --not 7          # not this person
uv run smart-gallery label-faces E:/ --faces 88 --ignore
uv run smart-gallery label-faces E:/ --list corrections.json     # as the app writes it
```

Give exactly one action with `--faces`; an unknown person id is an error. With
`--list`, an entry for a person deleted since the list was written is skipped
and counted. `label-faces` only records the corrections; run
`file-faces E:/ --no-group` afterwards (the app does this after each batch) to
sort the remaining faces again without a rescan, so the suggestions improve.
Add `--refile` to do that in the same step and the same undo batch. Whole-person fixes: `merge-persons E:/ 1 7 9` merges 7 and
9 into 1; `delete-person E:/ 12` returns its faces to the pool;
`split-person E:/ 12` splits a mixed person (see section 4).

`undo-faces E:/` reverts the latest `label-faces` batch, including faces the
re-sorting (`--refile`) moved. It refuses, changing nothing, if those faces
changed since (find faces, merge, rescan...). `undo-faces E:/ --forget` then
drops that last step without changing any faces, so older steps can be undone.

**4. Filter by person.** In the Library, the people filter selects photos with
a person; with several people it offers *any* (at least one) or *all* (every
one). On the command line:

```bash
uv run smart-gallery export --from E:/ --to D:/AlicePhotos --people Alice
uv run smart-gallery export --from E:/ --to D:/Both --people Alice Bob --people-match all
uv run smart-gallery report E:/ --to alice.xlsx --people Alice
uv run smart-gallery export --from E:/ --to D:/Some --person-ids 7 12 30
```

`--people NAME [NAME ...]` and `--person-ids ID [ID ...]` are accepted by
`export` and `report`. `--people-match any|all` (default `any`) decides whether
media must contain any of the people or all of them.

### Optional shortcut: sample folders

If you would rather seed people from photos than name groups, make one
directory per person, named as the person should appear, with clear
single-face examples:

```bash
#   E:/face_samples/Alice/*.jpg
#   E:/face_samples/Bob/*.jpg
uv run smart-gallery identify-faces E:/ --samples E:/face_samples
```

`identify-faces` requires exactly one detected face in each sample photo;
photos with zero or several faces are skipped and reported. Faces below the
similarity threshold or too close to another person's score stay unassigned
(`--threshold`, default `0.5`; `--margin`, default `0.05`). Re-running handles
only unassigned faces; `--reassign` reconsiders faces held by the people the
sample folders name. It never moves a face you decided (`user` or `ignored`)
and respects your rejections. Sample embeddings are stored separately, so
later updates do not replace them.

`cluster-faces` still works but is now a wrapper over `file-faces`;
`--incremental` files faces into existing people only, and `--rebuild` returns
undecided faces to the pool, drops unnamed groups and sorts again, keeping named
people and your own decisions. Its old clustering knobs (`--algo`, `--eps`,
`--min-samples`, `--min-cluster-size`, `--pca`) are accepted but ignored.

### Face states

Every face is in exactly one state:

| State | Meaning | Set by |
|---|---|---|
| `user` | You confirmed it, moved it or named it in the Faces view | you only |
| `auto` | Assigned automatically, very confident (also faces of a group you named) | sorting |
| `suggested` | A best guess for review, not assigned | sorting |
| `cluster` | In an unnamed group | sorting |
| `hidden` | Too small, low score, or in a tiny or hidden group | sorting |
| `ignored` | You said it is not a face or not worth filing | you only |
| `new` | Not sorted yet (fresh scan, or returned to the pool) | sorting |

**Your decisions are locked.** Sorting never changes a `user` or `ignored`
face. "Not this person" is remembered as a rejection: the face is never
assigned or suggested to that person again, and a face closer to a person's
rejected faces than to their examples is not matched to them either.

**Assignments are sticky.** A later run never demotes an `auto` face to a
suggestion, group or hidden, and moves it only if another named person beats
its current one by the margin and passes the auto threshold. The exception is
your own "not this person": a face rejected from its person (for example
after a merge brought it back) is sorted again without that person. A face
that was hidden (too small or weak, or a one-off that fits no group) stays
hidden, so it remains under *Show hidden faces*; `cluster-faces --rebuild`
sorts hidden faces again. Naming a group, or merging it into a named person,
turns its group faces into that person's `auto` faces. So a group you
named can grow but never shrinks on its own. Existing groups keep their id and
only you merge or split them. `split-person` keeps the person and the
sub-group closest to your confirmed faces; the other sub-groups become new
unnamed groups, and moved faces get a rejection against the original person.
Faces that fit no sub-group stay with the person; if the faces form no
separate sub-groups, nothing changes.

**Corrections survive rescans.** When a photo changes (`sync` sees a new size
or mtime) or the face model is bumped, its faces are rescanned, but your `user`
and `ignored` decisions and rejections are carried over, and so are the faces
of named people (they come back as `auto`): each old face is matched to the
new detection by box overlap (IoU of at least 0.5) and the decision is
copied, your own decisions first. Old faces with no match are dropped and counted in the job
report. A photo that fails to decode keeps its faces and any pending decisions.

**Thresholds** (`FilingConfig` in `smart_gallery/analysis/filing.py`):

| Setting | Default | Meaning |
|---|---|---|
| `auto_thresh` | `0.50` | Minimum score for an automatic assignment |
| `suggest_thresh` | `0.35` | Minimum score to suggest a person |
| `margin` | `0.05` | Required lead over the runner-up |
| `k` | `5` | Nearest examples averaged per person |
| `min_face_px` | `40` | Faces whose short side is smaller go to `hidden` |
| `min_det_score` | `0.60` | Detections below this go to `hidden` |
| `min_group_size` | `3` | Smaller new groups go to `hidden` |

Scores are cosine similarities of the 512-d embeddings. Re-running with no
new faces and no new corrections changes nothing.

---

## 3. Keeping it up to date (new photos)

`sync` never runs the GPU. After it ingests new or changed files it tells you
how many images are pending a face scan:

```bash
uv run smart-gallery sync E:/
# ... 1,204 image(s) pending face scan.

uv run smart-gallery find-faces E:/     # scans only the new images, then sorts
```

* `find-faces` skips images already scanned (tracked in `face_scan_state`), so
  it only processes what is new. New faces are matched to your people first:
  very confident ones are filed automatically, close ones become suggestions,
  the rest join or form groups. Use `file-faces` alone to re-sort without
  scanning.
* `sync` drops face data for files whose pixels changed (keeping your
  decisions for the rescan, see above), and the database FK cascade removes
  faces for deleted files.

---

## 4. Fixing assignments

Any matcher can make a wrong assignment, especially for similar-looking people
or low-quality faces. You may see an **impure person**, one id that mixes
several people. Fixes, least to most disruptive:

**A. Correct single faces.** The Faces view, or `label-faces` (section 2).
Moving a face to the right person also teaches the matcher.

**B. Split just that person (surgical):**
```bash
uv run smart-gallery split-person E:/ 12
uv run smart-gallery split-person E:/ 12 --eps 0.25 --min-cluster-size 4
```
It re-clusters only person 12's faces that you have not confirmed. The person
keeps the sub-group closest to the faces you confirmed (or the largest) and
the faces that fit no sub-group; the others become new unnamed groups. Name
the good ones.

**C. Delete the junk group:**
```bash
uv run smart-gallery delete-person E:/ 12   # faces return to the pool, not deleted
uv run smart-gallery file-faces E:/         # optionally re-home them
```

**D. Re-sort all undecided faces:**
```bash
uv run smart-gallery cluster-faces E:/ --rebuild
```
Named people and your own decisions stay.

**E. Drop weak faces at the source** (if bad merges persist): raise
`SG_FACES_MIN_SCORE` (e.g. `0.6`) and re-scan:
```bash
SG_FACES_MIN_SCORE=0.6 uv run smart-gallery scan-faces E:/ --rescan
uv run smart-gallery file-faces E:/
```
`--rescan` scans every image again. Your decisions and the faces of named
people are carried over to the new faces (by box overlap), and named and hidden
people stay; unnamed groups are deleted and rebuilt by `file-faces`.

## 5. Tuning (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SG_FACES_PROVIDERS` | `CUDAExecutionProvider,CPUExecutionProvider` | ONNX Runtime EPs, in order. Set to `TensorrtExecutionProvider,...` if CUDA won't engage. |
| `SG_FACES_DET_SIZE` | `640` | Detector input size (px). Larger finds smaller faces, slower. |
| `SG_FACES_MIN_SCORE` | `0.5` | Drop detections below this confidence. |
| `SG_FACES_MIN_PX` | `24` | Drop faces whose smaller side is under this many px. |
| `SG_FACES_DECODE_WORKERS` | `min(8, cores-1)` | CPU threads decoding images in parallel. |
| `SG_FACES_REQUIRE_GPU` | unset | `1` = abort instead of falling back to CPU. |

Clustering knobs (`--algo`, `--min-cluster-size`, `--eps`, `--min-samples`) are flags on
`split-person`; `cluster-faces` accepts them but ignores them. Filing thresholds are
in section 2.

---

## 6. Where the data lives

Tables in each drive's `gallery.db` (old catalogs migrate automatically on the
first read-write open):

* **`faces`** — one row per detected face: bounding box, detection score, a
  512-d L2-normalized ArcFace embedding (BLOB), and the `person_id` it belongs
  to, plus its `state`, `suggested_person_id` and `match_score`.
  `media_id` links to `media_items.id`.
* **`persons`** — one row per person: optional `name`, a catalog-face centroid,
  an optional sample-photo reference centroid, face count, and a cover face for
  listings.
* **`face_scan_state`** — bookkeeping for resumable scans.
* **`face_rejections`** — `(face, person)` pairs for "not this person".
* **`face_carryover`** — your decisions for a photo being rescanned, kept
  until they are applied to the new detections.

Because faces are keyed to `media_items.id` (not stored as media columns), the
catalog's media schema is untouched and `sync`/`import` can never overwrite face
data.
