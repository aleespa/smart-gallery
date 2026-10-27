# Mirror Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a working drive (E) and a mirror drive (D) in step from buttons in the existing app: sync E to D, copy face data across, import to both, and offload/bring back parts of the library.

**Architecture:** Each catalog stores its role and its peers; the working catalog may have several mirrors and stores, per mirror, a baseline (per relpath: size plus each drive's own mtime at the last sync), plus an offloaded set (with the mirrors holding each file) and offload rules; a mirror stores only its role and its one working peer. Services work on one working/mirror pair at a time. A pure planner classifies relpaths from the two catalogs after a stat-only refresh of both; a service applies the plan with atomic copies, a trash folder on the mirror, and a face-table transfer. The CLI exposes it (`pair`, `sync-mirror`, `free-up`, `bring-back`, an import switch); the desktop app adds a "Mirror" menu button in the Library header that runs those commands as jobs.

**Tech Stack:** Python 3.13+ (`requires-python = ">=3.13"` in `pyproject.toml`), sqlite3, argparse, loguru, pytest; PySide6 for the GUI task only.

**Spec:** `docs/superpowers/specs/2026-10-27-mirror-sync-design.md`

## Global Constraints

- Mtime is never used to say two drives differ. "Changed since the last sync" compares a drive's size and mtime with that same drive's baseline snapshot; cross-drive equality is size+mtime equal (trusted) or size+hash equal.
- Unchanged files are never hashed; the first run hashes only common files whose size or mtime differ between drives.
- Face data is never stored on `media_items`; the transfer writes only `persons`, `faces`, `face_scan_state`, `face_rejections`, `face_carryover` on the mirror.
- Face-writing commands refuse to run on a catalog whose pairing role is `mirror`.
- "Delete" on the mirror means move to `<mirror root>/.smart_gallery/trash/<YYYYmmdd-HHMMSS>/<relpath>`; purge after `TRASH_DAYS = 30`. Never the Recycle Bin, never permanent in the same step.
- Extra explicit confirmation when mirror deletes exceed `DELETE_GUARD_FRACTION = 0.05` of the mirror's files.
- Refuse to sync when a root is missing, its `db_uuid` does not match the pairing, or the working walk finds no files.
- Every destructive step is previewed first; the mirror never loses a file that exists on E.
- No new top-level area in the desktop app.
- The schema supports one working catalog with several mirrors: peers, baselines and offloaded holders are keyed by the mirror's `db_uuid`. With more than one mirror, commands that act on a pair require `--mirror`.
- Read-write opens migrate; read-only opens never do. `SCHEMA_VERSION` goes 6 -> 7. Code reading the new tables through a read-only connection must treat a missing table as empty.
- Follow `AGENTS.md`/`CLAUDE.md`: format and lint only touched files (`uv run ruff check <files>`, `uv run black <files>`); new CLI options need `help=` text and the right argument group (`test_every_field_has_a_hint`).

## Review Focus

- **Unmounted or empty E** (wrong letter, empty folder): sync refuses; nothing is trashed on D. Pinned in Task 5.
- **Case-only relpath differences between drives** (`IMG_1.JPG` vs `img_1.jpg`): same file, not a delete plus an add. Pinned in Task 3.
- **Sync interrupted mid-copy or mid-trash**: no truncated file on D, baseline records only finished work, re-running completes it. Pinned in Task 6.
- **Edit on E that keeps the same size**: mtime changes on E, so D is overwritten, not skipped. Pinned in Task 3.
- **Same file trashed on D twice, and a second sync right after the first**: no name collision in trash; the second sync does nothing. Pinned in Task 6.

## File Structure

New package `smart_gallery/mirror/`:
- `types.py`: dataclasses shared by planner and services (`Pairing`, `MirrorRow`, `BaselineRow`, `MirrorPlan`).
- `hashing.py`: file hashing and filling `content_hash`.
- `plan.py`: pure planner, no I/O.
- `faces.py`: face-table transfer between two repositories.
- `peers.py`: find the other drive's catalog from a pairing.

Other files:
- `db/mirror_store.py` (new): `MirrorStoreMixin` for `GalleryRepository`.
- `services/mirror.py` (new): pair, plan/apply sync, free up, bring back.
- Modified: `config.py`, `db/schema.py`, `db/repository.py`, `progress.py`, `services/sync.py`, `services/import_media.py`, `services/scan_faces.py`, `services/file_faces.py`, `services/label_faces.py`, `services/__init__.py`, `cli.py`, `gui/library/view.py`, `gui/library/model.py`, `README.md`, `CLAUDE.md`.
- New GUI: `gui/mirror/__init__.py`, `gui/mirror/menu.py`, `gui/mirror/dialogs.py`.
- Tests: `tests/unit/test_mirror_store.py`, `test_mirror_plan.py`, `test_mirror_hashing.py`, `tests/test_mirror_faces.py`, `tests/test_mirror_sync.py`, `tests/test_mirror_offload.py`, `tests/test_mirror_import.py`, `tests/test_mirror_cli.py`, `tests/gui/test_mirror_menu.py`.

Shared test fixture (add to `tests/conftest.py` in Task 1): `two_drives(tmp_path) -> tuple[Path, Path]` creating `tmp_path/E` and `tmp_path/D`, each initialised with `init_drive`.

---

### Task 1: Schema v7 and the mirror store

**Files:**
- Modify: `smart_gallery/config.py` (`SCHEMA_VERSION = 7`), `smart_gallery/db/schema.py`, `smart_gallery/db/repository.py` (add the mixin to `GalleryRepository`'s bases), `smart_gallery/services/sync.py` (`diff_drive`), `tests/conftest.py`
- Create: `smart_gallery/mirror/__init__.py`, `smart_gallery/mirror/types.py`, `smart_gallery/db/mirror_store.py`
- Test: `tests/unit/test_mirror_store.py`, `tests/test_sync.py` (extend)

**Interfaces:**
- Produces, in `mirror/types.py`:
  - `@dataclass(frozen=True) class Peer: uuid: str; db_path: str`
  - `@dataclass class Pairing: role: Literal["working","mirror"]; peers: list[Peer]; offload_rules: list[FilterOptions] = field(default_factory=list)` (a mirror has exactly one peer, its working catalog)
  - `@dataclass(frozen=True) class MirrorRow: relpath: str; size_bytes: int; mtime_ns: int; content_hash: Optional[str]`
  - `@dataclass(frozen=True) class BaselineRow: relpath: str; size_bytes: int; working_mtime_ns: int; mirror_mtime_ns: int`
  - `def key(relpath: str) -> str` returns `relpath.casefold()`.
- Produces, on `GalleryRepository` via `MirrorStoreMixin`:
  - `get_pairing() -> Optional[Pairing]` (None when no role is set), `add_peer(role: Literal["working","mirror"], peer: Peer) -> None` (sets the role; raises `ValueError` if the catalog already has the other role, or if a mirror already has a different peer), `remove_peer(uuid: str) -> None` (also deletes that peer's baseline and holder rows; clears the role when no peer is left), `set_offload_rules(rules: list[FilterOptions]) -> None`. Role and rules live in `meta` (`mirror_role`, `mirror_offload_rules` as JSON; rules serialise only `filetypes`, `extensions`, `date_range` with ISO dates); peers in `mirror_peers`.
  - `mirror_rows() -> dict[str, MirrorRow]` (all catalog rows, keyed by `key(relpath)`)
  - `baseline(peer_uuid: str) -> dict[str, BaselineRow]`, `update_baseline(peer_uuid: str, upserts: Iterable[BaselineRow], removals: Iterable[str]) -> None`
  - `offloaded_relpaths() -> set[str]` (offloaded with any holder), `offloaded_on(peer_uuid: str) -> set[str]`, `add_offloaded(relpaths: Iterable[str], holder_uuid: str) -> None`, `remove_offloaded(relpaths: Iterable[str]) -> None` (all holders)
  - `set_hashes(pairs: Iterable[tuple[str, str]]) -> None` (relpath, hash; a plain UPDATE, not the upsert)
  - `face_source_uuid() -> Optional[str]`, `set_face_source_uuid(uuid: str) -> None` (meta key `mirror_face_source`; used by Task 4)
  - Every reader returns empty/None when its table or meta key is missing (read-only open of a v5/v6 catalog).

New tables (`CREATE ... IF NOT EXISTS`):
- `mirror_peers(uuid TEXT PRIMARY KEY, db_path TEXT NOT NULL)`
- `mirror_baseline(peer_uuid TEXT NOT NULL REFERENCES mirror_peers(uuid) ON DELETE CASCADE, relpath TEXT NOT NULL COLLATE NOCASE, size_bytes INTEGER NOT NULL, working_mtime_ns INTEGER NOT NULL, mirror_mtime_ns INTEGER NOT NULL, PRIMARY KEY (peer_uuid, relpath))`
- `mirror_offloaded(relpath TEXT NOT NULL COLLATE NOCASE, holder_uuid TEXT NOT NULL REFERENCES mirror_peers(uuid) ON DELETE CASCADE, PRIMARY KEY (relpath, holder_uuid))`

No FK to `media_items`: the baseline must remember relpaths whose media row was deleted, which is how a deletion on E is recognised.

`diff_drive` changes: subtract offloaded relpaths (casefold) from `to_delete`; when a live file exists for an offloaded relpath, call `remove_offloaded` for it during `sync_drive` (not in `dry_run`).

- [ ] **Step 1: Write failing tests**:
  - `test_migrate_v6_catalog_adds_mirror_tables`: create a catalog, set `meta.schema_version` to `6`, drop the three tables, reopen read-write; they exist and `schema_version == 7`; reopen again (idempotent).
  - `test_readers_tolerate_unmigrated_read_only_catalog`: a v6 catalog without the tables opened `read_only=True` still reports 6, and `offloaded_relpaths() == set()`, `baseline("u1") == {}`, `get_pairing() is None`.
  - `test_pairing_round_trip`: `add_peer("working", Peer("d1", "D:/.smart_gallery/gallery.db"))`, `add_peer("working", Peer("f1", "F:/.smart_gallery/gallery.db"))`, `set_offload_rules([FilterOptions(filetypes=["video"]), FilterOptions(date_range=(date(2020,1,1), date(2020,12,31)))])`; `get_pairing()` returns role `working`, both peers, both rules.
  - `test_mirror_accepts_only_one_working_peer`: on a catalog, `add_peer("mirror", Peer("e1", ...))` then `add_peer("mirror", Peer("e2", ...))` raises `ValueError`; `add_peer("working", ...)` on it also raises.
  - `test_remove_peer_drops_its_baseline_and_holders`: two peers each with baseline rows and an offloaded holder row; `remove_peer("d1")` leaves only `f1`'s rows; removing the last peer makes `get_pairing() is None`.
  - `test_baselines_are_per_peer`: rows written for `d1` are absent from `baseline("f1")`.
  - `test_baseline_and_offloaded_are_case_insensitive`: add `IMG_1.JPG`, look up `img_1.jpg`; `update_baseline` upserts and removes; `offloaded_on("d1")` vs `offloaded_relpaths()` with a file held only on `f1`.
  - `test_set_hashes_survives_until_row_changes`: `set_hashes` then `mirror_rows()` shows the hash; re-upserting the same relpath with `upsert_many` resets it to `None`.
  - In `tests/test_sync.py`: `test_sync_keeps_offloaded_rows` (file deleted on disk, relpath offloaded, `sync_drive` reports `deleted == 0`, row remains; without the offloaded entry `deleted == 1`) and `test_sync_clears_offloaded_flag_when_file_returns`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_mirror_store.py tests/test_sync.py -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** the tables, an `if current < 7:` block in `migrate()` (create the three tables, no data changes), the same tables in `init_schema`, the version bump, `MirrorStoreMixin`, and the `diff_drive`/`sync_drive` changes.
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
  - `def hash_file(path: Path) -> str`: blake2b with `digest_size=20`, 1 MiB chunks, lowercase hex.
  - `def ensure_hashes(repo: GalleryRepository, relpaths: Iterable[str], progress_callback: Optional[ProgressCallback] = None) -> dict[str, str]`: for each relpath whose catalog row has no `content_hash`, hash `repo.drive_root / relpath`, store via `set_hashes`; return `{key(relpath): hash}` for every requested relpath that has or got a hash. Unreadable files are logged and left out.

- [ ] **Step 1: Write failing tests**: `test_hash_file_is_content_based` (same bytes, different names/mtimes -> equal; one byte different -> unequal); `test_ensure_hashes_fills_only_missing` (a pre-set sentinel hash is untouched; a spy shows `hash_file` called once); `test_ensure_hashes_skips_unreadable`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_mirror_hashing.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both functions; iterate with `counted(..., HASHING, progress_callback)`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Add content hashing for mirror comparisons"`.

### Task 3: Pure planner

**Files:**
- Modify: `smart_gallery/mirror/types.py`
- Create: `smart_gallery/mirror/plan.py`
- Test: `tests/unit/test_mirror_plan.py`

**Interfaces:**
- Produces, in `types.py`, `@dataclass class MirrorPlan` with list fields:
  - `copy_new: list[str]` (E relpaths to copy to D: new on E, or deleted on D)
  - `overwrite: list[str]` (edited on E only)
  - `trash: list[str]` (D relpaths deleted on E)
  - `moves: list[tuple[str, str]]` (old D relpath, new E relpath)
  - `only_on_mirror: list[str]` (D relpaths)
  - `changed_on_mirror: list[str]` (edited on D only)
  - `conflicts: list[str]` (edited on both, or different at first sight)
  - `adopt: list[BaselineRow]` (in step; write to baseline)
  - `offloaded_changed: list[str]`, `offloaded_missing: list[str]` (report only)
  - properties `delete_count -> int` (`len(trash)`) and `is_empty -> bool` (no copy, overwrite, trash, move, adopt, or question).
- Produces, in `plan.py`: `def build_plan(working: dict[str, MirrorRow], mirror: dict[str, MirrorRow], baseline: dict[str, BaselineRow], offloaded: set[str], held_here: set[str], hash_working: Callable[[str], Optional[str]], hash_mirror: Callable[[str], Optional[str]]) -> MirrorPlan`. `baseline` is this mirror's baseline; `offloaded` is every offloaded relpath (any holder); `held_here` is the subset whose holders include this mirror. All dicts and sets are keyed by `key(relpath)`. `working` includes offloaded rows; the planner splits them off itself. `hash_*` take a key and may compute lazily (a `None` result means "unknown": treat as not equal). Plan lists use E's real-case relpath for E-side classes and D's for `trash`, `only_on_mirror`, `changed_on_mirror`, the old half of `moves`, and the offloaded lists.

Classification is the spec's table under "Sync to mirror" step 3, applied with: `W` = in `working` and not offloaded, `O` = in `offloaded`, `M` = in `mirror`, `B` = in `baseline`; E changed = `(size, mtime)` differs from `(B.size_bytes, B.working_mtime_ns)`; D changed = differs from `(B.size_bytes, B.mirror_mtime_ns)`. Before putting a relpath in `overwrite`, `changed_on_mirror` or `conflicts`, compare sizes and then hashes; equal bytes go to `adopt` with fresh mtimes. "First sight" (`not B, W, M`): equal size and equal mtime -> `adopt` without hashing; equal size and equal hash -> `adopt`; otherwise `conflicts`. Moves: pair each `trash` candidate with a `copy_new` candidate of the same size whose `hash_working(new) == hash_mirror(old)`; matched pairs leave both lists. Hash only when sizes are equal and the answer is otherwise unknown.

- [ ] **Step 1: Write failing tests**, using `MirrorRow`/`BaselineRow` literals and counting hash providers:
  - `test_new_on_working_is_copied`, `test_deleted_on_working_is_trashed`, `test_deleted_on_mirror_is_copied_again`.
  - `test_unchanged_in_baseline_is_a_noop_without_hashing`: `is_empty`, providers never called.
  - `test_same_size_edit_on_working_overwrites` (**Review Focus 4**): same size, E mtime moved, hashes differ -> `overwrite`.
  - `test_touched_but_identical_is_adopted`: E mtime moved, hashes equal -> `adopt` with the new mtime.
  - `test_edit_on_mirror_only_is_asked`, `test_edit_on_both_is_conflict`.
  - `test_first_sight_equal_size_and_mtime_adopts_without_hashing`; `test_first_sight_mtime_differs_hash_equal_adopts`; `test_first_sight_different_is_conflict`.
  - `test_only_on_mirror_is_listed_not_deleted`.
  - `test_offloaded_rows_are_not_treated_as_present`: an offloaded relpath held here and absent from D -> `offloaded_missing`, not `copy_new`; present and unchanged on D -> nothing; changed on D -> `offloaded_changed`.
  - `test_offloaded_on_another_mirror_is_ignored`: offloaded, not in `held_here`, absent from D -> no list contains it.
  - `test_case_only_difference_is_same_file` (**Review Focus 2**).
  - `test_rename_is_a_move`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_mirror_plan.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `build_plan` and `MirrorPlan`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Add the mirror sync planner"`.

### Task 4: Face-data transfer and the mirror guard

**Files:**
- Create: `smart_gallery/mirror/faces.py`
- Modify: `smart_gallery/services/scan_faces.py`, `smart_gallery/services/file_faces.py`, `smart_gallery/services/label_faces.py`, `smart_gallery/services/cluster_faces.py`, `smart_gallery/services/identify_faces.py` (guard at entry), and the person-editing repository methods' CLI handlers in `smart_gallery/cli.py` (`name-person`, `merge-persons`, `split-person`, `hide-person`, `delete-person`)
- Test: `tests/test_mirror_faces.py`

**Interfaces:**
- Consumes: Task 1 `get_pairing`, `face_source_uuid`, `set_face_source_uuid`.
- Produces:
  - `@dataclass class FaceSyncReport: inserted: int = 0; updated: int = 0; deleted: int = 0; persons: int = 0; reset: bool = False`
  - `def sync_face_data(working: GalleryRepository, mirror: GalleryRepository, progress_callback: Optional[ProgressCallback] = None) -> FaceSyncReport`
  - `def ensure_not_mirror(repo: GalleryRepository) -> None` raising `ValueError("This gallery is a mirror; run face commands on the working drive.")` when the pairing role is `mirror`.

Behaviour of `sync_face_data`, in one mirror transaction: if `mirror.face_source_uuid()` is not the working catalog's `db_uuid`, delete all rows of the five face tables on the mirror, set the source uuid, and set `reset=True`. Then: upsert all `persons` by id; diff `faces` by id using the non-embedding columns (with `media_id` remapped through relpath), inserting missing faces with their embedding, updating changed metadata, deleting mirror faces absent from the working side; faces whose relpath is missing on the mirror are skipped; replace `face_scan_state` rows (remapped) for media whose faces changed; replace `face_rejections` and `face_carryover` (remapped) wholesale; finally delete mirror persons absent from the working side. Embeddings are read only for faces to insert.

- [ ] **Step 1: Write failing tests** (catalogs built with `make_item` + `upsert_many`; faces inserted with `add_scan_results` or direct `INSERT` over `FACE_COLUMNS`):
  - `test_first_transfer_replaces_mirror_faces_with_overlapping_ids`: the mirror starts with its own faces using ids 1..3 on other photos; afterwards every mirror face equals the working face with the same id (media relpath, `person_id`, `state`, embedding bytes) and none of the old rows remain; `reset is True`.
  - `test_second_run_is_a_noop`: all counts zero, `reset is False`, a spy shows no embedding reads.
  - `test_renaming_a_person_updates_the_mirror`: name changes, `updated == 0`.
  - `test_face_for_missing_relpath_is_skipped`.
  - `test_media_id_is_remapped_by_relpath`.
  - `test_face_commands_refuse_on_mirror`: with a mirror pairing, `scan_faces`, `file_faces` and `label_faces` raise `ValueError`; without a pairing they run.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_faces.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as described; work on `mirror.conn` inside one transaction with `PRAGMA foreign_keys` ON; call `ensure_not_mirror` first thing in each guarded service and handler.
- [ ] **Step 4: Run** `uv run pytest tests/test_mirror_faces.py tests/test_file_faces.py tests/test_label_faces.py tests/test_person_ops.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Copy face data to the mirror and keep face commands off it"`.

### Task 5: Pair and plan services (refresh, safeguards, free space)

**Files:**
- Create: `smart_gallery/mirror/peers.py`, `smart_gallery/services/mirror.py`
- Modify: `smart_gallery/services/__init__.py` (re-export)
- Test: `tests/test_mirror_sync.py`

**Interfaces:**
- Consumes: `sync_drive`, `ensure_hashes`, `build_plan`, Task 1 store methods.
- Produces, in `peers.py`:
  - `def find_peer(peer: Peer) -> Optional[Path]`: the catalog at `peer.db_path` if its `db_uuid` matches, else the same path under each existing drive letter whose catalog's `db_uuid` matches. Reuse `existing_drives`/`locate` from `smart_gallery/gui/galleries/store.py`; if importing that module loads PySide6, move both functions into `peers.py` and import them back into the store.
  - `def choose_mirror(pairing: Pairing, override: Optional[Path] = None) -> Peer`: with `override`, the peer whose uuid matches the catalog at that path (`MirrorError` if none); otherwise the only peer, or `MirrorError("This gallery has several mirrors; choose one with --mirror.")`.
- Produces, in `services/mirror.py`:
  - `DELETE_GUARD_FRACTION = 0.05`, `TRASH_DAYS = 30`
  - `class MirrorError(ValueError)` (the CLI already maps `ValueError` to a logged error and exit 1)
  - `def pair_galleries(working: GalleryRepository, mirror: GalleryRepository) -> None`: `working.add_peer("working", Peer(mirror uuid, str(mirror.db_path)))` and `mirror.add_peer("mirror", Peer(working uuid, str(working.db_path)))`, uuids from `get_meta("db_uuid")`. Refuses if the two uuids are equal, the working side is a mirror, or the mirror side is a working catalog or already has a different working peer. Adding a second mirror to a working catalog is allowed. (`MirrorError` must be importable by `peers.py`; define it in `mirror/types.py` and re-export it from `services/mirror.py`.)
  - `@dataclass class MirrorPreview: plan: MirrorPlan; copy_bytes: int; mirror_free_bytes: int; mirror_trash_bytes: int; large_delete: bool; working_files: int; mirror_files: int`
  - `def plan_mirror_sync(working: GalleryRepository, mirror: GalleryRepository, progress_callback=None) -> MirrorPreview`

`plan_mirror_sync` works on one pair; it reads `working.baseline(mirror_uuid)`, `working.offloaded_relpaths()` and `working.offloaded_on(mirror_uuid)`. In order: (1) raise `MirrorError` if either repo has no pairing, roles are not working/mirror, the mirror's uuid is not among the working side's peers or the working uuid is not the mirror's peer, a root does not exist, or `iter_files` yields nothing under the working root (stop at the first file); (2) refresh both catalogs with `sync_drive`; (3) call `build_plan` with hash providers that call `ensure_hashes` on the right repo for one relpath at a time, cached, so only the relpaths the planner asks about are hashed; (4) `copy_bytes` = total size of `copy_new` + `overwrite` (moves copy nothing); `mirror_free_bytes` from `shutil.disk_usage(mirror.drive_root).free`; `mirror_trash_bytes` = size of the trash folder; `large_delete = len(plan.trash) > DELETE_GUARD_FRACTION * mirror_files`.

- [ ] **Step 1: Write failing tests** with `two_drives` and `.txt` files (non-media files are recorded from `stat`, no ExifTool):
  - `test_pair_then_plan_with_identical_drives_is_empty` (files copied with `shutil.copy2`, so mtimes match).
  - `test_pair_refuses_same_catalog_twice`; `test_pair_second_mirror_is_allowed_and_baselines_stay_separate` (a third drive paired as a second mirror; planning against one leaves the other's baseline untouched).
  - `test_choose_mirror_requires_override_with_two_mirrors`.
  - `test_first_plan_hashes_only_mismatched_files`: 10 common files, 2 with different mtimes; a spy on `hash_file` sees only those 2 relpaths (on each side).
  - `test_new_file_on_working_shows_in_copy_new`; `test_delete_on_working_outside_the_app_is_detected` (unlink on disk, after a first apply has filled the baseline: use `update_baseline` directly to seed it).
  - `test_unmounted_working_root_refuses` (**Review Focus 1**): remove the working directory; `pytest.raises(MirrorError)`; mirror files and catalog unchanged.
  - `test_empty_working_root_refuses`.
  - `test_pairing_uuid_mismatch_refuses`.
  - `test_large_delete_flag`: 6 of 100 files deleted on E -> True; 4 of 100 -> False.
  - `test_plan_does_not_modify_mirror_files`.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_sync.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the functions above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Pair galleries and plan a mirror sync with safeguards"`.

### Task 6: Apply service

**Files:**
- Modify: `smart_gallery/services/mirror.py`, `smart_gallery/services/__init__.py`
- Test: `tests/test_mirror_sync.py` (extend)

**Interfaces:**
- Consumes: `MirrorPreview` (Task 5), `copy_atomic` from `smart_gallery/organize/organize.py`, `sync_face_data` (Task 4), `MediaItem.relocate`.
- Produces:
  - `@dataclass class MirrorChoices: only_on_mirror: Literal["later","keep-on-mirror","copy-back"] = "later"; changed_on_mirror: Literal["skip","take-working","keep-mirror"] = "skip"; conflicts: Literal["skip","take-working","keep-mirror"] = "skip"; confirm_large_delete: bool = False`
  - `@dataclass class MirrorSyncReport: copied: int = 0; overwritten: int = 0; trashed: int = 0; moved: int = 0; copied_back: int = 0; kept_on_mirror: int = 0; skipped: int = 0; faces: FaceSyncReport = field(default_factory=FaceSyncReport); failures: list[str] = field(default_factory=list)`
  - `def apply_mirror_sync(working, mirror, preview: MirrorPreview, choices: MirrorChoices = MirrorChoices(), progress_callback=None) -> MirrorSyncReport`
  - `def purge_trash(mirror: GalleryRepository, older_than_days: int = TRASH_DAYS, now: Optional[datetime] = None) -> int` (folders removed)

Behaviour:
- Raise `MirrorError` when `preview.large_delete` and not `choices.confirm_large_delete`, or when `copy_bytes > mirror_free_bytes`.
- Copy with `copy_atomic`, then restore the source mtime with `os.utime(dest, ns=(atime_ns, mtime_ns))`.
- Before overwriting or trashing a D file, move it to `<mirror root>/.smart_gallery/trash/<stamp>/<relpath>`; if that path exists, add `_1`, `_2`, ... before the suffix.
- Moves rename on D (creating parent folders).
- D catalog rows for files written to D come from E's `MediaItem` via `relocate` to the D path (no ExifTool); trashed rows are deleted, moved rows are re-anchored. Rows written to E (copy-back, keep-mirror) come from D's rows the same way.
- All baseline writes go to `working.update_baseline(mirror_uuid, ...)`.
- `only_on_mirror`: `"keep-on-mirror"` inserts E catalog rows built from D's rows, calls `add_offloaded(relpaths, mirror_uuid)`, and adds baseline rows; `"copy-back"` copies D -> E and adds baseline rows; `"later"` does nothing.
- `changed_on_mirror` and `conflicts`: `"take-working"` behaves as `overwrite`; `"keep-mirror"` copies D -> E, moving E's old file to `<working root>/.smart_gallery/trash/<stamp>/<relpath>`; `"skip"` does nothing.
- After each finished file, add or remove its baseline row (`BaselineRow` with the size and both drives' current mtimes), flushed in batches of 200, so an interruption keeps finished work. `plan.adopt` rows are written to the baseline.
- A per-file failure is appended to `failures` and the run continues.
- Finally run `sync_face_data`, then `purge_trash`.

- [ ] **Step 1: Write failing tests** (extend `tests/test_mirror_sync.py`):
  - `test_apply_copies_new_overwrites_and_trashes`: D gets the new file with E's mtime, the edited file has E's bytes, the deleted file is under `D/.smart_gallery/trash/*/<relpath>` and out of D's catalog; E is unchanged.
  - `test_second_sync_is_a_noop_and_trash_names_do_not_collide` (**Review Focus 5**): delete `a.txt` on E, sync; recreate `a.txt` on both, sync; delete on E again, sync; two trash entries exist; an immediate extra plan `is_empty`.
  - `test_interrupted_copy_leaves_no_partial_file_and_resumes` (**Review Focus 3**): `copy_atomic` raises `OSError` on the second file; no truncated file on D, failure reported, baseline lacks it; un-patch, sync again, it completes.
  - `test_large_delete_requires_confirmation`; `test_insufficient_space_refuses` (monkeypatch `shutil.disk_usage`).
  - `test_keep_on_mirror_is_not_asked_again`: D-only file, choice `keep-on-mirror`; E's catalog has an offloaded row for it; the next plan's `only_on_mirror` is empty.
  - `test_later_keeps_asking`; `test_copy_back_copies_to_working`.
  - `test_changed_on_mirror_take_working_trashes_mirror_copy`.
  - `test_rename_on_working_moves_on_mirror_without_recopying` (spy on `copy_atomic`: not called).
  - `test_purge_trash_removes_old_folders_only` (inject `now`).
  - `test_face_data_arrives_on_the_mirror`.
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
  - `@dataclass class OffloadReport: files: int = 0; bytes: int = 0; skipped: list[str] = field(default_factory=list)` (each entry `"<relpath>: <reason>"`, reasons `"no verified copy on mirror"`, `"faces not scanned"`)
  - `def free_up_space(working, mirror, selection: FilterOptions, folders: Sequence[str] = (), *, dry_run: bool = False, progress_callback=None) -> OffloadReport`
  - `def bring_back(working, mirror, selection: FilterOptions, folders: Sequence[str] = (), *, progress_callback=None) -> OffloadReport`
  - `def offload_rule_matches(rules: Sequence[FilterOptions], item: MediaItem) -> bool` (any rule `matches`; used by Task 8)

`free_up_space`: candidates are `working.query_relpaths(selection)` restricted to relpaths under any prefix in `folders` (POSIX, casefold) and not already offloaded. Eligible = mirror has the relpath with the same size and hash (`ensure_hashes` on both sides), and for images a `face_scan_state` row on E at the current `FACE_DET_VERSION`. Call `sync_face_data` before deleting. Eligible files are removed from E with `os.remove` and marked with `add_offloaded(relpaths, mirror_uuid)`; if `folders` is empty, append `selection` to the offload rules (`set_offload_rules`). A file already offloaded is not a candidate (adding a second holder for it is out of scope). `dry_run` reports only. `bring_back`: candidates are relpaths in `offloaded_on(mirror_uuid)` matching the same selection; check E's free space (`MirrorError` if short); copy from the given mirror with `copy_atomic`, restore mtime, rebuild E's row from the mirror's via `relocate`, `remove_offloaded` (all holders), update the working mtime in that mirror's baseline row, and remove an offload rule equal to `selection`.

- [ ] **Step 1: Write failing tests**:
  - `test_free_up_removes_files_from_working_and_marks_offloaded` (gone from E's disk, on D, offloaded, row still in E's catalog, D untouched).
  - `test_free_up_skips_file_without_verified_copy`; `test_free_up_skips_unscanned_images`; `test_free_up_dry_run_changes_nothing`.
  - `test_sync_after_free_up_does_not_trash_on_mirror`.
  - `test_bring_back_restores_file_and_clears_flag`; `test_bring_back_checks_free_space`.
  - `test_free_up_by_folder_prefix_is_case_insensitive_and_adds_no_rule`.
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
- Produces: `import_media(repo, sources, *, output_dir=None, options=None, query=None, mirrors: Sequence[GalleryRepository] = (), progress_callback=None) -> ImportReport`; `ImportReport` gains `mirrored: dict[str, int]` and `mirror_pending: dict[str, int]` (keyed by mirror `db_uuid`, `field(default_factory=dict)`).

With `mirrors` given: (1) analyze once; (2) split items by the working pairing's `offload_rules`; (3) place the kept items on E as today (E's relpath is authoritative), then for each mirror copy each placed file **from E's copy** to `mirror.drive_root / relpath` with `copy_atomic` and restore mtime; (4) for offloaded-on-arrival items, compute their E relpath with a dry-run `organize_items` (`Options(dry_run=True, ...)`, same layout options), copy them from the source straight to each mirror, insert E catalog rows built from the analysed items, and `add_offloaded(relpaths, mirror_uuid)` for each mirror that received them (an item no mirror received is left on E instead: copy it there and count it pending); (5) insert mirror catalog rows via `relocate`; (6) add a baseline row in that mirror's baseline for each file now on both sides (or on the mirror with an offloaded E row). A relpath already present on D with a different size is not overwritten and counts as `mirror_pending`, as does any copy failure; E's import stays intact.

- [ ] **Step 1: Write failing tests**:
  - `test_import_lands_on_both_drives_with_same_relpath`.
  - `test_import_without_mirror_is_unchanged` (and `tests/test_import.py` still passes).
  - `test_mirror_copy_is_read_from_working_copy` (spy on `copy_atomic` source paths: under E's root for kept items).
  - `test_mirror_failure_leaves_working_import_and_counts_pending`.
  - `test_offloaded_on_arrival` (rule `FilterOptions(filetypes=["video"])`: video on D only, offloaded row on E).
  - `test_existing_different_file_on_mirror_is_pending_not_overwritten`.
  - `test_imported_files_join_the_baseline`.
  - `test_import_to_two_mirrors_counts_pending_per_mirror` (one mirror's copy fails: that mirror's pending count is 1, the other mirror has the file).
  - `test_offloaded_item_no_mirror_received_stays_on_working`.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_import.py tests/test_import.py -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Import into the working and mirror drives in one pass"`.

### Task 9: CLI

**Files:**
- Modify: `smart_gallery/cli.py` (parser, handlers, `_HANDLERS`, `DETAIL_LOG_COMMANDS`)
- Test: `tests/test_mirror_cli.py`; existing `tests/unit/test_cli_parser.py`, `tests/unit/test_forms.py`, `tests/test_cli_progress.py` must still pass

**Interfaces:**
- Produces commands (`drive` positional uses `DRIVE_HELP`; `sync-mirror`, `free-up` and `bring-back` take `--mirror PATH` and pick the mirror with `choose_mirror`, then open it at `find_peer(peer)`; a mirror that cannot be found is a `MirrorError`):
  - `pair WORKING MIRROR`: runs `pair_galleries`.
  - `sync-mirror DRIVE [--dry-run] [--plan-out PATH] [--only-on-mirror {later,keep-on-mirror,copy-back}] [--changed-on-mirror {skip,take-working,keep-mirror}] [--conflicts {skip,take-working,keep-mirror}] [--confirm-large-delete]`: always runs `plan_mirror_sync`; writes the preview as JSON to `--plan-out` (keys: `counts` per plan class, a relpath list per class, `copy_bytes`, `mirror_free_bytes`, `mirror_trash_bytes`, `large_delete`); logs a summary; applies only without `--dry-run`. Run on the mirror drive it refuses with "Run sync-mirror on the working drive."
  - `free-up DRIVE` with the shared filter group (`_add_filter_args`), `--folder PATH` (repeatable), `--dry-run`.
  - `bring-back DRIVE` with the same flags minus `--dry-run`.
  - `import` gains `--no-mirror` (store_true, argument group "Mirror"); by default every paired, reachable mirror is used, and unreachable ones are logged as pending.
  - Add `sync-mirror`, `free-up`, `bring-back` to `DETAIL_LOG_COMMANDS`.

- [ ] **Step 1: Write failing tests**: `test_pair_then_sync_mirror_dry_run_writes_plan_json`, `test_sync_mirror_with_two_mirrors_needs_mirror_flag`, `test_sync_mirror_applies_and_second_run_is_empty`, `test_sync_mirror_on_the_mirror_side_refuses`, `test_large_delete_exits_1_without_flag`, `test_import_uses_mirror_by_default_and_no_mirror_opts_out`, `test_free_up_dry_run_lists_without_deleting`; call `main([...])` and handle `SystemExit` as `tests/test_cli_progress.py` does.
- [ ] **Step 2: Run** `uv run pytest tests/test_mirror_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the parser entries (each option in a named group, each with `help=`), handlers opening repositories with `GalleryRepository.open`, and `_HANDLERS` entries.
- [ ] **Step 4: Run** `uv run pytest tests/test_mirror_cli.py tests/unit/test_cli_parser.py tests/unit/test_forms.py tests/test_cli_progress.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Add pair, sync-mirror, free-up, bring-back and import mirroring to the CLI"`.

### Task 10: Desktop app buttons

**Files:**
- Create: `smart_gallery/gui/mirror/__init__.py`, `smart_gallery/gui/mirror/menu.py`, `smart_gallery/gui/mirror/dialogs.py`
- Modify: `smart_gallery/gui/library/view.py` (add the button to `header_controls`; grey offloaded items), `smart_gallery/gui/library/model.py` and its row query (`LibraryRow.offloaded: bool`, read through `offloaded_relpaths()`, which tolerates a catalog without the table)
- Test: `tests/gui/test_mirror_menu.py`, `tests/unit/test_library_model.py`

**Interfaces:**
- Consumes: Task 9 commands via `ctx.jobs.start(JobSpec(title, (argv,)))`; `JobRunner.finished` (`Signal(object)`, a `JobRecord`); `GalleryRepository.get_pairing()` through `ctx.open_repo()`; `ctx.galleries` for the list of other galleries.
- Produces: `class MirrorButton(QToolButton)` (`MirrorButton(ctx: AppContext, parent=None)`), refreshed on `ctx.gallery_changed` and `ctx.catalog_updated`. Menu entries by state:
  - working side: one "Sync to <peer label>…" entry per mirror, then "Free up space…" and "Bring back…" (with several mirrors, their dialogs include a mirror picker and pass `--mirror`), then "Pair with another gallery…" to add a mirror
  - mirror side: one disabled entry, "Open <peer label> to sync"
  - unpaired, with at least one other gallery in `ctx.galleries`: "Pair with another gallery…" (a picker, then a `pair` job, with the current gallery as working)
  - unpaired and no other gallery: button hidden
- Produces, in `dialogs.py`: `SyncPreviewDialog(plan: dict, parent=None)` (counts and sizes per class, combos for "Only on <D>", "Changed on <D>", "Conflicts", a confirm checkbox shown when `large_delete`; `flags() -> list[str]` returns the CLI flags), `FreeUpDialog(parent=None)` (file types, date range, folder; shows the dry-run result before confirming; `argv() -> list[str]`), `PairDialog(galleries, parent=None)`.

Flow for *Sync*: run `sync-mirror DRIVE --mirror <that mirror's db path> --dry-run --plan-out <app_data_dir()>/mirror/<stamp>.json` as a job; on `finished` for that job, load the JSON and show `SyncPreviewDialog`; on accept run `sync-mirror DRIVE` plus `flags()`. The dialog's "Only on <D>" combo defaults to "Keep on <D> only". Offloaded items are drawn greyed in the grid with the tooltip "On <peer label> only".

- [ ] **Step 1: Write failing tests** (marker `gui`): `test_button_hidden_when_unpaired_and_alone`, `test_unpaired_offers_pairing`, `test_working_side_menu_entries`, `test_one_sync_entry_per_mirror`, `test_mirror_side_shows_disabled_entry`, `test_sync_runs_dry_run_then_apply_with_dialog_flags` (fake `ctx.jobs` recording argvs; dialog stubbed to accept), `test_large_delete_checkbox_adds_confirm_flag`, and in `test_library_model.py` `test_offloaded_rows_are_flagged` plus `test_unmigrated_catalog_loads_without_offloaded_table`.
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_mirror_menu.py tests/unit/test_library_model.py -v` (after `uv sync --extra gui` if PySide6 is missing). Expected: FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** the same command plus `uv run pytest tests/gui -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add smart_gallery tests && git commit -m "Add the Mirror button to the Library header"`.

### Task 11: Docs and full verification

**Files:**
- Modify: `README.md` (new "Keeping two drives in step" section covering the four commands and the import switch), `CLAUDE.md` (schema v7, the `mirror` package, the baseline/offloaded tables and why they have no FK, the face guard on mirror catalogs)
- Test: whole suite

- [ ] **Step 1:** Update both docs.
- [ ] **Step 2:** Run `uv run pytest -m "not exiftool and not faces"`. Expected: all pass.
- [ ] **Step 3:** Run `uv run ruff check` and `uv run black --check` on every file touched by Tasks 1-10 only. Expected: clean.
- [ ] **Step 4: Commit** `git add README.md CLAUDE.md && git commit -m "Document mirror sync"`.
- [ ] **Step 5:** Hand-check on the real drives is for the user, not the executor: `pair E:/ D:/` then `sync-mirror E:/ --dry-run --plan-out plan.json`, expecting the 1 / 44 / 11 cases from the spec in the preview before anything is applied.

---

## Calls made in this plan (differences from the spec, or builder choices)

- Offload rules are a list of `FilterOptions` limited to file types, extensions and date range; `FilterOptions` has no folder filter, so folders are chosen per *Free up* run and not remembered for imports.
- Preview choices apply to a whole class (one choice for all "only on D" files), not per file.
- The sync button shows no live pending count; the preview dialog does.
- Hash: blake2b-160. Trash retention: 30 days. Delete guard: 5%.
- Person-editing commands are guarded on a mirror catalog as well as scanning, so D's face data stays a pure copy.
- Several mirrors (user request, future-proofing): schema and commands support them; a file offloaded to one mirror cannot gain a second holder through *Free up* yet.
