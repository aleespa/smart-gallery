# Face recognition — grouping photos by person

smart_gallery detects faces in your catalogued photos, identifies people from
sample photos you provide, and lets you filter/browse by person. Faces are
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
The first thing `scan-faces` logs is the bound execution provider:
```
Face model ready — execution provider: CUDAExecutionProvider
```
If you instead see `CPUExecutionProvider`, it will warn loudly — fix the CUDA
setup before scanning a big library (CPU is many times slower). To hard-fail
instead of falling back, set `SG_FACES_REQUIRE_GPU=1`.

---

## 2. The workflow

Assuming your photos already live in a catalog (`smart-gallery init <drive>` /
`sync`), point a drive letter or folder at each step. Examples use `E:/`.

```bash
# 1) Detect + embed faces for every image (resumable; safe to re-run / kill).
uv run smart-gallery scan-faces E:/

# 2) Make one directory per person, named as it should appear in the catalog.
#    Put clear, single-face examples in each directory. Multiple examples help.
#       E:/face_samples/Alice/*.jpg
#       E:/face_samples/Bob/*.jpg

# 3) Match catalog faces against those examples.
uv run smart-gallery identify-faces E:/ --samples E:/face_samples

# 4) Review the results. Each person lists its 3 best catalog photos as
#    clickable links (OSC-8) — click one to open the image in your viewer.
uv run smart-gallery people E:/
#   [   1]  Alice                    842 faces
#            IMG_0001.JPG   IMG_2207.JPG   IMG_3310.JPG     <- each is clickable
#   [   2]  Bob                      310 faces
#            ...
# Show more/fewer thumbnails per person with --samples N (default 3).

# 5) Browse / export by person.
uv run smart-gallery export --from E:/ --to D:/AlicePhotos --people Alice
uv run smart-gallery report E:/ --to alice.xlsx --people Alice

# Optional: group faces which do not match any supplied samples.
uv run smart-gallery cluster-faces E:/

# Optional manual corrections.
uv run smart-gallery merge-persons E:/ 1 7 9

#    If one person has mixed faces, split it or delete it for re-identification.
uv run smart-gallery split-person E:/ 12
uv run smart-gallery delete-person E:/ 12

# Export any person by its id (from `people`):
uv run smart-gallery export --from E:/ --to D:/Cluster7 --person-ids 7
uv run smart-gallery export --from E:/ --to D:/Some --person-ids 7 12 30
```

`identify-faces` requires exactly one detected face in each sample photo;
photos with zero or multiple detected faces are skipped and reported. Each
immediate subdirectory name becomes a person name. Faces below the similarity
threshold or too close to another person's score remain unassigned. Tune the
cosine threshold with `--threshold` (default `0.5`) and the required lead over
the runner-up with `--margin` (default `0.05`). Re-running processes only
unassigned faces, preserving existing assignments. Use `--reassign` to
reconsider faces currently assigned to people named by the supplied sample
directories; unmatched faces from those people become unassigned. Other people
are left alone. Sample embeddings are stored separately from catalog centroids,
so later updates do not replace the supplied references.

`--people NAME [NAME ...]` and `--person-ids ID [ID ...]` are accepted by
`export` and `report`. Combining several ids/names selects media containing any
of them.

---

## 3. Keeping it up to date (new photos)

`sync` never runs the GPU. After it ingests new/changed files it tells you how
many images are pending a face scan:

```bash
uv run smart-gallery sync E:/
# ... 1,204 image(s) pending face scan — run `smart-gallery scan-faces`.

uv run smart-gallery scan-faces E:/                 # only scans the new images
uv run smart-gallery identify-faces E:/ --samples E:/face_samples
# Or match new faces to known centroids, including sample references:
uv run smart-gallery cluster-faces E:/ --incremental
```

* `scan-faces` skips images already scanned (tracked in `face_scan_state`), so
  it only processes what's new.
