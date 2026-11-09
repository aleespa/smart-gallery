# Library Justified Grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Library's fixed, captioned cells with Google-Photos-style justified rows whose tile shapes come from catalog data, including a newly stored EXIF rotation.

**Architecture:** A new `media_items.orientation` column (schema v9) is filled at analysis time and backfilled by `sync` (and copied to mirrors by `sync-mirror`). A Qt-free `justify` module turns per-row aspects into rows; `GroupedGrid` lays tiles out from that table instead of equal cells, and a new tile delegate paints the look from the approved mock-up. Thumbnails grow to 512 px under new file names.

**Tech Stack:** Python 3.13, SQLite, ExifTool (`-j -n`), PySide6, Pillow, pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-11-09-library-justified-grid-design.md` (mock-up: https://claude.ai/artifact/HC2CMygNBEiFjA7S5kyEgT)

## Global Constraints

- Work on a new branch `library-justified-grid` cut from the current `history-undo` HEAD (it carries the UI kit this builds on).
- `orientation` uses EXIF meaning 1–8; `NULL` = not read yet; a file read with no tag stores 1.
- Video `Rotation` degrees map 0→1, 90→6, 180→3, 270→8.
- `SCHEMA_VERSION = 9`; `migrate()` stays idempotent; read-only opens never migrate and must tolerate a missing `orientation` column.
- The backfill writes only `orientation`; it never calls `invalidate_faces_by_relpath`.
- Row height: min 110, max 340, default 190, Ctrl+wheel step 20. Gap 4 px. Grid side padding 12 px.
- Aspect clamp 0.4–3.0; fallbacks 3:2 photo, 16:9 video, 1:1 other.
- `THUMB_EDGE = 512`.
- Colours only via `theme` (`theme.ACCENT` for lines, `palette(highlight)`/`ACCENT_FILL` for fills, `BADGE_BACK`/`BADGE_TEXT` for chips). Always dark; no light branches.
- Ruff + Black only the files you touch (`uv run ruff check <files>`, `uv run black <files>`).
- Commit messages end with the session's attribution lines.

## Review Focus

1. **A rotated photo read by real ExifTool** (camera portrait, Orientation=6) must get a portrait tile. Test: Task 1 `test_exiftool_reads_orientation` (marker `exiftool`).
2. **A mirror drive plugged in and browsed before any read-write open** (v8 catalog, read-only) must show the Library, not an SQL error. Test: Task 5 `test_library_rows_on_v8_catalog_read_only`.
3. **Cancelling the first sync mid-backfill** must keep the batches already read, and the next sync must continue from there. Test: Task 2 `test_backfill_keeps_finished_batches_on_cancel`.
4. **A large gallery (50k rows) resized** must re-lay out quickly. Test: Task 6 `test_relayout_50k_rows_is_fast` (generous bound, 1.5 s).
5. **Many small tiles on a big screen** must not evict visible thumbnails (thumbnails now 4× larger). Test: Task 7 `test_pixmap_cache_holds_a_full_screen_of_small_tiles`.

---

### Task 1: `orientation` column, schema v9, ExifTool mapping

**Files:**
- Modify: `smart_gallery/config.py` (`SCHEMA_VERSION = 9`)
- Modify: `smart_gallery/models.py` (field, `DB_COLUMNS`, `from_exiftool`, new helpers)
- Modify: `smart_gallery/db/schema.py` (`CREATE_MEDIA_ITEMS`, `migrate`)
- Modify: `smart_gallery/analysis/extract.py` (tag args)
- Test: `tests/unit/test_models.py`, `tests/test_repository.py`, `tests/integration/` (exiftool test; follow the folder's existing pattern)

**Interfaces:**
- Produces:
  - `MediaItem.orientation: Optional[int] = None`, last field; `"orientation"` last in `DB_COLUMNS`; `orientation INTEGER` as the last column of `CREATE_MEDIA_ITEMS` (after `updated_at`, which is where `ALTER TABLE ADD COLUMN` puts it on migrated catalogs — check `test_models.py` lock-step compares only `DB_COLUMNS`, and adjust the placement so the test and migrated catalogs agree).
  - `models.orientation_from_exif(record: dict, media_type: str) -> int` — photo: `int(record["Orientation"])` if 1–8 else 1; video: maps `record["Rotation"]` (normalised `% 360`) by the table above, else 1.
  - `models.display_size(width: int | None, height: int | None, orientation: int | None) -> tuple[int | None, int | None]` — swaps for orientation 5–8.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_models.py`)

