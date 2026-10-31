# RAW → JPEG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A third Storage list, **RAW → JPEG**, where the user picks CR3s in a grid and one job replaces each with the camera's embedded JPEG, moving its faces to the JPEG and sending the CR3 to the Recycle Bin.

**Architecture:** A Qt-free selection module (`storage/raw_jpeg.py`) beside `pairs.py`/`bursts.py`; a face-move helper on the repository; a new service `services/raw_to_jpeg.py` run by a new CLI command `raw-to-jpeg DRIVE --list PATH` (same list format as `recycle`); a grid view `gui/storage/raw_view.py` wired into `StorageView` as a third switch position. The GUI only writes the list file and starts the job.

**Tech Stack:** Python 3.13, SQLite, ExifTool (batch argfiles via `run_exiftool_argfile`), Pillow, PySide6, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-raw-to-jpeg-design.md`

## Global Constraints

- CR3 only in the new list and job (`.cr3`, case-insensitive). CR2 is out of scope.
- The JPEG is the CR3's embedded `JpgFromRaw`, never re-encoded; all metadata copied with `-tagsFromFile SRC -all:all` (as `convert_cr3._copy_metadata`).
- Output name `<stem>.JPG` beside the CR3; never overwrite any existing file; JPEG mtime set to the CR3's.
- Validation: decodes, non-empty, stored pixel size (no EXIF rotation) equals the CR3's `ImageWidth`×`ImageHeight` read by ExifTool.
- Per photo, in one transaction: insert JPEG row → move faces → delete CR3 row → recycle CR3 (+ `.xmp`) → commit. Recycle failure: roll back, delete the JPEG, CR3 untouched.
- Cancel stops between photos; uncommitted JPEGs are deleted.
- Refuse on a `mirror`-role catalog (`mirror.faces.ensure_not_mirror`).
- Only the Recycle Bin, never permanent deletion. The GUI never writes the catalog.
- No schema change, no migration. No new dependency.
- Hex colours only in `smart_gallery/colors.py`; style sheets use palette roles.
- Every new CLI option in an argument group with `help=` text (`test_every_field_has_a_hint`).
- Format and lint only touched files: `uv run black -t py313 <files>`, `uv run ruff check <files>`.
- Commit messages: a plain imperative sentence (repo style, e.g. "Group shots taken within seconds into bursts"), ending with the session's attribution lines.

## Review Focus

1. A same-stem `.JPG`/`.jpeg` appears on disk after the list was built (user exported one) → that CR3 is skipped, nothing overwritten. Pinned in Task 4.
2. A CR3 with no `JpgFromRaw`, or a truncated/undecodable extraction → CR3 kept, its partial JPEG deleted, failure reported. Pinned in Task 4.
3. The Recycle Bin refuses the CR3 (file locked) → catalog rolled back, JPEG deleted, CR3 row and faces intact. Pinned in Task 4.
4. Cancel arrives mid-batch → photos already committed stay converted, no stray JPEGs for the rest. Pinned in Task 4.
5. A portrait shot (Orientation 6) with a named face → after conversion the face box is in the JPEG's oriented frame, the person and rejections are unchanged, and the cached crop is removed so it is recut upright. Pinned in Task 3 (math, DB) and Task 4 (real fixture `test.CR3`, Orientation 6).

---

### Task 1: `convert-raw` falls back to `PreviewImage` for CR2

**Files:**
- Modify: `smart_gallery/services/convert_cr3.py` (`_extract_previews`)
- Test: `tests/integration/test_convert_raw.py`

**Interfaces:**
- Produces: `extract_embedded(sources: list[Path], suffix: str = ".jpg") -> str` (renamed from `_extract_previews`, public; writes `<dir><stem><suffix>`; returns ExifTool stderr). Task 4 calls it with `suffix=".JPG"`.

- [ ] **Step 1: Write the failing test** in `tests/integration/test_convert_raw.py`:

```python
def test_cr2_uses_preview_image(tmp_path, copy_image):
    raw = copy_image(tmp_path, "test.CR2", "Shots/IMG_0002.CR2")
    report = convert_cr3([tmp_path])
    assert report.converted == 1
    assert report.failures == []
    assert raw.with_suffix(".jpg").stat().st_size > 0
