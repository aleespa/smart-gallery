# Mirror Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a working drive (E) and a mirror drive (D) in step from buttons in the existing app: sync E to D, copy face data across, import to both, and offload/bring back parts of the library.

**Architecture:** Each catalog stores a pairing, a baseline of relpaths known identical on both drives, and a set of deliberately offloaded relpaths. A pure planner classifies relpaths from the two catalogs (after a stat-only refresh of both); a service applies the plan with atomic copies, a trash folder on the mirror, and a face-table transfer. The CLI exposes it (`pair`, `sync-mirror`, `free-up`, `bring-back`, an import flag); the desktop app adds a "Mirror" menu in the Library header that runs those commands as jobs.

**Tech Stack:** Python 3.12, sqlite3, argparse, loguru, pytest; PySide6 for the GUI part only.

**Spec:** `docs/superpowers/specs/2026-10-27-mirror-sync-design.md`

## Global Constraints

- Mtime is never compared across drives; compare relpath (NOCASE), then `size_bytes`, then `content_hash`.
- Face data is never stored on `media_items`; the transfer writes only `persons`, `faces`, `face_scan_state`, `face_rejections`, `face_carryover` on the mirror.
- "Delete" on the mirror means move to `<mirror root>/.smart_gallery/trash/<YYYYmmdd-HHMMSS>/<relpath>`; purge after `TRASH_DAYS = 30`. Never the Recycle Bin, never permanent in the same step.
- Extra explicit confirmation when mirror deletes exceed `DELETE_GUARD_FRACTION = 0.05` of the mirror's files.
- Refuse to sync when a root is missing, its `db_uuid` does not match the pairing, or a walk finds no files.
- Every destructive step is previewed first; the mirror never loses a file that exists on E.
- No new top-level area in the desktop app.
- Read-write opens migrate; read-only opens never do. `SCHEMA_VERSION` goes 6 -> 7.
- Follow `AGENTS.md`/`CLAUDE.md`: format and lint only touched files (`uv run ruff check <files>`, `uv run black <files>`); new CLI options need `help=` text and the right argument group (`test_every_field_has_a_hint`).

## Review Focus

- **Unmounted or empty E** (wrong letter, empty folder): sync refuses; nothing is trashed on D. Pinned in Task 5.
- **Case-only relpath differences between drives** (`IMG_1.JPG` vs `img_1.jpg`): same file, not a delete plus an add. Pinned in Task 3.
- **Sync interrupted mid-copy or mid-trash**: no truncated file on D, baseline records only finished work, re-running completes it. Pinned in Task 6.
- **Edit on E that keeps the same size** (mtime and hash change): D is overwritten, not skipped. Pinned in Task 3.
- **Same file trashed on D twice, and a second sync right after the first**: no name collision in trash; the second sync does nothing. Pinned in Task 6.

## File Structure

New package `smart_gallery/mirror/`:
- `types.py`: dataclasses shared by planner and services (`Pairing`, `MirrorRow`, `MirrorPlan`, reports).
- `hashing.py`: file hashing and filling `content_hash`.
- `plan.py`: pure planner, no I/O.
- `faces.py`: face-table transfer between two repositories.
- `peers.py`: find the other drive's catalog from a pairing.

Other files:
- `db/mirror_store.py` (new): `MirrorStoreMixin` for `GalleryRepository`.
- `services/mirror.py` (new): plan/apply sync, free up, bring back, pair.
- Modified: `config.py`, `db/schema.py`, `db/repository.py`, `progress.py`, `services/sync.py`, `services/import_media.py`, `services/__init__.py`, `cli.py`, `forms.py` (labels only), `gui/library/view.py` and `gui/library/model.py`, `README.md`, `CLAUDE.md`.
- New GUI: `gui/mirror/__init__.py`, `gui/mirror/menu.py`, `gui/mirror/dialogs.py`.
- Tests: `tests/unit/test_mirror_store.py`, `test_mirror_plan.py`, `test_mirror_hashing.py`, `tests/test_mirror_faces.py`, `tests/test_mirror_sync.py`, `tests/test_mirror_offload.py`, `tests/test_mirror_import.py`, `tests/test_mirror_cli.py`, `tests/gui/test_mirror_menu.py`.

Shared test fixture (add to `tests/conftest.py` in Task 1): `two_drives(tmp_path) -> tuple[Path, Path]` creating `tmp_path/E` and `tmp_path/D`, each initialised with `init_drive`.

---

### Task 1: Schema v7 and the mirror store