```python
@pytest.mark.parametrize("o,expected", [(1, (6000, 4000)), (3, (6000, 4000)), (5, (4000, 6000)),
                                         (6, (4000, 6000)), (7, (4000, 6000)), (8, (4000, 6000)),
                                         (None, (6000, 4000)), (2, (6000, 4000)), (4, (6000, 4000))])
def test_display_size(o, expected):
    assert display_size(6000, 4000, o) == expected

def test_display_size_missing_dims():
    assert display_size(None, 4000, 6) == (4000, None)

def test_from_exiftool_photo_orientation():
    rec = {"SourceFile": "E:/a.jpg", "FileName": "a.jpg", "Orientation": 6,
           "ImageWidth": 6000, "ImageHeight": 4000}
    item = MediaItem.from_exiftool(rec)
    assert (item.width, item.height, item.orientation) == (6000, 4000, 6)

def test_from_exiftool_photo_without_tag_is_1():
    assert MediaItem.from_exiftool({"SourceFile": "E:/a.jpg", "FileName": "a.jpg"}).orientation == 1

@pytest.mark.parametrize("deg,o", [(0, 1), (90, 6), (180, 3), (270, 8), (-90, 8), (45, 1)])
def test_from_exiftool_video_rotation(deg, o):
    rec = {"SourceFile": "E:/v.mp4", "FileName": "v.mp4", "Rotation": deg}
    assert MediaItem.from_exiftool(rec).orientation == o

def test_from_path_leaves_orientation_unread(tmp_path):
    f = tmp_path / "notes.txt"; f.write_text("x")
    assert MediaItem.from_path(f).orientation is None
```

