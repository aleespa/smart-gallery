# Native Dashboard Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redraw the desktop app's Dashboard with native Qt Charts. The photo
map stays a small web view. Both the app and the Streamlit dashboard draw
their numbers from one shared, Qt-free, Plotly-free data module.

**Architecture:**
- **New `smart_gallery/dashboard/data.py`.** It computes every chart's
  numbers.
- **`figures.py` (Plotly, for Streamlit) and `gui/dashboard/charts.py` (Qt
  Charts).** Both only draw.
- **`gui/dashboard/model.py`.** It runs all the data work on the worker thread
  and writes a map-only page.
- **`gui/dashboard/view.py`.** It lays out stat tiles, chart panels, the map
  panel and the tables.

**Tech Stack:** Python 3.13, PySide6 6.12 (QtCharts, QtWebEngineWidgets),
pandas, numpy, Plotly (Streamlit and the map only), pytest, pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-18-native-dashboard-charts-design.md`

## Global Constraints

- **`smart_gallery/dashboard/data.py` imports no Qt and no Plotly.**
  `gui/dashboard/model.py` imports no Qt.
- **Same chart set as today.** Metrics, file types, cameras, lenses, camera
  settings, photography timeline, video minutes per month, photo map, and
  the data tables.
- **Charts never filter anything.** There is no selection, zoom, pan or
  animation, and hover tooltips are the only interaction.
- **The photo map stays a `QWebEngineView`** built from `figures.photo_map`.
  Its panel is hidden when no filtered photo has a geotag.
- **A chart panel is hidden when its data result is empty.**
- **Both dashboards show the same counts and values.** Streamlit's camera and
  lens donuts keep showing percentages of the top 10, and that difference is
  accepted.
- **Stop lists:**
  - aperture: 1.0, 1.2, 1.4, 1.8, 2, 2.8, 3.5, 4, 5.6, 8, 11, 16, 22
  - shutter: 1/4000, 1/2000, 1/1000, 1/500, 1/250, 1/125, 1/60, 1/30, 1/15,
    1/8, 1/4, 1/2, 1, 2, 4, 8, 15, 30
  - ISO: 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600
  - focal length: 14, 18, 24, 28, 35, 50, 70, 85, 105, 135, 200, 300, 400
- **Stop labels:** `f/2.8`, `1/250` (below 1 s) or `2s` (1 s and above),
  `ISO 400`, `50mm`.
- **Colours:**
  - categorical series use today's `STACKED_COLORS` by rank (rank *i* gets
    `STACKED_COLORS[i]`), and "Other" is `#9e9e9e`
  - file types: `#66b3ff`, `#99ff99`, `#ff9999`
  - settings: aperture coral `#ff7f50`, shutter orchid `#da70d6`, ISO gold
    `#ffd700`, focal teal `#008080`
  - video minutes: crimson `#dc143c`
- **Theme:** take light or dark from
  `QGuiApplication.styleHints().colorScheme()`, take backgrounds, text and
  fonts from the application `QPalette` and font, and re-theme on
  `colorSchemeChanged`.
- **Data work stays off the GUI thread.** It goes through the existing
  `RepoQuery`; only building the Qt chart objects happens on the GUI thread.
- **Process:** `uv run --no-sync`. Black and Ruff only on the files you touch.
  The commit trailers are the same as on the previous branch.

## Review Focus

1. **A catalog spanning ten or more years (120+ months).** The month axis
   shows the first month plus one label per January, never every month. The
   tests are in Task 1 (`month_axis_labels`) and Task 3 (axis label count).
2. **Windows switches light↔dark while the Dashboard is open.** Charts
   re-theme, and series keep their fixed colours, because `QChart.setTheme`
   resets them. The test is in Task 3.
3. **A filter that leaves zero images but some videos, or an empty catalog.**
   Image panels are hidden, the video panel shows, and nothing raises. The
   test is in Task 4.
4. **Very long camera or lens names (40+ characters).** The ranked bar chart's
   categories hold the full names, unshortened. The test is in Task 3.
5. **A single month, or all photos at one stop.** The chart shows one bar with
   a label, with no empty or one-point axis errors. The tests are in Task 1
   and Task 3.

---

### Task 1: Shared chart data (`dashboard/data.py`)

**Files:**
- Create: `smart_gallery/dashboard/data.py`
- Test: `tests/unit/test_dashboard_data.py`

