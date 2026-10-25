# Dashboard Storage Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Storage tree panel (folder ▸ type sizes) below the Photo Map
in the desktop Dashboard, remove the data tables, and make every panel
collapsible with remembered state.

**Architecture:** A no-Qt module (`dashboard/storage.py`) builds two
pre-sorted trees of plain dataclasses from the filtered catalog frame on the
worker thread, inside `load_dashboard`. A Qt widget (`gui/dashboard/storage_view.py`)
shows one tree at a time in a `QTreeView` with a share-bar delegate.
`DashboardView` hosts it in a new last panel. `Panel` gains a collapse
toggle. Collapse state and grouping persist in `app_data_dir()/dashboard.json`
through a small no-Qt store.

**Tech Stack:** Python 3.13, pandas, PySide6 (QtWidgets), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-25-dashboard-storage-panel-design.md`

## Global Constraints

- Every number comes from the catalog (`relpath`, `ext`, `size_bytes`). No file-system scan and no ExifTool.
- The tree is computed in `load_dashboard` (worker thread), never on the GUI thread.
- Colours come only from `smart_gallery/colors.py` or palette roles. `tests/unit/test_colors.py` fails on any hex literal outside `colors.py`.
- No-Qt modules (`dashboard/storage.py`, `gui/dashboard/state.py`) must not import PySide6.
- The Dashboard is read-only: no writes to the catalog and no migrations.
- Sizes are 1024-based and shown as B / KB / MB / GB / TB.
- The type label is the extension upper-cased without its dot. `.jpg` and `.jpeg` stay separate. A missing extension shows as `(none)`.
- Type colours: `colors.categorical()` ranked by the type's total bytes, so the biggest type gets the accent. Ranks ≥ `colors.CATEGORY_COUNT` get `colors.NEUTRAL`. Each type keeps the same colour in both groupings.
- Run `uv run ruff check` / `uv run black` only on the files you touch.
- Commit messages are imperative sentences with no `feat:` prefix (repo style), and end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Hw1b36QuAsw6T2cJpUw3wc
  ```

## Review Focus