(Match `from_exiftool`'s real required keys by reading it; the records above show intent.)

- [ ] **Step 2: Write the failing migration tests** (`tests/test_repository.py`)

```python
def test_migrate_v8_adds_orientation(drive):
    r = GalleryRepository.create(drive, label="t"); path = r.db_path
    r.conn.execute("ALTER TABLE media_items DROP COLUMN orientation")
    r.conn.execute("UPDATE meta SET value='8' WHERE key='schema_version'"); r.conn.commit(); r.close()
    with GalleryRepository.open(path) as r2:
        cols = [c["name"] for c in r2.conn.execute("PRAGMA table_info(media_items)")]
        assert "orientation" in cols
        assert r2.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "9"
        migrate(r2.conn)  # second run is harmless
```

- [ ] **Step 3: Run them; verify they fail**

Run: `uv run pytest tests/unit/test_models.py tests/test_repository.py -k "orientation or display_size or rotation or migrate_v8" -v`
Expected: FAIL (`display_size` not defined / no `orientation` column).

- [ ] **Step 4: Implement**

Add the field, column and helpers; `from_exiftool` sets `item.orientation = orientation_from_exif(exif, media_type)` for image and video. Add `"-Orientation"` to `EXIFTOOL_IMAGE_TAG_ARGS` and `"-Rotation"` to `EXIFTOOL_VIDEO_TAG_ARGS`. Add the `if current < 9:` step: `PRAGMA table_info(media_items)`, `ALTER TABLE media_items ADD COLUMN orientation INTEGER` when missing.

- [ ] **Step 5: Add the ExifTool test** (marker `exiftool`)

`test_exiftool_reads_orientation`: copy a JPEG fixture from `tests/resources/images/` into `tmp_path`, run `exiftool -n -Orientation=6 -overwrite_original <file>`, call `analyze_paths([file])`, assert `items[0].orientation == 6` and `display_size(...)` returns `height > width` when the stored `width > height`.

- [ ] **Step 6: Run tests; verify they pass**

Run: `uv run pytest tests/unit/test_models.py tests/test_repository.py tests/unit/test_faces.py -v` then `uv run pytest -m exiftool -k orientation -v`
Expected: all PASS (lock-step test included).

- [ ] **Step 7: Commit** — `Store EXIF orientation on media items (schema v9)`

---

### Task 2: Backfill orientation during `sync`

**Files:**
- Modify: `smart_gallery/analysis/extract.py` (shared batch loop, `read_orientations`)
- Modify: `smart_gallery/progress.py` (`ROTATING = "Reading rotation"`)
- Modify: `smart_gallery/db/repository.py`
- Modify: `smart_gallery/services/sync.py`
- Test: `tests/test_sync.py`, `tests/test_extract_cancel.py` (must stay green)

**Interfaces:**
- Consumes: `orientation_from_exif` (Task 1).
- Produces:
  - `extract.read_orientations(items: list[tuple[Path, str]], progress_callback=None, on_batch: Callable[[dict[Path, int]], None] | None = None) -> dict[Path, int]` — `(abspath, media_type)` in; returns only files ExifTool produced a record for. Keys are the input `Path` objects (map records back via `os.path.normcase(os.path.normpath(...))` of `SourceFile`). Calls `on_batch` after each finished batch. Uses a new tag profile `"rotation"` (`-Orientation -Rotation`, with `-fast`, not `-fast2`: video `moov` can sit after `mdat`). Emits `ProgressEvent(ROTATING, done, total)`; honours `check_cancelled()`.
  - Refactor: the batching/threading loop inside `analyze_paths._run` becomes a module function `_run_batches(files, tag_profile, stop_event, on_records: Callable[[list[dict], int], None])` used by both; behaviour of `analyze_paths` unchanged.
  - `GalleryRepository.rows_missing_orientation() -> list[tuple[int, str, str]]` — `(id, relpath, media_type)` for `media_type IN ('image','video') AND orientation IS NULL`.
  - `GalleryRepository.set_orientations(values: dict[int, int]) -> None` — `UPDATE media_items SET orientation=? WHERE id=?`, one transaction, commits. Touches no other column (not `updated_at` either).
  - `SyncReport.rotation_read: int = 0` (count needing a read on dry run; count written on a real run).

- [ ] **Step 1: Write the failing tests** (`tests/test_sync.py`; monkeypatch `syncmod.read_orientations` the way `analyze_paths` is patched there)

```python
def test_sync_backfills_orientation_only(repo, drive, drive_file, make_item, monkeypatch): ...
    # rows a.jpg (orientation None, faces present), b.jpg (orientation 1), c.jpg offloaded (None)
    # fake read_orientations returns {path_of_a: 6}
    # assert: a.orientation == 6; a's other columns and updated_at unchanged; a's faces still there;
    #         b not passed to the reader; c not passed (offloaded); report.rotation_read == 1

def test_sync_backfill_skips_rows_being_reanalysed(...):
    # a.jpg changed on disk (mtime differs): goes through analyze_paths, not read_orientations

def test_sync_backfill_leaves_null_when_unreadable(...):
    # reader returns {} -> orientation stays None; next sync asks again

def test_sync_dry_run_counts_rotation_reads(...):
    # dry_run=True: reader not called, report.rotation_read == number of NULL present rows, DB unchanged

def test_backfill_keeps_finished_batches_on_cancel(...):
    # fake reader calls on_batch({a: 6}) then raises Cancelled
    # assert a.orientation == 6 after the exception propagates
```

- [ ] **Step 2: Run; verify they fail**

Run: `uv run pytest tests/test_sync.py -k "orientation or rotation or backfill" -v`
Expected: FAIL (`read_orientations` missing).

- [ ] **Step 3: Implement**

In `sync_drive`, after the existing apply (and on dry run, before returning), candidates = `rows_missing_orientation()` minus offloaded relpaths minus `to_add | to_update`, restricted to relpaths present in `live`; pass `(live[rel][0], media_type)`. Real run: `read_orientations(..., on_batch=lambda found: repo.set_orientations({id_of[p]: o for p, o in found.items()}))`. Log `Sync plan — … rotation=N` alongside the existing plan line.

- [ ] **Step 4: Run tests; verify they pass**

Run: `uv run pytest tests/test_sync.py tests/test_extract_cancel.py tests/test_progress_services.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** — `Read missing photo rotation during sync`

---

### Task 3: `sync-mirror` copies orientation to the mirror

**Files:**
- Modify: `smart_gallery/services/mirror.py`
- Test: `tests/test_mirror_sync.py`

**Interfaces:**
- Consumes: `orientation` column (Task 1); `working.baseline(uuid) -> dict[str, BaselineRow]`.
- Produces: `fill_mirror_orientation(working: GalleryRepository, mirror: GalleryRepository, uuid: str) -> int` — for each relpath whose D row has `orientation IS NULL` and E row has a value, copy it **only** when the baseline row matches both sides (`E.size == D.size == base.size_bytes`, `E.mtime_ns == base.working_mtime_ns`, `D.mtime_ns == base.mirror_mtime_ns`). Returns rows updated. Called in `apply_mirror_sync` right after `applier.pending.flush()` (inside the normal path, not the `finally`).

- [ ] **Step 1: Write the failing tests**

```python
def test_mirror_sync_fills_null_orientation_for_unchanged_files(...):
    # paired E/D, file synced earlier (baseline present), E row orientation 6, D row None
    # after apply_mirror_sync: D row orientation == 6

def test_mirror_sync_leaves_orientation_when_files_differ(...):
    # D's mtime differs from baseline.mirror_mtime_ns -> D stays None

def test_new_copy_to_mirror_carries_orientation(...):
    # file new on E with orientation 8 -> D row (via _row_for) has 8
```

- [ ] **Step 2: Run; verify they fail** — `uv run pytest tests/test_mirror_sync.py -k orientation -v` → FAIL.
- [ ] **Step 3: Implement** `fill_mirror_orientation` and the call.
- [ ] **Step 4: Run** `uv run pytest tests/test_mirror_sync.py tests/test_mirror_offload.py tests/test_mirror_cli.py -v` → PASS.
- [ ] **Step 5: Commit** — `Copy photo rotation to mirror catalogs`

---

### Task 4: 512 px thumbnails under new names

**Files:**
- Modify: `smart_gallery/gui/thumbs/cache.py`
- Test: `tests/unit/test_thumb_cache.py`, `tests/gui/test_thumb_loader.py` (keep green)

**Interfaces:**
- Produces: `THUMB_EDGE = 512`; `thumb_path(root, media_id, mtime_ns, ext="")` returns `.../thumbs/<id//1000>/<id>-<mtime>-s512.jpg` for every file type (`ext` kept for callers; the `-r1` RAW suffix is retired because every thumbnail is remade). `_remove_legacy` deletes `<id>-<mtime>.jpg`, `<id>-<mtime>-r1.jpg` and both `.none` markers once the new file is written.

- [ ] **Step 1: Write the failing tests**

```python
def test_thumbnail_long_edge_is_512(tmp_path):  # make a 2000x1000 JPEG, ensure_thumbnail -> 512x256

def test_old_256_thumbnail_is_not_reused_and_is_removed(tmp_path):
    # pre-create <id>-<mtime>.jpg (256 px) and <id>-<mtime>-r1.jpg; ensure_thumbnail
    # -> returns the -s512 path; both old files gone

def test_old_none_marker_does_not_block_new_thumbnail(tmp_path):
    # old <id>-<mtime>.none exists; a readable file now gets a thumbnail
```

- [ ] **Step 2: Run; verify fail** — `uv run pytest tests/unit/test_thumb_cache.py -v`.
- [ ] **Step 3: Implement** (update any existing test that asserts the `-r1` name to the new name).
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_thumb_cache.py tests/gui/test_thumb_loader.py tests/gui/test_viewer_window.py -v` → PASS.
- [ ] **Step 5: Commit** — `Make thumbnails 512 px`

---

### Task 5: Justify module and tile shapes on `LibraryRow`

**Files:**
- Create: `smart_gallery/gui/library/justify.py` (Qt-free)
- Modify: `smart_gallery/gui/library/model.py`
- Test: `tests/unit/test_justify.py`, `tests/unit/test_library_model.py`

**Interfaces:**
- Consumes: `display_size` (Task 1).
- Produces:
  - Constants in `justify.py`: `GAP = 4`, `MIN_ASPECT = 0.4`, `MAX_ASPECT = 3.0`, `ROW_HEIGHT_MIN = 110`, `ROW_HEIGHT_MAX = 340`, `ROW_HEIGHT_DEFAULT = 190`, `ROW_HEIGHT_STEP = 20`.
  - `tile_aspect(width, height, orientation, media_type: str) -> float` — upright ratio via `display_size`, clamped; fallbacks 1.5 / 16/9 / 1.0 when a side is missing or ≤ 0.
  - `@dataclass(frozen=True) class JustifiedRow: start: int; height: int; widths: tuple[int, ...]; full: bool`
  - `justify(aspects: Sequence[float], width: int, target: int, gap: int = GAP) -> list[JustifiedRow]` — `start` is the index of the row's first item.
  - `LibraryRow` gains `width: Optional[int] = None`, `height: Optional[int] = None`, `orientation: Optional[int] = None` (after `offloaded`), and `aspect` property → `tile_aspect(...)`.
  - `library_rows` selects them; when `PRAGMA table_info(media_items)` lacks `orientation`, select `NULL AS orientation`.

Algorithm (rounding is the part tests pin):

```python
# accumulate aspects; when sum*target + gap*(n-1) >= width, close a full row:
#   exact_h = (width - gap*(n-1)) / sum
#   widths = [floor(a*exact_h) for a in row[:-1]]; last = width - gap*(n-1) - sum(widths)
#   height = round(exact_h)
# leftover items: full=False, height=target, widths=[round(a*target)]
# width < 1 -> treat as 1. Empty input -> [].
```

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_justify.py`)

