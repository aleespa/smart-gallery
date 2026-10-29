# People Undo and Find More Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the People page (1) an **Undo** that reverts the last batch of face corrections *including every face the re-filing moved because of it*, and (2) a **Find more** search that ranks the unclassified faces by how much they look like one person, so the user can ✓/✕ them.

**Architecture:** Undo is stored in the catalog. `label-faces` snapshots the face filing before it writes, applies the corrections, optionally re-files (`--refile`, replacing the separate `file-faces --no-group` step), and stores the before/after diff as one undo batch in new v8 tables. `undo-faces` restores the latest batch only if every face it touched is still exactly as the batch left it. Find more is read-only: a Qt-free query scores unclassified faces against the person's filing examples with the existing `filing.score_matrix`, and the person page shows the results in its existing review grid.

**Tech Stack:** Python 3, SQLite, numpy, PySide6, pytest/pytest-qt.

**Spec:** this conversation (2026-10-29). Motivating incident: a mistaken ✕ all (81 faces) on Juliette Boutard stripped 3,819 of her auto faces during the re-filing; the user wants a one-click way back, and a way to search the unclassified faces for a person.

## Global Constraints

- `config.SCHEMA_VERSION` becomes `8`; `migrate()` gets an idempotent `if current < 8:` block. Read-only opens never migrate, so every reader of the undo tables tolerates them missing (treat as "no undo").
- Undo tables have **no FK to `faces` or `persons`** (rows must describe faces/persons that were since deleted); only `batch_id` cascades from `face_undo_batches`.
- Undo tables are not part of mirror face transfer (`mirror/faces.py` table list stays unchanged); mirror catalogs refuse `undo-faces` via `ensure_not_mirror`.
- Keep at most `MAX_UNDO_BATCHES = 20` batches; recording a new one deletes the oldest beyond that.
- No new data on `media_items` (CLAUDE.md rule).
- The GUI never writes the catalog: Undo runs as a CLI job; Find more only reads (`RepoQuery`).
- Every new CLI option gets `help=` text (`test_every_field_has_a_hint`).
- Lint/format only touched files: `uv run ruff check <files>` and `uv run black <files>`.

## Review Focus