1. **Folder names differing only in case** (`Canon/a.jpg`, `canon/b.jpg`). Windows treats these as one folder and `relpath` is `COLLATE NOCASE`, so expect one folder node named after the first spelling seen. The test is in Task 1.
2. **Mixed-case extensions** (`.JPG` next to `.jpg`). Expect one `JPG` type row. The test is in Task 1.
3. **`size_bytes` NULL / NaN** (a row stat'd without a size). Expect it to count as a file with 0 bytes, with no crash and no NaN in the totals. The test is in Task 1.
4. **A corrupt or hand-edited `dashboard.json`** (invalid JSON, `collapsed` not a list, unknown grouping). Expect the defaults: everything expanded, grouping `folder`. The test is in Task 3.
5. **Drive unplugged or folder deleted after the last sync**, then a double-click on a row. Expect no Explorer launch, a "Folder not found" caption, and no exception. The test is in Task 5.

---

### Task 1: Storage tree data (no Qt)

**Files:**
- Create: `smart_gallery/dashboard/storage.py`
- Test: `tests/unit/test_storage.py`

**Interfaces:**
- Produces:
  ```python
  FOLDER, TYPE = "folder", "type"           # StorageNode.kind
  NO_EXT = "(none)"

  @dataclass
  class StorageNode:
      name: str                 # folder name, or type label ("CR3")
      kind: str                 # FOLDER | TYPE
      relpath: str | None       # folder this row opens, POSIX, "" = root; None = no single folder
      bytes: int
      files: int
      ext: str | None = None    # type label for TYPE rows, else None
      children: list["StorageNode"] = field(default_factory=list)

  @dataclass(frozen=True)
  class StorageTree:
      by_folder: StorageNode    # root FOLDER node, relpath ""
      by_type: StorageNode      # root FOLDER node, relpath "", children are TYPE nodes
      type_colors: dict[str, str]   # type label -> "#RRGGBB"
      total_bytes: int
      total_files: int

  def type_label(ext: str | None) -> str
  def format_bytes(n: int) -> str
  def build_storage(df: pd.DataFrame, root_name: str) -> StorageTree | None   # None for an empty frame
  ```

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_storage.py`. Build frames directly with `pd.DataFrame({"relpath": [...], "ext": [...], "size_bytes": [...]})`.

```python
def test_type_label():
    assert storage.type_label(".cr3") == "CR3"
    assert storage.type_label(".JPG") == "JPG"
    assert storage.type_label(".jpeg") == "JPEG"
    assert storage.type_label("") == storage.NO_EXT
    assert storage.type_label(None) == storage.NO_EXT

def test_format_bytes():
    assert storage.format_bytes(0) == "0 B"
    assert storage.format_bytes(1023) == "1023 B"
    assert storage.format_bytes(1024) == "1.00 KB"
    assert storage.format_bytes(1536 * 1024) == "1.50 MB"
    assert storage.format_bytes(int(45.6 * 1024**3)) == "45.6 GB"
    assert storage.format_bytes(912 * 1024**3) == "912 GB"
    assert storage.format_bytes(int(1.82 * 1024**4)) == "1.82 TB"

def test_folder_tree_totals_and_order():
    # Canon/2025: a.cr3 300, a.jpg 100; Canon/2024: b.cr3 50; Drone/c.mp4 500; top.txt 7
    tree = storage.build_storage(df, "E:")
    root = tree.by_folder
    assert (root.name, root.relpath, root.bytes, root.files) == ("E:", "", 957, 5)
    assert [c.name for c in root.children] == ["Drone", "Canon", "TXT"]   # folders first, then types, each biggest first
    canon = root.children[1]
    assert (canon.bytes, canon.files, canon.relpath) == (450, 3, "Canon")
    assert [c.name for c in canon.children] == ["2025", "2024"]           # Canon has no own files -> no type rows
    y25 = canon.children[0]
    assert [(c.kind, c.name, c.bytes, c.relpath) for c in y25.children] == [
        ("type", "CR3", 300, "Canon/2025"), ("type", "JPG", 100, "Canon/2025")]
    assert root.children[2].relpath == ""                                  # root-level file's type row opens the root

def test_type_tree():
    tree = storage.build_storage(df, "E:")                                # same frame as above
    types = tree.by_type.children
    assert [(t.name, t.bytes, t.files, t.relpath) for t in types] == [
        ("MP4", 500, 1, None), ("CR3", 350, 2, None), ("JPG", 100, 1, None), ("TXT", 7, 1, None)]
    cr3 = types[1]
    assert [c.name for c in cr3.children] == ["Canon"]
    assert [(c.name, c.bytes, c.relpath) for c in cr3.children[0].children] == [
        ("2025", 300, "Canon/2025"), ("2024", 50, "Canon/2024")]
    assert all(c.kind == "folder" for c in cr3.children)                  # no type rows below a type

def test_type_colors_ranked_by_bytes():
    tree = storage.build_storage(df, "E:")
    ranks = colors.categorical()
    assert tree.type_colors == {"MP4": ranks[0], "CR3": ranks[1], "JPG": ranks[2], "TXT": ranks[3]}
    assert tree.type_colors["MP4"] == colors.ACCENT

def test_types_past_category_count_are_neutral():
    n = colors.CATEGORY_COUNT + 1
    many = pd.DataFrame({"relpath": [f"a/f.e{i}" for i in range(n)],
                         "ext": [f".e{i}" for i in range(n)],
                         "size_bytes": [100 - i for i in range(n)]})        # .e0 biggest
    tree = storage.build_storage(many, "E:")
    assert tree.type_colors["E0"] == colors.ACCENT
    assert tree.type_colors[f"E{n - 1}"] == colors.NEUTRAL

def test_extensionless_files():
    tree = storage.build_storage(pd.DataFrame({"relpath": ["a/README"], "ext": [""], "size_bytes": [5]}), "E:")
    assert tree.by_folder.children[0].children[0].name == storage.NO_EXT

def test_folders_merge_case_insensitively():          # Review Focus 1
    # "Canon/a.jpg" 10 and "canon/b.jpg" 20
    root = storage.build_storage(df, "E:").by_folder
    assert [(c.name, c.bytes, c.files) for c in root.children] == [("Canon", 30, 2)]

def test_extensions_merge_case_insensitively():       # Review Focus 2
    # "a/x.JPG" 10 and "a/y.jpg" 20
    folder = storage.build_storage(df, "E:").by_folder.children[0]
    assert [(c.name, c.bytes, c.files) for c in folder.children] == [("JPG", 30, 2)]

def test_missing_size_counts_as_zero():               # Review Focus 3
    # sizes [10, None]
    tree = storage.build_storage(df, "E:")
    assert (tree.total_bytes, tree.total_files) == (10, 2)
    assert isinstance(tree.total_bytes, int)

def test_empty_frame_is_none():
    assert storage.build_storage(pd.DataFrame(columns=["relpath", "ext", "size_bytes"]), "E:") is None
```

- [ ] **Step 2: Run the tests to verify they fail.**
  Run: `uv run pytest tests/unit/test_storage.py -v`
  Expected: FAIL with `ModuleNotFoundError: smart_gallery.dashboard.storage`.

- [ ] **Step 3: Implement `smart_gallery/dashboard/storage.py`.**
  First aggregate the frame with one pandas `groupby` on (lower-cased folder key, type label), summing `size_bytes.fillna(0)` and counting rows. Then build both trees from those groups rather than per file, so 100k rows stay fast. The folder of a file is its `relpath`'s POSIX parent, with `""` for the root. The display name of a folder is the first spelling seen, and its key is lower-cased. Ordering: in a folder, subfolders come first, then type rows, and each group is sorted by bytes descending with name ascending as the tiebreak. `format_bytes` uses three significant figures: below 10 units show 2 decimals, below 100 show 1 decimal, otherwise 0 decimals. Whole bytes are shown as `"N B"`.

- [ ] **Step 4: Run the tests to verify they pass.**
  Run: `uv run pytest tests/unit/test_storage.py -v`
  Expected: all PASS.

- [ ] **Step 5: Lint, format, and commit.**
  ```bash
  uv run ruff check smart_gallery/dashboard/storage.py tests/unit/test_storage.py
  uv run black smart_gallery/dashboard/storage.py tests/unit/test_storage.py
  git add smart_gallery/dashboard/storage.py tests/unit/test_storage.py
  git commit   # "Compute storage trees by folder and file type"
  ```

---

### Task 2: Storage in `load_dashboard`, and drop the data tables

**Files:**
- Modify: `smart_gallery/gui/dashboard/model.py` (`DashboardData`, `load_dashboard`)
- Modify: `smart_gallery/gui/dashboard/view.py:1-27, 68-98, 191-199, 241-250, 289-292, 376-378` (tables, `DataFrameModel`, splitter, `_show_message`)
- Test: `tests/unit/test_dashboard_page.py`, `tests/gui/test_dashboard_view.py`

**Interfaces:**
- Consumes: `storage.build_storage(df, root_name) -> StorageTree | None` (Task 1).
- Produces: `DashboardData.storage: StorageTree | None`. The field `DashboardData.split` is removed. `DashboardView.tables` and `DashboardView.tabs` no longer exist.

- [ ] **Step 1: Update the tests.**
  - In `tests/unit/test_dashboard_page.py::test_load_dashboard_computes_everything`, replace `result.split` with `fi, fv, fo = figures.split_media(figures.load_catalog_df(repo.db_path))`, then add:
    ```python
    assert result.storage.total_files == 4
    assert result.storage.by_folder.name == repo.db_path.parent.parent.name   # the drive root's folder name
    ```
  - Add `test_load_dashboard_empty_catalog_has_no_storage` (empty repo → `result.storage is None`).
  - In `tests/gui/test_dashboard_view.py::test_dashboard_native_panels`, delete the two `view.tables[...]` asserts and add:
    ```python
    assert not view.findChildren(QTableView)
    assert not view.findChildren(QTabWidget)
    ```

- [ ] **Step 2: Run the tests to verify they fail.**
  Run: `uv run pytest tests/unit/test_dashboard_page.py tests/gui/test_dashboard_view.py -v`
  Expected: FAIL. `DashboardData` has no attribute `storage`, and a `QTableView` is still found.

- [ ] **Step 3: Implement.**
  - In `model.py`: remove `split`, and add `storage: StorageTree | None` as the last field. In `load_dashboard`, set `root = config.drive_root_of(db_path)` and call `build_storage(df, root.name or root.as_posix())`. `drive_root_of` may only be called here, on the worker thread, because it resolves paths.
  - In `view.py`: delete `TABS`, `DataFrameModel`, the `QTabWidget`/`QTableView` construction, and the `charts_split` splitter. `self.stack`'s first page is now `top` directly. Remove the table resets from `_on_data` and `_show_message`, the `pandas` import, and the unused Qt imports. Update the module docstring ("…the photo map, storage").

- [ ] **Step 4: Run the tests to verify they pass.**
  Run: `uv run pytest tests/unit/test_dashboard_page.py tests/gui/test_dashboard_view.py -v`
  Expected: all PASS.

- [ ] **Step 5: Lint, format, and commit.** Use ruff/black on the four files. Message: "Drop the Dashboard data tables and compute storage on load".

---

### Task 3: Dashboard state store (no Qt)

**Files:**
- Create: `smart_gallery/gui/dashboard/state.py`
- Test: `tests/unit/test_dashboard_state.py`

**Interfaces:**
- Produces:
  ```python
  STATE_FILE = "dashboard.json"
  GROUPINGS = ("folder", "type")

  @dataclass
  class DashboardState:
      collapsed: set[str] = field(default_factory=set)   # panel titles
      grouping: str = "folder"

  def load_state(path: Path) -> DashboardState    # never raises; defaults on any problem
  def save_state(path: Path, state: DashboardState) -> None   # atomic: write .tmp then os.replace
  ```
  The JSON shape is `{"collapsed": [sorted titles], "grouping": "folder"}`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_round_trip(tmp_path):
    path = tmp_path / "dashboard.json"
    save_state(path, DashboardState({"Photo Map", "Storage"}, "type"))
    assert load_state(path) == DashboardState({"Photo Map", "Storage"}, "type")
    assert not list(tmp_path.glob("*.tmp"))

def test_missing_file_is_default(tmp_path):
    assert load_state(tmp_path / "nope.json") == DashboardState()

@pytest.mark.parametrize("text", ["{not json", "[]", '{"collapsed": "Storage"}',
                                  '{"grouping": "size"}', '{"collapsed": [1, 2]}'])
def test_bad_file_is_default(tmp_path, text):      # Review Focus 4
    path = tmp_path / "dashboard.json"
    path.write_text(text, encoding="utf-8")
    state = load_state(path)
    assert state.grouping == "folder"
    assert all(isinstance(t, str) for t in state.collapsed)

def test_module_imports_no_qt():
    # run `import smart_gallery.gui.dashboard.state; import sys; assert "PySide6" not in sys.modules`
    # in a subprocess, like test_core_import_without_pyside does
```

  For a bad file, `collapsed` falls back to an empty set and `grouping` falls back to `"folder"`, each independently of the other.

- [ ] **Step 2: Run the tests to verify they fail.** Run: `uv run pytest tests/unit/test_dashboard_state.py -v`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `state.py`.** Use `json` and `dataclasses` only. Note: `gui/dashboard/__init__.py` is empty, so importing this module loads no Qt.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: PASS.

- [ ] **Step 5: Lint, format, and commit.** Message: "Remember Dashboard panel state in dashboard.json".

---

### Task 4: Collapsible panels

**Files:**
- Modify: `smart_gallery/gui/dashboard/view.py` (`Panel`, `DashboardView.__init__`, `_show_map`)
- Test: `tests/gui/test_dashboard_view.py`

**Interfaces:**
- Consumes: `load_state`, `save_state`, `DashboardState`, `STATE_FILE` (Task 3).
- Produces:
  - `Panel.title: QToolButton` (text `"▾ {title}"` when expanded, `"▸ {title}"` when collapsed; flat; bold at 1.3× like today).
  - `Panel.collapsed: bool` (property).
  - `Panel.set_collapsed(collapsed: bool) -> None`.
  - `Panel.collapsed_changed = Signal(bool)`.
  - `DashboardView.state: DashboardState`, `DashboardView.state_path: Path` (`app_data_dir() / STATE_FILE`).

- [ ] **Step 1: Write the failing tests** in `tests/gui/test_dashboard_view.py`:

```python
def test_panel_collapses_to_its_title(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1.jpg", focal_length=50)])
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)
    panel = view.panels[FILES_PANEL]
    panel.title.click()
    assert panel.collapsed and panel.isVisibleTo(view)
    assert not panel.body.isVisibleTo(view)
    assert panel.title.text() == "▸ " + FILES_PANEL
    panel.title.click()
    assert not panel.collapsed and panel.body.isVisibleTo(view)

def test_collapse_survives_reload_and_restart(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1.jpg", focal_length=50)])
    view, _ = _view(qtbot, repo.db_path)
    first = _loaded(qtbot, view)
    view.panels[SETTINGS_PANEL].title.click()
    view.reload()
    qtbot.waitUntil(lambda: view.data is not first)
    assert not view.panels[SETTINGS_PANEL].body.isVisibleTo(view)   # new body stays hidden
    again, _ = _view(qtbot, repo.db_path)                            # same SG_APP_DATA
    _loaded(qtbot, again)
    assert again.panels[SETTINGS_PANEL].collapsed

def test_empty_panel_stays_hidden_even_if_collapsed(qtbot, repo, make_item):
    repo.upsert_many([_video(make_item, "v/1.mp4")])
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)
    view.panels[SETTINGS_PANEL].set_collapsed(True)
    assert not _shown(view, SETTINGS_PANEL)
```

- [ ] **Step 2: Run the tests to verify they fail.** Run: `uv run pytest tests/gui/test_dashboard_view.py -v -k "collapse"`. Expected: FAIL (`QLabel` has no `click`).

- [ ] **Step 3: Implement.** The title becomes a `QToolButton` whose click toggles collapse. `set_collapsed` sets the body's visibility to `not collapsed` and updates the chevron text. `set_body` applies the current collapsed state to the new body, and still hides the whole panel when the body is `None`. `_show_map` keeps toggling panel visibility only. The map body is never deleted, so collapsing simply hides it. In `DashboardView.__init__`, load the state, call `set_collapsed(title in state.collapsed)` on each panel, and connect `collapsed_changed` so that it updates `state.collapsed` and calls `save_state`.

- [ ] **Step 4: Run the whole dashboard GUI file to verify it passes.** Run: `uv run pytest tests/gui/test_dashboard_view.py -v`. Expected: all PASS.

- [ ] **Step 5: Lint, format, and commit.** Message: "Make every Dashboard panel collapsible".

---

### Task 5: Storage tree widget

**Files:**
- Create: `smart_gallery/gui/dashboard/storage_view.py`
- Modify: `smart_gallery/gui/library/viewer.py:140` (add `open_folder` next to `show_in_explorer`)
- Test: `tests/gui/test_storage_view.py`

**Interfaces:**
- Consumes: `StorageTree`, `StorageNode`, `FOLDER`, `TYPE`, `format_bytes` (Task 1).
- Produces:
  ```python
  # viewer.py
  def open_folder(path: Path) -> None          # explorer <path> on win32, else QDesktopServices.openUrl

  # storage_view.py
  COLUMNS = ("Name", "Size", "Share of parent", "Files")
  GROUPING_LABELS = {"folder": "Folder ▸ Type", "type": "Type ▸ Folder"}
  CAPTION = "{size} in {files:,} files"
  NOT_FOUND = "Folder not found: {path}"
  TREE_HEIGHT = 500
  NODE_ROLE = Qt.ItemDataRole.UserRole + 1      # the StorageNode on column 0
  SHARE_ROLE = Qt.ItemDataRole.UserRole + 2     # float 0..1 on column 2: bytes / parent bytes (root 1.0)

  class StorageView(QWidget):
      grouping_changed = Signal(str)
      caption: QLabel
      tree: QTreeView
      buttons: dict[str, QPushButton]           # keyed by grouping, checkable, exclusive
      def set_tree(self, tree: StorageTree, root: Path) -> None
      def set_grouping(self, grouping: str) -> None     # rebuilds the model; no signal when unchanged
      def grouping(self) -> str
      def open_node(self, index: QModelIndex) -> None   # what double-click and the menu call
      def context_menu(self, index: QModelIndex) -> QMenu  # "Open in Explorer", disabled when relpath is None
  ```

- [ ] **Step 1: Write the failing tests.** Build a `StorageTree` with `build_storage` from a small frame (`Canon/2025/a.cr3` 300, `Canon/2025/a.jpg` 100, `Drone/c.mp4` 500), with `root = tmp_path`. Create `tmp_path / "Canon" / "2025"` on disk so it exists. Monkeypatch `storage_view.open_folder` with a recorder.

```python
def test_folder_grouping_rows(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    m = view.tree.model()
    assert [m.headerData(i, Qt.Horizontal) for i in range(4)] == list(COLUMNS)
    root = m.index(0, 0)
    assert view.tree.isExpanded(root)                          # root open one level
    assert [m.index(r, 0, root).data() for r in range(m.rowCount(root))] == ["Drone", "Canon"]
    assert m.index(0, 1, root).data() == "500 B"
    assert m.index(0, 3, root).data() == "1"
    assert view.caption.text() == "900 B in 3 files"

def test_share_column_is_fraction_of_parent(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    m = view.tree.model(); root = m.index(0, 0); drone = m.index(0, 2, root)
    assert drone.data() == "56%"
    assert drone.data(SHARE_ROLE) == pytest.approx(500 / 900)
    assert m.index(0, 2).data(SHARE_ROLE) == 1.0                     # root
    cr3 = <CR3 row under Canon/2025, column 0>
    assert cr3.data(Qt.DecorationRole) == QColor(tree.type_colors["CR3"])

def test_grouping_switch_rebuilds(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    with qtbot.waitSignal(view.grouping_changed) as sig:
        view.buttons["type"].click()
    assert sig.args == ["type"]
    root = view.tree.model().index(0, 0)
    assert view.tree.model().index(0, 0, root).data() == "MP4"

def test_double_click_folder_opens_it(...):       # Canon/2025 row -> recorder got tmp_path / "Canon/2025"
def test_type_row_opens_its_folder(...):          # CR3 under Canon/2025 -> tmp_path / "Canon/2025"
def test_type_top_level_menu_is_disabled(...):    # type grouping, MP4 row: context_menu(index).actions()[0].isEnabled() is False
def test_missing_folder_shows_message(qtbot, view, tree, tmp_path, opened):   # Review Focus 5
    view.set_tree(tree, tmp_path)                 # "Drone" was never created on disk
    view.open_node(<Drone index>)
    assert opened == []
    assert view.caption.text() == NOT_FOUND.format(path=tmp_path / "Drone")
```

- [ ] **Step 2: Run the tests to verify they fail.** Run: `uv run pytest tests/gui/test_storage_view.py -v`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `storage_view.py` and `open_folder`.**
  Use a `QStandardItemModel` with four columns, rebuilt from the chosen root node in the order it is already sorted, and keep `setSortingEnabled(False)`. Put the `StorageNode` under `NODE_ROLE` on column 0. Type rows get `QColor(tree.type_colors[node.ext])` as `DecorationRole`. A `QStyledItemDelegate` on column 2 paints the share bar and then the percentage. The bar uses `palette(highlight)` for folders and the type's colour for type rows, so there are no hex literals. Size and Files are right-aligned. The tree view has a fixed height of `TREE_HEIGHT`, with `doubleClicked` connected to `open_node` and `customContextMenuRequested` connected to `context_menu(...).exec(...)`. `open_node` resolves `root / node.relpath`, sets `NOT_FOUND` on the caption when that path is not a directory, and otherwise calls `open_folder`. The caption shows `CAPTION.format(size=format_bytes(tree.total_bytes), files=tree.total_files)` and is reset on every `set_tree`.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: PASS.

- [ ] **Step 5: Lint, format, and commit.** Message: "Add the storage tree widget".

---

### Task 6: Storage panel in the Dashboard

**Files:**
- Modify: `smart_gallery/gui/dashboard/view.py` (`STORAGE_PANEL`, `PANELS`, `__init__`, `_on_data`)
- Test: `tests/gui/test_dashboard_view.py`

**Interfaces:**
- Consumes: `StorageView` (Task 5), `DashboardData.storage` (Task 2), `DashboardState.grouping` / `save_state` (Tasks 3–4).
- Produces: `STORAGE_PANEL = "Storage"`, with `PANELS` ending `(…, MAP_PANEL, STORAGE_PANEL)`, and `DashboardView.storage_view: StorageView`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_storage_panel_is_last_and_filled(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1.jpg"), _video(make_item, "b/v.mp4")])
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)
    assert PANELS[-1] == STORAGE_PANEL
    column = view.panels[MAP_PANEL].parentWidget().layout()
    assert column.indexOf(view.panels[STORAGE_PANEL]) == column.indexOf(view.panels[MAP_PANEL]) + 1
    assert _shown(view, STORAGE_PANEL)
    assert view.storage_view.caption.text() == "1.91 MB in 2 files"   # make_item default 1_000_000 bytes each

def test_storage_grouping_is_remembered(qtbot, repo, make_item):
    repo.upsert_many([make_item("a/1.jpg")])
    view, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, view)
    view.storage_view.buttons["type"].click()
    again, _ = _view(qtbot, repo.db_path)
    _loaded(qtbot, again)
    assert again.storage_view.grouping() == "type"
