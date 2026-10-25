# Storage Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the Dashboard tab to Stats, trim it (no data tables,
collapsible panels), and add a Storage tab. On the left, Storage shows the
folder ▸ type size tree. On the right, it lists RAW + JPEG pairs whose
unwanted copies go to the Recycle Bin through a new `recycle` CLI job.

**Architecture:** No-Qt logic lives in a new `smart_gallery/storage/`
package: `tree.py` (size tree), `pairs.py` (finding, scoping and resolving
choices), `compare.py` (picture check) and `check_cache.py`. The Recycle Bin
call and the `recycle` service are core, so the CLI can run them. The GUI
side is `smart_gallery/gui/storage/`: `tree_view.py`, `pairs_view.py`,
`checker.py` and `view.py`. Each loads its data on a worker thread (through
`RepoQuery` or a `QThreadPool`) and never writes to the catalog. The only
write, the move to the Recycle Bin, runs as a `JobSpec` through
`ctx.jobs`.

**Tech Stack:** Python 3.13, pandas, numpy, Pillow (`gui` extra), ExifTool,
PySide6, ctypes (`shell32`/`kernel32`), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-25-storage-page-design.md`
(mockups in `docs/superpowers/specs/2026-10-25-storage-page-mockups/`).
This plan replaces `docs/superpowers/plans/2026-10-25-dashboard-storage-panel.md`.
Delete that file and its spec, `2026-10-25-dashboard-storage-panel-design.md`,
in Task 1's commit. Task 1 reuses that plan's tree tests.

## Global Constraints

- Every Storage number comes from the catalog (`relpath`, `ext`, `size_bytes`, `mtime_ns`). Only the picture check reads files.
- The GUI opens the catalog read-only (`ctx.open_repo()`) and never migrates or writes to it. Writes go only through `ctx.jobs.start(JobSpec(...))`.
- Never delete a file permanently. If the Recycle Bin can't take a file, it stays where it is and is reported.
- Never remove a file that wasn't chosen for removal, or whose size or mtime on disk differs from what the GUI saw.
- Tree building, pair finding and picture checking run off the GUI thread.
- Colours come only from `smart_gallery/colors.py` or palette roles. `tests/unit/test_colors.py` rejects hex literals elsewhere.
- No-Qt modules (`smart_gallery/storage/*`, `smart_gallery/recycle_bin.py`, `services/recycle.py`, `gui/state_store.py`) import no PySide6. `smart_gallery/storage/compare.py` imports Pillow lazily, inside functions, so the core CLI imports cleanly without the `gui` extra.
- RAW extensions are `RAW_EXTS = {".cr2", ".cr3"}`. JPEG extensions are `JPEG_EXTS = {".jpg", ".jpeg"}`. The sidecar extension is `SIDECAR_EXT = ".xmp"`. All comparisons ignore case.
- Sizes are 1024-based and shown as B / KB / MB / GB / TB (`storage.tree.format_bytes`).
- Run `uv run ruff check` and `uv run black` only on the files you touch.
- Commit messages are imperative sentences with no `feat:` prefix (repo style), and end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01KsGXCcQgQpLaPHpY25VRpe
  ```

## Review Focus

1. **Pairs at the gallery root** (`IMG_1.CR3` + `IMG_1.JPG` directly in `E:/`). Expect folder `""`, shown as the root name, and in scope for "All folders" and for a root selection. Test: Task 2 `test_root_level_pair`.
2. **A file deleted or edited after the last sync.** The check shows "unreadable" and `recycle` skips the file without touching it. Tests: Task 3 `test_missing_jpeg_is_unreadable`, Task 4 `test_changed_or_missing_file_is_skipped`.
3. **Non-ASCII folder names** (`Año 2026`), both through ExifTool's argfile and through the UTF-8 list file. Tests: Task 3 `test_extract_previews_non_ascii`, Task 4 `test_read_list_utf8`.
4. **A recycle job cancelled halfway.** Rows are removed only for files that actually went to the Recycle Bin, so the catalog still matches the disk. Test: Task 4 `test_cancel_keeps_catalog_in_step`.
5. **A recycle job where some files fail.** After the reload, the removed pairs are gone and the choices for failed pairs remain. Test: Task 8 `test_choices_survive_reload_for_remaining_pairs`.

---

### Task 1: Storage size tree (no Qt)

**Files:**
- Create: `smart_gallery/storage/__init__.py` (empty), `smart_gallery/storage/tree.py`
- Delete: `docs/superpowers/plans/2026-10-25-dashboard-storage-panel.md`, `docs/superpowers/specs/2026-10-25-dashboard-storage-panel-design.md`
- Test: `tests/unit/test_storage_tree.py`

**Interfaces:**
- Produces, exactly as in the replaced plan's Task 1: `FOLDER`, `TYPE`, `NO_EXT`, `StorageNode`, `StorageTree`, `type_label(ext)`, `format_bytes(n)`, and `build_storage(df, root_name) -> StorageTree | None`. The module is `smart_gallery.storage.tree`. Field definitions:
  ```python
  @dataclass
  class StorageNode:
      name: str; kind: str; relpath: str | None; bytes: int; files: int
      ext: str | None = None; children: list["StorageNode"] = field(default_factory=list)

  @dataclass(frozen=True)
  class StorageTree:
      by_folder: StorageNode; by_type: StorageNode
      type_colors: dict[str, str]; total_bytes: int; total_files: int
  ```

- [ ] **Step 1: Write the failing tests.** Copy the eleven tests from the replaced plan's Task 1 verbatim into `tests/unit/test_storage_tree.py`, importing `from smart_gallery.storage import tree as storage`: `test_type_label`, `test_format_bytes`, `test_folder_tree_totals_and_order`, `test_type_tree`, `test_type_colors_ranked_by_bytes`, `test_types_past_category_count_are_neutral`, `test_extensionless_files`, `test_folders_merge_case_insensitively`, `test_extensions_merge_case_insensitively`, `test_missing_size_counts_as_zero` and `test_empty_frame_is_none`. The replaced plan stays readable after its deletion with `git show 56c00d9:docs/superpowers/plans/2026-10-25-dashboard-storage-panel.md`.
- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/unit/test_storage_tree.py -v`. Expected: `ModuleNotFoundError: smart_gallery.storage`.
- [ ] **Step 3: Implement `tree.py`** as the replaced plan's Task 1 Step 3 describes. Aggregate with one pandas `groupby` on (lower-cased folder key, type label) and build both trees from those groups. The display name is the first spelling seen. Subfolders come before type rows, and each group is sorted by bytes descending, then by name. `format_bytes` shows three significant figures.
- [ ] **Step 4: Run them to verify they pass.** Same command. Expected: all PASS.
- [ ] **Step 5: Lint, format, delete the replaced docs, and commit.** Message: "Compute storage trees by folder and file type".

---

### Task 2: Finding pairs, scoping them, resolving choices (no Qt)

**Files:**
- Create: `smart_gallery/storage/pairs.py`
- Test: `tests/unit/test_storage_pairs.py`

**Interfaces:**
- Produces:
  ```python
  RAW_EXTS = frozenset({".cr2", ".cr3"}); JPEG_EXTS = frozenset({".jpg", ".jpeg"}); SIDECAR_EXT = ".xmp"
  KEEP_RAW, KEEP_JPEG, KEEP_BOTH = "raw", "jpeg", "both"
  PairKey = tuple[str, str]            # (raw relpath casefolded, jpeg relpath casefolded)

  @dataclass(frozen=True)
  class FileRef:
      id: int; relpath: str; ext: str; size_bytes: int; mtime_ns: int | None; media_type: str

  @dataclass(frozen=True)
  class Pair:
      folder: str            # POSIX parent of relpath, "" at the root, first spelling seen
      stem: str              # first spelling seen
      raw: FileRef
      jpeg: FileRef
      sidecar: FileRef | None
      @property
      def key(self) -> PairKey

  def find_pairs(repo: GalleryRepository) -> list[Pair]
  def in_scope(pair: Pair, folder: str | None) -> bool
  def resolve_removals(pairs: Sequence[Pair], choices: Mapping[PairKey, str]) -> list[FileRef]
  ```
- `find_pairs` runs one query, `SELECT id, relpath, ext, size_bytes, mtime_ns, media_type FROM media_items WHERE lower(ext) IN (...)`, over the RAW, JPEG and sidecar extensions. It then groups the rows in Python by `(folder.casefold(), stem.casefold())`, which is linear. Derive folder and stem from `relpath` (`PurePosixPath`), not from `directory_rel`. A NULL `size_bytes` becomes 0. Each RAW is paired with each JPEG in a group. Pairs are ordered by folder (casefolded), then stem, then raw relpath, then jpeg relpath. This replaces the spec's suggested self-join, which has no usable index. No migration is needed.
- `in_scope`: `None` or `""` means everything. Otherwise the pair's folder equals the scope folder or sits under it (`casefold`, then a `== f` or `startswith(f + "/")` check).
- `resolve_removals` applies keep wins, per file. A RAW or JPEG is removed only if at least one row removes it and no row keeps it. `KEEP_RAW` removes the JPEG, `KEEP_JPEG` removes the RAW, and `KEEP_BOTH` keeps both. A sidecar is removed only if every RAW in its pairs is removed. It returns unique files sorted by `relpath.casefold()`. Keys not in `pairs` are ignored.

- [ ] **Step 1: Write the failing tests** using the `repo` and `make_item` fixtures (`repo.upsert_many([...])`). Pass `size_bytes=` to `make_item` where sizes matter.

```python
def test_pairs_same_folder_only(repo, make_item):
    # a/IMG_1.CR3, a/IMG_1.JPG, b/IMG_1.JPG, a/IMG_2.CR3 (no jpeg), a/IMG_3.JPG (no raw)
    pairs = find_pairs(repo)
    assert [(p.folder, p.stem, p.raw.relpath, p.jpeg.relpath) for p in pairs] == [
        ("a", "IMG_1", "a/IMG_1.CR3", "a/IMG_1.JPG")]

def test_pairs_ignore_case(repo, make_item):
    # Canon/img_5.cr3 and canon/IMG_5.JPEG -> one pair, folder "Canon", stem "img_5"
def test_cr2_pairs(repo, make_item):                 # x/A.CR2 + x/A.jpg -> one pair
def test_no_same_format_pairs(repo, make_item):      # x/A.CR2 + x/A.CR3 (no jpeg) -> []; x/B.JPG + x/B.jpeg -> []
def test_one_raw_two_jpegs(repo, make_item):         # x/A.CR3, x/A.JPG, x/A.jpeg -> 2 pairs sharing raw
def test_sidecar_attached(repo, make_item):          # x/A.CR3, x/A.JPG, x/A.XMP -> pairs[0].sidecar.relpath == "x/A.XMP"
def test_root_level_pair(repo, make_item):           # Review Focus 1
    # IMG_1.CR3 + IMG_1.JPG at the root
    (pair,) = find_pairs(repo)
    assert pair.folder == ""
    assert in_scope(pair, None) and in_scope(pair, "")
    assert not in_scope(pair, "Canon")

def test_scope_includes_subfolders():
    # pairs in "Canon", "Canon/2026", "Canonical", "Drone"
    assert [p.folder for p in pairs if in_scope(p, "canon")] == ["Canon", "Canon/2026"]

def test_resolve_basic():
    # p1 KEEP_RAW, p2 KEEP_JPEG, p3 KEEP_BOTH, p4 undecided
    assert [f.relpath for f in resolve_removals(pairs, choices)] == ["a/1.JPG", "a/2.CR3"]

def test_resolve_keep_wins_for_shared_file():
    # A.CR3 in two rows: (A.CR3, A.JPG) KEEP_JPEG, (A.CR3, A.jpeg) KEEP_RAW
    assert [f.relpath for f in resolve_removals(pairs, choices)] == ["x/A.jpeg"]   # the RAW is kept

def test_resolve_counts_each_file_once():
    # (A.CR3, A.JPG) KEEP_JPEG and (A.CR3, A.jpeg) KEEP_JPEG -> A.CR3 listed once

def test_sidecar_follows_raw():
    # A.CR3 + A.JPG + A.XMP, KEEP_JPEG -> ["x/A.CR3", "x/A.XMP"]; KEEP_RAW -> ["x/A.JPG"]

def test_sidecar_stays_while_any_raw_kept():
    # A.CR2 + A.CR3 + A.JPG + A.XMP: (CR2, JPG) KEEP_JPEG, (CR3, JPG) KEEP_RAW -> ["x/A.CR2"]
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/unit/test_storage_pairs.py -v`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement `pairs.py`** to the interfaces above.
- [ ] **Step 4: Run them to verify they pass.** Same command. Expected: all PASS.
- [ ] **Step 5: Lint, format, and commit.** Message: "Find RAW + JPEG pairs in the catalog and resolve keep choices".

---

### Task 3: Picture check and its cache (no Qt)

**Files:**
- Create: `smart_gallery/storage/compare.py`, `smart_gallery/storage/check_cache.py`
- Test: `tests/unit/test_check_cache.py`, `tests/integration/test_pair_compare.py` (marked `exiftool`)

**Interfaces:**
- Consumes: `Pair`, `PairKey` (Task 2). From `services/convert_cr3.py`, `_run_exiftool(args) -> str`, an existing argfile runner with UTF-8 filenames. Move it to `smart_gallery/analysis/extract.py` as `run_exiftool_argfile(args: list[str]) -> str`, and have `convert_cr3.py` import it from there.
- Produces:
  ```python
  # compare.py
  MATCH, DIFFERENT, UNREADABLE = "match", "different", "unreadable"
  def extract_previews(raws: Sequence[Path], out_dir: Path) -> dict[Path, Path]   # raw -> preview jpeg; missing = none
  def compare_images(preview: Path, jpeg: Path) -> str                          # MATCH | DIFFERENT | UNREADABLE
  def check_pairs(pairs: Sequence[Pair], root: Path, cache: CheckCache,
                  on_result: Callable[[PairKey, str], None],
                  cancelled: Callable[[], bool] = lambda: False, batch: int = 50) -> None

  # check_cache.py
  class CheckCache:
      def __init__(self, path: Path)          # loads; a missing or invalid file is an empty cache
      def get(self, pair: Pair) -> str | None
      def put(self, pair: Pair, verdict: str) -> None
      def save(self) -> None                  # atomic: .tmp then os.replace
  ```
- **`extract_previews`** starts one ExifTool process per batch, never one per file. For each raw `i`, the argfile has `-b`, `-PreviewImage`, `-W`, `<out_dir>/<i>.jpg`, `<raw>` and `-execute`. A second process does the same with `-JpgFromRaw`, only for raws that produced no file in the first pass. A zero-byte output counts as missing.
- **`compare_images`** works like this:
  - Open each file with Pillow and call `draft("L", (256, 256))`, then `convert("L")`. Ignore EXIF orientation; both images are in sensor orientation.
  - If the aspect ratios (`w/h`) differ by more than 2%, return `DIFFERENT`.
  - Otherwise resize both to 32×32 with `Image.Resampling.BOX` and compute the Pearson correlation of the pixel arrays with numpy. Return `MATCH` when the correlation is ≥ `MATCH_CORRELATION = 0.90`, else `DIFFERENT`.
  - Any `OSError` or Pillow error returns `UNREADABLE`.
  - The tests pin the outcomes. The threshold may be tuned if they still hold.
- **`check_pairs`**:
  - First, call `on_result` with a cached verdict for each pair the cache has.
  - Then, in batches of `batch`, extract previews into a `TemporaryDirectory`, compare, `put`, call `on_result`, and `save()` after each batch.
  - A missing RAW, preview or JPEG gives `UNREADABLE`.
  - `cancelled()` is checked between batches. On cancel it saves and returns.
  - Absolute paths are `root / relpath`.
- **Cache file:** JSON `{"version": 1, "checks": {key: verdict}}`. A key is `"\t".join([raw.relpath.casefold(), str(raw.size_bytes), str(raw.mtime_ns), jpeg.relpath.casefold(), str(jpeg.size_bytes), str(jpeg.mtime_ns)])`. Only `MATCH`, `DIFFERENT` and `UNREADABLE` are accepted on load.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/unit/test_check_cache.py
def test_hit_when_keys_match(tmp_path):        # put, save, new CheckCache(path).get(pair) == "match"
def test_miss_after_size_or_mtime_change(tmp_path):
    # same relpaths, raw.mtime_ns + 1 -> None; jpeg.size_bytes + 1 -> None
def test_bad_file_is_empty(tmp_path):          # "{oops", '{"checks": []}', '{"checks": {"k": "maybe"}}' -> get() is None, no raise

# tests/integration/test_pair_compare.py  (pytestmark = pytest.mark.exiftool)
# Fixture: copy tests/resources/images/test.CR3 to tmp; build the "camera JPEG" variants from its
# preview with Pillow (`Image.open(extract_previews(...)[raw])`):
def test_reencoded_preview_matches(tmp_path):  # saved at quality=95 and with ImageEnhance.Brightness(1.1) -> MATCH
def test_cropped_is_different(tmp_path):       # centre crop to 50% width, full height -> DIFFERENT
def test_inverted_is_different(tmp_path):      # ImageOps.invert -> DIFFERENT
def test_other_shot_is_different(tmp_path):    # test.JPG (another camera, same 3:2 aspect) -> DIFFERENT
def test_missing_jpeg_is_unreadable(tmp_path): # Review Focus 2: check_pairs with the jpeg file absent -> UNREADABLE via on_result
def test_no_preview_is_unreadable(tmp_path):   # a .CR3 of b"junk" -> extract_previews has no entry; check_pairs -> UNREADABLE
def test_extract_previews_non_ascii(tmp_path): # Review Focus 3: raw under tmp/"Año 2026"/ -> one preview, non-empty
def test_check_pairs_uses_cache(tmp_path, monkeypatch):
    # second check_pairs run with the same cache: monkeypatch compare.extract_previews to raise -> still reports MATCH
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/unit/test_check_cache.py tests/integration/test_pair_compare.py -v`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement `check_cache.py` and `compare.py`, and move the argfile runner** to the interfaces above. `tests/integration/test_convert_raw.py` must still pass.
- [ ] **Step 4: Run them to verify they pass,** together with `tests/integration/test_convert_raw.py`. Expected: all PASS.
- [ ] **Step 5: Time it by hand.** Run `check_pairs` over 50 pairs of copies of the fixture and check that it averages ≤ 0.2 s per pair. Report the number in the commit body.
- [ ] **Step 6: Lint, format, and commit.** Message: "Check whether a RAW and its JPEG show the same picture".

---

### Task 4: Recycle Bin, the `recycle` service and its CLI command

**Files:**
- Create: `smart_gallery/recycle_bin.py`, `smart_gallery/services/recycle.py`
- Modify: `smart_gallery/services/__init__.py` (export `recycle_files`, `RecycleReport`, `read_list`), `smart_gallery/progress.py` (add `RECYCLING`), `smart_gallery/cli.py` (parser, `_handle_recycle`, `_HANDLERS`, `DETAIL_LOG_COMMANDS`), `smart_gallery/forms.py` (a `LABELS` entry if the generated label reads badly), `README.md` (command list and a `### recycle` section)
- Test: `tests/unit/test_recycle.py`, `tests/unit/test_forms.py` (stays green)

**Interfaces:**
- Produces:
  ```python
  # recycle_bin.py
  def supports_recycle_bin(path: Path) -> bool      # win32 and GetDriveTypeW(drive anchor) == DRIVE_FIXED (3); else False
  def move_to_recycle_bin(path: Path) -> None       # raises OSError on any failure; never deletes permanently

  # progress.py
  RECYCLING = "Moving files to Recycle Bin"

  # services/recycle.py
  @dataclass(frozen=True)
  class RecycleEntry:
      relpath: str; size_bytes: int; mtime_ns: int | None
  @dataclass
  class RecycleReport:
      recycled: int = 0; skipped: int = 0; freed_bytes: int = 0
      failures: list[str] = field(default_factory=list)
  def read_list(path: Path) -> list[RecycleEntry]   # UTF-8, "relpath\tsize\tmtime" per line; mtime "" -> None; blank lines skipped
  def write_list(path: Path, entries: Iterable[RecycleEntry]) -> None
  def recycle_files(repo: GalleryRepository, entries: Sequence[RecycleEntry],
                    progress_callback: ProgressCallback | None = None) -> RecycleReport
  ```
- **`move_to_recycle_bin`** uses `SHFileOperationW` through ctypes. It sets `FO_DELETE` with `FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT | FOF_WANTNUKEWARNING`, and passes a double-NUL-terminated path. A nonzero return or `fAnyOperationsAborted` raises `OSError`. `FOF_WANTNUKEWARNING` is the last line of defence: it makes Windows ask rather than delete for good. `supports_recycle_bin` is the real guard and runs first.
- **`recycle_files`**:
  - If `not supports_recycle_bin(root)`, raise `ValueError("The Recycle Bin is not available on <root>; nothing was moved.")` before touching anything. The CLI turns a `ValueError` into exit code 1.
  - For each entry, emit `ProgressEvent(RECYCLING, done, total, outcomes=(("recycled", r), ("skipped", s), ("failed", f)))`. That event is where cancellation is raised, through `CancellableProgress`.
  - **Skip** an entry when its catalog row is missing, the file is missing, or its on-disk `st_size`/`st_mtime_ns` differs from the entry. The catalog's own values must match the entry too.
  - Otherwise call `move_to_recycle_bin`. On success, `repo.delete_by_relpaths([relpath])` (one commit per file), add to `recycled` and `freed_bytes`, and log `DEBUG "Recycled: <relpath>"`. On `OSError`, record the failure and keep the row.
- **CLI:** `recycle drive --list LIST`. Add a `"recycle"` subparser with help "Move listed catalog files to the Recycle Bin and drop them from the catalog." `drive` takes `DRIVE_HELP`. `--list` is required, of type `Path`, with help "Text file with one 'relpath<TAB>size<TAB>mtime_ns' line per file." The handler opens `GalleryRepository.open(args.drive)` read-write, uses `progress_reporter()` with `try/finally: cb.close()`, and logs `success` with the counts and `format_bytes(freed)`. Add `"recycle"` to `DETAIL_LOG_COMMANDS`.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_recycle.py`). Monkeypatch `recycle.supports_recycle_bin` to return True, and monkeypatch `recycle.move_to_recycle_bin` with a recorder that unlinks the file, unless a test says otherwise. Use `drive_file` plus `make_item(relpath, size_bytes=st.st_size, mtime_ns=st.st_mtime_ns)` so catalog and disk agree.

```python
def test_recycles_and_drops_rows(repo, drive, ...):
    report = recycle_files(repo, [entry_a, entry_b])
    assert (report.recycled, report.skipped, report.failures) == (2, 0, [])
    assert report.freed_bytes == size_a + size_b
    assert repo.index_for_sync().keys() == {"keep/c.jpg"}

def test_changed_or_missing_file_is_skipped(...):     # Review Focus 2
    # a: file rewritten (size differs); b: file deleted; c: no catalog row -> skipped 3, recorder never called, rows a/b kept

def test_refuses_without_recycle_bin(...):
    # supports_recycle_bin -> False: raises ValueError, recorder never called, all rows kept

def test_failure_keeps_row(...):                      # recorder raises OSError for a -> failures has a, its row and file kept; b recycled

def test_cancel_keeps_catalog_in_step(...):           # Review Focus 4
    # progress_callback raises progress.Cancelled on the event after the first file
    with pytest.raises(Cancelled): recycle_files(repo, [a, b, c], progress_callback=cb)
    # a gone from disk and catalog; b, c still on disk and in catalog

def test_read_list_utf8(tmp_path):                    # Review Focus 3
    write_list(p, [RecycleEntry("Año 2026/IMG_1.CR3", 10, 5), RecycleEntry("x.JPG", 3, None)])
    assert read_list(p) == [RecycleEntry("Año 2026/IMG_1.CR3", 10, 5), RecycleEntry("x.JPG", 3, None)]

def test_cli_recycle(repo, drive, monkeypatch, tmp_path):
    # main(["recycle", str(drive), "--list", str(list_file)]) recycles the listed file

@pytest.mark.skipif(sys.platform != "win32", reason="Windows Recycle Bin")
def test_real_recycle_bin(tmp_path):
    # only when supports_recycle_bin(tmp_path): a real temp file disappears from disk after move_to_recycle_bin
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/unit/test_recycle.py -v`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `recycle_bin.py`, `services/recycle.py`, `RECYCLING`, and the CLI wiring. Then add the README section: what the command does, the list-file format, that it never deletes permanently, and that it refuses on drives without a Recycle Bin.
- [ ] **Step 4: Run them to verify they pass,** together with `tests/unit/test_forms.py` (`test_every_field_has_a_hint`). Expected: all PASS.
- [ ] **Step 5: Lint, format, and commit.** Message: "Add a recycle command that moves catalog files to the Recycle Bin".

---

### Task 5: Stats (rename, no tables, collapsible panels)

**Files:**
- Create: `smart_gallery/gui/state_store.py`
- Modify: `smart_gallery/gui/areas.py` (label `"Stats"`), `smart_gallery/gui/dashboard/view.py` (`Panel`, `DashboardView`, delete the tables), `smart_gallery/gui/dashboard/model.py` (drop `split`)
- Test: `tests/unit/test_state_store.py`, `tests/gui/test_dashboard_view.py`, `tests/unit/test_dashboard_page.py`, and any test asserting the `"Dashboard"` tab label

**Interfaces:**
- Produces:
  ```python
  # gui/state_store.py (no Qt)
  def load_json(path: Path) -> dict       # {} on a missing file, invalid JSON, or a non-object
  def save_json(path: Path, data: dict) -> None   # atomic: write .tmp, then os.replace

  # gui/dashboard/view.py
  STATE_FILE = "dashboard.json"           # {"collapsed": [sorted panel titles]}
  class Panel(QFrame):
      collapsed_changed = Signal(bool)
      title: QToolButton                  # "▾ {title}" expanded, "▸ {title}" collapsed; flat; bold 1.3×
      @property
      def collapsed(self) -> bool
      def set_collapsed(self, collapsed: bool) -> None
  ```
  `DashboardData.split` is removed, and `DashboardView.tables` and `.tabs` no longer exist. The area id stays `"dashboard"`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/unit/test_state_store.py`: `test_round_trip` (no `.tmp` left behind), `test_missing_is_empty`, and `test_bad_file_is_empty`, parametrized over `"{not json"` and `"[]"`. `test_module_imports_no_qt` runs `import smart_gallery.gui.state_store; import sys; assert "PySide6" not in sys.modules` in a subprocess.
  - `tests/gui/test_dashboard_view.py`:
    - in `test_dashboard_native_panels`, replace the `view.tables` asserts with `assert not view.findChildren(QTableView)` and `assert not view.findChildren(QTabWidget)`;
    - add `test_panel_collapses_to_its_title`, `test_collapse_survives_reload_and_restart` and `test_empty_panel_stays_hidden_even_if_collapsed`, verbatim from the replaced plan's Task 4 (`git show 56c00d9:docs/superpowers/plans/2026-10-25-dashboard-storage-panel.md`).
  - `tests/unit/test_dashboard_page.py`: replace `result.split` with `figures.split_media(figures.load_catalog_df(repo.db_path))`.
  - Add `test_stats_label` to the areas test (or a new `tests/gui/test_areas.py`): `[a.label for a in AREAS][:4] == ["Library", "Import", "Stats", "Storage"]`, and the Stats area's id is `"dashboard"`. This test fails until Task 8 adds Storage. Mark it `xfail(strict=True)` here and remove the mark in Task 8.
- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/unit/test_state_store.py tests/gui/test_dashboard_view.py tests/unit/test_dashboard_page.py -v`. Expected: FAIL (`ModuleNotFoundError`, `QTableView` found, `QLabel` has no `click`).
- [ ] **Step 3: Implement.**
  - **Tables:** delete `TABS`, `DataFrameModel`, the `QTabWidget`/`QTableView` and the `charts_split` splitter. The stack's first page becomes `top`. Drop `split` from `DashboardData`.
  - **Collapsing:** the `Panel` title becomes a `QToolButton` that toggles collapse. `set_body` keeps the current collapsed state, and a `None` body still hides the whole panel. `DashboardView.__init__` loads `load_json(app_data_dir() / STATE_FILE).get("collapsed")`, ignoring anything that isn't a list of strings, and saves on every `collapsed_changed`.
  - **Rename:** set the `areas.py` label to `"Stats"`, and update the module docstrings.
- [ ] **Step 4: Run them to verify they pass.** Same command, plus `uv run pytest tests/gui -q`. Expected: all PASS; `test_stats_label` shows as XFAIL.
- [ ] **Step 5: Lint, format, and commit.** Message: "Rename Dashboard to Stats, drop its tables and let panels collapse".

---

### Task 6: Storage tree widget

**Files:**
- Create: `smart_gallery/gui/storage/__init__.py` (empty), `smart_gallery/gui/storage/tree_view.py`
- Modify: `smart_gallery/gui/library/viewer.py` (add `open_folder` next to `show_in_explorer`)
- Test: `tests/gui/test_storage_tree_view.py`

**Interfaces:**
- Consumes: `StorageTree`, `StorageNode`, `FOLDER`, `TYPE` and `format_bytes` (Task 1).
- Produces:
  ```python
  # viewer.py
  def open_folder(path: Path) -> None       # win32: subprocess "explorer <path>"; else QDesktopServices.openUrl

  # tree_view.py
  COLUMNS = ("Name", "Size", "Share of parent", "Files")
  GROUPING_LABELS = {"folder": "Folder ▸ Type", "type": "Type ▸ Folder"}
  NOT_FOUND = "Folder not found: {path}"
  NODE_ROLE = Qt.ItemDataRole.UserRole + 1    # StorageNode, column 0
  SHARE_ROLE = Qt.ItemDataRole.UserRole + 2   # float 0..1, column 2 (root 1.0)
  class StorageTreeView(QWidget):
      grouping_changed = Signal(str)
      folder_selected = Signal(object)        # str relpath ("" = root) or None (no single folder)
      message = Signal(str)                   # NOT_FOUND text for the page caption
      tree: QTreeView
      buttons: dict[str, QPushButton]         # checkable, exclusive, keyed "folder" / "type"
      def set_tree(self, tree: StorageTree, root: Path) -> None
      def set_grouping(self, grouping: str) -> None   # rebuilds the model; emits only on change
      def grouping(self) -> str
      def open_node(self, index: QModelIndex) -> None
      def context_menu(self, index: QModelIndex) -> QMenu   # "Open in Explorer", disabled when relpath is None
  ```
- **Single-click selection** emits `folder_selected(node.relpath)`. That's the folder for folder rows and the owning folder for type rows. A Type ▸ Folder top-level type row has `relpath` `None`. A double-click calls `open_node`. The tree fills the column and has no fixed height (a change from the replaced plan, now that the tree has a page of its own).

- [ ] **Step 1: Write the failing tests.** Port the seven tests from the replaced plan's Task 5 with these changes: the class is `StorageTreeView`; the caption asserts go; `test_missing_folder_shows_message` asserts that the `message` signal carries `NOT_FOUND.format(path=tmp_path / "Drone")`. Add:

```python
def test_click_selects_folder(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    with qtbot.waitSignal(view.folder_selected) as sig:
        view.tree.setCurrentIndex(<Canon/2025 index>)
    assert sig.args == ["Canon/2025"]

def test_type_row_selects_its_folder(...):          # CR3 under Canon/2025 -> "Canon/2025"
def test_top_level_type_selects_none(...):          # type grouping, MP4 row -> None
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/gui/test_storage_tree_view.py -v`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** as the replaced plan's Task 5 Step 3 describes. Use a `QStandardItemModel` in pre-sorted order, a share-bar `QStyledItemDelegate` on column 2 with `palette(highlight)` for folder bars and type colours for type bars, right-aligned Size and Files, and the root expanded one level. Emit `folder_selected` from `selectionModel().currentChanged`.
- [ ] **Step 4: Run them to verify they pass.** Same command. Expected: PASS.
- [ ] **Step 5: Lint, format, and commit.** Message: "Add the storage tree widget".

---

### Task 7: Pairs list widget and background checker

**Files:**
- Create: `smart_gallery/gui/storage/checker.py`, `smart_gallery/gui/storage/pairs_view.py`
- Test: `tests/gui/test_pairs_view.py`

**Interfaces:**
- Consumes: `Pair`, `PairKey`, `FileRef`, `KEEP_*`, `in_scope` and `resolve_removals` (Task 2); `check_pairs`, `CheckCache`, `MATCH`, `DIFFERENT` and `UNREADABLE` (Task 3); `format_bytes` and `type_label` (Task 1); `ctx.thumbs.request(root, media_id, mtime_ns, relpath, ext, media_type)` and `ctx.thumbs.ready(root, media_id, path)`.
- Produces:
  ```python
  # checker.py
  class PairChecker(QObject):
      checked = Signal(object, str)          # PairKey, verdict
      progress = Signal(int, int)            # done, total
      finished = Signal()
      def start(self, pairs: list[Pair], root: Path, cache_path: Path) -> None   # cancels any running check first
      def cancel(self) -> None

  # pairs_view.py: user-visible copy, exact
  SCOPE_ONE = "In {folder} · {n:,} pairs"
  SCOPE_ALL = "All folders · {n:,} pairs"
  SHOW_ALL = "Show all {n:,} pairs"
  EMPTY_FOLDER = "No RAW + JPEG pairs in this folder"
  EMPTY_ALL = "No RAW + JPEG pairs in this gallery"
  CHECKING = "Checking pictures… {done:,} / {total:,}"
  STATUS = {None: "checking…", DIFFERENT: "⚠ look different", UNREADABLE: "⚠ couldn't compare", MATCH: ""}
  FOOTER = "{n:,} chosen · frees {size}"
  FOOTER_FOLDERS = " (in {k:,} folders)"     # appended when chosen pairs lie outside the current scope
  FOOTER_NOTE = "Undecided pairs are left alone"
  MOVE = "Move {n:,} files to Recycle Bin"
  MOVE_NONE = "Move files to Recycle Bin"     # disabled
  CONFIRM = "Move {n:,} files ({size}) to the Recycle Bin?"
  LEAVE = "You have {n:,} unapplied choices. Discard them?"
  KEY_ROLE, CHOICE_ROLE, VERDICT_ROLE = (Qt.ItemDataRole.UserRole + i for i in (1, 2, 3))

  class PairsView(QWidget):
      move_requested = Signal(list)           # list[FileRef] confirmed by the user
      pair_activated = Signal(object)         # PairKey; opens PairDialog
      list: QListView; scope_label: QLabel; show_all: QPushButton; status: QLabel
      footer_label: QLabel; clear_button: QPushButton; move_button: QPushButton
      def set_pairs(self, pairs: list[Pair], root: Path) -> None   # keeps choices whose key still exists
      def set_scope(self, folder: str | None) -> None
      def set_verdict(self, key: PairKey, verdict: str) -> None
      def choose(self, key: PairKey, choice: str | None) -> None   # same choice again -> None (undecided)
      def choices(self) -> dict[PairKey, str]
      def clear_choices(self) -> None
      def removals(self) -> list[FileRef]                          # resolve_removals over ALL pairs
      def confirm_leave(self) -> bool                              # True when no choices, else asks LEAVE
  class PairsDelegate(QStyledItemDelegate):
      def button_rects(self, option_rect: QRect) -> dict[str, QRect]   # KEEP_RAW / KEEP_JPEG / KEEP_BOTH
  ```
- **Model:** a `QAbstractListModel` over the pairs in scope, plus folder heading rows (headings have `KEY_ROLE` `None`). It keeps every pair, so `removals()` and the footer cover all choices.
- **Delegate:**
  - Paints the two thumbnails (RAW first), the stem, and the details line, `"CR3 24 MB · JPG 9.8 MB"`, with `" + XMP"` after the RAW size when a sidecar exists.
  - Paints the `STATUS` text and three pills, RAW / JPEG / Both. The selected pill uses `palette(highlight)`.
  - `editorEvent` maps a click inside `button_rects` to `choose`.
  - A click elsewhere on a pair row emits `pair_activated(key)`, which opens `PairDialog`. That's a `QDialog` with both images (`thumbs.cache.load_preview(abspath, ext)`, off the GUI thread) and the same three buttons.
- **The footer's "frees"** sums `size_bytes` of `removals()`. "n chosen" counts decided pairs, `KEEP_BOTH` included. `move_button` uses `len(removals())`. Clicking it shows `QMessageBox.question` with `CONFIRM`; on Yes it emits `move_requested(removals())`.
- **`PairChecker`** runs `check_pairs` in one `QRunnable` on a private one-thread pool. It relays results to the GUI thread with signals. Generations work like `RepoQuery`'s: results from a cancelled run are dropped. Its `cancelled` callback reads a `threading.Event`.

- [ ] **Step 1: Write the failing tests** (`tests/gui/test_pairs_view.py`). Build `Pair`s directly. Construct `PairsView(ctx)` with `ctx.thumbs` set to a stub that records requests.

```python
def test_rows_grouped_by_folder(qtbot, view, pairs):          # headings "A", "B" before their pairs, in pair order
def test_scope_filters_and_labels(qtbot, view, pairs):
    view.set_scope("a")
    assert view.scope_label.text() == "In a · 2 pairs" and view.show_all.isVisible()
    view.show_all.click()
    assert view.scope_label.text() == "All folders · 3 pairs" and not view.show_all.isVisible()
def test_empty_states(qtbot, view):                           # EMPTY_ALL with no pairs; EMPTY_FOLDER when the scope has none
def test_choose_updates_footer(qtbot, view, pairs):
    view.choose(p1.key, KEEP_RAW)                            # removes p1.jpeg (9.8 MB)
    assert view.footer_label.text().startswith("1 chosen · frees ")
    assert view.move_button.text() == "Move 1 files to Recycle Bin" and view.move_button.isEnabled()
    view.choose(p1.key, KEEP_RAW)                            # same again -> undecided
    assert view.choices() == {} and view.move_button.text() == MOVE_NONE and not view.move_button.isEnabled()
def test_keep_both_counts_as_chosen_but_frees_nothing(...)
def test_footer_mentions_other_folders(...):                 # choose in "a" and "b", scope "a" -> ends with " (in 2 folders)"
def test_clear_choices(...)
def test_pill_click_chooses(qtbot, view, pairs):             # QTest.mouseClick at button_rects(visualRect(row))[KEEP_JPEG].center()
def test_verdict_shows_warning(...):                         # set_verdict(p1.key, DIFFERENT) -> index.data(VERDICT_ROLE) == DIFFERENT
def test_move_emits_removals_after_confirm(qtbot, view, pairs, monkeypatch):
    # monkeypatch QMessageBox.question -> Yes; choose(p1.key, KEEP_JPEG) with sidecar
    # -> move_requested args == [[p1.raw, p1.sidecar]]
def test_move_cancelled_emits_nothing(...)                   # QMessageBox.question -> No
def test_set_pairs_keeps_existing_choices(...):              # choose p1 and p2; set_pairs([p2, p3]) -> choices() == {p2.key: ...}
def test_confirm_leave(...):                                 # no choices -> True without asking; with choices -> asks LEAVE; No -> False
def test_checker_reports_cached_and_new(qtbot, tmp_path, monkeypatch):
    # monkeypatch checker's check_pairs with a fake that calls on_result twice -> two `checked` signals, then `finished`
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/gui/test_pairs_view.py -v`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement `checker.py` and `pairs_view.py`** to the interfaces above. Request thumbnails only for rows being painted (from the delegate), and repaint on `thumbs.ready`.
- [ ] **Step 4: Run them to verify they pass.** Same command. Expected: PASS.
- [ ] **Step 5: Lint, format, and commit.** Message: "Add the RAW + JPEG pairs list and its background checker".

---

### Task 8: Storage page, its tab, the recycle job and leave guards

**Files:**
- Create: `smart_gallery/gui/storage/view.py`
- Modify:
  - `smart_gallery/gui/areas.py`: add a `_storage` factory and `Area("storage", "Storage", <generic drive/disk icon name from the existing set>, _storage)` right after Stats.
  - `smart_gallery/gui/context.py`: leave guards.
  - `smart_gallery/gui/app.py`: `closeEvent`.
  - `smart_gallery/gui/galleries/view.py`: `_select`.
- Test: `tests/gui/test_storage_view.py`, the areas test (remove the `xfail` from Task 5), `tests/gui/test_app.py` (or the existing main-window test file)

**Interfaces:**
- Consumes: `StorageTreeView` (Task 6), `PairsView` and `PairChecker` (Task 7), `find_pairs` (Task 2), `build_storage` and `format_bytes` (Task 1), `write_list` and `RecycleEntry` (Task 4), `load_json`/`save_json` (Task 5), `RepoQuery`, `JobSpec`.
- Produces:
  ```python
  # context.py
  class AppContext:
      def add_leave_guard(self, guard: Callable[[], bool]) -> None
      def can_leave(self) -> bool            # True when every guard returns True

  # storage/view.py
  STATE_FILE = "storage.json"                # {"grouping": "folder"|"type", "split": [int, int]}
  CHECKS_DIR = "pair_checks"                 # app_data_dir() / CHECKS_DIR / f"{gallery.db_uuid}.json"
  LISTS_DIR = "recycle_lists"                # app_data_dir() / LISTS_DIR / f"{timestamp}.tsv"
  CAPTION = "{root} · {size} in {files:,} files"
  JOB_TITLE = "Move to Recycle Bin"

  @dataclass(frozen=True)
  class StorageData:
      tree: StorageTree | None; pairs: list[Pair]; root_name: str
  def load_storage(repo: GalleryRepository) -> StorageData   # no Qt; runs on the worker

  class StorageView(QWidget):
      caption: QLabel; splitter: QSplitter
      tree_view: StorageTreeView; pairs_view: PairsView; checker: PairChecker
      def reload(self, *_args) -> None
  ```
- **`load_storage`** builds the tree from `repo.query_df()`, using its `relpath`, `ext` and `size_bytes` columns. The root name is `config.drive_root_of(repo.db_path)`'s `.name`, or its `as_posix()` when the name is empty, as with `E:/`. Pairs come from `find_pairs(repo)`.
- **Reloading:**
  - `StorageView` reloads on `gallery_changed` and `catalog_updated` through `RepoQuery`.
  - On data, it calls `tree_view.set_tree`, then `pairs_view.set_pairs`, then `checker.start(pairs, root, cache_path)`, and sets `caption` to `CAPTION`.
  - Wiring: `tree_view.folder_selected` → `pairs_view.set_scope`; `tree_view.message` → `caption`; `checker.checked` → `pairs_view.set_verdict`; `checker.progress` → `pairs_view.status`.
  - With no gallery, or no catalog rows, it shows the app's usual empty message, like Stats.
- **On a gallery change** (not on a catalog update), `pairs_view.clear_choices()` runs before reloading, and the scope resets to all folders.
- **`move_requested(files)`**:
  - Writes `LISTS_DIR/<YYYYmmdd-HHMMSS>.tsv` with `write_list`.
  - Starts `ctx.jobs.start(JobSpec(JOB_TITLE, (("recycle", str(root), "--list", str(path)),)))`. If `start` returns False because a job is already running, it shows the app's usual "a job is running" message.
- **The left/right `QSplitter`** starts at equal halves. Grouping and splitter sizes are restored from and saved to `STATE_FILE`, ignoring invalid values.
- **Leave guards:**
  - `StorageView.__init__` calls `ctx.add_leave_guard(self.pairs_view.confirm_leave)`.
  - `MainWindow.closeEvent` begins with `if not self.ctx.can_leave(): event.ignore(); return`, before its running-job prompt.
  - `GalleriesPanel._select(gallery)`: when `self.ctx.current != gallery and not self.ctx.can_leave()`, it re-selects the current gallery in its list (via `_show_current()`) and returns without switching.
  - `refresh`/`recheck` don't ask, because a drive coming or going isn't the user leaving.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/gui/test_storage_view.py
def test_load_storage(repo, make_item):                     # pairs and tree from one repo; root_name == drive folder name
def test_page_shows_both_columns(qtbot, repo, make_item):   # caption == CAPTION...; tree root row present; pairs_view has 1 pair
def test_folder_click_scopes_pairs(qtbot, ...):             # select "a" in the tree -> pairs_view.scope_label "In a · 1 pairs"
def test_move_starts_recycle_job(qtbot, ..., monkeypatch):
    # ctx.jobs = stub recording start(spec) -> True; emit pairs_view.move_requested([fileref])
    # spec.title == JOB_TITLE; spec.argvs[0][:3] == ("recycle", str(root), "--list")
    # read_list(Path(spec.argvs[0][4])) == [RecycleEntry(fileref.relpath, fileref.size_bytes, fileref.mtime_ns)]
def test_choices_survive_reload_for_remaining_pairs(qtbot, ...):   # Review Focus 5
    # choose p1 and p2; delete p1.jpeg's row from the catalog (a writable repo in the test); emit ctx.catalog_updated
    # -> after reload, choices() == {p2.key: ...}
def test_gallery_change_clears_choices(qtbot, ...)
def test_grouping_and_split_remembered(qtbot, ...)          # new StorageView reads the same storage.json

# areas test: drop xfail on test_stats_label; also assert AREAS[3].id == "storage"

# main window / galleries tests
def test_close_blocked_by_leave_guard(qtbot, ...):          # ctx.add_leave_guard(lambda: False); window.close() -> window.isVisible()
def test_gallery_switch_blocked_by_leave_guard(qtbot, ...): # guard False; panel._select(other) -> ctx.current unchanged
def test_can_leave_true_without_guards():                   # AppContext().can_leave() is True
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/gui/test_storage_view.py -v`, plus the areas and main window tests. Expected: FAIL (`ModuleNotFoundError`, XPASS strict on the areas test).
- [ ] **Step 3: Implement** `storage/view.py`, the area entry, and the leave guards to the interfaces above.
- [ ] **Step 4: Run the full suite.** Run `uv run pytest -m "not faces"`. Expected: all PASS, including `tests/unit/test_colors.py` and the core-import-without-PySide test.
- [ ] **Step 5: Lint, format, and commit.** Message: "Add the Storage page with the size tree and RAW + JPEG pairs".
- [ ] **Step 6: Check by hand.** Run `uv run smart-gallery gui` on a real gallery and check the following:
  - The tabs read Library, Import, Stats, Storage.
  - On Stats, panels collapse and stay collapsed after a restart.
  - On Storage, the biggest folder is first, and clicking it scopes the pairs.
  - The checks fill in as they finish.
  - Choose a few pairs and move them. The files are in the Recycle Bin and can be restored, and the rows are gone from the list.
  - With a choice pending, closing the app asks first, and so does switching gallery.