**Interfaces:**
- Produces (all Qt-free and Plotly-free):
  - **Colours:** `PALETTE: tuple[str, ...]` (today's `STACKED_COLORS`
    values), `OTHER = "Other"`, `OTHER_COLOR = "#9e9e9e"`,
    `FILE_TYPE_COLORS = ("#66b3ff", "#99ff99", "#ff9999")`,
    `SETTINGS_COLORS: dict[str, str]` keyed like `SETTINGS`,
    `VIDEO_COLOR = "#dc143c"`.
  - `color_for(name: str, rank: int) -> str` returns `OTHER_COLOR` when
    `name == OTHER`, else `PALETTE[rank % len(PALETTE)]`.
  - `@dataclass(frozen=True) class Counts: items: tuple[tuple[str, int], ...]; total: int`
  - `file_type_counts(fi, fv, fo) -> tuple[tuple[str, int], ...]`, in the
    order Images, Videos, Others, with zero counts dropped.
  - `top_counts(df, column, n=10) -> Counts`. Items are sorted by count
    descending, then by name. `total = len(df)` covers all filtered rows,
    including those missing the column. It returns `Counts((), len(df))` when
    the column is absent or empty.
  - `@dataclass(frozen=True) class StopSpec: name: str; stops: tuple[float, ...]; labels: tuple[str, ...]`
  - `APERTURE`, `SHUTTER`, `ISO`, `FOCAL: StopSpec`, with the Global
    Constraints values and labels, and
    `SETTINGS: tuple[StopSpec, ...] = (APERTURE, SHUTTER, ISO, FOCAL)` named
    "Aperture", "Shutter Speed", "ISO", "Focal Length".
  - `stop_buckets(values: pd.Series, spec: StopSpec) -> tuple[tuple[str, int], ...]`
  - `settings_buckets(fi) -> dict[str, tuple[tuple[str, int], ...]]` holds
    only the non-empty specs, in `SETTINGS` order. The shutter column
    `shutter_speed` goes through `figures.parse_shutter`, which moves here as
    `parse_shutter`; `figures` re-exports it.
  - `@dataclass(frozen=True) class MonthSeries: months: tuple[str, ...]; series: dict[str, tuple[float, ...]]`.
    Months are `"YYYY-MM"`. The series dict is ordered by rank, with `OTHER`
    last.
  - `monthly_by_camera(fi, n=10) -> MonthSeries | None`
  - `video_minutes_by_month(fv) -> MonthSeries | None`, with series
    `{"Minutes": ...}`.
  - `month_label(month: str) -> str` turns `"2026-03"` into `"Mar 2026"`.
  - `month_axis_labels(months) -> tuple[tuple[int, str], ...]`:
    - 24 months or fewer: every index, labelled with `month_label`
    - more: index 0 with `month_label`, plus each later January's index
      labelled with the year (`"2027"`)
  - `geotagged(fi) -> pd.DataFrame` moves here from `figures`, which
    re-exports it.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_dashboard_data.py`:
  - `test_file_type_counts_drops_zero`
  - `test_top_counts_ranks_and_total`: cameras A×3, B×3, C×1 plus 2 rows with
    no camera give `items == (("A", 3), ("B", 3), ("C", 1))` and `total == 9`.
    11 distinct cameras give 10 items.
  - `test_stop_buckets_nearest_on_log_scale`. Aperture values [1.4, 1.5, 2.0,
    2.8, 2.9] give `(("f/1.4", 2), ("f/1.8", 0), ("f/2", 1), ("f/2.8", 2))`.
    1.5 is nearer 1.4 in log terms, the range is continuous, and the zero
    stops in between are kept.
  - `test_stop_buckets_tie_goes_lower`, with
    `v = 10 ** ((log10(2.8) + log10(3.5)) / 2)`, lands on "f/2.8". Compare
    distances with a 1e-12 tolerance.
  - `test_stop_buckets_clamps_and_drops`: 0.7 goes to "f/1.0" and 40 to
    "f/22"; 0, -1, NaN and "abc" are dropped. An all-invalid input gives `()`.
  - `test_shutter_labels_and_bulb`: "1/250s" gives "1/250", "2" gives "2s",
    "Bulb" is dropped.
  - `test_iso_and_focal_labels`: 400 gives "ISO 400"; 37 gives "35mm" (37 is
    nearer 35 than 50 on a log scale).
  - `test_single_stop_gives_one_bucket` (Review Focus 5).
  - `test_monthly_by_camera_zero_fill_and_other`:
    - data in 2025-11 and 2026-02 gives months
      `("2025-11", "2025-12", "2026-01", "2026-02")`, with zeros in the gap
    - 11 cameras put the least-used one in `OTHER`, which comes last
    - rows without `date_taken` or `camera` are left out
  - `test_video_minutes_by_month`: 90 000 ms in 2026-01 and 30 000 ms in
    2026-03 give `(1.5, 0.0, 0.5)`.
  - `test_month_axis_labels`: 24 months give 24 labels. 130 months starting
    "2016-03" give index 0 with "Mar 2016", then "2017" at index 10, and 11
    labels in total (Review Focus 1). A single month gives one label (Review
    Focus 5).
  - `test_color_for`
  - `test_no_qt_or_plotly_import`: in a subprocess, with
    `sys.modules["PySide6"] = None` and `sys.modules["plotly"] = None`,
    importing `smart_gallery.dashboard.data` succeeds.
  - `test_empty_inputs`: every function returns an empty result or `None`.
- [ ] **Step 2:** Run `uv run --no-sync pytest tests/unit/test_dashboard_data.py -v`.
  Expected: FAIL (ImportError).
- [ ] **Step 3:** Implement `data.py`. Nearest stop is
  `argmin(|log10(v) − log10(stops)|)`, with ties going to the lower stop.
  Zero-fill months with `pd.period_range`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add shared dashboard chart data module`.