* `identify-faces` matches new unassigned faces against the supplied sample
  references and preserves existing assignments. `cluster-faces --incremental`
  is also available and prefers sample references where present.
* `cluster-faces --rebuild` is an optional unsupervised workflow; it removes
  current people, including their names and sample references.
* `sync` automatically drops face data for files whose pixels changed, and the
  database FK cascade removes faces for deleted files.

---

## 4. Fixing assignments

Any matcher can occasionally make a wrong assignment, especially for similar
looking people or low-quality faces. You may see an **impure person** — one
person id that mixes several different people. This usually comes from a few
low-quality faces (blurry, profile, very small) bridging distinct people, or
thresholds that were a touch too loose. Fixes, least to most disruptive:

**A. Split just that cluster (surgical — leaves all other people alone):**
```bash
uv run smart-gallery split-person E:/ 12          # re-cluster person 12, tighter
uv run smart-gallery split-person E:/ 12 --eps 0.25 --min-cluster-size 4
```
It re-clusters only person 12's faces with stricter settings and replaces it
with the resulting sub-clusters (new ids); faces that no longer group cleanly
become unassigned. Then `people` to review and `name-person` the good ones.

**B. Delete the junk cluster entirely:**
```bash
uv run smart-gallery delete-person E:/ 12   # faces become unassigned, not deleted
uv run smart-gallery cluster-faces E:/ --incremental   # optionally re-home them
```

**C. Optionally re-cluster unassigned faces or rebuild all clusters:**
```bash
uv run smart-gallery cluster-faces E:/ --rebuild --pca 128 --min-cluster-size 8
# or DBSCAN with a tighter radius:
uv run smart-gallery cluster-faces E:/ --rebuild --algo dbscan --eps 0.38 --min-samples 5
```
Stricter settings reduce over-merging, at the cost of more ungrouped faces. A
full rebuild removes sample-created people and their labels; use it only when
that is intended.

**D. Drop weak faces at the source** (if bad merges persist): raise
`SG_FACES_MIN_SCORE` (e.g. `0.6`) and re-scan, so blurry bridge-faces never enter
clustering:
```bash
SG_FACES_MIN_SCORE=0.6 uv run smart-gallery scan-faces E:/ --rescan
uv run smart-gallery cluster-faces E:/
```

## 5. Tuning (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SG_FACES_PROVIDERS` | `CUDAExecutionProvider,CPUExecutionProvider` | ONNX Runtime EPs, in order. Set to `TensorrtExecutionProvider,...` if CUDA won't engage. |
| `SG_FACES_DET_SIZE` | `640` | Detector input size (px). Larger finds smaller faces, slower. |
| `SG_FACES_MIN_SCORE` | `0.5` | Drop detections below this confidence. |
| `SG_FACES_MIN_PX` | `24` | Drop faces whose smaller side is under this many px. |
| `SG_FACES_DECODE_WORKERS` | `min(8, cores-1)` | CPU threads decoding images in parallel. |
| `SG_FACES_REQUIRE_GPU` | unset | `1` = abort instead of falling back to CPU. |

Clustering knobs are flags on `cluster-faces`: `--algo {hdbscan,dbscan}`,
`--min-cluster-size` (HDBSCAN), `--eps` / `--min-samples` (DBSCAN).

---

## 6. Where the data lives

Two new tables in each drive's `gallery.db` (schema v2; old catalogs migrate
automatically on first open):

* **`faces`** — one row per detected face: bounding box, detection score, a
  512-d L2-normalized ArcFace embedding (BLOB), and the `person_id` it belongs
  to. `media_id` links to `media_items.id`.
* **`persons`** — one row per person: optional `name`, a catalog-face centroid,
  an optional sample-photo reference centroid, face count, and a cover face for
  listings.
* **`face_scan_state`** — bookkeeping for resumable scans.

Because faces are keyed to `media_items.id` (not stored as media columns), the
catalog's media schema is untouched and `sync`/`import` can never overwrite face
data.