```python
def test_full_rows_end_flush():
    rows = justify([1.5] * 10, 1000, 190)
    for r in rows:
        if r.full:
            assert sum(r.widths) + GAP * (len(r.widths) - 1) == 1000

def test_last_row_keeps_target_height_and_is_not_stretched():
    rows = justify([1.5] * 4, 1000, 190)
    assert rows[-1].full is False and rows[-1].height == 190
    assert sum(rows[-1].widths) < 1000

def test_rows_cover_every_item_in_order():
    rows = justify([1.5, 0.67, 1.0, 3.0, 1.33] * 7, 800, 150)
    assert [r.start for r in rows] == sorted(r.start for r in rows)
    assert sum(len(r.widths) for r in rows) == 35

def test_single_very_wide_item_fills_a_row_lower():
    [r] = justify([3.0], 300, 190)
    assert r.full and r.widths == (300,) and r.height == 100

def test_empty():
    assert justify([], 800, 190) == []

@pytest.mark.parametrize("args,expected", [
    ((6000, 4000, 1, "image"), 1.5), ((6000, 4000, 6, "image"), 4000 / 6000),
    ((1920, 1080, 6, "video"), 1080 / 1920), ((None, None, None, "image"), 1.5),
    ((0, 1080, 1, "video"), 16 / 9), ((None, None, None, "other"), 1.0),
    ((12000, 2000, 1, "image"), 3.0), ((500, 5000, 1, "image"), 0.4)])
def test_tile_aspect(args, expected):
    assert tile_aspect(*args) == pytest.approx(expected)
```