---

### Task 2: Plotly figures drawn from shared data (Streamlit)

**Files:**
- Modify: `smart_gallery/dashboard/figures.py`,
  `smart_gallery/dashboard/app.py` (only if the settings chart's height or
  layout needs it)
- Test: `tests/unit/test_dashboard_figures.py`

**Interfaces:**
- Consumes: everything in Task 1.
- Produces: the same public names as today: `metrics`, `split_media`,
  `load_catalog_df`, `file_type_donut`, `top_donut`, `settings_histograms`,
  `timeline_by_camera`, `video_minutes`, `photo_map`, plus re-exported
  `geotagged`, `parse_shutter` and `STACKED_COLORS` (an alias of
  `data.PALETTE`). Each builder draws only from `data.*` results:
  - `file_type_donut`: built from `file_type_counts`.
  - `top_donut`: slices from `top_counts(df, column).items`, coloured by rank
    with `color_for`.
  - `settings_histograms(fi)`: a 2x2 `make_subplots`, one `go.Bar` per
    non-empty spec in `settings_buckets(fi)`, with x = labels, y = counts,
    the colours from `SETTINGS_COLORS`, and the titles "Aperture", "Shutter
    Speed", "ISO" and "Focal Length". It returns `None` when all are empty.
  - `timeline_by_camera`: one stacked `go.Bar` per series of
    `monthly_by_camera`, x = months, coloured by `color_for(name, rank)`, with
    the title kept.
  - `video_minutes`: one `go.Bar` from `video_minutes_by_month` in
    `VIDEO_COLOR`.
  - `photo_map`: unchanged, apart from using `data.geotagged`.

- [ ] **Step 1: Write the failing parity tests:**
  - `test_top_donut_matches_data`: slice labels and values equal
    `top_counts(...).items`.
  - `test_settings_bars_match_buckets`: each subplot's bar x/y equals
    `settings_buckets(fi)[name]`.
  - `test_timeline_matches_monthly_by_camera`: trace names, in order, equal
    the series keys, and each trace's y equals its series. "Other" has
    `marker.color == "#9e9e9e"`.
  - `test_video_minutes_matches_data`
  - Keep the existing tests passing, adjusting only the assertions that
    referenced 30-bin histograms.
- [ ] **Step 2:** Run `uv run --no-sync pytest tests/unit/test_dashboard_figures.py -v`.
  Expected: the new tests FAIL.
- [ ] **Step 3:** Implement it. Remove the old in-function counting so every
  number comes from `data.py`.
- [ ] **Step 4:** Run the same command, plus `tests/unit/test_dashboard_page.py`.
  Expected: PASS.
- [ ] **Step 5:** Commit: `Draw Streamlit figures from the shared chart data`.

---

### Task 3: Qt chart builders and theming (`gui/dashboard/charts.py`)

**Files:**
- Create: `smart_gallery/gui/dashboard/charts.py`
- Test: `tests/gui/test_dashboard_charts.py`

**Interfaces:**
- Consumes: Task 1 (`Counts`, `MonthSeries`, `month_axis_labels`,
  `color_for`, and the colour constants).
