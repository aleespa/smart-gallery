# Face recognition — grouping photos by person

smart_gallery can detect every face in your catalogued photos, group them into
people automatically, let you name each person, and then filter/browse by
person. Faces are stored **in the same per-drive `gallery.db`**, linked to the
existing `media_items` rows — nothing in your media catalog changes.

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

# 2) Group the faces into people (creates unnamed "person" clusters).
uv run smart-gallery cluster-faces E:/

# 3) Review the clusters. Each person lists its 3 best sample photos as
#    clickable links (OSC-8) — click one to open the image in your viewer.
uv run smart-gallery people E:/
#   [   1]  (unnamed)                 842 faces
#            IMG_0001.JPG   IMG_2207.JPG   IMG_3310.JPG     <- each is clickable
#   [   2]  (unnamed)                 310 faces
#            ...
# Show more/fewer thumbnails per person with --samples N (default 3).

# 4) Name the ones you recognise.
uv run smart-gallery name-person E:/ 1 "Alice"
uv run smart-gallery name-person E:/ 2 "Bob"

# 5) If one person was split into two clusters, merge them (keep id 1).
uv run smart-gallery merge-persons E:/ 1 7 9

# 6) Browse / export by person — works anywhere the normal filters work.
uv run smart-gallery export --from E:/ --to D:/AlicePhotos --people Alice
uv run smart-gallery report E:/ --to alice.xlsx --people Alice

# Export an UNNAMED cluster by its id (from `people`) before you've named it:
uv run smart-gallery export --from E:/ --to D:/Cluster7 --person-ids 7
uv run smart-gallery export --from E:/ --to D:/Some --person-ids 7 12 30
```

`--people NAME [NAME ...]` (by name) and `--person-ids ID [ID ...]` (by cluster
id, works before naming) are both accepted by `export` and `report`. Combining
several ids/names selects media containing **any** of them.

---

## 3. Keeping it up to date (new photos)

`sync` never runs the GPU. After it ingests new/changed files it tells you how
many images are pending a face scan:

```bash
uv run smart-gallery sync E:/
# ... 1,204 image(s) pending face scan — run `smart-gallery scan-faces`.

uv run smart-gallery scan-faces E:/                 # only scans the new images
uv run smart-gallery cluster-faces E:/ --incremental  # attach new faces to known people
```

* `scan-faces` skips images already scanned (tracked in `face_scan_state`), so
  it only processes what's new.
* `cluster-faces --incremental` matches each new face to the nearest existing
  named/unnamed person by centroid — fast, and it preserves your names.
* Run a full `cluster-faces --rebuild` occasionally to re-derive clusters from
  scratch (this drops names — re-name afterwards).
* `sync` automatically drops face data for files whose pixels changed, and the
  database FK cascade removes faces for deleted files.

---

## 4. Tuning (environment variables)

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

## 5. Where the data lives

Two new tables in each drive's `gallery.db` (schema v2; old catalogs migrate
automatically on first open):

* **`faces`** — one row per detected face: bounding box, detection score, a
  512-d L2-normalized ArcFace embedding (BLOB), and the `person_id` it belongs
  to. `media_id` links to `media_items.id`.
* **`persons`** — one row per person: optional `name`, a centroid embedding for
  matching, face count, and a cover face for listings.
* **`face_scan_state`** — bookkeeping for resumable scans.

Because faces are keyed to `media_items.id` (not stored as media columns), the
catalog's media schema is untouched and `sync`/`import` can never overwrite face
data.