`tests/unit/test_library_model.py`: `test_library_rows_carry_size_and_orientation` (row with 6000×4000, orientation 6 → `aspect ≈ 0.667`), and `test_library_rows_on_v8_catalog_read_only` (drop the column + set version 8 as in Task 1, open `read_only=True`, `library_rows` returns rows with `orientation is None`).

- [ ] **Step 2: Run; verify fail** — `uv run pytest tests/unit/test_justify.py tests/unit/test_library_model.py -v`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; verify pass** (same command).
- [ ] **Step 5: Commit** — `Justified row layout and tile shapes`

---

### Task 6: `GroupedGrid` lays out justified rows

**Files:**
- Modify: `smart_gallery/gui/library/grid.py`
- Test: create `tests/gui/test_justified_grid.py`; update `tests/gui/test_library_view.py:285-335`

**Interfaces:**
- Consumes: `justify`, `JustifiedRow`, constants (Task 5).
- Produces (grid stays model-agnostic):
  - `ASPECT_ROLE = Qt.ItemDataRole.UserRole + 20` in `grid.py`; the grid reads each index's aspect from it (missing → 1.5).
  - `GroupedGrid.set_row_height(px: int) -> None` (clamped to min/max; re-lays out; emits `row_height_changed(int)` only when it changes), `row_height() -> int`, `row_height_changed = Signal(int)`.
  - `setGridSize`/`gridSize` removed (callers updated in Task 8).
  - Ctrl+wheel over the viewport: `set_row_height(row_height() ± ROW_HEIGHT_STEP)` (wheel up = bigger), event accepted, no scroll.
  - Layout: per open group, `justify(aspects, viewport_width - 2*SIDE_PAD, row_height)`; content keeps flat tables (row tops, row → first model row, tile x offsets) so `visualRect`, `indexAt`, `_rows_in`, `visible_rows`, `scrollTo`, `setSelection` use `bisect`. `SIDE_PAD = 12`. Rows separated by `GAP`. `TILE_INSET` goes (the gap replaces it). Headings keep `HEADER_HEIGHT = 40`, collapse, Collapse all / Expand all.
  - Pinned heading: `_paint_headings` also draws, at viewport y 0, the heading of the group containing the top visible content (pushed up by the next heading as it arrives), over the tiles with the window background. `heading_at(point)` returns that group for points inside the pinned band, so a click there toggles it.
  - Keyboard: Left/Right reading order (across groups, skipping collapsed, as today); Up/Down → the tile in the adjacent visual row (crossing groups) whose horizontal centre is nearest the current tile's centre; PageUp/PageDown move by about one viewport height; Home/End unchanged.

