# UI Consistency Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every page of the desktop app share one button hierarchy, page skeleton, set of components, wording and number format.

**Architecture:** A Qt-free `smart_gallery/format.py` for numbers, a small component kit in `smart_gallery/gui/ui/` (section header, empty state, action bar, panel footer, disclosure, status pill), new theme roles (`danger`, segment and card styles), then each page moved onto them one commit at a time.

**Tech Stack:** Python 3.13, PySide6, pytest + pytest-qt (offscreen).

**Spec:** `docs/superpowers/specs/2026-11-08-ui-consistency-design.md`

## Global Constraints

- Branch `ui-consistency` off `library-import-export` (that branch is not merged yet).
- Colours only from `smart_gallery/colors.py` via `theme`; style sheets use `palette(...)` for fills and `theme.ACCENT` for lines, never literals.
- Always dark; no light-mode branches.
- At most one *visible* `primaryButton` per page or window.
- Spacing scale 4 / 8 / 12 / 16 / 24 px; page side margin 24 px (= `HEADER_MARGIN`).
- Side panels: 260 px (tree, people list, command list), 320 px filter panel, 420 px Storage tree.
- Copy: photos / videos / other files; "files" never "items"; sentence case.
- Ruff + Black only on touched files. Run `uv run pytest tests/gui tests/unit` before each commit.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Pages built while no gallery is open, or with a disconnected drive: the `EmptyState` must show, and the header summary must be empty, not stale from the previous gallery.
2. A primary button hidden by a stack (Library's Export… sits in the filter panel, which can be collapsed): the one-primary rule counts visible buttons only.
3. Sizes at unit boundaries (0 B, 1023 B, 1024 B, 999.5 MB) and counts of 1 (`1 file`, not `1 files`).
4. History records with unreadable timestamps or unknown statuses (old `history.jsonl` lines): show the raw text and a neutral pill, never crash.
5. Switching gallery while a Storage tool has selections: the renamed leave-guard text still appears and still blocks.

---

### Task 1: One number formatter

**Files:**
- Create: `smart_gallery/format.py`
- Modify: `smart_gallery/storage/tree.py:49` (`format_bytes` → re-export of `format_size`), `smart_gallery/gui/viewer/info.py:55` (`format_size` → wrap `format.format_size`, keep `EMPTY` for `None`), `smart_gallery/gui/history/view.py:41` (duration via `format_duration`), `smart_gallery/dashboard/figures.py:37` (`metrics`), `smart_gallery/gui/library/view.py:672`
- Test: `tests/unit/test_format.py`, update `tests/unit/test_dashboard_figures.py`, `tests/gui/test_dashboard_view.py`

**Interfaces:**
- Produces: `format_size(num_bytes: int) -> str`, `format_count(n: int, noun: str, plural: str | None = None) -> str`, `format_duration(seconds: float) -> str`

- [ ] **Step 1: Write the failing tests**

```python
from smart_gallery.format import format_count, format_duration, format_size

def test_size_three_significant_figures():
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.00 KB"
    assert format_size(int(7.43 * 2**20)) == "7.43 MB"
    assert format_size(int(45.6 * 2**30)) == "45.6 GB"
    assert format_size(int(781.2 * 2**30)) == "781 GB"

def test_count_singular_and_plural():
    assert format_count(1, "file") == "1 file"
    assert format_count(84355, "file") == "84,355 files"
    assert format_count(2, "person", "people") == "2 people"

def test_duration():
    assert format_duration(12) == "12s"
    assert format_duration(243) == "4m 3s"
    assert format_duration(57299) == "15h 54m 59s"
```

And in `test_dashboard_figures.py`, the metric labels become `["Total files", "Photos", "Videos", "Total size", "Video length"]` with `m["Total size"] == format_size(<fixture bytes>)`.

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_format.py tests/unit/test_dashboard_figures.py -v` — expect FAIL (module missing / old labels).
- [ ] **Step 3: Implement** `format.py` with the rules from `storage.tree.format_bytes` (it already does three significant figures); point `tree.format_bytes` at it. In `metrics()`, sum `size_bytes` when the column exists (else `size (MB)` × 1048576) and label as above. Library header text becomes `f"{format_count(count, noun)} · {format_size(size)}"` (noun `file`, not `item`).
- [ ] **Step 4: Run** `uv run pytest tests/unit tests/gui/test_dashboard_view.py tests/gui/test_library_view.py tests/gui/test_viewer_info.py tests/gui/test_history_view.py -v` — PASS (update expected strings that said `items` or `MB`).
- [ ] **Step 5: Commit** `git commit -m "One size, count and duration format across the app"`

### Task 2: Button roles

**Files:**
- Modify: `smart_gallery/gui/theme.py` (add `DANGER_STYLE`, `danger()`, `SEGMENT_STYLE`), `smart_gallery/gui/segment.py` (apply `SEGMENT_STYLE`, object name `segment`), `smart_gallery/gui/filters_panel.py:153` (drop `theme.primary` on Clear all filters), `smart_gallery/gui/library/view.py` (Export… plain)
- Test: `tests/gui/test_button_roles.py`, extend `tests/unit/test_colors.py`

**Interfaces:**
- Produces: `theme.danger(button: QPushButton) -> QPushButton` (object name `dangerButton`); `segment(...)` unchanged signature.

- [ ] **Step 1: Write the failing tests**

```python
def visible_primaries(widget):
    return [b for b in widget.findChildren(QPushButton)
            if b.objectName() == "primaryButton" and b.isVisibleTo(widget)]

@pytest.mark.parametrize("area", [a.id for a in AREAS])
def test_at_most_one_primary_per_page(qtbot, area):
    win = MainWindow(); qtbot.addWidget(win); win.show_area(area)
    assert len(visible_primaries(win._pages[area])) + len(
        visible_primaries(win.header_controls.currentWidget())) <= 1

def test_danger_button_has_no_fill_until_hover():
    b = theme.danger(QPushButton("x"))
    assert b.objectName() == "dangerButton"
    assert "background: transparent" in b.styleSheet()

def test_checked_segment_is_not_a_fill():
    assert "palette(highlight)" not in theme.SEGMENT_STYLE
```

Header controls belong to their page, so count both but dedupe if the page owns them. In `test_colors.py`: `ERROR` on `DARK_PALETTE["Window"]` contrast ≥ 3.0.

- [ ] **Step 2: Run** `uv run pytest tests/gui/test_button_roles.py -v` — FAIL on Library (three primaries).
- [ ] **Step 3: Implement.** `DANGER_STYLE`: transparent background, 1px `ERROR` border, `ERROR` text, 4px radius, `7px 20px` padding; hover fills `ERROR` with `ON_ACCENT` text; disabled same as primary's disabled. `SEGMENT_STYLE`: checked → `background: rgba(ACCENT_FILL, 60)` (build with `theme.qcolor` like `filter_widgets._accent_rgba`), `border-bottom: 2px solid ACCENT`, normal text colour.
- [ ] **Step 4: Run** the same tests plus `tests/gui/test_filters_panel.py tests/gui/test_library_view.py tests/gui/test_storage_view.py` — PASS.
- [ ] **Step 5: Commit** `"Button roles: one primary per page, danger outline, tinted segments"`

### Task 3: Shared icons

**Files:**
- Move: `smart_gallery/gui/viewer/icons.py` → `smart_gallery/gui/icons.py` (update imports in `viewer/`; keep `spinner_angle`/`dot_position` with it)
- Modify: `smart_gallery/gui/library/view.py` (`descending` QToolButton: not checkable; click flips the order; icon `sort-down`/`sort-up`; tooltip `Newest first`/`Oldest first`-style from `SORTS`)
- Test: `tests/gui/test_icons.py`, `tests/gui/test_library_view.py`

**Interfaces:**
- Produces: `icons.icon(name, color, size=18)` with new names `sort-up`, `sort-down`, `refresh`, `undo`, `browse`.

- [ ] **Step 1: Failing tests:** every name in `icons._PAINTERS` (or the dict's current name) renders a non-null 18 px `QIcon`; Library's sort toggle `isCheckable() is False`, and clicking it twice restores the original order.
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_icons.py tests/gui/test_library_view.py -v` — FAIL.
- [ ] **Step 3: Implement** the five painters in the same line style as `_chevron`/`_folder`; replace the direction toggle. Keep the sort state in a `self._descending: bool` attribute.
- [ ] **Step 4: Run** `uv run pytest tests/gui -k "icon or library or viewer" -v` — PASS.
- [ ] **Step 5: Commit** `"Shared icon set; sort direction is an icon button"`

### Task 4: Component kit and card group boxes

**Files:**
- Create: `smart_gallery/gui/ui/__init__.py`, `smart_gallery/gui/ui/metrics.py` (no Qt), `smart_gallery/gui/ui/widgets.py`
- Modify: `smart_gallery/gui/theme.py` (add `CARD_STYLE`), `smart_gallery/gui/app.py` (append `CARD_STYLE` to `WINDOW_STYLE`, drop its `QGroupBox` line), `smart_gallery/gui/form_window.py` (set `CARD_STYLE`)
- Test: `tests/gui/test_ui_kit.py`

**Interfaces:**
- Produces (`ui/metrics.py`): `SPACE = (4, 8, 12, 16, 24)`, `PAGE_MARGIN = 24`, `PAGE_TOP = 12`, `PAGE_BOTTOM = 16`, `SIDEBAR_WIDTH = 260`, `FILTER_WIDTH = 320`, `WIDE_SIDEBAR_WIDTH = 420`, `RADIUS = 6`, `FOOTER_HEIGHT = 40`.
- Produces (`ui/widgets.py`):
  - `SectionHeader(title: str, subtitle: str = "")` with `set_title(str)`, `set_subtitle(str)`; title `serif_font(HEADER_PT)`, subtitle muted (`PlaceholderText` role), hidden when empty.
  - `EmptyState()` with `show_text(text: str, hint: str = "", action_label: str | None = None, action: Callable[[], None] | None = None, icon: str | None = None)`; action button hidden unless `action_label`.
  - `ActionBar()` with `status: QLabel`, `note: QLabel` (muted, second line), `add_left(widget)`, `add_right(widget)`; right widgets end-aligned, left-to-right order kept.
  - `PanelFooter()` (a QWidget, fixed height `FOOTER_HEIGHT`, margins 8/0/8/0) with `add(widget)`.
  - `Disclosure(text: str, body: QWidget)` — the Export `command_toggle` behaviour (arrow right/down, body hidden until opened); `opened` property, `toggled = Signal(bool)`.
  - `status_pill(painter, rect, status: str)` plus `PILL_STATUSES = {"ok": ("done", KEEP), "failed": ("failed", ERROR), "cancelled": ("cancelled", NEUTRAL)}`; unknown status → its own text, `NEUTRAL`.

- [ ] **Step 1: Failing tests:**

```python
def test_empty_state_hides_action_without_label(qtbot):
    e = EmptyState(); qtbot.addWidget(e); e.show_text("Nothing")
    assert e.action.isHidden()
    e.show_text("Nothing", action_label="Go", action=lambda: None)
    assert not e.action.isHidden()

def test_action_bar_keeps_right_order(qtbot):
    bar = ActionBar(); qtbot.addWidget(bar)
    a, b = QPushButton("a"), QPushButton("b"); bar.add_right(a); bar.add_right(b)
    bar.show(); assert a.x() < b.x()

def test_disclosure_starts_closed(qtbot):
    body = QLabel("x"); d = Disclosure("Command", body); qtbot.addWidget(d)
    assert not d.opened and body.isHidden()

def test_unknown_status_pill_is_neutral():
    assert pill_style("weird") == ("weird", NEUTRAL)

def test_group_box_is_a_card():
    assert "border-radius" in theme.CARD_STYLE and "QGroupBox::title" in theme.CARD_STYLE
```

(`pill_style(status) -> tuple[str, str]` is the pure lookup `status_pill` uses.)
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_ui_kit.py -v` — FAIL.
- [ ] **Step 3: Implement.** `CARD_STYLE`: `QGroupBox { background: palette(base); border: 1px solid palette(mid); border-radius: 6px; margin-top: 0; padding: 28px 10px 10px 10px; font-family: SERIF[0]; font-size: SECTION_PT }` and `QGroupBox::title { subcontrol-origin: padding; subcontrol-position: top left; left: 10px; top: 6px; }`.
- [ ] **Step 4: Run** `uv run pytest tests/gui -v` — PASS (the filter panel, Export and Advanced now draw cards; check no test asserts the old look).
- [ ] **Step 5: Commit** `"UI kit: section header, empty state, action bar, footer, disclosure, pill; cards"`

### Task 5: Page shell — alignment, summary, widths

**Files:**
- Modify: `smart_gallery/gui/app.py` (wrap `self.stack` in a container with margins `PAGE_MARGIN, PAGE_TOP, PAGE_MARGIN, PAGE_BOTTOM`; add `self.summary` QLabel beside `self.title`; connect each page's optional `summary_changed`), every area root layout (`library/view.py`, `dashboard/view.py`, `storage/view.py`, `people/view.py`, `history/view.py`, `actions/view.py`: `setContentsMargins(0, 0, 0, 0)`)
- Test: `tests/gui/test_shell.py`

**Interfaces:**
- Consumes: `ui.metrics`.
- Produces: page protocol `summary_changed = Signal(str)` (optional); `MainWindow.summary: QLabel` shows the current page's last emitted text (kept per page in `self._summaries: dict[str, str]`), cleared on `gallery_changed`.

- [ ] **Step 1: Failing tests:** for every area, `win._pages[id].layout().contentsMargins()` is all zero and the stack container's left margin equals `HEADER_MARGIN`; a page emitting `summary_changed("3 files · 1 KB")` shows that text in `win.summary` only while that page is open; switching gallery clears it.
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_shell.py -v` — FAIL.
- [ ] **Step 3: Implement.** Summary label: muted palette like `eyebrow`, sans 10 pt, added to a row `[title] 12px [summary] stretch`, aligned `AlignBaseline`.
- [ ] **Step 4: Run** `uv run pytest tests/gui -v` — PASS.
- [ ] **Step 5: Commit** `"Page content lines up with the title; header summary line"`

### Task 6: Library and Stats

**Files:**
- Create: `smart_gallery/gui/collapsible_filters.py` (move `CollapsedStrip` from `library/view.py:337` and the hide/show logic)
- Modify: `smart_gallery/gui/library/view.py`, `smart_gallery/gui/dashboard/view.py`, `smart_gallery/gui/filters_panel.py` (`PANEL_WIDTH = FILTER_WIDTH`)
- Test: `tests/gui/test_library_view.py`, `tests/gui/test_dashboard_view.py`

**Interfaces:**
- Consumes: `EmptyState`, `PanelFooter`, `SectionHeader`, `ui.metrics`, `format_count/format_size`.
- Produces: `CollapsibleFilters(panel: FilterPanel, state_key: str)` exposing `strip: CollapsedStrip`, `set_shown(bool)`, `shown: bool`; Library and Stats each add `panel` and `strip` to their splitter. Shown state persists in the page's existing state file under `"filters_shown"`.

- [ ] **Step 1: Failing tests:** Stats has a « button that hides its filter panel and shows the strip; Library emits `summary_changed` with `"N files · S"` after rows load and has no `stats` label in `header_controls`; Library's message page is an `EmptyState`; `Include subfolders` sits in a `PanelFooter`; Stats panel titles are `SectionHeader`s (dashboard `Panel.title` keeps its click-to-collapse, so make the chevron button sit in the header's row).
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_library_view.py tests/gui/test_dashboard_view.py -v` — FAIL.
- [ ] **Step 3: Implement.** Splitter sizes `[SIDEBAR_WIDTH, FILTER_WIDTH, 0, rest]`. Library header controls: `Sort by`, sort box, direction, Sync options, Import… (primary last). Stats `_show_message` → `EmptyState.show_text`.
- [ ] **Step 4: Run** `uv run pytest tests/gui -v` — PASS.
- [ ] **Step 5: Commit** `"Library and Stats share collapsible filters, empty state and summary"`

### Task 7: Storage

**Files:**
- Create: `smart_gallery/gui/thumbs/placeholder.py` (move `_placeholder`/`_placeholder_for` from `library/view.py:102-211`)
- Modify: `smart_gallery/gui/storage/view.py`, `pairs_view.py`, `bursts_view.py`, `raw_view.py`, `library/view.py` (import the moved placeholder)
- Test: `tests/gui/test_storage_view.py`, `test_pairs_view.py`, `test_bursts_view.py`, `test_raw_view.py`

**Interfaces:**
- Consumes: `ActionBar`, `SectionHeader`, `EmptyState`, `theme.danger`, `format_*`.
- Produces: `placeholder_pixmap(kind: str, size: int) -> QPixmap` (`kind` in `image|video|other`).

Copy constants (exact):
- pairs: `FOOTER = "{n:,} selected · frees {size}"`, `FOOTER_NOTE = "Unselected pairs are left alone"`, `CLEAR = "Clear selection"`
- bursts: `FOOTER = "{n:,} selected · frees {size}"`, `FOOTER_NOTE = "Unselected bursts are left alone"`, `CLEAR = "Clear selection"`
- raw: `FOOTER = "{n:,} selected · {raw} of CR3 → about {jpeg} of JPEG · frees ~{freed}"`, `CLEAR = "Clear selection"`
- all three: `LEAVE_TITLE = "Selections not applied"`, `LEAVE = "You have {n:,} selections not applied yet. Discard them?"`
- storage view `CAPTION` removed; emits `summary_changed(f"{format_count(files, 'file')} · {format_size(size)}")`.

- [ ] **Step 1: Failing tests:** the constants above; each tool's move/convert button has object name `dangerButton`; each tool's title is a `SectionHeader` whose subtitle is the scope line; Bursts and RAW draw `placeholder_pixmap` for missing thumbnails (assert the delegate calls it — patch it and count calls); switching gallery with one selection still asks with the new `LEAVE` text (Review Focus 5); splitter starts at `WIDE_SIDEBAR_WIDTH` when no saved split.
- [ ] **Step 2: Run** `uv run pytest tests/gui -k "storage or pairs or bursts or raw" -v` — FAIL.
- [ ] **Step 3: Implement.** Each tool's footer becomes an `ActionBar` (`status` = FOOTER, `note` = FOOTER_NOTE, right: Clear selection, then the danger button). `CHECK_HINT` stays at the header row's right, muted.
- [ ] **Step 4: Run** `uv run pytest tests/gui -v` — PASS.
- [ ] **Step 5: Commit** `"Storage tools: same words, action bar, danger buttons, shared placeholder"`

### Task 8: People

**Files:**
- Modify: `smart_gallery/gui/people/view.py` (message page → `EmptyState`; `status` label → `summary_changed`; Undo gets the `undo` icon; header order Undo, Find new faces), `smart_gallery/gui/people/list_view.py` (Show hidden faces in a `PanelFooter`, list width `SIDEBAR_WIDTH`)
- Test: `tests/gui/test_people_view.py`

**Interfaces:**
- Consumes: `EmptyState`, `PanelFooter`, `icons.icon`.
- Keeps: `PeopleView.show_message(text, action_label=None, action=None)` signature (callers and tests use it), now delegating to `EmptyState.show_text`.

- [ ] **Step 1: Failing tests:** right after construction with no gallery, no visible empty `QPushButton` on the message page (the bug in the survey); "N photos not scanned yet" arrives via `summary_changed` (copy says photos, not images); `Show hidden faces` is inside a `PanelFooter`.
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_people_view.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/gui -k people -v` — PASS.
- [ ] **Step 5: Commit** `"People: empty state, summary line, footer"`