```

- [ ] **Step 2: Run it:** `uv run pytest tests/integration/test_convert_raw.py -m exiftool -v` → FAIL (no embedded JpgFromRaw). If `test.CR2` does have `JpgFromRaw`, change the test to assert, via `exiftool -b -JpgFromRaw`, that the fallback path is exercised with a CR2 lacking it (strip it with `exiftool -JpgFromRaw= -overwrite_original` on the copy).
- [ ] **Step 3: Implement.** ExifTool can't fall back within one `-b -TAG -w` call, so run the `JpgFromRaw` pass, then a second `-b -PreviewImage -w %d%f<suffix>` pass over the sources whose output is still missing or empty (`-w` never overwrites, so delete empty outputs first). Keep one process per pass. Update the module and `convert_cr3` docstrings.
- [ ] **Step 4: Run** `uv run pytest tests/integration/test_convert_raw.py tests/test_export.py -v` → PASS.
- [ ] **Step 5: Commit** "Fall back to the preview image when a RAW has no JpgFromRaw".

---

### Task 2: Which CR3s can be converted (Qt-free)

**Files:**
- Create: `smart_gallery/storage/raw_jpeg.py`
- Test: `tests/unit/test_storage_raw_jpeg.py`

**Interfaces:**
- Consumes: `storage.pairs.FileRef`, `SIDECAR_EXT`; `repo.offloaded_relpaths()` (tolerate `sqlite3.OperationalError` as `bursts.load_shots` does).
- Produces:
  - `@dataclass(frozen=True) class RawCandidate: folder: str; stem: str; raw: FileRef; sidecar: FileRef | None; taken_ts: int | None` with `key -> str` (raw relpath casefolded), `edited -> bool` (sidecar is not None), `size_bytes -> int` (raw + sidecar).
  - `load_raw_candidates(repo) -> list[RawCandidate]`, ordered by (`taken_ts` with None last, relpath).
  - `in_scope(c: RawCandidate, folder: str | None) -> bool` (same rule as `pairs.in_scope`).
  - `replacement_entries(cands: Iterable[RawCandidate]) -> list[FileRef]` — the CR3s only (the job finds the sidecar itself), sorted by relpath casefolded.
  - `reconcile_picks(cands: Sequence[RawCandidate], picks: AbstractSet[str], before: Mapping[str, RawCandidate]) -> set[str]` — keeps a pick only if its key is still a candidate with the same raw size and mtime.
  - `JPEG_RATIO = 0.05`; `estimate_jpeg_bytes(cands) -> int` = round(sum(raw.size_bytes) × `JPEG_RATIO`).

- [ ] **Step 1: Write the failing tests** (use `repo`, `make_item`; insert rows with `repo.upsert_many`):

```python
def test_lone_cr3_is_a_candidate(repo, make_item): ...      # A/IMG_1.CR3 alone -> 1 candidate, edited False
def test_cr3_with_jpeg_or_heic_is_not(repo, make_item): ... # IMG_2.CR3+IMG_2.jpg, IMG_3.CR3+img_3.HEIC -> none
def test_cr2_is_not(repo, make_item): ...
def test_offloaded_cr3_is_not(repo, make_item): ...        # add to mirror_offloaded via repo's mirror store
def test_sidecar_marks_edited(repo, make_item): ...        # IMG_4.CR3 + IMG_4.xmp -> edited True, size includes xmp
def test_order_by_time_then_relpath(repo, make_item): ...
def test_reconcile_drops_changed_and_gone(): ...           # size change -> dropped; key gone -> dropped; same -> kept
def test_estimate(): assert estimate_jpeg_bytes([cand(size=20_000_000)]) == 1_000_000
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_storage_raw_jpeg.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** with one query over `media_items` (id, relpath, ext, size_bytes, mtime_ns, media_type, taken_ts), grouping by (folder casefolded, stem casefolded) as `pairs.find_pairs` does; a group is a candidate group when it has ≥1 `.cr3` and no `.jpg`/`.jpeg`/`.heic`; each `.cr3` in it is one candidate (sidecar attached only when the group has exactly one CR3).
- [ ] **Step 4: Run** the tests → PASS.
- [ ] **Step 5: Commit** "Find CR3s that have no JPEG beside them".