- [ ] **Step 1: Write the failing tests** (`tests/gui/test_justified_grid.py`, using a `QStandardItemModel` whose items set `ASPECT_ROLE`, and `set_groups`)

```python
def test_full_row_tiles_end_flush_with_padding(qtbot): # widths 1000; first full row: last tile right edge == 1000 - 12
def test_tiles_keep_their_aspect(qtbot): # rect.width()/rect.height() ≈ aspect for tiles of the last row
def test_index_at_and_visual_rect_agree(qtbot): # for every row: indexAt(visualRect(i).center()).row() == i
def test_gap_is_not_a_tile(qtbot): # point between two tiles -> invalid index
def test_set_row_height_clamps_and_signals(qtbot): # set 1000 -> 340, one signal; set 340 again -> no signal
def test_ctrl_wheel_changes_row_height(qtbot): # wheel up with Ctrl -> 210 from 190; scrollbar unchanged
def test_collapsed_group_has_no_tiles(qtbot): # visualRect null; heading_at still finds it
def test_down_moves_to_nearest_tile_below(qtbot): # construct aspects so row 2's tile under row 1's 3rd tile is known
def test_pinned_heading_toggles_its_group(qtbot): # scroll into group 1; click y=5 -> group 1 collapses
def test_relayout_50k_rows_is_fast(qtbot): # 50k items, one group; resize 900->1200; _relayout under 1.5 s
```

Update `test_grid_headings_take_space_and_are_not_tiles` and the collapse test in `test_library_view.py` to the new geometry (heading still `HEADER_HEIGHT` tall; first tile starts at `HEADER_HEIGHT`; `x == SIDE_PAD`).