```

  Then extend `test_dashboard_empty_catalog`. Since `STORAGE_PANEL` is in `PANELS`, its existing `not any(_shown…)` assert already covers the empty case. Also extend `test_dashboard_native_panels`, whose `all(_shown…)` already covers the shown case. Add `STORAGE_PANEL` to the `test_dashboard_videos_only_hides_image_panels` expectations as shown.

- [ ] **Step 2: Run the tests to verify they fail.** Run: `uv run pytest tests/gui/test_dashboard_view.py -v`. Expected: `ImportError: STORAGE_PANEL`.

- [ ] **Step 3: Implement.** Build one `StorageView` in `__init__`, as is already done for the map, and set it as the Storage panel's body. Call `set_grouping(self.state.grouping)`, and connect `grouping_changed` so it updates `state.grouping` and calls `save_state`. In `_on_data`, do `panel.setVisible(result.storage is not None)`, and when the tree is present call `storage_view.set_tree(result.storage, self.ctx.current.root)`.

- [ ] **Step 4: Run the full suite.**
  Run: `uv run pytest -m "not exiftool and not faces"`
  Expected: all PASS, including `tests/unit/test_colors.py` (no hex literals).

- [ ] **Step 5: Lint, format, and commit.** Message: "Show the Storage panel below the Photo Map".

- [ ] **Step 6: Check it by hand.** Run `uv run smart-gallery gui` and open a real gallery. Check that Storage appears under the map with the biggest folder first, that the switch flips the grouping, that double-clicking a row opens Explorer, and that folding panels survives an app restart.
