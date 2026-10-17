# Native dashboard charts: design

Date: 2026-10-18
Status: draft for owner approval
Builds on: `2026-10-17-windows-desktop-app-design.md` (the Dashboard section)

## Intent

The desktop app's Dashboard currently draws Plotly figures in a hidden web page
(`QWebEngineView`). It works, but it doesn't look or behave like the rest of the
window:
- it ignores the Windows theme and font
- it shows Plotly's floating toolbar
- it reflows like a web page rather than a desktop panel

The owner wants **native look and behaviour**. The charts should be drawn with
Qt's own chart widgets (`PySide6.QtCharts`) so they follow the Windows
light/dark theme and the system font, resize like the rest of the window, and
show native tooltips.

The **set of charts stays the same**. The owner allows compromises in how each
one is drawn, judged by what each chart is for (see "Chart by chart").

## Goals

1. Redraw every Dashboard chart with Qt Charts, except the photo map.
2. **The photo map stays a web view** (the owner's decision), on the same
   street-map tiles and density heatmap as today.
3. The charts follow the app's light/dark theme and font, and switch live when
   Windows changes theme.
4. Hovering a bar or slice shows a native tooltip with its exact value.
5. The Streamlit dashboard and the app show **the same numbers**. Only the
   drawing differs.
6. Stay as easy to extend as the rest of the app. Adding a chart means one data
   function plus one drawing function, with no HTML.

## Non-goals

- **Charts never filter anything** (the owner's decision). No click-to-filter
  and no selection in charts. The filter panel stays the only way to filter.
- Zoom and pan inside charts. They are summary charts.
- New charts or removed charts.
- Removing QtWebEngine from the build. The map still needs it.
- Changing the metrics, the filters, or the data tables, except as listed below.

## Anti-goals

- Chart code that computes its own numbers. All counting and bucketing lives
  in one shared, Qt-free, Plotly-free module that both dashboards use.
- A dashboard that freezes the window while it loads. Data still loads on the
  worker thread, as today.
- Charts that are unreadable in dark mode, or with real data: long camera
  names, ten years of months, a single month, zero videos.

## Decisions

### The owner's decisions

- Native Qt charts for everything except the map.
- The map stays a web view.
- Same set of charts as today.
- Compromises in how a chart is drawn are fine where they serve its purpose.
- No filtering from charts.

### The assistant's calls (open to revision)

- **Toolkit: Qt Charts** (`PySide6.QtCharts`). It ships inside PySide6, which is
  already a dependency.
- **One shared data module.** It is Qt-free and Plotly-free, and both front
  ends draw from it:
  - Plotly, for Streamlit
  - Qt Charts, for the app
- **Colours.** Keep today's categorical palette (`STACKED_COLORS`), so a given
  camera has the same colour in both dashboards. Chart backgrounds, grid,
  axis text and fonts come from the theme.
- **Tooltips on hover. No zoom, no pan, no animations.** Qt Charts animations
  feel non-native and slow down large series.
- **Layout.** A scrollable column of chart panels, each with a title. The
  donut row and the 2x2 settings grid keep their arrangement and wrap to one
  column when the window is narrow.
- **Theme source.** The app does not set a style or palette of its own. On
  Windows 11, Qt 6.12's default style follows the OS light/dark setting and
  keeps the application `QPalette` in sync, so **the palette is the source of
  truth**. Charts:
  - pick the light or dark `QChart` theme from
    `QGuiApplication.styleHints().colorScheme()`
  - then take backgrounds, grid, axis text and fonts from the palette
  - re-theme when `colorSchemeChanged` fires (or on a `PaletteChange` event)

  No app-wide theming work is part of this project.
- **Colours by rank.** `data.py` returns cameras and lenses in a fixed rank
  order (by count, ties broken by name), with "Other" last. Both dashboards
  colour series by that rank: position *i* gets `STACKED_COLORS[i]`, and
  "Other" gets a fixed neutral grey. Plotly figures pass that mapping
  explicitly. Streamlit's timeline colours may therefore shift slightly from
  today, which used Plotly's order of appearance.
- **"Same numbers" means the same counts and values.** Streamlit's camera and
  lens donuts still show slice percentages of the top 10, while the app's
  tooltips give each one's share of all filtered images. That difference is
  accepted.
- **Streamlit's settings chart switches to stop bars.** This is required, not
  optional. It keeps both dashboards on the same numbers.
- **Breakpoint.** The narrow-window breakpoint is left to the builder.

## Chart by chart

Each entry gives what the chart is for, how it looks today, and how it is drawn
natively, including any compromise.

### 1. Metrics row

- **Purpose:** headline numbers for the current filter: total files, images,
  videos, total size, video length.
- **Native:** a row of native "stat tiles" (a large value label over a small
  caption), instead of HTML.
- **Compromise:** none. Same labels and formatting, from `figures.metrics`.

### 2. File types donut

- **Purpose:** the mix of images, videos and others.
- **Today:** a 3-slice Plotly donut showing "Images 72%" inside each slice.
- **Native:** a `QPieSeries` with a 40 % hole and the same three colours.
  Labels show "Images 72 %", and a tooltip shows the exact count.
  - Slices under about 4 % move their label outside, with a leader line, so it
    stays readable.
- **Compromise:** none.

### 3. Cameras and 4. Lenses (top 10)

- **Purpose:** which cameras and lenses I use most.
- **Today:** a donut of the top 10. It has a horizontal legend underneath, and
  each slice shows only its percentage.
  - Two problems: long names make the legend hard to read, and the percentages
    are of the top 10 only, which is misleading when there's a long tail.
- **Share denominator:** for both cameras and lenses, the share is out of
  all filtered images, including images with no camera or lens recorded.
- **Native:** a horizontal bar chart (`QHorizontalBarSeries`), sorted with the
  largest at the top:
  - the full camera or lens name on the axis
  - the count at the end of each bar
  - a tooltip with the count and its share of **all** filtered images
  - each bar coloured from the same palette position its slice has in
    Streamlit
- **Compromise:** a donut becomes ranked bars. That reads ten long labels far
  better and states counts honestly. Streamlit keeps its donuts, drawn from the
  same top-10 counts.

### 5. Camera settings (aperture, shutter speed, ISO, focal length)

- **Purpose:** what settings I shoot at, for example "mostly f/1.8 and f/2.8,
  ISO 100–400".
- **Today:** a 2x2 grid of histograms with 30 log-scale bins:
  - outliers are trimmed at the 1st and 99th percentiles
  - ticks sit at standard stops (f/1.2 … f/22, 1/4000 … 30 s, ISO 100 …
    12800, 14 … 400 mm)
- **Problem:** Qt Charts has no histogram type, and 30 arbitrary bins say less
  than the stops the photographer actually chose.
- **Native:** a 2x2 grid of **bar charts with one bar per standard stop**:
  - each photo counts toward its nearest stop, measured on a log scale
  - values beyond the ends count toward the end stops, so there's no
    percentile trimming
  - the stops shown run continuously from the lowest to the highest stop used,
    with zero-count stops in between filled in
  - labels are "f/2.8", "1/250", "2s", "ISO 400" and "50mm"
  - a tooltip shows the count
- **Rules (the assistant's calls):**
  - Values that are missing, non-numeric, or ≤ 0 are dropped, as today.
  - Shutter strings go through `parse_shutter`; anything it can't parse (for
    example Bulb) is dropped.
  - On a tie, the value goes to the lower stop.
  - **Today's aperture list is too coarse for its purpose.** It skips f/1.4
    and f/2, so common fast-lens values would land on the wrong bar. The
    stop lists are therefore:
    - **Aperture:** 1.0, 1.2, 1.4, 1.8, 2, 2.8, 3.5, 4, 5.6, 8, 11, 16, 22
    - **Shutter:** today's list (1/4000 … 30 s)
    - **ISO:** 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600
    - **Focal length:** today's list (14 … 400 mm)
- **Compromise:** binned histograms become bars per stop. The numbers change,
  so **Streamlit's settings chart switches to the same stop bars** to keep the
  two dashboards matching. This is the only visible change on the Streamlit
  side.
- **Focal length:** zoom lenses record in-between values such as 37 mm, which
  count toward the nearest listed focal length. This is acceptable for "what do
  I shoot at".

### 6. Photography timeline (monthly photos by camera)

- **Purpose:** when I shoot, and which camera I used over time, with
  transitions between bodies.
- **Today:** stacked bars per month, coloured by camera:
  - the top 10 cameras, with the rest grouped as "Other"
  - every month labelled, at 45°
- **Problem:** a catalog spanning many years has hundreds of months, and a Qt
  category axis would print every label.
- **Native:** a `QStackedBarSeries` with one bar per calendar month, including
  months with zero photos so gaps show:
  - bars sit at index positions on a hidden value axis
  - a `QCategoryAxis` labels them:
    - up to 24 months: every month ("Mar 2026")
    - longer spans: the year at each January
  - legend: the cameras, plus "Other"
  - tooltip: the month, the camera and the count
- **Compromise:** not every month gets a label on long spans. Monthly detail
  is kept.
- **Rules (the assistant's calls), kept from today unless stated:**
  - The month range runs from the first to the last month of this chart's own
    data. The video chart has its own range.
  - Rows without `date_taken` or `camera` are left out. There's no "Unknown"
    bucket.
  - The top 10 cameras are ranked by count within the filtered images.
  - On long spans, the **first month is always labelled as well**, so a span
    with no January still has a label.

### 7. Video recording summary (minutes per month)

- **Purpose:** how much video I record over time.
- **Today:** bars per month, in crimson.
- **Native:** a single `QBarSeries` with the same month axis rules as the
  timeline and the same crimson colour. The tooltip shows the minutes to one
  decimal place.
- **Compromise:** same as the timeline: labels are thinned on long spans.

### 8. Photo map

- **Purpose:** where my photos were taken.
- **Today:** a Plotly density heatmap on OpenStreetMap tiles, with the caption
  "Displaying N geotagged photos".
- **Native:** stays a web view (the owner's decision). It is a small
  `QWebEngineView` panel showing a map-only page, built from the existing
  `figures.photo_map`. The caption becomes a native label above it.
- **Theming:** the map tiles are light in both themes. The panel around them
  follows the theme.
- **Offline:** without internet the tiles are blank, as today.
- **Panel visibility (the assistant's call):** the map panel is hidden when no
  filtered photo has a geotag, the same way Streamlit leaves the section out.
  Without internet it still shows, with blank tiles. The map HTML is written
  on the worker thread and references the `plotly.min.js` already on disk, as
  today.

### Panel visibility in general

A chart panel is hidden when its `data.py` result is empty. Examples:
- no videos hides the video chart
- no camera data hides the camera chart

This matches Streamlit, which leaves those sections out.

### 9. Data tables

The Images, Videos and Others tables are unchanged. They are already native
`QTableView`s.

## Architecture

```
smart_gallery/dashboard/
  data.py      # NEW: Qt-free, Plotly-free numbers for every chart
  figures.py   # Plotly drawing for Streamlit, now built from data.py (+ the map)
  app.py       # Streamlit layout (unchanged apart from the settings chart)
smart_gallery/gui/dashboard/
  model.py     # Qt-free: runs data.py on the worker, writes the map-only page
  charts.py    # NEW: Qt Charts builders, one function per chart, + theming
  view.py      # layout: stat tiles, chart panels, map panel, tables
```

- **`data.py`** returns plain dataclasses or lists. Examples:
  - `file_type_counts(fi, fv, fo) -> list[(label, count)]`
  - `top_counts(df, column, n=10) -> list[(name, count)]` and the total
  - `stop_buckets(series, stops, labels) -> list[(label, count)]`
  - `monthly_by_camera(fi) -> (months, {camera: counts})`, top 10 plus "Other",
    with zero-filled months
  - `video_minutes_by_month(fv) -> (months, minutes)`
  - `geotagged(fi)` (moved from `figures`)

  The exact names and shapes are left to the plan.
- **`figures.py`** keeps its public function names. Streamlit's `app.py`
  barely changes:
  - Each figure is drawn from `data.py`, so both dashboards share one source
    of numbers.
  - `settings_histograms` becomes stop bars.
- **Threading.**
  - On the worker thread, alongside the DataFrame: `split_media`, `metrics`,
    all `data.py` aggregations and the map HTML.
  - On the GUI thread: building Qt chart objects from those small results.
    This is cheap, because there are at most a few hundred bars.
- **Theming.**
  - Light or dark comes from `QGuiApplication.styleHints().colorScheme()`.
  - The matching `QChart` theme is applied, then backgrounds, grid, axis label
    colours and fonts are overridden from the app palette and font.
  - Charts are re-themed on `colorSchemeChanged`.
  - Series colours come from the fixed palette in both themes.
- **Removed:** the multi-chart HTML page and its inline donut/section HTML. The
  page now holds only the map. `plotly.min.js` is still written for the map.

## Testing

- **`data.py` unit tests**, with no Qt and no Plotly. They cover:
  - file-type counts
  - top-10 ordering and the total
  - nearest-stop bucketing on a log scale, including both ends and clamping
  - the label formats
  - zero-filled months across a year boundary
  - top-10 plus "Other" grouping in the timeline
  - video minutes
  - empty-input cases
- **Parity tests:** each `figures.*` Plotly figure and each `charts.*` Qt chart
  contains exactly the numbers its `data.py` function returned. Both
  dashboards therefore match by construction.
- **Qt chart tests** (`tests/gui`, headless). Each builder produces the right
  series type, count and values:
  - the month axis labels follow the 24-month rule
  - tooltips are wired (call the hover handler directly)
  - re-theming on a colour-scheme change updates the chart theme
- **Dashboard view test:** with a real catalog, all chart panels and the map
  panel appear. An empty catalog and a catalog without videos or geotags show
  the right panels without errors.
- **By eye (owner):** light and dark mode, a long-span catalog (E:\), and a
  narrow window.

## Left to the builder

- Exact sizes, spacing, panel styling, and the stat-tile look.
- The threshold where a slice label moves outside (about 4 %).
- Whether the donut row and the settings grid use a `QGridLayout` with a
  reflow, or a simple flow layout.
- Exact `data.py` function names and return types, as long as they are
  Qt-free and Plotly-free.
- How tooltips are shown (`QToolTip.showText` on `hovered` is fine).