- [ ] **Step 2: Run; verify fail** — `uv run pytest tests/gui/test_justified_grid.py -v`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/gui/test_justified_grid.py tests/gui/test_library_view.py tests/gui/test_person_view.py -v` → PASS (person view may need Task 8's caller update; if `setGridSize` callers break, do the minimal caller edit here and finish in Task 8).
- [ ] **Step 5: Commit** — `Lay out the photo grid in justified rows`

---

### Task 7: Tile look (delegate) and model roles

**Files:**
- Create: `smart_gallery/gui/library/tiles.py` (`PhotoTileDelegate`)
- Modify: `smart_gallery/gui/library/view.py` (`LibraryGridModel`, remove `_with_video_badge`, `ICON_EDGE`, `GRID_SIZE`)
- Modify: `smart_gallery/colors.py` only if a new colour constant is needed (then `tests/unit/test_colors.py` must still pass)
- Test: `tests/gui/test_library_view.py`

**Interfaces:**
- Consumes: `ASPECT_ROLE` (Task 6), `LibraryRow.aspect` (Task 5).
- Produces:
  - Roles in `tiles.py`: `DURATION_ROLE = UserRole + 21` (`"0:42"` or `""`), `OFFLOADED_ROLE = UserRole + 22` (holder label or `None`).
  - `LibraryGridModel.data`: `ASPECT_ROLE` → `row.aspect`; `DecorationRole` → the full thumbnail pixmap as loaded (no scaling, no burned-in badge); `ToolTipRole` → `"{file name}\n{date taken} · {format_size}"` plus `"\nOn {label} only"` when offloaded (date line falls back to `"No date"`); `DisplayRole` still returns the file name (accessibility; not painted).
  - Pixmap cache bounded by bytes: `PIXMAP_CACHE_BYTES = 200 * 1024 * 1024` (LRU, `pixmap.width()*height()*4`), replacing `PIXMAP_CACHE_SIZE`.
  - `PhotoTileDelegate(QStyledItemDelegate)`: paints aspect-fill centre crop; keeps its own LRU of scaled pixmaps keyed `(pixmap.cacheKey(), w, h, dpr)` bounded at 64 MB; hover → brighten (white overlay ~8%) + empty white check circle (20 px, 6 px inset top-left); selected → image at 90% centred, 2 px `theme.ACCENT` ring, check circle filled with highlight colour + white tick; `DURATION_ROLE` → chip top-right (play glyph + text, `BADGE_BACK`/`BADGE_TEXT`); `OFFLOADED_ROLE` → faded placeholder (existing `_faded_placeholder`) + chip bottom-left `"On {label}"`. No text otherwise. Placeholder (no thumbnail yet) fills the tile with `TILE_BACK` and the centred glyph from `placeholder_for`.

- [ ] **Step 1: Write the failing tests**

```python
def test_tiles_draw_no_file_name(qtbot, ...): # render grid to QImage; delegate.paint never calls drawText for DisplayRole (patch QPainter.drawText via a recording delegate subclass or assert the delegate's _texts list is empty for a photo row)
def test_video_tile_has_duration_role(...): # DURATION_ROLE == "0:42" for 42 s video; "" for photo
def test_tooltip_has_name_date_size(...): # "a.jpg\n1 Mar 2026 · 3.1 MB" (format via the project's format helpers; assert pieces)
def test_offloaded_tooltip_and_role(...): # OFFLOADED_ROLE == holder label; tooltip ends "only"
def test_thumbnail_kept_full_size(...): # set_thumbnail with a 512x341 file -> DecorationRole pixmap is 512x341
def test_pixmap_cache_holds_a_full_screen_of_small_tiles(...): # insert 300 512x341 thumbnails -> all 300 still cached
```

Update the existing assertion at `tests/gui/test_library_view.py:169` only if its meaning changed (DisplayRole still returns the name, so it should not).

- [ ] **Step 2: Run; verify fail** — `uv run pytest tests/gui/test_library_view.py -v`.
- [ ] **Step 3: Implement**; set the delegate on the Library grid.
- [ ] **Step 4: Run** `uv run pytest tests/gui/test_library_view.py tests/unit/test_colors.py -v` → PASS.
- [ ] **Step 5: Commit** — `Paint photo tiles without names`

---

### Task 8: Photo size control, shared by Library and person page

**Files:**
- Create: `smart_gallery/gui/library/photo_size.py`
- Modify: `smart_gallery/gui/context.py` (`photo_size_changed = Signal(int)`)
- Modify: `smart_gallery/gui/library/view.py` (header slider, grid wiring)
- Modify: `smart_gallery/gui/people/person_view.py` (`PhotoGrid` uses delegate, row height, slider)
- Modify: `smart_gallery/gui/icons.py` only if glyph icons are added there
- Test: `tests/gui/test_library_view.py`, `tests/gui/test_person_view.py`, `tests/gui/test_button_roles.py` (keep green)

**Interfaces:**
- Consumes: `GroupedGrid.set_row_height/row_height_changed` (Task 6), `PhotoTileDelegate` (Task 7), constants (Task 5).
- Produces:
  - `photo_size.load_row_height() -> int` / `save_row_height(px: int) -> None` — `app_data_dir() / "library.json"`, key `"row_height"`, via `load_json`/`save_json`, preserving other keys (`filters_shown`); invalid/missing → `ROW_HEIGHT_DEFAULT`; clamped.
  - `PhotoSizeSlider(ctx, parent=None)` QWidget: small square glyph, `QSlider` (110–340, page step 20), large square glyph; tooltip `"Photo size (Ctrl + scroll wheel)"`; on change saves and emits `ctx.photo_size_changed`; follows `ctx.photo_size_changed` from elsewhere without re-emitting.
  - Library: slider first in `header_controls` (before "Sort by"); grid starts at `load_row_height()`; `grid.row_height_changed` → slider; `ctx.photo_size_changed` → grid. Thumbnails re-requested after a size change (connect to `_schedule_thumbs`).
  - Person page `PhotoGrid`: same delegate, same wiring; its slider sits at the right end of the row holding the Photos/Faces segment.

- [ ] **Step 1: Write the failing tests**

```python
def test_slider_sets_row_height_and_is_remembered(qtbot, tmp_app_data, ...): # slider -> 250; grid.row_height()==250; load_row_height()==250; filters_shown key kept
def test_ctrl_wheel_moves_slider(...): # grid Ctrl+wheel -> slider value follows
def test_person_page_follows_library_size(...): # Library slider -> 150; person PhotoGrid.grid.row_height()==150
def test_bad_saved_value_falls_back(...): # library.json row_height "huge" -> 190; 9999 -> 340
```

(Use the app-data fixture the existing GUI tests use for `library.json`; find it in `tests/gui/conftest.py`.)

- [ ] **Step 2: Run; verify fail** — `uv run pytest tests/gui/test_library_view.py tests/gui/test_person_view.py -k "size or row_height" -v`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/gui -v` → PASS (includes `test_button_roles.py`: still one primary per page).
- [ ] **Step 5: Commit** — `Photo size slider for Library and person pages`