1. **Undo after something else changed the same faces** (a find-faces, merge, rescan, or a later correction batch that was not undone first): undo must refuse and change nothing, rather than overwrite newer decisions. → Task 1 `test_undo_refuses_when_a_face_changed_since`, `test_undo_refuses_when_face_deleted`.
2. **Undo when the batch deleted an emptied unnamed group** (moving a group's last faces away): the group must come back with its id so faces can point at it again. → Task 1 `test_undo_recreates_deleted_group`.
3. **Clicking Undo within the 400 ms send delay** (corrections still queued, not sent): the queued corrections are dropped and the page reloads; no `undo-faces` job undoes an *older* batch by mistake. → Task 3 `test_undo_with_pending_corrections_drops_them`.
4. **Catalog reload while Find more results are on screen** (the job after a ✓ ends and `catalog_updated` fires): the search results must be refreshed, not replaced by the suggestion list. → Task 5 `test_find_more_survives_reload`.
5. **Find more on a person with no usable examples** (unnamed group with no centroid, or a person whose faces were all removed): show the empty message, not an exception. → Task 4 `test_find_more_without_examples_is_empty`.

---

## File Structure

| File | Responsibility |
|---|---|
| `smart_gallery/db/schema.py` (modify) | `CREATE_FACE_UNDO_TABLES`, created in `init_schema` and migration v8 |
| `smart_gallery/config.py` (modify) | `SCHEMA_VERSION = 8` |
| `smart_gallery/db/face_undo.py` (create) | `FaceUndoMixin`: snapshot, record, last, undo |
| `smart_gallery/db/repository.py` (modify) | add `FaceUndoMixin` to `GalleryRepository` bases |
| `smart_gallery/services/label_faces.py` (modify) | `refile=` option, `describe()`, record the undo batch |
| `smart_gallery/services/undo_faces.py` (create) | `undo_faces(repo) -> UndoReport` |
| `smart_gallery/services/__init__.py`, `cli.py` (modify) | `label-faces --refile`, `undo-faces` command |
| `smart_gallery/gui/people/model.py` (modify) | `PeopleList.last_undo`, `find_more()` |
| `smart_gallery/db/face_labels.py` (modify) | `person_examples(person_id)` |
| `smart_gallery/gui/people/view.py` (modify) | Undo button + Ctrl+Z, job argv change |
| `smart_gallery/gui/people/person_view.py` (modify) | Find more button, review page "more" mode |
| `FACES.md`, `docs/USER_GUIDE.md`, `CLAUDE.md` (modify) | document undo and Find more |
| Tests | `tests/test_face_undo.py`, `tests/test_label_faces.py`, `tests/test_people_find_more.py`, `tests/gui/test_people_view.py`, `tests/unit/test_faces.py` |

---

### Task 1: Undo store (schema v8 + `FaceUndoMixin`)

**Files:**
- Modify: `smart_gallery/config.py:16`, `smart_gallery/db/schema.py` (new constant after `CREATE_MIRROR_TABLES`; `init_schema`; `migrate` block `if current < 8`), `smart_gallery/db/repository.py:87`
- Create: `smart_gallery/db/face_undo.py`
- Test: `tests/test_face_undo.py`; update `tests/unit/test_faces.py:541` (`== 7` → `== 8`)

**Interfaces:**
- Produces:
  - Tables:
    - `face_undo_batches(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT (datetime('now')), summary TEXT NOT NULL)`
    - `face_undo_faces(batch_id INTEGER NOT NULL REFERENCES face_undo_batches(id) ON DELETE CASCADE, face_id INTEGER NOT NULL, before_state TEXT, before_person INTEGER, before_suggested INTEGER, before_score REAL, after_state TEXT, after_person INTEGER, after_suggested INTEGER, after_score REAL, PRIMARY KEY (batch_id, face_id))`
    - `face_undo_rejections(batch_id … CASCADE, face_id INTEGER NOT NULL, person_id INTEGER NOT NULL, added INTEGER NOT NULL)` — `added=1`: the batch inserted it; `0`: the batch removed it
    - `face_undo_persons(batch_id … CASCADE, person_id INTEGER NOT NULL, name TEXT, hidden INTEGER NOT NULL, created INTEGER NOT NULL)` — `created=1`: the batch created it; `0`: the batch deleted it (values are the row before deletion)
  - `FaceSnapshot` (frozen dataclass in `face_undo.py`): `faces: dict[int, tuple]` (face_id → `(state, person_id, suggested_person_id, match_score)`), `rejections: set[tuple[int, int]]`, `persons: dict[int, tuple[str | None, int]]` (id → `(name, hidden)`)
  - `UndoConflict(ValueError)`
  - `UndoInfo` (frozen dataclass): `batch_id: int`, `summary: str`, `created_at: str`
  - Mixin methods on `GalleryRepository`:
    - `face_snapshot() -> FaceSnapshot`
    - `record_undo(summary: str, before: FaceSnapshot) -> int | None` — diffs `before` against the current catalog, writes one batch (commits), prunes to `MAX_UNDO_BATCHES`; returns `None` and writes nothing when nothing changed
    - `last_undo() -> UndoInfo | None` — newest batch; `None` when the table is missing (`sqlite3.OperationalError` "no such table") or empty
    - `undo_last() -> tuple[UndoInfo, int]` — `(batch, faces_restored)`; raises `UndoConflict` (and changes nothing) or `ValueError("Nothing to undo.")`

- [ ] **Step 1: Write the failing tests** in `tests/test_face_undo.py`, using `Catalog`, `_unit` from `tests.test_file_faces` and the `repo`/`make_item` fixtures:

```python
def test_record_and_undo_restores_faces_rejections_and_scores(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "suggested", None, suggested=alice)
    g = cat.face(_unit(1), "auto", alice)
    before = repo.face_snapshot()
    repo.conn.execute("UPDATE faces SET state='new', suggested_person_id=NULL WHERE id=?", (f,))
    repo.conn.execute("UPDATE faces SET match_score=0.9 WHERE id=?", (g,))
    repo.conn.execute("INSERT INTO face_rejections VALUES (?, ?)", (f, alice))
    repo.conn.commit()
    bid = repo.record_undo("Marked 1 face as not Alice", before)
    assert repo.last_undo().batch_id == bid
    info, n = repo.undo_last()
    assert info.summary == "Marked 1 face as not Alice" and n == 2
    assert repo.face_snapshot() == before
    assert repo.last_undo() is None

def test_record_nothing_changed_returns_none(cat, repo): ...   # record_undo(...) is None, last_undo() is None
def test_undo_recreates_deleted_group(cat, repo): ...           # group of 1 face; move face to Alice + recompute_person(group) deletes it; undo → persons row back with same id, face back as 'cluster' in it
def test_undo_deletes_person_the_batch_created(cat, repo): ...  # INSERT persons 'Carol' + move face to her after snapshot; undo → Carol row gone
def test_undo_refuses_when_a_face_changed_since(cat, repo): ... # after record, change a recorded face's state; pytest.raises(UndoConflict); face_snapshot() unchanged by the attempt; batch still listed
def test_undo_refuses_when_face_deleted(cat, repo): ...         # after record, DELETE the face → UndoConflict
def test_undo_refuses_when_deleted_person_id_reused(cat, repo): ... # batch deleted group id G; INSERT persons (id=G) afterwards → UndoConflict
def test_batches_are_capped(cat, repo): ...                     # 21 recorded batches → 20 rows in face_undo_batches, oldest gone
def test_undo_twice_walks_back(cat, repo): ...                  # two batches on different faces; undo, undo → original snapshot; third undo raises ValueError("Nothing to undo.")
def test_last_undo_tolerates_missing_table(cat, repo): ...      # DROP the four tables; last_undo() is None
def test_migrate_v7_catalog_adds_undo_tables(...): ...          # set meta schema_version '7', drop the tables, reopen read-write → tables exist, schema_version == 8
```

- [ ] **Step 2: Run** `uv run pytest tests/test_face_undo.py -v` — expected: FAIL (`AttributeError: ... face_snapshot`).

- [ ] **Step 3: Implement.** Schema constant + `init_schema` + `if current < 8:` creating the tables with `IF NOT EXISTS`; `SCHEMA_VERSION = 8`; add `FaceUndoMixin` to `GalleryRepository` bases. In `face_undo.py`:
  - `face_snapshot` reads `SELECT id, state, person_id, suggested_person_id, match_score FROM faces`, all of `face_rejections`, and `SELECT id, name, hidden FROM persons`.
  - `record_undo`: faces whose tuple differs (or are new/gone) → `face_undo_faces` rows (`None` columns for a side where the face doesn't exist); rejection set differences → `added` 1/0; persons present only after → `created=1`, only before → `created=0` with the before values. One transaction.
  - `undo_last`, in one transaction, rollback on any error:
    1. every recorded face must exist with exactly its `after_*` values; every `added=1` rejection must exist, every `added=0` must not; every `created=0` person id must not exist — else `UndoConflict(f"Can't undo \"{summary}\": {n:,} of its faces changed since. Nothing was changed.")`
    2. re-insert `created=0` persons (`INSERT INTO persons (id, name, hidden)`)
    3. `UPDATE faces` to the `before_*` values
    4. delete `added=1` rejections, insert `added=0` ones
    5. delete `created=1` persons that no face references
    6. delete the batch, commit, then `recompute_person` for every person id named in the batch (faces before/after, rejections, persons) that still exists.

- [ ] **Step 4: Run** `uv run pytest tests/test_face_undo.py tests/test_repository.py tests/unit/test_faces.py -v` — expected: PASS.

- [ ] **Step 5: Commit** `git commit -m "Store face corrections as undoable batches (schema v8)"`

---

### Task 2: `label-faces` records undo; `--refile`; `undo-faces` command

**Files:**
- Modify: `smart_gallery/services/label_faces.py`, `smart_gallery/services/__init__.py`, `smart_gallery/cli.py` (parser near line 654, `_handle_label_faces` ~1266, `_HANDLERS`), `FACES.md` (section 2), `smart_gallery/cli.py` `DETAIL_LOG_COMMANDS` only if `file-faces` is in it (then add `label-faces`)
- Create: `smart_gallery/services/undo_faces.py`
- Test: `tests/test_label_faces.py`

**Interfaces:**
- Consumes: Task 1 `face_snapshot`, `record_undo`, `undo_last`, `UndoConflict`.
- Produces:
  - `label_faces(repo, entries, *, skip_missing=False, refile=False) -> LabelReport` — snapshot after validation, `apply_labels`, then if `refile`: `file_faces(repo, group=False)`; then `record_undo(describe(entries, names), before)`. `LabelReport` gains `filed: FileFacesReport | None = None`.
  - `describe(entries: Sequence[LabelEntry], names: dict[int, str]) -> str` — exact copy, `{who}` = the person's name, else `"a group"`:
    - confirm: `"Confirmed {n} face(s) as {who}"`; reject: `"Marked {n} face(s) as not {who}"`; move: `"Moved {n} face(s) to {who}"`; new_person: `"Moved {n} face(s) to {name}"`; ignore: `"Ignored {n} face(s)"` — use "face" when n == 1, "faces" otherwise
    - several entries: `"{k} corrections"` when actions differ, else the single phrase with n summed when they share action and person
  - `undo_faces(repo) -> UndoReport` (`summary: str`, `faces_restored: int`) — calls `ensure_not_mirror(repo)` then `repo.undo_last()`.
  - CLI: `label-faces … --refile` (help: `"Afterwards re-sort the other faces (as file-faces --no-group), in the same undo step."`); new command `undo-faces DRIVE` (help: `"Undo the last face correction made with label-faces, if nothing has changed those faces since."`), handler logs `logger.success(f"Undid: {r.summary} ({r.faces_restored:,} faces restored).")`. `UndoConflict` is a `ValueError`, so `main()` already logs it and exits 1.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_label_faces.py`):

```python
def test_label_then_undo_restores_everything_refile_moved(cat, repo):
    # The incident: few confirmed examples, many auto faces, rejecting look-alikes
    # makes the re-filing strip the auto faces. Undo must bring all of them back.
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    autos = [cat.face(_near(_unit(0), 0.8, axis=500 + i), "auto", alice) for i in range(20)]
    look = [cat.face(_near(_unit(0), 0.95, axis=300 + i), "suggested", None, suggested=alice) for i in range(5)]
    repo.recompute_person(alice)
    before = repo.face_snapshot()
    label_faces(repo, [LabelEntry("reject", tuple(look), person_id=alice)], refile=True)
    assert repo.face_snapshot() != before
    report = undo_faces(repo)
    assert report.summary == "Marked 5 faces as not Alice"
    assert repo.face_snapshot() == before

def test_describe_phrases(): ...                 # each action's exact string above, singular/plural, "a group", "{k} corrections"
def test_label_without_changes_records_nothing(cat, repo): ...   # all face ids missing → last_undo() is None
def test_cli_undo_faces(cat, repo, drive): ...   # _HANDLERS["undo-faces"](build_parser().parse_args(["undo-faces", str(drive)])) after a CLI label-faces --confirm; face back to its old state
def test_cli_undo_faces_nothing_to_undo_raises(drive): ...  # ValueError
def test_cli_label_faces_refile_flag_parses(drive): ...     # parse_args(["label-faces", d, "--list", "x.json", "--refile"]).refile is True
def test_undo_faces_refuses_on_mirror(...): ...  # follow the existing mirror-guard test pattern in tests/test_mirror_faces.py
```

- [ ] **Step 2: Run** `uv run pytest tests/test_label_faces.py -v` — expected: the new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above; `names` comes from one `SELECT id, name FROM persons WHERE id IN (...)` read *before* applying (a group that gets deleted still describes as "a group").
- [ ] **Step 4: Run** `uv run pytest tests/test_label_faces.py tests/test_file_faces.py tests/test_mirror_faces.py -v` — expected: PASS.
- [ ] **Step 5: Document** in `FACES.md` section 2: `--refile`, `undo-faces`, and the rule "undo reverts the latest correction batch, including faces the re-sorting moved; it refuses if those faces changed since (find faces, merge, rescan…)".
- [ ] **Step 6: Commit** `git commit -m "Record label-faces batches for undo; add undo-faces and --refile"`

---

### Task 3: Undo button on the People page

**Files:**
- Modify: `smart_gallery/gui/people/model.py` (`PeopleList`, `people_list`), `smart_gallery/gui/people/view.py`
- Test: `tests/gui/test_people_view.py`, `tests/gui/test_people_model.py`

**Interfaces:**
- Consumes: Task 1 `repo.last_undo()`; Task 2 `undo-faces`, `--refile`.
- Produces:
  - `PeopleList.last_undo: Optional[str] = None` (the summary; field with default so existing constructions keep working)
  - `PeopleView.undo_button: QPushButton` (text `UNDO = "Undo"`, tooltip `UNDO_TIP = "Undo: {summary}"`), placed in `header_controls` before `find_button`; `QShortcut(QKeySequence.StandardKey.Undo, self)` triggers the same slot; `PeopleView.undo() -> None`
  - Constants: `UNDO_TITLE = "Undo"`
  - The correction job becomes one step: `JobSpec(FIX_TITLE, (("label-faces", root, "--list", str(path), "--refile"),))`

- [ ] **Step 1: Write the failing tests**:

```python
def test_burst_of_corrections_becomes_one_job(...):   # UPDATE the existing test:
    (label,) = spec.argvs
    assert label[:3] == ("label-faces", root, "--list") and label[4] == "--refile"

def test_undo_button_reflects_last_batch(qtbot, repo, cat): ...
    # no batch → disabled, tooltip ""; record_undo("Confirmed 2 faces as Alice", snap) then view.reload() → enabled, tooltip "Undo: Confirmed 2 faces as Alice"
def test_undo_starts_job(qtbot, repo, cat): ...      # with a batch: view.undo() → ctx.jobs.specs[-1] == JobSpec("Undo", (("undo-faces", root),))
def test_undo_with_pending_corrections_drops_them(qtbot, repo, cat): ...
    # send_labels([...]) then view.undo() before the timer fires → no job started, view._pending == [], a reload was requested
def test_people_list_last_undo(repo, cat): ...       # in test_people_model.py: None without batches; summary with one
```
  Also add `view.undo()` (with a recorded batch) to `test_every_argv_the_page_builds_parses` so `undo-faces` is parse-checked.

- [ ] **Step 2: Run** `uv run pytest tests/gui/test_people_view.py tests/gui/test_people_model.py -v` — expected: new/updated tests FAIL.
- [ ] **Step 3: Implement.** `people_list` sets `last_undo=(info.summary if (info := repo.last_undo()) else None)`. `_on_data` enables the button and sets the tooltip; `_clear` disables it. `undo()`: if `self._pending` → clear `_pending`/`_pending_roots`, stop `_send_timer`, `self.reload()` and the person page (`self.person_page.show_person(self.person_page.person_id)` when it has one) so the hidden tiles come back; return. Otherwise `self._start(UNDO_TITLE, ("undo-faces", str(self._root)))`. The page refreshes through the existing `catalog_updated` → `reload`.
- [ ] **Step 4: Run** the same tests — expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add Undo to the People page"`

---

### Task 4: `find_more` query

**Files:**
- Modify: `smart_gallery/db/face_labels.py` (new method), `smart_gallery/gui/people/model.py`
- Test: `tests/test_people_find_more.py`

**Interfaces:**
- Produces:
  - `GalleryRepository.person_examples(person_id: int) -> np.ndarray` — `(n, 512)` float32, the same examples `load_filing_inputs` gives that person (named: its `user` faces' embeddings plus `reference_centroid`, else `centroid` when it has neither; unnamed group: `centroid`); `(0, 512)` when none or the person doesn't exist. Have `load_filing_inputs` keep its own bulk query (do not route it through this per-person method).
  - `model.find_more(repo, person_id: int, *, limit: int = FIND_MORE_LIMIT, floor: float = FIND_MORE_FLOOR) -> list[FaceTile]` with `FIND_MORE_LIMIT = 300`, `FIND_MORE_FLOOR = 0.35` (= `FilingConfig.suggest_thresh`). Tiles carry the similarity in `score`, ordered by score desc then face id.
  - Pool ("unclassified"): `state NOT IN ('user', 'ignored')` AND (`person_id IS NULL` OR the person is unnamed and not hidden) AND `person_id IS NOT :pid` AND no `face_rejections` row for `(face, :pid)`.
  - Scoring: `filing.score_matrix(embs, [Candidate(pid, "named", examples, empty)], FilingConfig().k)[:, 0]`; read the pool in chunks of `filing.CHUNK` rows and keep the best `limit` with `heapq`, so memory stays bounded on ~90k faces.

- [ ] **Step 1: Write the failing tests** (`Catalog`, `_unit`, `_near` from `tests.test_file_faces`):

```python
def test_find_more_ranks_unclassified_by_similarity(cat, repo):
    alice = cat.person("Alice"); cat.face(_unit(0), "user", alice)
    close = cat.face(_near(_unit(0), 0.9), "new")
    mid = cat.face(_near(_unit(0), 0.6, axis=501), "hidden")
    far = cat.face(_unit(1), "new")
    tiles = find_more(repo, alice)
    assert [t.face_id for t in tiles] == [close, mid]
    assert tiles[0].score == pytest.approx(0.9, abs=0.01)

def test_find_more_includes_unnamed_groups_not_named_people(cat, repo): ...  # face in unnamed group: in; face 'auto' of Bob (named): out; face in hidden group: out
def test_find_more_skips_user_ignored_rejected_and_own(cat, repo): ...      # each excluded case
def test_find_more_limit(cat, repo): ...                                     # 5 matches, limit=3 → best 3
def test_find_more_without_examples_is_empty(cat, repo): ...                 # unnamed group with centroid NULL → []
def test_person_examples_matches_filing_inputs(cat, repo): ...               # for a named person with user faces + reference_centroid, equals that person's Candidate.examples from load_filing_inputs
```

- [ ] **Step 2: Run** `uv run pytest tests/test_people_find_more.py -v` — expected: FAIL (import error).
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `uv run pytest tests/test_people_find_more.py tests/test_file_faces.py -v` — expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Rank unclassified faces by likeness to a person"`

---

### Task 5: Find more on the person page

**Files:**
- Modify: `smart_gallery/gui/people/person_view.py`, `docs/USER_GUIDE.md` (People section), `CLAUDE.md` (Face pipeline step 3: undo; one line on Find more)
- Test: `tests/gui/test_people_view.py`

**Interfaces:**
- Consumes: Task 4 `model.find_more`.
- Produces on `PersonPage`:
  - `find_more_button: QPushButton` (`FIND_MORE = "Find more"`) in the header, beside `library_button`, shown for any person
  - `review_mode: Literal["suggestions", "more"]`; `find_more() -> None` switches the review page to `"more"`, sets the title `MORE_TITLE = "Could this be {name}?"` and runs `find_more` through a dedicated `RepoQuery` (`self._more_query`); while it runs the title reads `SEARCHING = "Searching…"`; empty result shows `NO_MORE = "No more faces that look like {name}."` in the title
  - `_review()` (the existing suggestions strip button) sets `review_mode = "suggestions"` and title `REVIEW_TITLE`
  - ✓/✕/✓ all/✕ all keep using `_decide` on `review_grid` unchanged (so they emit `confirm`/`reject` for the shown faces)
  - `_on_data`: only `review_grid.set_tiles(suggestions, …)` when `review_mode == "suggestions"`; in `"more"` mode, if the review page is showing, re-run `find_more()` instead
  - `show_person` with a different person leaves `"more"` mode (back to `"suggestions"`)

- [ ] **Step 1: Write the failing tests**:

```python
def test_find_more_shows_ranked_faces(qtbot, repo, cat): ...
    # catalog: Alice with a user face at _unit(0) and two 'new' faces near it (use real embeddings, not _BLOB);
    # page.find_more(); qtbot.waitUntil(review page current and grid has 2 tiles); order by score
def test_find_more_confirm_emits_label(qtbot, repo, cat): ...
    # select a tile, click confirm_button → labels signal carries LabelEntry("confirm", (fid,), person_id=alice)
def test_find_more_survives_reload(qtbot, repo, cat): ...
    # in "more" mode call page.show_person(alice) again (what catalog_updated does) → grid still holds find_more results, not suggestions
def test_find_more_empty_message(qtbot, repo, cat): ...   # no candidates → title == NO_MORE.format(name="Alice")
def test_review_button_returns_to_suggestions(qtbot, repo, cat): ...
```
  (`tests/gui/test_people_view.py`'s `Cat.face` writes an 8-byte dummy embedding; give it an optional `emb` argument, or build these catalogs with `tests.test_file_faces.Catalog`.)

- [ ] **Step 2: Run** `uv run pytest tests/gui/test_people_view.py -v` — expected: new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `uv run pytest tests/gui -v` then the full suite `uv run pytest -m "not exiftool"` — expected: PASS (faces-marked tests skip when the extra is missing).
- [ ] **Step 5: Document** Undo and Find more in `docs/USER_GUIDE.md` (People section) and `CLAUDE.md`; lint/format touched files.
- [ ] **Step 6: Commit** `git commit -m "Add Find more to the person page"`

---

## Not in this plan

- **The re-filing cascade itself** (rejecting a few look-alikes strips a person whose only examples are a handful of confirmed faces: `filing.py` rule 3, `worse = best_rej > best_ex`). Undo makes a mistake reversible, but a *correct* ✕ on look-alikes found with Find more can still strip many auto faces. That fix (and the user's point 3, using filed faces as examples) needs its own decision and plan.
- Undo for merge/rename/split/hide/delete (only face corrections are undoable).