---

### Task 3: Move a photo's faces to another media row

**Files:**
- Create: `smart_gallery/faces_move.py` (no Qt, no cv2)
- Test: `tests/unit/test_faces_move.py`

**Interfaces:**
- Produces:
  - `rotate_box(box: tuple[float, float, float, float], orientation: int, width: int, height: int) -> tuple[float, float, float, float]` — `box` is (x, y, w, h) in the stored frame `width`×`height`; returns it in the EXIF-oriented frame. Raises `ValueError` for orientations 2, 4, 5, 7 (mirrored).
  - `move_faces(conn: sqlite3.Connection, src_id: int, dst_id: int, orientation: int, width: int, height: int) -> list[int]` — returns the moved face ids. Does **not** commit (the caller's transaction).

Rotation (stored W×H → oriented):

```
1 / None: (x, y, w, h)
3:        (W - x - w, H - y - h, w, h)
6:        (H - y - h, x, h, w)          # Rotate 90 CW
8:        (y, W - x - w, h, w)          # Rotate 270 CW
```

`move_faces` must: `UPDATE faces SET media_id=dst, bbox_*=rotated WHERE media_id=src` (ids kept, so `face_rejections` and the undo tables stay valid); re-point `face_carryover` rows with rotated boxes and swapped `img_w/img_h` for 6/8; move `face_scan_state` to `dst` (delete any row for `dst` first) with `img_w/img_h` swapped for 6/8. Face crops are deleted by the caller after commit (Task 4), since files aren't part of the transaction.

- [ ] **Step 1: Write the failing tests:**

```python
@pytest.mark.parametrize("o,expected", [
    (1, (10, 20, 30, 40)),
    (3, (6000 - 10 - 30, 4000 - 20 - 40, 30, 40)),
    (6, (4000 - 20 - 40, 10, 40, 30)),
    (8, (20, 6000 - 10 - 30, 40, 30)),
])
def test_rotate_box(o, expected):
    assert rotate_box((10, 20, 30, 40), o, 6000, 4000) == expected

def test_rotate_box_refuses_mirrored(): ...  # 2, 4, 5, 7 -> ValueError

def test_six_then_crop_center_lands_on_same_pixel():
    # a point inside the box, rotated with PIL's Image.transpose(ROTATE_270)
    # (what Orientation 6 displays as) on a tiny synthetic image, is inside the rotated box

def test_move_keeps_ids_person_state_and_rejections(repo, make_item): ...
def test_move_updates_scan_state_and_carryover_for_portrait(repo, make_item): ...
def test_move_does_not_commit(repo, make_item): ...  # rollback restores media_id
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_faces_move.py -v` → FAIL.
- [ ] **Step 3: Implement** both functions in `smart_gallery/faces_move.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** "Move a photo's faces to another row, rotating boxes to the new frame".

---

### Task 4: The `raw-to-jpeg` job

**Files:**
- Create: `smart_gallery/services/raw_to_jpeg.py`
- Modify: `smart_gallery/services/__init__.py` (re-export), `smart_gallery/cli.py` (parser + `_handle_raw_to_jpeg` + `_HANDLERS` + `DETAIL_LOG_COMMANDS`), `smart_gallery/progress.py` (`CONVERTING = "Converting to JPEG"`), `smart_gallery/forms.py` only if a label is needed
- Test: `tests/unit/test_raw_to_jpeg.py`, `tests/integration/test_raw_to_jpeg_exiftool.py` (marker `exiftool`)

**Interfaces:**
- Consumes: `services.recycle.RecycleEntry`, `read_list`, `_skip_reason` (or an extracted public `skip_reason`), `recycle_bin.move_to_recycle_bin`, `supports_recycle_bin`, `recycle_bin_capacity`; `convert_cr3.extract_embedded(..., suffix=".JPG")` and `_copy_metadata`; `analysis.extract.analyze_paths(paths, drive_root=root)`; `faces_move.move_faces`; `face_crops.crop_path`; `mirror.faces.ensure_not_mirror`.
- Produces:
  - `@dataclass class RawToJpegReport: converted: int = 0; skipped: int = 0; freed_bytes: int = 0; faces_moved: int = 0; failures: list[str]`.
  - `replace_raws(repo, entries: Sequence[RecycleEntry], progress_callback=None) -> RawToJpegReport`.
  - CLI: `smart-gallery raw-to-jpeg DRIVE --list PATH`; list deleted once work starts, kept when refused (as `_handle_recycle`). Help: "Replace listed CR3 photos with their camera JPEG; the CR3 goes to the Recycle Bin and its faces move to the JPEG."
  - `BATCH = 50` (ExifTool work per batch).

Order per batch (the algorithm the tests pin):

```
ensure_not_mirror(repo); refuse (ValueError) without a Recycle Bin
for each batch of entries:
    check each: skip reason as recycle does; ext must be .cr3; skip if <stem>.jpg/.jpeg exists
               (any case) on disk; fail if size > Recycle Bin capacity
    extract_embedded(ok, ".JPG"); _copy_metadata(pairs); read CR3 ImageWidth/ImageHeight/Orientation
               (one ExifTool -j call); os.utime(jpeg, ns=(cr3 atime, cr3 mtime))
    validate each JPEG (Pillow: open, verify, size == CR3 W×H); on failure delete it, record
    items = analyze_paths(valid jpegs, drive_root=root)
    for each valid photo:
        with transaction: upsert JPEG item; move_faces(src, dst, orientation, W, H);
                          delete CR3 row (and .xmp row); move_to_recycle_bin(cr3) (+ .xmp)
                          -> commit; on OSError: rollback, delete JPEG, record failure
        after commit: delete crop_path(root, id) for moved face ids (ignore errors)
        report + progress event (CONVERTING, done, total, outcomes)
finally: delete every JPEG this run wrote that was not committed
```

Cancellation arrives as `progress.Cancelled` from the reporter (`CancellableProgress`); the `finally` cleanup must run, and the handler re-raises so the job exits with `CANCELLED_EXIT` as other commands do.

- [ ] **Step 1: Write the failing unit tests** (monkeypatch `raw_to_jpeg.extract_embedded` to write a small real JPEG of the CR3's listed size with Pillow, `_copy_metadata` to no-op, the ExifTool size/orientation reader to return fixed values, `analyze_paths` to return `make_item` items, and `move_to_recycle_bin` to a recorder, as `tests/unit/test_recycle.py` does):

```python
def test_converts_and_moves_faces(...)        # JPEG row exists, CR3 row gone, faces now on JPEG id, CR3 recycled
def test_existing_jpeg_on_disk_is_skipped(...)   # Review Focus 1: no overwrite, CR3 untouched
def test_missing_embedded_image_keeps_cr3(...)   # Review Focus 2: no JPEG left, failure listed
def test_wrong_size_jpeg_keeps_cr3(...)          # validation
def test_recycle_failure_rolls_back(...)         # Review Focus 3: CR3 row + faces intact, JPEG deleted
def test_cancel_mid_batch_leaves_no_stray_jpegs(...)  # Review Focus 4: raise Cancelled from the callback after photo 1
def test_xmp_goes_with_cr3(...)
def test_refuses_on_mirror(...)
def test_cli_deletes_list_once_started(...)
```

- [ ] **Step 2: Write the ExifTool integration test** `test_real_cr3_portrait(tmp_path, copy_image, repo-on-tmp)`: copy `test.CR3` (Orientation 6) into a catalog, add a face with a person and a rejection, run `replace_raws` with `move_to_recycle_bin` patched; assert `IMG.JPG` exists, its Orientation is 6, its stored size equals the CR3's, the face box equals `rotate_box(old, 6, W, H)`, the person and rejection are kept, `face_scan_state.img_w/img_h` are swapped.
- [ ] **Step 3: Run** `uv run pytest tests/unit/test_raw_to_jpeg.py tests/integration/test_raw_to_jpeg_exiftool.py -v` → FAIL.
- [ ] **Step 4: Implement** the service and CLI. Add `raw-to-jpeg` to `forms` only as the existing tests require (`uv run pytest tests/unit/test_forms.py tests/unit/test_tui_forms.py`).
- [ ] **Step 5: Run** the two test files plus `tests/unit/test_forms.py tests/unit/test_recycle.py` → PASS.
- [ ] **Step 6: Commit** "Replace CR3 photos with their camera JPEG, keeping their faces".

---

### Task 5: The RAW → JPEG grid

**Files:**
- Create: `smart_gallery/gui/storage/raw_view.py`
- Modify: `smart_gallery/colors.py` only if a new colour is needed
- Test: `tests/gui/test_raw_view.py`

**Interfaces:**
- Consumes: Task 2's `RawCandidate`, `in_scope`, `reconcile_picks`, `estimate_jpeg_bytes`, `replacement_entries`; `ctx.thumbs` exactly as `BurstsView.thumbnail` uses it; `gui.library.viewer.MediaViewer` and `gui.library.model.LibraryRow`; `recycle_bin.recycle_bin_capacity`.
- Produces `class RawView(QWidget)` with the same surface `StorageView` uses on `BurstsView`:
  - signals `convert_requested = Signal(list)` (list[FileRef] the user confirmed), `scope_changed = Signal(object)`, `thumbs_changed = Signal()`
  - `show_all: QPushButton`; `set_candidates(cands: list[RawCandidate], root: Path) -> None`; `set_scope(folder: str | None)`; `scope() -> str | None`; `picks() -> set[str]`; `toggle(key: str)`; `pick_range(key: str)`; `pick_all_in_scope()`; `clear_picks()`; `clear_picks_in_scope()`; `forget_thumb_requests()`; `open_viewer(key: str) -> MediaViewer`.
- Copy (constants at the top, as in `bursts_view.py`):
  - `TITLE = "RAW → JPEG"`
  - `SCOPE_ONE = "In {folder} · {n:,} CR3s without a JPEG · {size}"`, `SCOPE_ALL = "All folders · {n:,} CR3s without a JPEG · {size}"`, `DROPPED = "{n:,} picks cleared because their files changed"`
  - `EMPTY_ONE = "No CR3s without a JPEG in this folder"`, `EMPTY_ALL = "No CR3s without a JPEG in this gallery"`
  - `FOOTER = "{n:,} picked · {raw} of CR3 → about {jpeg} of JPEG · frees ~{freed}"`, `FOOTER_FOLDERS = " (in {k} folders)"`
  - `CONVERT = "Convert {n:,} and recycle the CR3s"`, `CONVERT_NONE = "Convert and recycle"`, `CLEAR = "Clear"`
  - `CONFIRM_TITLE = "Convert to JPEG"`, `CONFIRM = "Convert {n:,} photos to JPEG and move their CR3s ({size}) to the Recycle Bin?"`, `BIN_WARNING = "\n\nThat is more than E:'s Recycle Bin holds ({limit}); Windows will empty the oldest items to make room."` (use the drive's name, not a literal "E:")
  - `EDITED = "edited"`

- [ ] **Step 1: Write the failing GUI tests** (headless, as `tests/gui/test_bursts_view.py`):

```python
def test_grid_shows_scope_only(qtbot, ...)          # two folders, scope one -> only its cells, scope label text
def test_click_toggles_and_footer_counts(...)       # 2 picks of 20 MB -> "2 picked · 40.0 MB of CR3 → about 2.0 MB of JPEG · frees ~38.0 MB"
def test_shift_click_picks_range(...)
def test_ctrl_a_picks_scope_and_esc_clears_scope(...)
def test_footer_mentions_other_folders(...)
def test_reload_drops_changed_picks_and_says_so(...)
def test_confirm_emits_cr3_refs(...)                # monkeypatch QMessageBox.question -> Yes; payload == replacement_entries(picked)
def test_confirm_warns_over_bin_limit(...)          # recycle_bin_capacity patched small -> BIN_WARNING text in the question
def test_double_click_opens_viewer_and_space_toggles(...)
def test_edited_badge_drawn_for_sidecar(...)        # via the model's data role, not pixels
```

- [ ] **Step 2: Run** `uv run pytest tests/gui/test_raw_view.py -v` → FAIL.
- [ ] **Step 3: Implement.** A `QListView` in `IconMode` with a `QAbstractListModel` over the scoped candidates and a delegate drawing thumbnail (≈118 px wide), pick box, name · size and the badge; selection colour from `palette(highlight)`. Handle click/shift-click in `mousePressEvent` on the view (not the selection model). The viewer: a small `MediaViewer` subclass adding a Space shortcut that calls `toggle` for the current row and shows "Picked"/"Not picked" in its title.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** "Add the RAW → JPEG grid with picks and footer".

---

### Task 6: Show the grid on the Storage page

**Files:**
- Modify: `smart_gallery/gui/storage/view.py`, `README.md` (Storage section)
- Test: `tests/gui/test_storage_view.py`

**Interfaces:**
- Consumes: Task 2 `load_raw_candidates`, Task 4 CLI argv, Task 5 `RawView`.
- Produces: `StorageData.raws: list[RawCandidate]` (loaded in `load_storage`); `RIGHT_LABELS = {"pairs": ..., "bursts": ..., "raw": "RAW → JPEG"}`; `storage.json` `"right"` accepts `"raw"`; job title `CONVERT_JOB_TITLE = "Convert to JPEG"`; job argv `("raw-to-jpeg", str(root), "--list", str(path))`, list written with `write_list` into `LISTS_DIR` via `_new_list_path`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_third_switch_shows_raw_view_and_is_remembered(...)
def test_tree_scopes_raw_view(...)
def test_leave_guard_counts_raw_picks(...)        # n = choices + marks + picks
def test_convert_starts_raw_to_jpeg_job(...)      # fake ctx.jobs records JobSpec; argv[0] == "raw-to-jpeg"; list file has the CR3 line
def test_gallery_change_clears_picks(...)
```

- [ ] **Step 2: Run** `uv run pytest tests/gui/test_storage_view.py -v` → FAIL.
- [ ] **Step 3: Implement** the wiring alongside the bursts wiring (scope, show_all, thumbs on `showEvent`, reload via `set_candidates` after the scope is restored, clear on gallery change, `_save_state`). Update the module docstring. README: a "RAW → JPEG" subsection under "Storage page: pairs and bursts" covering what is offered (CR3 without a JPEG), that the JPEG is the camera's own (measured identical in look, about 5% of the size), that faces move with the photo, the Recycle Bin limit warning, that CR2 is not offered, and that the next mirror sync copies the JPEGs to D and trashes D's CR3s (expect the large-delete confirmation).
- [ ] **Step 4: Run** the full suite: `uv run pytest tests/gui tests/unit -q` → all pass; `uv run pytest -m exiftool tests/integration -q` → pass.
- [ ] **Step 5: Hand-check (left to the user; agents must not drive the GUI against real photos):** note in the final report: `uv run smart-gallery gui` → Storage → RAW → JPEG → `Canon/2026/10`, pick 2 photos (one portrait with a named person), convert, confirm both JPEGs open upright with their date, the person still shows that photo on the People page, and both CR3s are in the Recycle Bin.
- [ ] **Step 6: Commit** "Show the RAW → JPEG list on the Storage page".