### Task 9: History

**Files:**
- Modify: `smart_gallery/gui/history/view.py`
- Test: `tests/gui/test_history_view.py`

**Interfaces:**
- Consumes: `status_pill`, `pill_style`, `EmptyState`, `ActionBar`.
- Produces: `format_start(started_at: str, now: datetime | None = None) -> str`.

- [ ] **Step 1: Failing tests:**

```python
NOW = datetime(2026, 11, 8, 12, 0)
def test_start_today_yesterday_else_date():
    assert format_start("2026-11-08T17:01:00", NOW) == "Today 17:01"
    assert format_start("2026-11-07T09:12:00", NOW) == "Yesterday 09:12"
    assert format_start("2026-11-01T09:12:00", NOW) == "2026-11-01 09:12"
    assert format_start("garbage", NOW) == "garbage"
```

Plus: status column paints a pill (a delegate on column 3; an unknown status like `"weird"` renders without error); header alignment is left; Started column resize mode `ResizeToContents`; the detail area shows an `EmptyState` until a row is selected, then the log.
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_history_view.py -v` — FAIL.
- [ ] **Step 3: Implement.** Detail = `QStackedWidget(EmptyState, QPlainTextEdit)`; footer `ActionBar` (status = `note`, right = Run again).
- [ ] **Step 4: Run** `uv run pytest tests/gui -k history -v` — PASS.
- [ ] **Step 5: Commit** `"History: status pills, readable start times, log on selection"`

### Task 10: Advanced

**Files:**
- Modify: `smart_gallery/gui/actions/model.py` (add `COMMAND_GROUPS`), `smart_gallery/gui/actions/view.py`
- Test: `tests/unit/test_actions_model.py` (create if absent; Qt-free), `tests/gui/test_actions_view.py`

**Interfaces:**
- Produces: `COMMAND_GROUPS: dict[str, dict[str, str]]` (group → {cli name: label}), and `command_label(name: str) -> str` (falls back to the CLI name).

Exact content:

```python
COMMAND_GROUPS = {
    "Catalog": {"init": "Create catalog", "sync": "Sync with disk", "import": "Import",
                "export": "Export", "report": "Report", "compare": "Compare catalogs",
                "rename": "Rename files"},
    "Mirror": {"pair": "Pair drives", "sync-mirror": "Sync mirror",
               "free-up": "Free up space", "bring-back": "Bring back"},
    "Files": {"convert-raw": "Convert RAW", "raw-to-jpeg": "RAW to JPEG",
              "recycle": "Move to Recycle Bin"},
    "Faces": {"scan-faces": "Scan faces", "find-faces": "Find faces",
              "file-faces": "Sort faces", "identify-faces": "Identify from samples",
              "cluster-faces": "Group faces", "label-faces": "Label faces",
              "undo-faces": "Undo face labels"},
    "People": {"people": "List people", "name-person": "Name person",
               "hide-person": "Hide person", "merge-persons": "Merge people",
               "split-person": "Split person", "delete-person": "Delete person"},
}
```

- [ ] **Step 1: Failing tests:** every name in `gui_forms()` appears in exactly one group (so a new CLI command fails the test until it is placed); the list shows group headings that cannot be selected; each command item's text is its label and its tooltip/second column is the CLI name; the page title is a `SectionHeader` (label, form help as subtitle); the list is `SIDEBAR_WIDTH` wide.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_actions_model.py tests/gui/test_actions_view.py -v` — FAIL.
- [ ] **Step 3: Implement.** Heading items: flags `Qt.ItemFlag.NoItemFlags`, serif `SECTION_PT`. Command item: a delegate drawing the label left and the CLI name muted right. Selection by CLI name stays the public API (`select(name)` or whatever the tests use now).
- [ ] **Step 4: Run** `uv run pytest tests/gui -k actions -v` — PASS.
- [ ] **Step 5: Commit** `"Advanced: readable command names in groups"`