- Produces, each returning a `QChartView` with antialiasing on, animations
  off and the legend hidden unless stated:
  - `donut_chart(items, colors) -> QChartView` uses a `QPieSeries` with
    `holeSize 0.4`.
    - Labels read `"{name} {pct:.0f} %"`.
    - A slice under 4 % gets `LabelOutside`, and the others `LabelInsideHorizontal`.
    - The tooltip on `hovered` is `"{name}: {count:,}"`.
  - `ranked_bar_chart(counts: Counts) -> QChartView` uses a
    `QHorizontalBarSeries` with one `QBarSet` per item.
    - Bars run largest at the top, and each is coloured with
      `color_for(name, rank)`.
    - The category axis holds the full names.
    - Each bar's value label is the count.
    - The tooltip is `"{name}: {count:,} ({share:.1%} of {total:,} images)"`.
  - `stop_bar_chart(buckets, color) -> QChartView` uses a `QBarSeries` over a
    `QBarCategoryAxis` of stop labels. The tooltip is `"{label}: {count:,}"`.
  - `month_bar_chart(ms: MonthSeries, colors: dict[str, str], unit: str, stacked: bool) -> QChartView`:
    - `QStackedBarSeries` when stacked, otherwise `QBarSeries`
    - the x axis is a `QCategoryAxis` with labels from `month_axis_labels`,
      at `AxisLabelsPositionOnValue`, alongside a hidden `QValueAxis` over
      `[-0.5, len(months) - 0.5]`
    - the legend is shown when stacked
    - the tooltip is `"{month_label}: {series} {value:,.1f} {unit}"`, or
      without the series name when unstacked
  - `apply_theme(view: QChartView) -> None`:
    - sets `ChartThemeDark` or `ChartThemeLight` from
      `QGuiApplication.styleHints().colorScheme()`
    - then sets the chart background from `palette.Window`, the axis label
      and legend colours from `palette.WindowText`, and the fonts from
      `QApplication.font()`
    - then **re-applies each series' stored colours** (keep them on the
      series, e.g. as a Python attribute or `setProperty("sg_colors", ...)`),
      because `setTheme` resets them
  - `class ThemeWatcher(QObject)`, constructed as
    `ThemeWatcher(views: list[QChartView], parent)`. It calls `apply_theme`
    on each view when `styleHints().colorSchemeChanged` fires, and offers
    `add(view)`.

- [ ] **Step 1: Write the failing tests** (headless, `tests/gui`):
  - `test_donut_slices_and_labels`
  - `test_ranked_bar_full_long_names` (Review Focus 4): with a 45-character
    camera name, the axis categories include it unchanged and the order is
    largest at the top.
  - `test_ranked_bar_tooltip_share`: calling the hover handler gives the
    expected text, with a total including rows that have no camera.
  - `test_stop_bar_values`
  - `test_month_bar_axis_labels_long_span` (Review Focus 1): 130 months give a
    `QCategoryAxis` with 11 labels.
  - `test_month_bar_single_month` (Review Focus 5)
  - `test_stacked_series_order_and_colors`
  - `test_apply_theme_keeps_series_colors` (Review Focus 2): build a
    `month_bar_chart` with known colours, call `apply_theme` with
    `colorScheme` monkeypatched to Dark and then to Light, and assert each
    `QBarSet.color()` still equals its assigned colour and the chart theme
    changed.
  - `test_theme_watcher_reapplies_on_signal`: emit through a fake
    style-hints object or call the slot.
- [ ] **Step 2:** Run `uv run --no-sync pytest tests/gui/test_dashboard_charts.py -v`.
  Expected: FAIL (ImportError).
- [ ] **Step 3:** Implement it. Tooltips use `QToolTip.showText(QCursor.pos(), text)`
  on `hovered(True, ...)` and `QToolTip.hideText()` on `False`. Factor the
  text into small pure functions so tests can call them.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `Add native Qt chart builders with theming`.

---

### Task 4: Native Dashboard view and map-only page

**Files:**
- Modify: `smart_gallery/gui/dashboard/model.py`,
  `smart_gallery/gui/dashboard/view.py`
- Test: `tests/unit/test_dashboard_page.py` (rewrite),
  `tests/gui/test_dashboard_view.py` (extend)

**Interfaces:**
- Consumes: Tasks 1–3, `figures.photo_map`, `figures.metrics`,
  `figures.split_media`, `figures.load_catalog_df`, and `RepoQuery`.
