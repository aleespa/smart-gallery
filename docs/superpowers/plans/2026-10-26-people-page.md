# People Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A People page where the computer groups faces, the user names and corrects them, the matcher learns from those corrections, and the user finds photos by one or several people.

**Architecture:** Face decisions live on the `faces` table as a `state` plus two new tables (`face_rejections`, `face_carryover`). A numpy-only filing module (`analysis/filing.py`) decides auto / suggested / group / hidden for every face that the user has not decided; a `file-faces` service applies it. Every GUI write is a CLI job (`label-faces`, `file-faces`, `find-faces`, `hide-person`, existing person commands). The page itself is Qt widgets over a Qt-free query module (`gui/people/model.py`) and reuses the Library grid and thumbnail loader.

**Tech Stack:** Python 3, SQLite, numpy, scikit-learn/HDBSCAN (faces extra, grouping only), InsightFace `buffalo_l` (unchanged), PySide6, Pillow, pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-25-people-page-design.md` (mockups in `2026-10-25-people-page-mockups/`). Read it before each task; this plan argues from it.

## Global Constraints

- Keep the model: InsightFace `buffalo_l`; no training, no new model downloads, no network calls.
- Core install must import without the `faces` extra: `insightface`, `onnxruntime`, `cv2`, `rawpy`, `sklearn`, `hdbscan` only inside functions. numpy may be imported at module level.
- Nothing face-related goes on `media_items` (CLAUDE.md, "Why face data is never stored on `media_items`").
- `FACE_COLUMNS` / `PERSON_COLUMNS` must match `CREATE_FACES` / `CREATE_PERSONS` in order (`tests/unit/test_faces.py`).
- `migrate()` stays idempotent; read-only opens never migrate.
- Every write from the GUI process goes through a CLI job (`gui/jobs.py`). The GUI only reads through `ctx.open_repo()`.
- New CLI options go in the right argument group with `help=` text (`test_every_field_has_a_hint`).
- New `FilterOptions` fields follow the filter-parity rules in CLAUDE.md.
- GUI work runs off the GUI thread (`RepoQuery`, loader thread pools); GUI tests are headless in `tests/gui`.
- Style sheets use `palette(highlight)`; headings use `theme.serif_font`.
- Run `uv run ruff check` and `uv run black` on the files you touch only.
- Commits end with the attribution lines from the session's system reminder.

## Review Focus

1. **A v4 catalog opened read-only by the page** (no `state` column yet): the page must say "Run Find faces to update this gallery" with the button, not crash. Test in Task 9.
2. **A named person whose every face is moved away or rejected** stays listed with 0 photos; it is never deleted by `recompute_person`. Test in Task 1.
3. **Correcting while a long job runs** (a 30-minute scan): corrections are queued and sent when the job ends, and the tiles already disappear from the grid. Test in Task 12.
4. **Two persons sharing a name** in an older catalog: the "all" people filter treats a shared name as one person, as "any" does. Test in Task 7.
5. **A face crop whose file is gone** (drive unplugged, file deleted since the scan): the crop loader returns no path and the tile shows a placeholder. Test in Task 8.

---

## File structure

| file | role |
|---|---|
| `smart_gallery/config.py` | `SCHEMA_VERSION = 5` |
| `smart_gallery/models.py` | face states, new `Face`/`Person` fields and column tuples |
| `smart_gallery/db/schema.py` | new columns, `face_rejections`, `face_carryover`, v5 migration |
| `smart_gallery/db/face_labels.py` (new) | `FaceLabelsMixin`: filing, label and carry-over SQL, mixed into `GalleryRepository` |
| `smart_gallery/db/repository.py` | inherit the mixin; adjust `recompute_person`, `merge_persons`, `delete_person`, `reset_all_faces`, `invalidate_faces_by_relpath`, `add_scan_results`, `clear_persons`, `identify_faces_from_samples` |
| `smart_gallery/analysis/filing.py` (new) | numpy-only filing decisions |
| `smart_gallery/services/file_faces.py` (new) | `file_faces()` service: load, decide, group leftovers, apply |
| `smart_gallery/services/label_faces.py` (new) | correction entries, list-file I/O, `label_faces()` |
| `smart_gallery/services/scan_faces.py` | image size, crops, carry-over counts, `find_faces()` |
| `smart_gallery/services/cluster_faces.py` | `cluster-faces` delegates to `file_faces`; new `split_person` rules |
| `smart_gallery/face_crops.py` (new) | Qt-free crop path/box/encode helpers |
| `smart_gallery/gui/thumbs/face_crops.py` (new) | lazy crop from the source file (Pillow) |
| `smart_gallery/gui/thumbs/loader.py` | `FaceCropLoader` |
| `smart_gallery/organize/filters.py`, `db/where.py`, `cli.py`, `gui/filters_model.py`, `gui/filters_panel.py` | `people_match` any/all |
| `smart_gallery/gui/people/model.py` (new) | Qt-free queries for the page |
| `smart_gallery/gui/people/list_view.py` (new) | left list |
| `smart_gallery/gui/people/faces_grid.py` (new) | face-crop grid + action bar |
| `smart_gallery/gui/people/person_view.py` (new) | one person / several people pages |
| `smart_gallery/gui/people/view.py` | page shell, header controls, empty states, job sending |
| `FACES.md`, `README.md` | new workflow |

---

### Task 1: Schema v5 for face decisions

**Files:**
- Modify: `smart_gallery/config.py`, `smart_gallery/models.py:310-430`, `smart_gallery/db/schema.py:58-160`, `smart_gallery/db/repository.py:601-631` (`recompute_person`), `:685-693` (`delete_person`)
- Test: `tests/unit/test_faces.py`

**Interfaces:**
- Produces in `models.py`:
  - `FACE_STATES = ("new", "auto", "user", "suggested", "cluster", "hidden", "ignored")`. `"new"` = not filed yet (fresh scan or returned to the pool). This is the plan's addition to the spec's table.
  - `USER_STATES = frozenset({"user", "ignored"})`. The filing step never touches these.
  - `FACE_COLUMNS` gains, after `cluster_id`: `"state"`, `"suggested_person_id"`, `"match_score"`. `Face` gains `state: str = "new"`, `suggested_person_id: Optional[int] = None`, `match_score: Optional[float] = None` (before `id`).
  - `PERSON_COLUMNS` gains `"hidden"` after `cover_face_id`; `Person.hidden: bool = False` (stored as 0/1).
- Produces in `schema.py`:
  - `faces`: `state TEXT NOT NULL DEFAULT 'new'`, `suggested_person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL`, `match_score REAL`.
  - `persons`: `hidden INTEGER NOT NULL DEFAULT 0`.
  - `face_scan_state`: `img_w INTEGER`, `img_h INTEGER` (size of the decoded image the boxes refer to).
  - `CREATE_FACE_REJECTIONS`: `face_rejections(face_id INTEGER NOT NULL REFERENCES faces(id) ON DELETE CASCADE, person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE, PRIMARY KEY(face_id, person_id))`.
  - `CREATE_FACE_CARRYOVER`: `face_carryover(id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE, bbox_x REAL NOT NULL, bbox_y REAL NOT NULL, bbox_w REAL NOT NULL, bbox_h REAL NOT NULL, img_w INTEGER, img_h INTEGER, state TEXT, person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL, rejected TEXT)`. `rejected` is comma-separated person ids.
  - Indexes `ix_faces_state ON faces(state)`, `ix_carryover_media ON face_carryover(media_id)`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_faces.py`
  - `test_face_person_columns_match_schema` keeps passing with the new tuples (update nothing in the test; it reads the tuples).
  - `test_migrate_v4_to_v5_maps_states(tmp_path)`: build a v4 catalog (create with the v4 DDL copied into the test as literals, set `schema_version` to `4`): media 1–3; person 1 named `"Alice"`, person 2 unnamed; face A → person 1, face B → person 2, face C → no person. Open read-write. Assert `state` A `"auto"`, B `"cluster"`, C `"new"`; `persons.hidden` both 0; tables `face_rejections` and `face_carryover` exist; `schema_version == "5"`. Open again: same values (idempotent).
  - `test_recompute_keeps_empty_named_person(repo, make_item)`: named person with one face; set that face's `person_id` to NULL; `repo.recompute_person(pid)`; `repo.get_person(pid)` is not None and `face_count == 0`, `cover_face_id is None`, centroid NULL.
  - `test_recompute_drops_empty_unnamed_group(repo, make_item)`: same with an unnamed, non-hidden person → `get_person` is None (today's behaviour).
  - `test_delete_person_returns_faces_to_pool(repo, make_item)`: a `user` face and an `auto` face on a person plus a rejection row for another face; `delete_person` → both faces `state="new"`, `person_id` NULL; the rejection row is gone (cascade).

- [ ] **Step 2: Run them to verify they fail**
  Run: `uv run pytest tests/unit/test_faces.py -v`
  Expected: the new tests FAIL (no `state` column / version 4).

- [ ] **Step 3: Implement the schema and models** as listed under Interfaces. In `migrate()`, add `if current < 5:` that `ALTER TABLE`s each missing column (check `PRAGMA table_info` as the v3 step does), creates the two tables and indexes, then runs:
  - `UPDATE faces SET state='auto' WHERE person_id IN (SELECT id FROM persons WHERE name IS NOT NULL AND name != '')`
  - `UPDATE faces SET state='cluster' WHERE person_id IS NOT NULL AND state='new'`
  Add the two tables to `create_schema` too. Bump `SCHEMA_VERSION = 5`.

- [ ] **Step 4: Change `recompute_person(person_id)`**: when no faces remain, delete the row only if it is unnamed and not hidden; otherwise set `centroid=NULL, face_count=0, cover_face_id=NULL`. Cover choice: highest `det_score` among faces with `state IN ('user','auto','cluster')`, falling back to any face. **Change `delete_person`** to set `person_id=NULL, cluster_id=NULL, state='new', suggested_person_id=NULL` for its faces.

- [ ] **Step 5: Run the face tests and the full unit suite**
  Run: `uv run pytest tests/unit -v`
  Expected: PASS.

- [ ] **Step 6: Commit** — `git commit -m "Add face states, rejections and carry-over tables (schema v5)"`

---

### Task 2: The filing decisions (numpy only)

**Files:**
- Create: `smart_gallery/analysis/filing.py`
- Test: `tests/unit/test_filing.py`

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class FilingConfig:
    auto_thresh: float = 0.50      # = cluster_faces.DEFAULT_MATCH_THRESH
    suggest_thresh: float = 0.35
    margin: float = 0.05
    k: int = 5                     # nearest examples averaged per person
    min_face_px: float = 40.0      # short side of the box, decoded-image pixels
    min_det_score: float = 0.60
    min_group_size: int = 3

@dataclass(frozen=True)
class Candidate:
    person_id: int
    kind: Literal["named", "group", "hidden"]
    examples: np.ndarray           # (n, 512) L2-normalized, n >= 1
    rejected: np.ndarray           # (m, 512) faces rejected from this person, m >= 0

@dataclass(frozen=True)
class FaceIn:
    face_id: int
    embedding: np.ndarray          # (512,)
    state: str                     # never in USER_STATES
    person_id: Optional[int]
    short_side: float
    det_score: Optional[float]
    rejected_from: frozenset[int]

@dataclass(frozen=True)
class Decision:
    face_id: int
    state: str                     # "auto" | "suggested" | "cluster" | "hidden" | "new"
    person_id: Optional[int]
    suggested_person_id: Optional[int]
    score: Optional[float]

def score_matrix(embs: np.ndarray, candidates: Sequence[Candidate], k: int) -> np.ndarray: ...
    # (N, P): for each candidate, mean of the top-min(k, n) cosine similarities to its examples
def decide(faces: Sequence[FaceIn], candidates: Sequence[Candidate], cfg: FilingConfig) -> list[Decision]: ...
```
  `"new"` in a `Decision` means "leftover: send to grouping". The config values are this plan's starting values, under the spec's "builder sets the two thresholds".

Rules for `decide`, per face (this is the algorithm; the tests below pin it):
1. Allowed candidates: drop any `person_id` in `rejected_from`; drop a candidate when the face's best similarity to its `rejected` rows is greater than its best similarity to its `examples`.
2. Rank the allowed candidates by score; `best`, `lead = best - runner_up` (runner-up `-1.0` when there is none).
3. **Sticky** (`state` is `"auto"` or `"cluster"` with a `person_id`): keep the same `(state, person_id)` unless a **named** candidate other than the current one has score ≥ `auto_thresh` and beats the current person's score by ≥ `margin`. Then it becomes `auto` for that one. The current person's score is `-1.0` if it is no longer an allowed candidate. The quality gate is not applied here.
4. Otherwise apply the **quality gate**: `short_side < min_face_px` or `det_score < min_det_score` (None counts as passing) → `hidden`, no person.
5. Then, with best ≥ `auto_thresh` and lead ≥ `margin`: named → `auto`; group → `cluster` for it; hidden group → `hidden` with `person_id` of that group.
6. Else, when the best **named** candidate is ≥ `suggest_thresh` → `suggested` with `suggested_person_id`, no person.
7. Else → `"new"` (leftover).
`score` is the best allowed score (None when there are no candidates).

- [ ] **Step 1: Write the failing tests** (synthetic unit vectors, like `_unit()` in `test_faces.py`):
  - `test_confident_named_match_is_auto`
  - `test_close_match_is_suggested_not_assigned` (score between 0.35 and 0.50)
  - `test_no_match_is_leftover` → `state == "new"`
  - `test_ambiguous_between_two_named_is_not_auto` (lead < 0.05 → `suggested` for the best)
  - `test_rejected_person_is_never_chosen` (face identical to Alice's example but `rejected_from={alice}` → not auto/suggested for Alice)
  - `test_closer_to_rejected_faces_drops_candidate`
  - `test_small_or_weak_face_is_hidden` (`short_side=30` and, separately, `det_score=0.4`)
  - `test_auto_face_is_sticky_when_nobody_clearly_wins` (current person's score falls to 0.3; no other candidate ≥ 0.5 → unchanged)
  - `test_auto_face_moves_when_named_person_clearly_wins`
  - `test_cluster_face_joins_named_person_that_clearly_wins`
  - `test_match_to_group_is_cluster` and `test_match_to_hidden_group_is_hidden_with_group_id`
  - `test_score_matrix_uses_top_k_mean` (examples with similarities 0.9, 0.8, 0.1 and k=2 → 0.85)

- [ ] **Step 2: Run them to verify they fail**
  Run: `uv run pytest tests/unit/test_filing.py -v` → FAIL (module missing)

- [ ] **Step 3: Implement `filing.py`.** Compute similarities in chunks of 4,096 faces so a 200k-face catalog stays under ~1 GB of memory.

- [ ] **Step 4: Run them to verify they pass** → PASS

- [ ] **Step 5: Commit** — `git commit -m "Add the face filing decisions"`

---

### Task 3: `file-faces`: load, decide, group leftovers, apply

**Files:**
- Create: `smart_gallery/db/face_labels.py`, `smart_gallery/services/file_faces.py`
- Modify: `smart_gallery/db/repository.py` (class `GalleryRepository(FaceLabelsMixin)`), `smart_gallery/services/__init__.py`, `smart_gallery/cli.py` (parser, `_handle_file_faces`, `_HANDLERS`, `DETAIL_LOG_COMMANDS`), `smart_gallery/progress.py` (step `FILING = "Sorting faces"`)
- Test: `tests/test_file_faces.py`

**Interfaces:**
- Consumes: `analysis.filing.{FilingConfig, Candidate, FaceIn, Decision, decide}` (Task 2); `cluster_faces._run_clustering` (existing).
- Produces:
  - `FaceLabelsMixin.load_filing_inputs(self) -> tuple[list[FaceIn], list[Candidate]]`: faces with `state NOT IN ('user','ignored')`. `short_side = min(bbox_w, bbox_h)`. Candidates:
    - named, non-hidden persons: examples = embeddings of their `user` faces plus `reference_centroid` if set; if neither exists, their `centroid`. Skip a person with no examples.
    - unnamed non-hidden persons: kind `"group"`, examples = `centroid`.
    - hidden persons: kind `"hidden"`, examples = `centroid`.
    - `rejected` = embeddings of faces in `face_rejections` for that person.
  - `FaceLabelsMixin.apply_filing(self, decisions: list[Decision], new_groups: list[list[int]]) -> None`: one transaction. Writes `state, person_id, suggested_person_id, match_score` per decision. Creates one unnamed person per new group (`state='cluster'`). Then `recompute_person` for every person touched.
  - `services.file_faces.file_faces(repo, *, group: bool = True, config: FilingConfig = FilingConfig(), progress_callback=None) -> FileFacesReport`
  - `FileFacesReport` dataclass: `auto, suggested, grouped, hidden, left, groups_created: int`.
  - CLI `file-faces <drive> [--no-group]`, help "Sort scanned faces into people, suggestions and new groups (no GPU)."; `--no-group` help "Skip forming new groups; only match faces to existing people and groups."

  Grouping step (only when `group=True`): take the decisions with `state == "new"`, run `_run_clustering` (HDBSCAN defaults from `cluster_faces`) on their embeddings, and turn each label with ≥ `min_group_size` members into a new group. Noise and small labels become `hidden`. With fewer than `min_group_size` leftovers, skip clustering. sklearn/hdbscan are imported inside this step only, so `--no-group` works without the faces extra.

- [ ] **Step 1: Write the failing tests** in `tests/test_file_faces.py`, using `repo`, `make_item` and direct face inserts (no GPU; mark the grouping tests `@pytest.mark.faces` with `importorskip("sklearn")`, like `test_cluster_synthetic_three_people`):
  - `test_user_and_ignored_faces_untouched`
  - `test_new_face_close_to_user_examples_becomes_auto`
  - `test_named_person_without_user_faces_uses_centroid`
  - `test_rerun_without_changes_changes_nothing` (snapshot `faces` rows before and after a second run: equal)
  - `test_new_user_example_turns_suggestion_into_auto`: a face suggested for Alice at 0.45; add a `user` face for Alice at similarity 0.9 to it; rerun → `auto`
  - `test_no_group_leaves_leftovers_new`
  - `test_grouping_creates_unnamed_groups_and_hides_noise` (faces marker)
  - `test_existing_group_keeps_its_id_and_gains_faces` (faces marker)
  - `test_cli_file_faces_wired` (parser accepts `file-faces E:/ --no-group`; the handler is in `_HANDLERS`)

- [ ] **Step 2: Run them to verify they fail**
  Run: `uv run pytest tests/test_file_faces.py -v` → FAIL

- [ ] **Step 3: Implement** the mixin methods, the service and the CLI wiring. Log a one-line summary from the report in the handler.

- [ ] **Step 4: Run the tests to verify they pass**
  Run: `uv run pytest tests/test_file_faces.py tests/unit -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "Add file-faces: sort faces into people, suggestions and groups"`

---

### Task 4: Corrections: `label-faces`

**Files:**
- Create: `smart_gallery/services/label_faces.py`
- Modify: `smart_gallery/db/face_labels.py`, `smart_gallery/cli.py`, `smart_gallery/services/__init__.py`
- Test: `tests/test_label_faces.py`

**Interfaces:**
- Produces:
```python
Action = Literal["confirm", "reject", "move", "new_person", "ignore"]

@dataclass(frozen=True)
class LabelEntry:
    action: Action
    faces: tuple[int, ...]
    person_id: Optional[int] = None   # confirm / reject / move
    name: Optional[str] = None        # new_person

def write_label_list(path: Path, entries: Sequence[LabelEntry]) -> None   # JSON array of objects
def read_label_list(path: Path) -> list[LabelEntry]                     # ValueError on a bad entry
def label_faces(repo, entries: Sequence[LabelEntry]) -> LabelReport     # one transaction for all entries
@dataclass
class LabelReport: faces_changed: int = 0; people_created: int = 0; missing_faces: int = 0
```
- `FaceLabelsMixin.apply_labels(self, entries) -> LabelReport` does the SQL (the service validates and calls it).
- CLI `label-faces <drive>` with a mutually exclusive group (one form control):
  - `--list PATH` (help "A JSON list of corrections, as the desktop app writes it.")
  - or `--faces ID...` plus exactly one of `--to PERSON_ID` / `--new-person NAME` / `--not PERSON_ID` / `--confirm PERSON_ID` / `--ignore`, each with help text.

Effects. These follow the spec's corrections table, except that a "back to the pool" face here is `state='new'`, `person_id=NULL`, `suggested_person_id=NULL`:

| action | faces become | rejections |
|---|---|---|
| confirm p | `user`, person p, suggestion cleared | delete `(f, p)` |
| reject p | to the pool if its person or suggestion is p, else unchanged | insert `(f, p)` |
| move p | `user`, person p | insert `(f, old)` when old is a different, non-NULL person; delete `(f, p)` |
| new_person name | creates a named person, then same as move | as move |
| ignore | `ignored`, person NULL, suggestion cleared | none |

Face ids that no longer exist are counted in `missing_faces` and skipped; the entry still applies to the rest. `recompute_person` runs for every person touched.

- [ ] **Step 1: Write the failing tests**, one per row of the table, plus:
  - `test_label_list_roundtrip(tmp_path)`
  - `test_read_label_list_rejects_unknown_action(tmp_path)` → `ValueError`
  - `test_missing_face_ids_are_counted_not_fatal`
  - `test_all_entries_apply_atomically`: second entry's person id doesn't exist → `ValueError`, first entry not applied
  - `test_rejected_face_not_resuggested_after_file_faces`: reject, then `file_faces(repo, group=False)` → not suggested/assigned to that person
  - `test_cli_label_faces_single_action` (parser + handler on a real `repo` drive)

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/test_label_faces.py -v` → FAIL

- [ ] **Step 3: Implement** the service, the mixin method and the CLI.

- [ ] **Step 4: Run them to verify they pass** → PASS

- [ ] **Step 5: Commit** — `git commit -m "Add label-faces for confirming, rejecting, moving and ignoring faces"`

---

### Task 5: Person operations keep the user's decisions

**Files:**
- Modify: `smart_gallery/db/repository.py` (`merge_persons`, `clear_persons`, `identify_faces_from_samples`), `smart_gallery/db/face_labels.py`, `smart_gallery/services/cluster_faces.py` (`cluster_faces`, `split_person`), `smart_gallery/cli.py` (`name-person --merge`, new `hide-person`, `cluster-faces` handler)
- Test: `tests/unit/test_faces.py`, `tests/test_person_ops.py`

**Interfaces:**
- `merge_persons(dst_id, src_ids)`:
  - re-points faces and `suggested_person_id`;
  - moves rejections to `dst` (`UPDATE OR IGNORE`, then delete what is left on the sources);
  - deletes `(f, dst)` rejections where `f` is a `user` face of `dst`;
  - if `dst` is unnamed and exactly one source is named, `dst` takes that name;
  - unchanged signature.
- `FaceLabelsMixin.set_person_hidden(self, person_id: int, hidden: bool) -> None`: ValueError for a named person. Hide → `hidden=1`, its faces `state='ignored'`. Unhide → `hidden=0`, its faces with `state IN ('ignored','hidden')` → `cluster`.
- CLI `hide-person <drive> <person_id> [--unhide]` (help "Hide an unnamed group of strangers, or show it again.").
- CLI `name-person ... --merge` (help "If another person already has this name, merge into them."). Without it, a name already used by another person (case-insensitive) is a `ValueError`; with it, this person is merged into the existing one (`merge_persons(existing, [this])`).
- `split_person(repo, person_id, ...) -> ClusterReport`, same signature:
  - re-cluster only its non-`user` faces;
  - the label whose centroid is closest to the mean of its `user` faces stays (no `user` faces → the largest label);
  - other labels → new unnamed groups (`cluster`), each moved face gets a rejection against `person_id`;
  - noise → `new` with that rejection too;
  - the person itself is never deleted.
- `clear_persons()` (used by `cluster-faces --rebuild`):
  - keeps named and hidden persons and all `user`/`ignored` faces;
  - deletes the other unnamed groups;
  - returns `auto`, `cluster`, `suggested`, `hidden`, `new` faces to the pool;
  - leaves rejections alone.
- `cluster_faces(repo, ..., rebuild, incremental, ...)` becomes: optional `clear_persons()` when `rebuild`, then `file_faces(repo, group=not incremental)`. Keep `ClusterReport` and map from `FileFacesReport` (`persons_created = groups_created`, `faces_assigned = auto + grouped`, `noise = hidden`, `persons_matched` = people touched).
- `identify_faces_from_samples`:
  - skips faces in `USER_STATES` and `(face, person)` pairs in `face_rejections`;
  - writes `state='auto'` on assignment;
  - `--reassign` resets only `auto` faces of the target people to `new`.

- [ ] **Step 1: Write the failing tests**
  - `test_merge_moves_rejections_and_user_assignment_wins`
  - `test_merge_unnamed_target_takes_source_name`
  - `test_hide_and_unhide_group`; `test_hide_named_person_refused`
  - `test_name_person_duplicate_refused_without_merge`; `test_name_person_merge_flag_merges`
  - `test_split_keeps_closest_subgroup_and_rejects_the_rest` (faces marker, `importorskip("sklearn")`)
  - `test_split_never_deletes_person_and_keeps_user_faces` (faces marker)
  - `test_rebuild_keeps_named_people_and_user_faces`
  - `test_identify_reassign_leaves_user_faces` (adapt the existing identify tests if they assert `person_id` only)
  - Existing `test_split_person_breaks_impure_cluster` and `test_cluster_synthetic_three_people`: update their expectations to the new rules, keeping their intent (an impure cluster splits; three people are found).

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/unit/test_faces.py tests/test_person_ops.py -v`

- [ ] **Step 3: Implement** the changes above.

- [ ] **Step 4: Run the face tests** — `uv run pytest tests -m "not exiftool" -k "face or person or cluster or identify" -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "Keep user decisions through merge, split, rebuild and identify; add hide-person"`

---

### Task 6: Scan writes image size and crops, carries corrections over; `find-faces`

**Files:**
- Create: `smart_gallery/face_crops.py`
- Modify: `smart_gallery/db/repository.py` (`add_scan_results`, `invalidate_faces_by_relpath`, `reset_all_faces`), `smart_gallery/db/face_labels.py`, `smart_gallery/services/scan_faces.py`, `smart_gallery/cli.py`, `smart_gallery/services/__init__.py`
- Test: `tests/test_carryover.py`, `tests/unit/test_face_crops.py`

**Interfaces:**
- `face_crops.py` (no Qt; cv2 only inside `encode_crop`):
  - `CROP_EDGE = 160`, `CROP_MARGIN = 0.4` (the box grows by 40% of its size on each side, then becomes a square clamped to the image)
  - `crop_path(root: Path, face_id: int) -> Path` → `<root>/.smart_gallery/faces/<face_id // 1000>/<face_id>.jpg`
  - `crop_box(bbox: tuple[float, float, float, float], img_w: int, img_h: int) -> tuple[int, int, int, int]` (bbox is x, y, w, h; returns x1, y1, x2, y2)
  - `encode_crop(img_bgr, box) -> bytes` (JPEG, quality 85, resized to `CROP_EDGE`)
  - `write_crop(root: Path, face_id: int, data: bytes) -> None` (temp file + `os.replace`)
- `add_scan_results(results, det_version)`: each item is now `(media_id, faces, img_size)` with `img_size: tuple[int, int] | None`. Per media, in order:
  1. stash the decisions of any existing faces of that media (same as `stash_decisions`) and delete them, so a `FACE_DET_VERSION` bump no longer duplicates faces;
  2. insert the new faces and set each `Face.id` from `lastrowid`;
  3. write `img_w/img_h` on `face_scan_state`;
  4. apply carry-over.
  Returns faces inserted, as today.
- `FaceLabelsMixin.stash_decisions(self, media_ids: Iterable[int]) -> int`: copies every face of those media with `state IN ('user','ignored')` or with rejections into `face_carryover`, with the scan state's `img_w/img_h`.
- `FaceLabelsMixin.apply_carryover(self, media_id: int) -> tuple[int, int]` → `(carried, dropped)`:
  - match each stashed row to the new face of the same media with the highest IoU, on boxes divided by `img_w/img_h` (raw boxes when either size is NULL);
  - IoU ≥ `CARRYOVER_MIN_IOU = 0.5` → copy `state`, `person_id` and rejections, and remove the row;
  - stashed rows with no match are deleted and counted as dropped.
- `invalidate_faces_by_relpath`: `stash_decisions` before `clear_faces_for`.
- `reset_all_faces()` (`--rescan`): `stash_decisions` for all media, delete faces and scan state, then delete unnamed non-hidden persons; named and hidden persons stay.
- `ScanFacesReport` gains `decisions_carried: int = 0`, `decisions_dropped: int = 0`.
- In the scan loop: `img_size = (img.shape[1], img.shape[0])` when decoded. For each face, `encode_crop(img, crop_box(...))` right away; keep the bytes in the buffer (not the image). After `add_scan_results` assigns ids, `write_crop` each one. A crop failure is logged at DEBUG and never fails the scan.
- `services.scan_faces.find_faces(repo, *, progress_callback=None) -> tuple[ScanFacesReport, FileFacesReport]` = `scan_faces` then `file_faces(group=True)`.
- CLI `find-faces <drive>` (help "Scan new images for faces, then sort them into people (GPU)."). Add it to `DETAIL_LOG_COMMANDS`.

- [ ] **Step 1: Write the failing tests**
  - `test_crop_box_square_clamped` (box at an image corner stays inside, is square where room allows)
  - `test_crop_path_layout`
  - `test_metadata_edit_keeps_user_ignored_and_rejection`: user face, ignored face and a rejection on one media; `invalidate_faces_by_relpath`; `add_scan_results` with the same boxes → states, person and rejection restored on the new face ids; carry-over table empty
  - `test_moved_face_is_dropped_and_counted` (new box IoU < 0.5 → `(0, 1)`)
  - `test_resized_image_still_matches` (boxes and `img_size` both doubled → carried)
  - `test_rescan_keeps_named_people`
  - `test_version_bump_does_not_duplicate_faces` (scan at "v1", then `add_scan_results` at "v2" for the same media → one set of faces)
  - `test_cli_find_faces_wired`

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/test_carryover.py tests/unit/test_face_crops.py -v`

- [ ] **Step 3: Implement.** Update `tests/unit/test_faces.py::test_scan_results_resumability` and any other `add_scan_results` caller to the 3-tuple.

- [ ] **Step 4: Run them to verify they pass**, plus `uv run pytest -m "not exiftool" tests/unit tests/test_sync.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "Carry face corrections over rescans; save face crops; add find-faces"`

---

### Task 7: Library people filter: any / all

**Files:**
- Modify: `smart_gallery/organize/filters.py` (`FilterOptions`, `is_query_empty`), `smart_gallery/db/where.py:80-100`, `smart_gallery/cli.py` (`_filter_parser`, `build_filter_query`), `smart_gallery/gui/filters_model.py`, `smart_gallery/gui/filters_panel.py`
- Test: `tests/unit/test_where_parity.py`, `tests/unit/test_faces.py`, `tests/gui/test_filters_panel.py`

**Interfaces:**
- `FilterOptions.people_match: Literal["any", "all"] = "any"`. SQL-only, like `people`. It never makes a query non-empty on its own (`is_query_empty` ignores it, with a comment saying why).
- `build_where`: for `"all"`, one `id IN (...)` clause **per name** (matching `p.name = ?`, so persons sharing a name count as one) and **per id**, all ANDed. For `"any"`, the current clauses.
- CLI `--people-match {any,all}` (default `any`, help "With several people: photos with any of them, or with all of them.").
- `FilterValues.people_match: str = "any"`, round-tripped by `to_filter_options` / `from_filter_options`.
- Filter panel: an "Any of them / All of them" switch under the people choice. It is shown when the people box is shown, and enabled when two or more people are ticked.

- [ ] **Step 1: Write the failing tests**
  - Parity: add `FilterOptions(people_match="all")` to `QUERIES` (selects everything on both sides).
  - `test_filter_people_all_requires_every_person`: media 1 has Alice+Bob, media 2 Alice only → `all` → {1}, `any` → {1, 2}
  - `test_filter_people_all_treats_shared_name_as_one`: two persons named "Alice" (ids 1, 2) on media 1 and 2, Bob on media 2 → `people=["Alice","Bob"], all` → {2}
  - `test_filter_person_ids_all`
  - `test_people_match_alone_is_empty` (`is_query_empty(FilterOptions(people_match="all"))` is True)
  - `test_cli_people_match_builds_query`
  - GUI: `test_people_match_switch_roundtrip` (set values with two people and "all" → `to_filter_options(...).people_match == "all"`; the switch is enabled only with two people ticked)

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/unit/test_where_parity.py tests/unit/test_faces.py tests/gui/test_filters_panel.py -v`

- [ ] **Step 3: Implement** everywhere listed under Files.

- [ ] **Step 4: Run them to verify they pass** → PASS

- [ ] **Step 5: Commit** — `git commit -m "Let the people filter match all of the chosen people"`

---

### Task 8: Face crops in the GUI

**Files:**
- Create: `smart_gallery/gui/thumbs/face_crops.py`
- Modify: `smart_gallery/gui/thumbs/loader.py`, `smart_gallery/gui/app.py` (create `ctx.face_crops` next to `ctx.thumbs`), `smart_gallery/gui/context.py` (attribute + comment)
- Test: `tests/gui/test_face_crops.py`

**Interfaces:**
- `ensure_face_crop(root: Path, face_id: int, abspath: Path, ext: str, bbox: tuple[float, float, float, float], img_size: tuple[int, int] | None) -> Path | None`:
  - returns `crop_path` if it exists;
  - otherwise opens the source with `thumbs.cache.load_preview(abspath, ext, max_edge=4096)`;
  - scales the box by `preview.width / img_w`. With no `img_size`, it uses the preview's own size for non-RAW files and assumes the box already refers to the preview for RAW files, since the scanner decoded the same embedded preview;
  - crops with `crop_box`, writes the JPEG and returns the path;
  - returns None when the file is missing or unreadable (no marker file; it's retried next time).
- `FaceCropLoader(QObject)` with `ready = Signal(str, int, str)` `(root, face_id, path or "")` and `request(root, face_id, relpath, ext, bbox, img_size)` / `cancel_pending()`. It follows `ThumbnailLoader`'s generation/inflight pattern; share code with it if that stays simple.

- [ ] **Step 1: Write the failing tests**
  - `test_existing_crop_is_returned_without_opening_source`
  - `test_lazy_crop_from_jpeg(tmp_path)` (Pillow-made 800×600 JPEG, box in the middle → a 160×160 file)
  - `test_missing_source_returns_none` (Review Focus 5)
  - `test_loader_emits_ready(qtbot)`

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/gui/test_face_crops.py -v`

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run them to verify they pass** → PASS

- [ ] **Step 5: Commit** — `git commit -m "Load face crops for the People page"`

---

### Task 9: People queries (Qt-free)

**Files:**
- Create: `smart_gallery/gui/people/model.py`
- Test: `tests/gui/test_people_model.py`

**Interfaces:**
```python
class NeedsUpgrade(Exception): ...   # catalog older than schema v5 (read-only open)

@dataclass(frozen=True)
class FaceTile:
    face_id: int; media_id: int; relpath: str; ext: str
    state: str; score: Optional[float]
    bbox: tuple[float, float, float, float]; img_size: Optional[tuple[int, int]]

@dataclass(frozen=True)
class PersonRow:
    id: int; name: Optional[str]; photos: int; faces: int
    suggestions: int; cover: Optional[FaceTile]; hidden: bool   # cover = persons.cover_face_id as a tile

@dataclass(frozen=True)
class PeopleList:
    named: list[PersonRow]           # non-hidden, photos desc, then name
    groups: list[PersonRow]          # unnamed non-hidden, photos desc
    hidden_groups: list[PersonRow]
    has_faces: bool                  # any row in faces
    pending_images: int              # repo.count_unscanned_images(FACE_DET_VERSION)

@dataclass(frozen=True)
class PersonSummary:
    row: PersonRow; first_year: Optional[int]; last_year: Optional[int]

def people_list(repo) -> PeopleList                      # raises NeedsUpgrade on a v<5 catalog
def person_summary(repo, person_id: int) -> PersonSummary | None
def person_faces(repo, person_id: int, sort: Literal["least_sure", "newest"]) -> list[FaceTile]
def suggestions_for(repo, person_id: int) -> list[FaceTile]   # state 'suggested', score desc
def hidden_faces(repo) -> list[FaceTile]                    # state 'hidden' with no person, and 'ignored' with no person
def photos_filter(person_ids: Sequence[int], match: Literal["any", "all"]) -> FilterOptions
def library_filter(base: FilterOptions, names: Sequence[str], match: str) -> FilterOptions
    # dataclasses.replace(base, people=list(names), people_match=match, person_ids=None)
def name_owner(rows: PeopleList, name: str, except_id: int) -> Optional[PersonRow]   # case-insensitive
```
- `photos` counts distinct media with a face whose `person_id` is the person.
- `"least_sure"` order: non-`user` faces by `match_score` ascending (NULL first), then `user` faces.
- `"newest"`: by `media_items.date_taken` descending, NULL last.
- `NeedsUpgrade` is raised when `PRAGMA table_info(faces)` lacks `state`.

- [ ] **Step 1: Write the failing tests** (the `repo` fixture, rows inserted directly):
  - `test_people_list_sections_and_order`
  - `test_photo_count_is_distinct_media` (two faces of one person in one photo → 1)
  - `test_suggestion_count_per_person`
  - `test_person_faces_least_sure_first_user_last`
  - `test_person_summary_year_range`
  - `test_photos_filter_and_library_filter_keep_other_filters` (`base` with `cameras=["X"]` keeps it)
  - `test_name_owner_case_insensitive`
  - `test_old_catalog_raises_needs_upgrade(tmp_path)` (a v4 DDL catalog opened `read_only=True`; Review Focus 1)

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/gui/test_people_model.py -v`

- [ ] **Step 3: Implement.** Keep `PySide6` out of this module.

- [ ] **Step 4: Run them to verify they pass** → PASS

- [ ] **Step 5: Commit** — `git commit -m "Add the People page queries"`

---

### Task 10: People page shell and left list

**Files:**
- Create: `smart_gallery/gui/people/list_view.py`
- Modify: `smart_gallery/gui/people/view.py` (replace the placeholder)
- Test: `tests/gui/test_people_view.py`

**Interfaces:**
- `PeopleList(QWidget)`, in `list_view.py`:
  - signals: `selection_changed = Signal(list)` (selected person ids, in click order), `merge_requested = Signal(int, list)` (dst, srcs), `rename_requested = Signal(int)`, `split_requested = Signal(int)`, `hide_requested = Signal(int, bool)`, `delete_requested = Signal(int)`, `show_hidden_requested = Signal()`;
  - `set_people(people: PeopleList) -> None` keeps the selection by id when the ids still exist;
  - a search box that filters by name (case-insensitive substring);
  - section headers "Named" and "To name (N)";
  - an `ExtendedSelection` list view; drag a row onto another to emit `merge_requested`; a context menu with Rename, Merge (with ≥ 2 selected), Split (disabled when `faces_available()` is False), Hide (groups only), Delete (named only);
  - a "Show hidden faces" button at the bottom;
  - each row: cover crop from `ctx.face_crops`, name or "Unnamed", photo count, and a suggestions badge when > 0.
- `PeopleView(QWidget)`, in `view.py`:
  - the left `PeopleList` and a right `QStackedWidget` (filled in Task 11) in a splitter;
  - `header_controls`: a "Find new faces" button plus a status label ("1,204 images not scanned yet" when `pending_images > 0`);
  - loads `people_list` through `RepoQuery`, reloads on `ctx.gallery_changed` and `ctx.catalog_updated`;
  - empty states, as one message label + action button in the right stack:
    - no gallery, or the drive is not connected (`FileNotFoundError`);
    - `NeedsUpgrade` → "This gallery needs a face update. Find faces updates it.";
    - `has_faces` False → "No faces found yet." + Find faces;
    - `faces_available()` False → install text `uv sync --extra faces`, Find faces disabled.
  - `find_faces()` starts `JobSpec("Find faces", (("find-faces", root),))`. When a job is running, it shows `QMessageBox.information` with the same text the Storage page uses for a running job.
  - The person actions emit jobs:
    - merge → `merge-persons root dst srcs...`. When both rows are named and the names differ, ask which name to keep first, then add `name-person root dst NAME` after the merge in the same `JobSpec`;
    - rename → an inline `QInputDialog`. When `name_owner` finds another owner, ask "Merge with the existing Alice?"; yes → `name-person root id NAME --merge`;
    - split → `split-person root id`;
    - hide → `hide-person root id [--unhide]`;
    - delete → confirm first, then `delete-person root id`.

- [ ] **Step 1: Write the failing tests** (`ctx.jobs = _Jobs()` stub as in `tests/gui/test_storage_view.py`; monkeypatch `QMessageBox`/`QInputDialog`):
  - `test_list_shows_named_then_groups_with_counts`
  - `test_search_filters_rows`
  - `test_selection_survives_reload`
  - `test_find_faces_starts_job`; `test_find_faces_when_job_running_tells_user`
  - `test_old_catalog_shows_update_message` (Review Focus 1)
  - `test_no_faces_state`; `test_faces_extra_missing_disables_find` (monkeypatch `faces_available`)
  - `test_rename_to_existing_name_offers_merge` → argv `("name-person", root, "7", "Alice", "--merge")`
  - `test_merge_two_named_asks_which_name`
  - `test_hide_group_job`; `test_delete_asks_first`
  - `test_pending_count_in_header`

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/gui/test_people_view.py -v`

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run them, plus `tests/gui/test_areas.py`** → PASS

- [ ] **Step 5: Commit** — `git commit -m "Build the People page list and its person actions"`

---

### Task 11: Person page: Photos, Faces, several people, Show in Library

**Files:**
- Create: `smart_gallery/gui/people/person_view.py`, `smart_gallery/gui/people/faces_grid.py`
- Modify: `smart_gallery/gui/people/view.py`
- Test: `tests/gui/test_person_view.py`

**Interfaces:**
- `FaceGrid(QListView)`, in `faces_grid.py`:
  - icon mode with `ExtendedSelection`; tiles show the crop from `ctx.face_crops` (placeholder until ready, and when the path is "") with a small tick on `user` faces;
  - `set_tiles(tiles: list[FaceTile])`, `selected_face_ids() -> list[int]`, `remove_faces(ids: Iterable[int])`.
- `FaceActionBar(QWidget)`: shows "N faces selected" and the buttons **Not <name>**, **Move to… ▾** (a menu of named people plus groups, then "New person…") and **Ignore**. Its signal is `action = Signal(object)` carrying a `LabelEntry`.
- `PersonPage(QWidget)`, in `person_view.py`:
  - `show_person(person_id: int)`;
  - header: cover, name in the serif font (an unnamed group shows an "Add a name" `QLineEdit`), "N photos · first–last", Rename, Show in Library (named only), and a Photos | Faces segmented switch;
  - a suggestions strip ("Is this Alice? N suggestions [Review]") that is visible when there are suggestions. Review swaps the body for a `FaceGrid` of `suggestions_for` with ✓ / ✕ for the selection and "✓ all" / "✕ all";
  - Photos uses `LibraryGridModel` + `GroupedGrid` + `ctx.thumbs`, with rows from `library_rows(repo, LibraryQuery(filters=photos_filter([id], "any"), sort="date", descending=True))`. A double-click opens the Library viewer the way `LibraryView.open_viewer` does;
  - Faces uses `FaceGrid` with `person_faces(..., sort)`, a sort combo (least sure first / newest) and the `FaceActionBar`;
  - signal `labels = Signal(list)` carrying `list[LabelEntry]`; `PeopleView` sends them (Task 12).
- `SeveralPeoplePage(QWidget)`:
  - `show_people(rows: list[PersonRow])`;
  - an all/any switch (default "all"), a count, and a photo grid from `photos_filter(ids, match)`;
  - **Show in Library** is enabled only when every row is named. It calls `ctx.set_filters(library_filter(ctx.filters, names, match))` and then `ctx.navigate.emit("library")`.
- "Show hidden faces" shows a `FaceGrid` of `hidden_faces` with only **Move to…**, plus a list of hidden groups, each with "Show again" (`hide-person --unhide`).
- `PeopleView` routes `selection_changed`: one id → `PersonPage`, two or more → `SeveralPeoplePage`, none → an empty hint.

- [ ] **Step 1: Write the failing tests**
  - `test_person_page_header_and_photos`
  - `test_switch_to_faces_lists_least_sure_first`
  - `test_move_to_emits_move_entry`; `test_not_person_emits_reject_entry`; `test_new_person_emits_new_person_entry` (monkeypatch `QInputDialog.getText`)
  - `test_review_confirm_all_emits_confirm_entry`
  - `test_add_name_on_group_runs_name_person` (through `PeopleView`, the argv check)
  - `test_several_people_all_and_any_counts`
  - `test_show_in_library_sets_filters_and_navigates`; `test_show_in_library_disabled_with_unnamed_group`
  - `test_hidden_faces_view_and_unhide_job`

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/gui/test_person_view.py -v`

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run them to verify they pass** → PASS

- [ ] **Step 5: Commit** — `git commit -m "Add the person page with photos, faces and several-people views"`

---

### Task 12: Sending corrections: batching, queueing, optimistic updates

**Files:**
- Modify: `smart_gallery/gui/people/view.py`
- Test: `tests/gui/test_people_view.py`

**Interfaces:**
- `PeopleView.send_labels(entries: list[LabelEntry]) -> None`:
  - removes the affected face ids from the visible grids right away (`FaceGrid.remove_faces`);
  - appends the entries to `self._pending`;
  - starts a 400 ms single-shot timer, so a burst of clicks becomes one job;
  - on the timer, when `ctx.jobs.running` is False, writes `self._pending` with `write_label_list` to `app_data_dir() / "face-labels" / <timestamp>.json` (named the way `StorageView._new_list_path` names its lists), starts `JobSpec("Fix faces", (("label-faces", root, "--list", path), ("file-faces", root, "--no-group")))` and clears `self._pending`;
  - when a job is running, it keeps the entries and sends them on `ctx.jobs.finished`.
- The page refreshes on `ctx.catalog_updated`, as in Task 10.
- A leave guard (`ctx.add_leave_guard`) asks before closing or switching gallery while `_pending` is not empty, and sends them first when the user agrees.

- [ ] **Step 1: Write the failing tests**
  - `test_burst_of_corrections_becomes_one_job` (`qtbot.wait(500)`; one `JobSpec` with both argvs; the list file holds every entry)
  - `test_corrections_wait_for_running_job` (stub `running=True`; nothing started; flip it and emit `finished` → one job; tiles were already removed; Review Focus 3)
  - `test_leave_guard_with_pending_corrections`

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/gui/test_people_view.py -v`

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run them to verify they pass** → PASS

- [ ] **Step 5: Commit** — `git commit -m "Batch and queue face corrections from the People page"`

---

### Task 13: Docs and full check

**Files:**
- Modify: `FACES.md`, `README.md`, `CLAUDE.md` (face pipeline section: `find-faces` / `file-faces` / `label-faces`, face states, carry-over)

- [ ] **Step 1: Rewrite `FACES.md` section 2** around: Find faces (the page button, or `find-faces`) → name groups → fix with Faces view / `label-faces` → filter (Library people filter, any/all, `--people-match`). Then cover the sample folders as an optional shortcut, and the face states, sticky assignments, carry-over and the thresholds from `FilingConfig`. Update section 3 (keeping up to date) to `find-faces`. Update the README command list.
- [ ] **Step 2: Run the whole suite**
  Run: `uv run pytest -m "not exiftool"`
  Expected: PASS (faces-marked tests run when the extra is installed).
- [ ] **Step 3: Lint the touched files**
  Run: `uv run ruff check <touched files>` and `uv run black --check <touched files>` → clean
- [ ] **Step 4: Commit** — `git commit -m "Document the People workflow"`