### Task 11: Import and Export windows

**Files:**
- Modify: `smart_gallery/gui/form_window.py` (header + footer), `smart_gallery/gui/imports/view.py`, `smart_gallery/gui/exports/view.py`
- Test: `tests/gui/test_imports_view.py`, `tests/gui/test_export_view.py`, new `tests/gui/test_form_window.py`

**Interfaces:**
- Consumes: `SectionHeader`, `ActionBar`, `Disclosure`, `CARD_STYLE`.
- Produces: a form passed to `FormWindow` may expose `heading: SectionHeader` and `footer: ActionBar`; `FormWindow` puts the heading at the top and the footer at the bottom of the window, outside the form's scroll area.

- [ ] **Step 1: Failing tests:** in both windows the primary button is in the footer, right-most, and narrower than half the window (not full width); Import has a heading (`Importing into …` title, source summary subtitle) and its sections are `QGroupBox`es (Source, Import into); Import's command text sits in a `Disclosure("Command")`, closed by default; Export's `command_toggle` is replaced by the same `Disclosure`; Dry run checkbox is in the footer's left.
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_imports_view.py tests/gui/test_export_view.py tests/gui/test_form_window.py -v` — FAIL.
- [ ] **Step 3: Implement.** Import heading title `Import`, subtitle the chosen profile and source (`Canon SD card · F:\`) or `Choose a source to import from.`; Profile row buttons New / Edit / Delete stay secondary.
- [ ] **Step 4: Run** `uv run pytest tests/gui -v` — PASS.
- [ ] **Step 5: Commit** `"Import and Export windows share heading, cards and footer"`

### Task 12: Icons on actions, docs, final check

**Files:**
- Modify: Browse… buttons (`grep -rn "Browse" smart_gallery/gui`), Import's Refresh — icon + text via `icons.icon`; `CLAUDE.md` (Theme bullet: button roles, `gui/ui/` kit, one-primary test, `format.py`); `docs/USER_GUIDE.md` (renamed copy: Clear selection, Stats tile names, Advanced labels)
- Test: `tests/gui/test_button_roles.py` (every button whose text starts with `Browse` has a non-null icon)

- [ ] **Step 1: Failing test** for Browse icons.
- [ ] **Step 2: Run** it — FAIL.
- [ ] **Step 3: Implement**, update the two docs.
- [ ] **Step 4: Run** `uv run pytest -m "not exiftool and not faces"` — PASS; `uv run ruff check` and `uv run black --check` on every touched file — clean. Retake the page screenshots (scratch script from this session, D: gallery, `SG_APP_DATA` copy) and compare against the survey: one coral button per page, content aligned under the title, same footers.
- [ ] **Step 5: Commit** `"Icons on common actions; docs for the UI kit"`