**Files:**
- Modify: `smart_gallery/config.py` (`SCHEMA_VERSION = 7`), `smart_gallery/db/schema.py`, `smart_gallery/db/repository.py` (add the mixin to `GalleryRepository`'s bases), `smart_gallery/services/sync.py` (`diff_drive`), `tests/conftest.py`
- Create: `smart_gallery/mirror/__init__.py`, `smart_gallery/mirror/types.py`, `smart_gallery/db/mirror_store.py`
- Test: `tests/unit/test_mirror_store.py`

**Interfaces:**
- Produces, in `mirror/types.py`:
  - `@dataclass class Pairing: peer_uuid: str; peer_db_path: str; role: Literal["working","mirror"]; offload_rules: list[FilterOptions] = field(default_factory=list)`
  - `@dataclass(frozen=True) class MirrorRow: relpath: str; size_bytes: int; content_hash: Optional[str]`
  - `def key(relpath: str) -> str` returns `relpath.casefold()`.
- Produces, on `GalleryRepository` via `MirrorStoreMixin`:
  - `get_pairing() -> Optional[Pairing]`, `set_pairing(p: Pairing) -> None`, `clear_pairing() -> None` (stored in `meta`: `mirror_peer_uuid`, `mirror_peer_db_path`, `mirror_role`, `mirror_offload_rules` as JSON; `FilterOptions` rules serialise only `filetypes`, `extensions`, `date_range` as ISO strings)
  - `mirror_rows() -> dict[str, MirrorRow]` (keyed by `key(relpath)`)
  - `baseline() -> dict[str, MirrorRow]`, `replace_baseline(rows: Iterable[MirrorRow]) -> None`, `update_baseline(upserts: Iterable[MirrorRow], removals: Iterable[str]) -> None`
  - `offloaded_relpaths() -> set[str]`, `add_offloaded(relpaths: Iterable[str]) -> None`, `remove_offloaded(relpaths: Iterable[str]) -> None`
  - `set_hashes(pairs: Iterable[tuple[str, str]]) -> None` (relpath, hash; plain UPDATE, not the upsert)

New tables (`CREATE ... IF NOT EXISTS`): `mirror_baseline(relpath TEXT PRIMARY KEY COLLATE NOCASE, size_bytes INTEGER NOT NULL, content_hash TEXT)` and `mirror_offloaded(relpath TEXT PRIMARY KEY COLLATE NOCASE)`. They hold no FK to `media_items` on purpose: an offloaded row must outlive nothing but its own flag, and the baseline must remember deleted files.

- [ ] **Step 1: Write failing tests** in `test_mirror_store.py`:
  - `test_migrate_v6_catalog_adds_mirror_tables`: create a catalog, set `meta.schema_version` to `6`, drop both tables, reopen read-write; assert both tables exist and `schema_version == 7`; reopen again (idempotent).
  - `test_read_only_open_does_not_migrate`: a v6 catalog opened `read_only=True` still reports 6.
  - `test_pairing_round_trip`: `set_pairing(Pairing("u1","E:/.smart_gallery/gallery.db","working",[FilterOptions(filetypes=["video"]), FilterOptions(date_range=(date(2020,1,1), date(2020,12,31)))]))`; `get_pairing()` equals it; `clear_pairing()` then `get_pairing() is None`.
  - `test_baseline_and_offloaded_are_case_insensitive`: add `IMG_1.JPG`, query `img_1.jpg` membership via `key()`; `update_baseline` upserts and removes.
  - `test_set_hashes_survives_until_row_changes`: `set_hashes` then `mirror_rows()` shows the hash; re-upsert the same relpath via `upsert_many` resets `content_hash` to `None`.
  - `test_diff_drive_ignores_offloaded_rows` (in `tests/test_sync.py` style): file on disk, `init_drive`, delete the file, `add_offloaded([relpath])`, `sync_drive(repo)` reports `deleted == 0` and the row remains; without the offloaded entry it reports `deleted == 1`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_mirror_store.py -v`. Expected: FAIL (imports missing).
- [ ] **Step 3: Implement** the tables and a `if current < 7:` block in `migrate()` (create both tables; no data changes), add them to `init_schema`, bump `SCHEMA_VERSION`, write `MirrorStoreMixin`, and in `diff_drive` subtract `repo.offloaded_relpaths()` from `to_delete` (match with `casefold`).
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_mirror_store.py tests/unit/test_models.py tests/test_sync.py tests/test_repository.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Add mirror pairing, baseline and offloaded storage (schema v7)"`.

### Task 2: Hashing

**Files:**
- Modify: `smart_gallery/progress.py` (add `HASHING = "Comparing files"` beside `FINDING`)
- Create: `smart_gallery/mirror/hashing.py`
- Test: `tests/unit/test_mirror_hashing.py`

**Interfaces:**
- Consumes: `GalleryRepository.mirror_rows`, `set_hashes` (Task 1).
- Produces:
  - `def hash_file(path: Path) -> str`: blake2b with `digest_size=20`, 1 MiB chunks, lowercase hex. (Designer's call: any stable digest is fine; this one is fast.)
  - `def ensure_hashes(repo: GalleryRepository, relpaths: Iterable[str], progress_callback: Optional[ProgressCallback] = None) -> dict[str, str]`: for each relpath missing a `content_hash` in the catalog, hash the file at `repo.drive_root / relpath`, store via `set_hashes`, and return `{key(relpath): hash}` for all requested relpaths. A file that cannot be read is skipped (logged) and absent from the result.

- [ ] **Step 1: Write failing tests**: `test_hash_file_is_content_based` (two files, same bytes, different names/mtimes -> equal; one byte different -> unequal); `test_ensure_hashes_fills_only_missing` (pre-set one hash to a sentinel; after the call the sentinel is untouched and the other row has a real hash; a spy shows `hash_file` called once); `test_ensure_hashes_skips_unreadable` (missing file -> not in result, no exception).
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_mirror_hashing.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `hash_file` and `ensure_hashes`; wrap iteration with `counted(..., HASHING, progress_callback)`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Add content hashing for mirror comparisons"` (plus new files via `git add`).

### Task 3: Pure planner

**Files:**
- Modify: `smart_gallery/mirror/types.py`
- Create: `smart_gallery/mirror/plan.py`
- Test: `tests/unit/test_mirror_plan.py`

**Interfaces:**
- Produces, in `types.py`:
  - `@dataclass class MirrorPlan` with list fields `copy_new: list[str]` (relpaths on E, absent on D), `overwrite: list[str]` (edited on E only), `trash: list[str]` (relpaths on D to trash), `moves: list[tuple[str, str]]` (old D relpath, new relpath), `only_on_mirror: list[str]`, `conflicts: list[str]` (edited on both or divergent), `adopt: list[str]` (identical, add to baseline), `skipped_offloaded: list[str]`; properties `delete_count -> int` (`len(trash)`) and `is_empty -> bool` (nothing to copy, overwrite, trash, move or adopt).
- Produces, in `plan.py`:
  - `def build_plan(working: dict[str, MirrorRow], mirror: dict[str, MirrorRow], baseline: dict[str, MirrorRow], offloaded: set[str], hash_working: Callable[[str], Optional[str]], hash_mirror: Callable[[str], Optional[str]]) -> MirrorPlan`. All dicts and `offloaded` are keyed by `key(relpath)`; `hash_*` take a relpath key and may compute lazily. Rows keep the side's real-case relpath; plan lists use E's relpath for E-side classes and D's relpath for `trash`, `only_on_mirror`, and the old half of `moves`.

Classification (the spec's table; `W` = in working, `M` = in mirror, `B` = in baseline, `O` = offloaded):
- `B and not W and M`: `O` -> `skipped_offloaded`; otherwise `trash`.
- `not B and not W and M` -> `only_on_mirror`.
- `not B and W and not M` -> `copy_new`.
- `W and M`: sizes equal and (hashes equal) -> `adopt` if not in `B` else nothing; `B` present: working changed iff its size or hash differs from the baseline row, mirror changed likewise; only working changed -> `overwrite`; both changed -> `conflicts`; mirror only changed -> `only_on_mirror`-style note is out of scope (leave untouched). `not B` and different -> `conflicts`.
- Hash only when sizes are equal and the answer is otherwise unknown, or when a move is possible.
- Move: a `trash` candidate and a `copy_new` candidate with the same size and same hash (baseline hash for the trash side) become `moves` and leave both lists.

- [ ] **Step 1: Write failing tests**, one per row of the table, using `MirrorRow` literals and lambda hash providers:
  - `test_new_on_working_is_copied`, `test_deleted_on_working_is_trashed`, `test_deleted_and_offloaded_is_skipped`, `test_only_on_mirror_is_listed_not_deleted` (absent from baseline, absent from working).
  - `test_same_size_edit_on_working_overwrites`: baseline hash `h1`, working hash `h2`, mirror hash `h1`, all sizes equal -> `overwrite`.
  - `test_edit_on_both_is_conflict`, `test_divergent_without_baseline_is_conflict`.
  - `test_case_only_difference_is_same_file`: working `IMG_1.JPG`, mirror `img_1.jpg`, identical -> not in `copy_new`, not in `trash`.
  - `test_rename_is_a_move`: baseline `a/x.jpg` hash `h`, working has `b/x.jpg` hash `h`, mirror has `a/x.jpg` -> `moves == [("a/x.jpg","b/x.jpg")]`, `trash == []`, `copy_new == []`.
  - `test_hash_provider_not_called_when_sizes_differ`.
  - `test_identical_and_in_baseline_is_a_noop`: `is_empty` is True.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_mirror_plan.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `build_plan` and the dataclass.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery/mirror tests && git commit -m "Add the mirror sync planner"`.

### Task 4: Face-data transfer

**Files:**
- Create: `smart_gallery/mirror/faces.py`
- Test: `tests/test_mirror_faces.py`

**Interfaces:**
- Consumes: two open read-write `GalleryRepository` objects with the same relpaths in `media_items`.
- Produces: `@dataclass class FaceSyncReport: inserted: int = 0; updated: int = 0; deleted: int = 0; persons: int = 0` and `def sync_face_data(working: GalleryRepository, mirror: GalleryRepository, progress_callback: Optional[ProgressCallback] = None) -> FaceSyncReport`.

Behaviour: face and person ids are preserved from the working catalog (the mirror never creates faces, so ids cannot clash). In one mirror transaction: upsert all `persons` by id and remove mirror persons not on the working side only after the faces step; `faces` are diffed by id using their non-embedding columns, inserting missing ones (with embedding), updating changed metadata, and deleting mirror faces absent from the working side; `media_id` is remapped through relpath (faces whose relpath is missing on the mirror are skipped); `face_scan_state` follows the same remap and is replaced for media whose faces changed; `face_rejections` and `face_carryover` are replaced wholesale (small). Embeddings are read only for faces to insert.

- [ ] **Step 1: Write failing tests** (build both catalogs with `make_item` + `repo.upsert_many`, create faces with `repo.add_scan_results` or direct `INSERT` using `FACE_COLUMNS`):
  - `test_first_transfer_copies_persons_and_faces`: after the call the mirror has the same persons (ids, names) and the same faces (ids, `person_id`, `state`, embedding bytes) as the working side; report counts match.
  - `test_discards_stale_mirror_faces`: the mirror starts with 216-like unrelated faces; they are gone afterwards.
  - `test_second_run_is_a_noop`: `FaceSyncReport` all zero; a spy on the embedding read shows no embeddings loaded.
  - `test_renaming_a_person_updates_the_mirror`: change a name on the working side; the mirror shows it; faces are `updated == 0`.
  - `test_face_for_missing_relpath_is_skipped`.
  - `test_media_id_is_remapped_by_relpath`: working and mirror assign different `media_items.id` to the same relpath; the mirror face points at the mirror's id.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_faces.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as described; use the repositories' `conn` directly inside one `BEGIN` on the mirror; `PRAGMA foreign_keys` stays ON.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery/mirror tests && git commit -m "Copy face data from the working catalog to the mirror"`.

### Task 5: Plan service (refresh, safeguards, free space)

**Files:**
- Create: `smart_gallery/mirror/peers.py`, `smart_gallery/services/mirror.py`
- Modify: `smart_gallery/services/__init__.py` (re-export)
- Test: `tests/test_mirror_sync.py`

**Interfaces:**
- Consumes: `sync_drive`, `ensure_hashes`, `build_plan`, Task 1 store methods.
- Produces, in `peers.py`: `def find_peer(pairing: Pairing) -> Optional[Path]`: the catalog at `pairing.peer_db_path` if its `db_uuid` matches, otherwise the same path under each existing drive letter whose catalog's `db_uuid` matches (reuse `existing_drives`/`locate` logic from `smart_gallery/gui/galleries/store.py`; if importing that module pulls in Qt, move those two functions to `smart_gallery/mirror/peers.py` and import them back into the store).
- Produces, in `services/mirror.py`:
  - `DELETE_GUARD_FRACTION = 0.05`, `TRASH_DAYS = 30`
  - `class MirrorError(ValueError)` (the CLI already turns `ValueError` into a logged error and exit 1)
  - `def pair_galleries(working: GalleryRepository, mirror: GalleryRepository) -> None`: writes a `Pairing` on each side (roles `working`/`mirror`, peer uuid from `get_meta("db_uuid")`, peer path from `repo.db_path`), and sets the baseline empty on both.
  - `@dataclass class MirrorPreview: plan: MirrorPlan; copy_bytes: int; mirror_free_bytes: int; large_delete: bool; working_files: int; mirror_files: int`
  - `def plan_mirror_sync(working: GalleryRepository, mirror: GalleryRepository, progress_callback=None) -> MirrorPreview`

`plan_mirror_sync` does, in order: (1) `_check_pair` raises `MirrorError` if either repo has no pairing, the pairing's peer uuid is not the other repo's `db_uuid`, a root directory does not exist, or `iter_files` finds nothing under the working root; (2) refresh both catalogs with `sync_drive` (read-write); (3) hash the candidates needed (equal-size rows with no baseline, same-size move candidates) with `ensure_hashes` on each side; (4) `build_plan`; (5) compute `copy_bytes` from E's sizes for `copy_new` + `overwrite`, `mirror_free_bytes` via `shutil.disk_usage(mirror.drive_root).free`, and `large_delete = len(plan.trash) > DELETE_GUARD_FRACTION * mirror_files`.

- [ ] **Step 1: Write failing tests** using `two_drives` and `drive_file` (monkeypatch `analyze_paths` in `smart_gallery.services.sync` as `tests/test_sync.py` does, or use non-media `.txt` files, which are recorded from `stat`):
  - `test_pair_then_plan_with_identical_drives_is_empty`.
  - `test_new_file_on_working_shows_in_copy_new`; `test_delete_on_working_outside_the_app_is_detected` (unlink the file on disk, no app action).
  - `test_plan_ignores_mtime_differences`: same bytes, different mtimes -> `adopt`/no-op, not `overwrite`.
  - `test_unmounted_working_root_refuses` (**Review Focus 1**): delete the working directory; `pytest.raises(MirrorError)`; assert mirror files and catalog unchanged.
  - `test_empty_working_root_refuses`: working root exists but all files removed -> `MirrorError`; nothing trashed.
  - `test_pairing_uuid_mismatch_refuses`.
  - `test_large_delete_flag`: delete 6 of 100 files on working -> `large_delete is True`; 4 of 100 -> False.
  - `test_plan_does_not_modify_mirror_files`.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_sync.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the functions above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Plan a mirror sync with safeguards"`.

### Task 6: Apply service

**Files:**
- Modify: `smart_gallery/services/mirror.py`, `smart_gallery/services/__init__.py`
- Test: `tests/test_mirror_sync.py` (extend)

**Interfaces:**
- Consumes: `MirrorPreview` (Task 5), `copy_atomic` from `smart_gallery/organize/organize.py`, `sync_face_data` (Task 4).
- Produces:
  - `@dataclass class MirrorChoices: only_on_mirror: Literal["leave","copy-back"] = "leave"; conflicts: Literal["skip","take-working","keep-mirror"] = "skip"; confirm_large_delete: bool = False`
  - `@dataclass class MirrorSyncReport: copied: int = 0; overwritten: int = 0; trashed: int = 0; moved: int = 0; copied_back: int = 0; skipped_conflicts: int = 0; faces: FaceSyncReport = field(default_factory=FaceSyncReport); failures: list[str] = field(default_factory=list)`
  - `def apply_mirror_sync(working, mirror, preview: MirrorPreview, choices: MirrorChoices = MirrorChoices(), progress_callback=None) -> MirrorSyncReport`
  - `def purge_trash(mirror: GalleryRepository, older_than_days: int = TRASH_DAYS, now: Optional[datetime] = None) -> int` (number of folders removed)

Behaviour: raise `MirrorError` when `preview.large_delete` and not `choices.confirm_large_delete`, or when `copy_bytes` exceeds `mirror_free_bytes`. Copies use `copy_atomic` then restore mtime (`os.utime(..., ns=(atime, mtime_ns))`). Overwrites and trashes first move the old D file into `<mirror root>/.smart_gallery/trash/<stamp>/<relpath>` (if the target exists, add `_1`, `_2`, ... suffix). Moves rename on D (creating parent folders). Mirror catalog rows for copied/overwritten files are built from E's `MediaItem` rows via `relocate` to the D path (no ExifTool run); trashed and moved rows are deleted/renamed on D. After each file succeeds, record it in the baseline with `update_baseline` in batches of 200 (so an interruption keeps finished work). `adopt` and unchanged relpaths are added to the baseline. A per-file failure is appended to `failures` and does not stop the run. Finally run `sync_face_data`, then `purge_trash`. `only_on_mirror == "copy-back"` copies D -> E and upserts E rows the same way; `conflicts == "take-working"` treats them as `overwrite`, `"keep-mirror"` copies D -> E (E's old file goes to a `trash` folder under E's `.smart_gallery`).

- [ ] **Step 1: Write failing tests** (extend `tests/test_mirror_sync.py`):
  - `test_apply_copies_new_overwrites_and_trashes`: afterwards D has the new file with E's mtime, the edited file has E's bytes, the deleted file is under `D/.smart_gallery/trash/*/<relpath>` and absent from D's catalog; E's catalog is unchanged.
  - `test_same_size_edit_overwrites` (**Review Focus 4**): rewrite a file with different bytes of equal length; after sync D matches E.
  - `test_second_sync_is_a_noop_and_trash_names_do_not_collide` (**Review Focus 5**): delete `a.txt` on E, sync, recreate `a.txt` on both, delete on E again, sync; two trash entries exist, nothing overwritten; an immediate extra sync reports all zeros.
  - `test_interrupted_copy_leaves_no_partial_file_and_resumes` (**Review Focus 3**): monkeypatch `copy_atomic` to raise `OSError` on the second file; D has no truncated file for it, the failure is reported, the baseline lacks it; un-patch, run again; it completes.
  - `test_large_delete_requires_confirmation`; `test_insufficient_space_refuses` (monkeypatch `shutil.disk_usage`).
  - `test_only_on_mirror_is_left_by_default_and_copied_back_on_choice`.
  - `test_conflict_default_skips`; `test_conflict_take_working_overwrites_with_trash`.
  - `test_rename_on_working_moves_on_mirror_without_recopying` (spy on `copy_atomic`: not called).
  - `test_purge_trash_removes_old_folders_only` (inject `now`).
  - `test_face_data_arrives_on_the_mirror`: faces on E appear on D after apply.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_sync.py -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Apply a mirror sync with trash, atomic copies and face transfer"`.

### Task 7: Free up space and bring back

**Files:**
- Modify: `smart_gallery/services/mirror.py`, `smart_gallery/services/__init__.py`
- Test: `tests/test_mirror_offload.py`

**Interfaces:**
- Produces:
  - `@dataclass class OffloadReport: files: int = 0; bytes: int = 0; skipped: list[str] = field(default_factory=list)` (skipped = relpath with a reason, e.g. "no verified copy on mirror", "faces not scanned")
  - `def free_up_space(working, mirror, selection: FilterOptions, folders: Sequence[str] = (), *, dry_run: bool = False, progress_callback=None) -> OffloadReport`
  - `def bring_back(working, mirror, selection: FilterOptions, folders: Sequence[str] = (), *, progress_callback=None) -> OffloadReport`
  - `def offload_rule_matches(rules: Sequence[FilterOptions], item: MediaItem) -> bool` (uses `organize.filters.matches`; used by import in Task 8)

Behaviour of `free_up_space`: choose relpaths with `working.query_relpaths(selection)` limited to those under any prefix in `folders` (POSIX, case-insensitive) and not already offloaded; hash both sides with `ensure_hashes`; a file is eligible only if the mirror has the same relpath, size and hash and, for images, it has a `face_scan_state` row on E at the current `FACE_DET_VERSION`. Run `sync_face_data` before deleting anything. Eligible files are removed from E with `os.remove`, added with `add_offloaded`, and `selection` is appended to the pairing's `offload_rules` unless `folders` is non-empty. `dry_run` reports without changes. Ineligible files go to `skipped`. `bring_back` copies from D with `copy_atomic` (checks E free space first, raising `MirrorError`), restores mtime, rebuilds E's row from D's via `relocate`, calls `remove_offloaded`, and removes an offload rule equal to `selection`.

- [ ] **Step 1: Write failing tests**:
  - `test_free_up_removes_files_from_working_and_marks_offloaded`: the file is gone from E's disk, present on D, in `offloaded_relpaths()`, row still in E's catalog; D untouched.
  - `test_free_up_skips_file_without_verified_copy`: file only on E -> in `skipped`, still on disk.
  - `test_free_up_skips_unscanned_images`.
  - `test_free_up_dry_run_changes_nothing`.
  - `test_sync_after_free_up_does_not_trash_on_mirror`: free up, then plan+apply sync; D still has the file.
  - `test_refresh_keeps_offloaded_rows` (E's `sync_drive` does not drop them).
  - `test_bring_back_restores_file_and_clears_flag`; `test_bring_back_checks_free_space`.
  - `test_free_up_by_folder_prefix_is_case_insensitive`.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_offload.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Free up space on the working drive and bring files back"`.

### Task 8: Import to both drives

**Files:**
- Modify: `smart_gallery/services/import_media.py`, `smart_gallery/services/__init__.py`
- Test: `tests/test_mirror_import.py`

**Interfaces:**
- Consumes: `offload_rule_matches` (Task 7), `organize_items`, `copy_atomic`.
- Produces: `import_media(repo, sources, *, output_dir=None, options=None, query=None, mirror: Optional[GalleryRepository] = None, progress_callback=None) -> ImportReport`, with `ImportReport` gaining `mirrored: int = 0` and `mirror_pending: int = 0`. When `mirror` is given: (1) analyze once; (2) items matching an offload rule are not copied to E: compute their E-relative destination with a dry-run `organize_items` (`Options(dry_run=True, ...)`, same structure options) and record them in `add_offloaded`, with an E catalog row built from the analysed item; (3) the rest are placed on E as today (E's relpath is authoritative); (4) every item is then copied from its source to `mirror.drive_root / relpath` with `copy_atomic` and inserted in the mirror catalog; (5) successfully mirrored relpaths join both baselines (`update_baseline`); failures, or a mirror that raises on open, increment `mirror_pending` and leave E's import intact. If the mirror already has the relpath with a different size, count it as pending (never overwrite).

- [ ] **Step 1: Write failing tests**:
  - `test_import_lands_on_both_drives_with_same_relpath`.
  - `test_import_without_mirror_is_unchanged` (existing `tests/test_import.py` still passes).
  - `test_mirror_failure_leaves_working_import_and_counts_pending`: monkeypatch `copy_atomic` to fail for the mirror target only.
  - `test_offloaded_on_arrival`: with `Pairing.offload_rules=[FilterOptions(filetypes=["video"])]`, a video is copied to D only, absent from E's disk, present in E's catalog and `offloaded_relpaths()`.
  - `test_existing_different_file_on_mirror_is_pending_not_overwritten`.
  - `test_imported_files_are_in_both_baselines`.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_import.py tests/test_import.py -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Import into the working and mirror drives in one pass"`.

### Task 9: CLI

**Files:**
- Modify: `smart_gallery/cli.py` (parser, handlers, `_HANDLERS`, `DETAIL_LOG_COMMANDS`), `smart_gallery/forms.py` (labels only if the form test requires)
- Test: `tests/test_mirror_cli.py`, `tests/unit/test_cli_parser.py`, `tests/unit/test_forms.py` (existing, must still pass)

**Interfaces:**
- Produces commands (all take `DRIVE_HELP`-style positional `drive`, the working or mirror side; the peer is found with `find_peer`; `--mirror PATH` overrides discovery):
  - `pair WORKING MIRROR`: runs `pair_galleries`.
  - `sync-mirror DRIVE [--dry-run] [--plan-out PATH] [--only-on-mirror {leave,copy-back}] [--conflicts {skip,take-working,keep-mirror}] [--confirm-large-delete]`: always runs `plan_mirror_sync`, writes the preview as JSON to `--plan-out` when given (keys: counts and relpath lists for each plan class, `copy_bytes`, `mirror_free_bytes`, `large_delete`), prints a summary, and applies only without `--dry-run`. A sync started on the mirror drive refuses with "run it from the working drive".
  - `free-up DRIVE` with the shared filter group (`_add_filter_args`), `--folder PATH` (repeatable), `--dry-run`.
  - `bring-back DRIVE` with the same flags minus `--dry-run`.
  - `import` gains `--no-mirror` (store_true, group "Mirror"): by default a paired and reachable mirror is used.
  - Add `sync-mirror`, `free-up`, `bring-back` to `DETAIL_LOG_COMMANDS`.

- [ ] **Step 1: Write failing tests**: `test_pair_then_sync_mirror_dry_run_writes_plan_json` (parse the file; counts match a staged scenario), `test_sync_mirror_applies_and_second_run_is_empty`, `test_sync_mirror_on_the_mirror_side_refuses`, `test_large_delete_exits_1_without_flag`, `test_import_uses_mirror_by_default_and_no_mirror_opts_out`, `test_free_up_dry_run_lists_without_deleting`; call `main([...])` with `SystemExit` handling as `tests/test_cli_progress.py` does.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the parser entries (each option in a named argument group, each with `help=`), handlers that open repositories with `GalleryRepository.open`, and register them in `_HANDLERS`.
- [ ] **Step 4: Run** `uv run pytest tests/test_mirror_cli.py tests/unit/test_cli_parser.py tests/unit/test_forms.py tests/test_cli_progress.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Add pair, sync-mirror, free-up, bring-back and import mirroring to the CLI"`.

### Task 10: Desktop app buttons

**Files:**
- Create: `smart_gallery/gui/mirror/__init__.py`, `smart_gallery/gui/mirror/menu.py`, `smart_gallery/gui/mirror/dialogs.py`
- Modify: `smart_gallery/gui/library/view.py` (add the menu button to `header_controls`), `smart_gallery/gui/library/model.py` and the library row query (an `offloaded: bool` on `LibraryRow`), `smart_gallery/gui/library/view.py` grid model (grey icon and text for offloaded rows)
- Test: `tests/gui/test_mirror_menu.py`, `tests/unit/test_library_model.py`

**Interfaces:**
- Consumes: CLI commands from Task 9 through `ctx.jobs.start(JobSpec(title, (argv,)))`; `GalleryRepository.get_pairing()`.
- Produces: `class MirrorButton(QToolButton)` in `menu.py`, built with `MirrorButton(ctx: AppContext, parent=None)`, hidden when the current gallery has no pairing and visible with a menu when it does (for the working side: "Sync to <label>…", "Free up space…", "Bring back…"; for an unpaired gallery a hidden button plus a "Pair with another gallery…" entry reachable from the Library header menu is not required: pairing is done with `pair` once and by `Pair…` in this menu when the open gallery has no pairing and `ctx.galleries` holds another gallery; show the button then too, with only that entry).
- Produces in `dialogs.py`: `SyncPreviewDialog(plan: dict, parent)` (counts per class, byte sizes, choice combos for "Only on the mirror" and "Conflicts", the extra checkbox when `large_delete`; `choices() -> list[str]` returns the CLI flags), `FreeUpDialog(parent)` (media types, date range, folder, shows the dry-run result before confirming).

Flow: *Sync* runs `sync-mirror DRIVE --dry-run --plan-out <app-data>/mirror/<stamp>.json` as a job, loads the JSON when the job finishes (connect `ctx.jobs.finished`), shows `SyncPreviewDialog`, and on accept runs `sync-mirror DRIVE` with the dialog's flags. The pending count is shown in the dialog, not on the button (computing it needs a disk walk). Offloaded rows are drawn greyed in the grid and carry the tooltip "On the mirror only".

- [ ] **Step 1: Write failing tests** (marker `gui`): `test_button_hidden_without_pairing`, `test_button_shows_menu_entries_with_pairing`, `test_sync_runs_dry_run_then_apply_with_dialog_flags` (fake `ctx.jobs` that records `JobSpec` argvs; stub the dialog to accept), `test_large_delete_checkbox_adds_confirm_flag`, `test_offloaded_rows_are_flagged` (library model).
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_mirror_menu.py tests/unit/test_library_model.py -v`. Expected: FAIL (or skipped if PySide6 is missing: install with `uv sync --extra gui` first).
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** the same command plus `uv run pytest tests/gui -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Add mirror buttons to the Library header"`.

### Task 11: Docs and full verification

**Files:**
- Modify: `README.md` (new "Keeping two drives in step" section with the four commands), `CLAUDE.md` (schema v7, the new package, and the lock-step note: baseline/offloaded tables have no FK on purpose)
- Test: whole suite

- [ ] **Step 1:** Update both docs.
- [ ] **Step 2:** Run `uv run pytest -m "not exiftool and not faces"`. Expected: all pass.
- [ ] **Step 3:** Run `uv run ruff check` and `uv run black --check` on every file touched by Tasks 1-10 only. Expected: clean.
- [ ] **Step 4:** Hand-check with the real drives, read-only first: `uv run smart-gallery pair E:/ D:/`, then `uv run smart-gallery sync-mirror E:/ --dry-run --plan-out plan.json`; confirm the 1 / 44 / 11 cases from the spec appear before applying anything.
- [ ] **Step 5: Commit** `git add README.md CLAUDE.md && git commit -m "Document mirror sync"`.

---

## Calls made in this plan (differences from the spec)

- The spec's "keep-rule" is stored as `offload_rules`, a list of `FilterOptions` (only file types, extensions and date range), because `FilterOptions` has no folder filter. Folders are chosen per *Free up* run and are not remembered for later imports.
- The explicit `mirror_offloaded` table decides what counts as offloaded; rules only steer new imports.
- Conflict and "only on D" choices are per class in the dialog (one choice for all), not per file.
- The sync button does not show a live pending count; the preview dialog does.
- Hash: blake2b-160. Trash retention: 30 days. Delete guard: 5%.