---

### Task 9: Docs and full check

**Files:**
- Modify: `CLAUDE.md` (schema v9 bullet next to the mirror-schema one: `orientation` column, backfill in `sync`, mirror fill; grid section: justified layout, `justify.py`, `tiles.py`, `ASPECT_ROLE`)
- Modify: `docs/TECHNICAL_REFERENCE.md` (schema column list, thumbnail size/names)
- Modify: `docs/USER_GUIDE.md` (Library: no names, hover for details, photo size slider and Ctrl+wheel, first sync after upgrading reads rotation once)

- [ ] **Step 1: Edit the three docs.**
- [ ] **Step 2: Full suite** — `uv run pytest -m "not exiftool and not faces"` → all PASS; then `uv run pytest -m exiftool` if ExifTool is on PATH.
- [ ] **Step 3: Lint touched files** — `uv run ruff check <touched .py files>` and `uv run black --check <touched .py files>` → clean.
- [ ] **Step 4: Hand check** (record results in the final report, don't skip silently): `uv run smart-gallery gui` on a copy or the real E: — run Sync once (watch "Reading rotation"), confirm a portrait camera shot is a portrait tile, slider + Ctrl+wheel, pinned heading, a video chip, an offloaded tile, person page. Screenshots for the guide follow the existing screenshot conventions (separate follow-up if time is short).
- [ ] **Step 5: Commit** — `Docs: justified Library grid and stored rotation`