- Produces, in `model.py` (no Qt):
  - `@dataclass(frozen=True) class DashboardData`, with these fields:
    - `split: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]`
    - `metrics: list[tuple[str, str]]`
    - `file_types`
    - `cameras: Counts` and `lenses: Counts`
    - `settings: dict`
    - `timeline: MonthSeries | None` and `video: MonthSeries | None`
    - `geotag_count: int`
    - `map_page: Path | None`
  - `write_map_page(out_dir: Path, fig) -> Path` writes `plotly.min.js` once,
    as today, plus an atomic `map.html` holding only the figure div, with
    `body { margin: 0 }`.
  - `load_dashboard(db_path, filters, out_dir) -> DashboardData` runs on the
    worker. `map_page` is `None` and no page is written when `geotag_count`
    is 0.
  - Removed: `Section`, `build_sections`, `_section_html`, `write_dashboard`
    and `INDEX_HTML`.
- `view.py`, `DashboardView`:
  - **Top:** a row of stat tiles, one per metric, each a large value
    `QLabel` above a small caption label.
  - **Middle:** a `QScrollArea` of titled `QFrame` panels, in this order:
    1. "File Types, Cameras, Lenses": the donut and the two ranked bar charts
       side by side in a `QGridLayout`, reflowing to one column below the
       builder's chosen width
    2. "Camera Settings Distribution": a 2x2 grid of stop bar charts, each
       titled with its spec name
    3. "Photography Timeline": stacked
    4. "Video Recording Summary": unit "min"
    5. "Photo Map": the caption label "Displaying N geotagged photos" above a
       `QWebEngineView` about 500 px tall, loading `map_page`
  - **Bottom:** the existing tables tab, in the same vertical splitter.
  - Panels are rebuilt on each result. A panel, or a settings sub-chart, is
    hidden when its data is empty or `None`.
  - Every built chart view goes to one `ThemeWatcher`.
  - The messages for no gallery, a disconnected drive and a read error are
    unchanged.

- [ ] **Step 1: Write the failing tests:**
  - Unit:
    - `test_load_dashboard_computes_everything(repo, make_item)`, with
      geotags: all fields are filled, `map_page` exists, and its HTML holds
      exactly one `plotly-graph-div` and `<script src="plotly.min.js">`.
    - `test_load_dashboard_no_geotags_writes_no_map`
    - `test_plotly_js_written_once`
  - GUI:
    - `test_dashboard_native_panels(qtbot, repo, make_item)`: stat tiles show
      the five metric labels; every panel is visible, with `QChartView`s for
      the charts and one `QWebEngineView` for the map.
    - `test_dashboard_videos_only_hides_image_panels(qtbot, ...)` (Review
      Focus 3): with zero images and some videos, the file-type panel shows,
      the cameras/lenses/settings/timeline/map panels are hidden, the video
      panel shows, and nothing raises.
    - `test_dashboard_empty_catalog`
    - Keep the disconnected-drive test.
- [ ] **Step 2:** Run
  `uv run --no-sync pytest tests/unit/test_dashboard_page.py tests/gui/test_dashboard_view.py -v`.
  Expected: FAIL.
- [ ] **Step 3:** Implement it. The view's `reload` passes
  `lambda repo: load_dashboard(repo.db_path, filters, self.out_dir)` to
  `RepoQuery`. `_on_data` rebuilds the panels on the GUI thread.
- [ ] **Step 4:** Run the same command, then
  `uv run --no-sync pytest -m "not exiftool and not faces"`. Expected: PASS.
  Then:
  - run `uv run --no-sync smart-gallery gui`
  - by eye (owner): the Dashboard on E:\ in light and dark mode, plus a
    narrow window
  - `uv run --no-sync smart-gallery dashboard E:/`: the Streamlit settings
    chart now shows stop bars
- [ ] **Step 5:** Commit: `Draw the desktop Dashboard with native Qt charts`.

---

## Self-review notes

- **Spec coverage:**
  - Metrics tiles: Task 4.
  - Donut, ranked bars, stop bars and month bars: Task 3, drawn in Task 4.
  - Map web view and hiding: Task 4.
  - Tables unchanged: Task 4.
  - Shared numbers and Streamlit stop bars: Tasks 1 and 2.
  - Theming and live switching: Task 3.
  - Panel hiding: Task 4.
  - Colours by rank: Tasks 1, 2 and 3.
  - Threading: Task 4.
- **Calls made here, within the spec's "left to the builder":**
  - Settings colours as hex values for coral, orchid, gold and teal.
  - Tooltip text formats.
  - A ~500 px map height.
  - `map.html` replaces `index.html`.
