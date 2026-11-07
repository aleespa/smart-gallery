# Photo Viewer ("Studio") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the window-resizing `MediaViewer` with a fixed-layout "Studio" viewer: a dark stage that fits each photo, fixed edge arrows, a top bar, a hideable info panel, a filmstrip hidden by default, zoom, keys, preloading, and face boxes, used by every caller.

**Architecture:** A new package `smart_gallery/gui/viewer/` holds the parts: a Qt-free info/people model (`info.py`), remembered state (`state.py`), the painted stage with fit/zoom/pan/boxes (`stage.py`), a cached preview loader with prefetch (`previews.py`), the filmstrip (`filmstrip.py`), small painted icons (`icons.py`) and the window that composes them (`window.py`). `gui/library/viewer.py` keeps its helper functions and re-exports `MediaViewer` from the new package, so callers keep their import.

**Tech Stack:** Python 3.13, PySide6, Pillow, SQLite (read-only), pytest + pytest-qt (offscreen).

**Spec:** `docs/superpowers/specs/2026-11-07-photo-viewer-design.md`

## Global Constraints

- Colours only from `smart_gallery/colors.py` / `gui/theme.py`; style sheets use `palette(highlight)` for the accent. New colour: `VIEWER_STAGE = "#161616"` in `colors.py`.
- Fonts: Segoe UI Variable (body, `theme.SANS`); Georgia (`theme.serif_font`) only for the date heading.
- Read-only: the viewer never writes the catalog or files. Catalog reads and image decoding run off the GUI thread (`RepoQuery` or a pool); the only GUI-thread file read allowed is loading an already-cached 256 px thumbnail (as the Library grid does).
- Nothing in the window moves when the photo changes: window size, top bar, arrows, info panel and filmstrip geometry depend only on the window size and the panel toggles.
- Viewer window: separate top-level `QWidget` with `Qt.WindowType.Window`, system frame (design call), parented to the caller so it closes with it, non-modal; closes on `ctx.gallery_changed`.
- Remembered state file: `paths.app_data_dir() / "viewer.json"` via `state_store.load_json/save_json`.
- GUI tests: `tests/gui`, marker `gui`, headless (`QT_QPA_PLATFORM=offscreen`); run with `uv run pytest tests/gui -q`.
- Format/lint only touched files: `uv run black --target-version py313 <files>`, `uv run ruff check <files>`.
- Commits end with the session's attribution lines.

## Review Focus

1. Holding → (key auto-repeat, many `show_next` calls in a burst): the photo, info and position that end up shown are all the last index's; stale loads are dropped. Test in Task 5.
2. A file missing from disk / drive unplugged mid-browse: the stage shows "Drive not connected." (or "No preview for this file.") and the arrows and info still work. Test in Task 5.
3. Extreme shapes: an 8000×1000 panorama fits the stage width; a 50×50 image is not upscaled at fit. Test in Task 3.
4. Remembered geometry off-screen (monitor removed): the window opens fully on the available screen. Test in Task 2.
5. The gallery changes while the viewer is open: the viewer closes. Test in Task 5.

---

### Task 1: Info model (Qt-free) and people in a photo

**Files:**
- Create: `smart_gallery/gui/viewer/__init__.py` (empty), `smart_gallery/gui/viewer/info.py`
- Modify: `smart_gallery/colors.py` (add `VIEWER_STAGE`)
- Test: `tests/gui/test_viewer_info.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class InfoSection: key: str; label: str; lines: tuple[str, ...] = (); chips: tuple[str, ...] = (); accent_chips: tuple[str, ...] = (); heading: str = ""` — `key` one of `"date"`, `"exposure"`, `"camera"`, `"people"`, `"video"`, `"file"`; `heading` is the Georgia line (date only); `accent_chips` are named people.
  - `@dataclass(frozen=True) class PhotoFace: face_id: int; name: str | None; bbox: tuple[float, float, float, float]; img_size: tuple[int, int] | None`
  - `@dataclass(frozen=True) class PhotoPeople: names: tuple[str, ...]; unnamed: int; faces: tuple[PhotoFace, ...]`
  - `photo_people(repo, media_id: int) -> PhotoPeople | None` — None when the catalog has no `faces` table; named people (distinct, sorted by name, persons with a non-empty name and `hidden=0`), `unnamed` = faces with no named person, excluding `state='ignored'` faces and faces of hidden persons.
  - `build_info(item: MediaItem, people: PhotoPeople | None) -> list[InfoSection]` — sections in the order date, exposure, camera, people, video (videos only), file; a section with nothing to show is left out, except date, which shows "Date unknown".

- [ ] **Step 1: Write the failing tests**

```python
def test_info_sections_for_a_photo(make_item):
    item = make_item("2026/06/IMG_4512.CR3", date_taken="2026-06-14", time_taken="17:42:05",
                     camera="Canon EOS R6", lens="RF 24-105mm F4 L", aperture=2.8,
                     shutter_speed_sec=0.004, iso=400, focal_length=50.0,
                     width=6000, height=4000, size_bytes=29_779_558)
    people = PhotoPeople(("Ana", "Luis"), 1, ())
    s = {x.key: x for x in build_info(item, people)}
    assert list(s) == ["date", "exposure", "camera", "people", "file"]
    assert s["date"].heading == "14 June 2026" and s["date"].lines == ("Saturday · 17:42",)
    assert s["exposure"].chips == ("f/2.8", "1/250 s", "ISO 400", "50 mm")
    assert s["camera"].lines == ("Canon EOS R6", "RF 24-105mm F4 L")
    assert s["people"].accent_chips == ("Ana", "Luis") and s["people"].chips == ("+1 unnamed",)
    assert s["file"].lines == ("6000 × 4000 · 28.4 MB", "2026/06/IMG_4512.CR3")

def test_info_without_metadata(make_item):
    s = {x.key: x for x in build_info(make_item("a.jpg", date_taken=None, camera=None), None)}
    assert s["date"].heading == "Date unknown"
    assert "exposure" not in s and "camera" not in s and "people" not in s

def test_info_video_section(make_item):
    v = make_item("a.mp4", media_type="video", ext=".mp4", duration_ms=83_000, codec="h264", frame_rate=29.97)
    s = {x.key: x for x in build_info(v, None)}
    assert s["video"].lines == ("1:23", "h264 · 29.97 fps")

def test_photo_people_counts(repo, make_item):  # build persons/faces with SQL like tests/gui/test_people_view.py Cat
    ...  # Ana (named), a hidden group, an unnamed group, one ignored face, one 'new' face
    p = photo_people(repo, media_id)
    assert p.names == ("Ana",) and p.unnamed == 2 and len(p.faces) == 3
```
(`make_item` may need the extra kwargs; it forwards to `MediaItem`.) Location: when `latitude`/`longitude` are set, the file section gets a third line `f"{lat:.5f}, {lon:.5f}"`.

- [ ] **Step 2: Run** `uv run pytest tests/gui/test_viewer_info.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement `info.py`.** Weekday/month via `datetime.date.strftime` is locale-dependent; use English names from fixed tuples. Shutter: `item.shutter_speed_text` if set, else `format_shutter(...)`, then normalise a trailing `"s"` to `" s"`. Size formatting: reuse `gui/library/viewer.format_size` (moved into `info.py`; `library/viewer.py` re-imports it). Duration: `m:ss` (`h:mm:ss` from an hour). `photo_people` reads `faces f LEFT JOIN persons p` and `face_scan_state` for `img_w/img_h`; check the table exists via `sqlite_master` first.
- [ ] **Step 4: Run** the tests — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add the viewer's info and people model"`

---

### Task 2: Remembered viewer state

**Files:**
- Create: `smart_gallery/gui/viewer/state.py`
- Test: `tests/gui/test_viewer_state.py`

**Interfaces:**
- Produces:
  - `@dataclass class ViewerState: geometry: tuple[int, int, int, int] | None = None; info_open: bool = True; strip_open: bool = False`
  - `load_state(path: Path) -> ViewerState` (bad/missing file → defaults; wrong types ignored field by field)
  - `save_state(path: Path, state: ViewerState) -> None` (OSError swallowed)
  - `place(saved: tuple[int,int,int,int] | None, screen: QRect) -> QRect` — saved rect clamped fully onto `screen` (shrunk if larger); None → 80 % of the screen, centred.
  - `STATE_FILE = "viewer.json"`

- [ ] **Step 1: Failing tests**

```python
def test_state_round_trip(tmp_path):
    p = tmp_path / "viewer.json"
    save_state(p, ViewerState((10, 20, 900, 700), False, True))
    assert load_state(p) == ViewerState((10, 20, 900, 700), False, True)

def test_state_defaults_on_bad_file(tmp_path):
    p = tmp_path / "viewer.json"; p.write_text("{\"info_open\": \"yes\"")
    assert load_state(p) == ViewerState()

def test_place_first_time_and_off_screen():
    screen = QRect(0, 0, 1920, 1080)
    assert place(None, screen) == QRect(192, 108, 1536, 864)
    r = place((2500, 900, 1000, 800), screen)   # from a monitor that is gone
    assert screen.contains(r) and r.size() == QSize(1000, 800)
    assert place((0, 0, 4000, 3000), screen).size() == QSize(1920, 1080)
```
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_viewer_state.py -q` — FAIL.
- [ ] **Step 3: Implement** `state.py` (Qt only for `QRect` in `place`).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "Remember the viewer's window and panels"`

---

### Task 3: The stage: fit, zoom, pan, boxes, messages, arrows

**Files:**
- Create: `smart_gallery/gui/viewer/stage.py`, `smart_gallery/gui/viewer/icons.py`
- Modify: `smart_gallery/gui/people/faces_grid.py` (move `face_rect` to `stage.py`; import it back)
- Test: `tests/gui/test_viewer_stage.py`

**Interfaces:**
- Consumes: `colors.VIEWER_STAGE`, `theme.ACCENT`.
- Produces:
  - `MARGIN = 24`; `ARROW_SIZE = QSize(40, 64)`; `ARROW_INSET = 12`; `MAX_ZOOM = 2.0`; `ZOOM_STEP = 1.25`
  - `fit_rect(image: QSize, area: QSize, margin: int = MARGIN) -> QRect` — largest rect with the image's aspect inside `area` minus `margin` on every side, centred, never larger than `image` (no upscaling).
  - `face_rect(face, image: QSize) -> QRectF | None` (moved unchanged from `faces_grid`; accepts any object with `bbox` and `img_size`).
  - `@dataclass(frozen=True) class Box: rect: QRectF; label: str = ""` — `rect` in preview-image pixels.
  - `class PhotoStage(QWidget)` with signals `previous()`, `next()`, `zoom_changed(bool)`, `strip_toggle()`; methods `set_image(image: QImage, *, sharp: bool) -> None` (sharp=False: a thumbnail placeholder; boxes are drawn only when sharp), `set_message(text: str) -> None` (clears the image), `set_loading(on: bool) -> None` (spinner after 150 ms), `set_boxes(boxes: list[Box]) -> None`, `set_nav(has_prev: bool, has_next: bool) -> None`, `set_strip_handle(visible: bool) -> None`, `toggle_zoom(anchor: QPointF | None = None) -> None`, `zoom_by(steps: int, anchor: QPointF) -> None`, `is_zoomed() -> bool`, `reset_zoom() -> None`, `image_rect() -> QRectF` (where the image is painted now); attributes `prev_btn`, `next_btn` (QToolButtons with `NoFocus`, painted chevrons from `icons.py`), `strip_tab` (QToolButton "▴ Filmstrip").
  - Wheel: no Ctrl → `previous`/`next` (one per notch); Ctrl → `zoom_by`. Double-click → `toggle_zoom(pos)`. Drag pans when zoomed. Zoom levels: fit, then 100 % (`toggle_zoom`), wheel between fit and `MAX_ZOOM` (relative to image pixels).
  - `icons.py`: `icon(name: str, color: QColor, size: int = 18) -> QIcon` painting `"prev"`, `"next"`, `"zoom"`, `"faces"`, `"folder"`, `"full"`, `"info"`, `"close"`, `"play"`, `"open"` with QPainter (no files).

- [ ] **Step 1: Failing tests**

```python
def test_fit_rect_keeps_shape_and_never_upscales():
    assert fit_rect(QSize(3000, 2000), QSize(1048, 748)) == QRect(24, 41, 1000, 666)  # centred
    assert fit_rect(QSize(50, 50), QSize(1000, 800)).size() == QSize(50, 50)
    pano = fit_rect(QSize(8000, 1000), QSize(1048, 748))
    assert pano.width() == 1000 and pano.height() == 125

def test_arrows_do_not_move_between_shapes(qtbot):
    stage = PhotoStage(); qtbot.addWidget(stage); stage.resize(1000, 700); stage.show()
    stage.set_image(_img(3000, 2000), sharp=True); before = (stage.prev_btn.geometry(), stage.next_btn.geometry())
    stage.set_image(_img(2000, 3000), sharp=True)
    assert (stage.prev_btn.geometry(), stage.next_btn.geometry()) == before
    assert stage.next_btn.geometry().right() == 1000 - ARROW_INSET - 1

def test_zoom_toggle_and_reset(qtbot): ...  # toggle_zoom(center) -> is_zoomed(), image_rect().width() == 3000; reset_zoom() -> fit
def test_wheel_steps_and_ctrl_wheel_zooms(qtbot): ...  # QTest wheel events: next emitted once per notch; Ctrl zooms, no next
def test_boxes_only_on_sharp_image(qtbot): ...  # grab(): pixel on the box edge is accent only after set_image(sharp=True)
def test_message_replaces_image(qtbot): ...  # set_message("No preview for this file.") -> image_rect().isEmpty()
```
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_viewer_stage.py -q` — FAIL.
- [ ] **Step 3: Implement** `PhotoStage.paintEvent` (fill `VIEWER_STAGE`, draw the image at `fit_rect` or the zoomed rect with a soft shadow, boxes via `Box.rect` mapped by `image_rect()` scale, accent pen `max(2, 3 px)`, label chip above the box when `label`), arrows placed in `resizeEvent` only (left/right `ARROW_INSET`, vertical centre), arrows disabled per `set_nav`. Update `faces_grid.py` to `from smart_gallery.gui.viewer.stage import face_rect`.
- [ ] **Step 4: Run** stage tests and `uv run pytest tests/gui/test_person_view.py -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Add the viewer stage: fit, zoom, pan and face boxes"`

---

### Task 4: Preview loader with cache and prefetch

**Files:**
- Create: `smart_gallery/gui/viewer/previews.py`
- Modify: `smart_gallery/gui/thumbs/cache.py` (`load_preview(..., largest: bool = False)`: for RAW, try `("-JpgFromRaw", "-PreviewImage", "-ThumbnailImage")` when `largest`)
- Test: `tests/gui/test_viewer_previews.py`, `tests/unit/test_thumb_cache.py` (one test for `largest`)

**Interfaces:**
- Produces:
  - `PREVIEW_EDGE = 2048`; `ZOOM_EDGE = 8192`; `CACHE_SIZE = 6`
  - `class PreviewLoader(QObject)`: signal `ready(object)` carrying `PreviewResult`; `request(key: int, abspath: Path, ext: str, media_type: str, edge: int = PREVIEW_EDGE) -> None` (cache hit → `ready` emitted on the next event-loop turn; videos/`other` → result with `image=None, error=""` without reading); `prefetch(items: list[tuple[int, Path, str, str]]) -> None` (low priority, cached quietly, no signal); `cached(key: int, edge: int) -> QImage | None`.
  - `@dataclass(frozen=True) class PreviewResult: key: int; edge: int; image: QImage | None; error: str` — `error` is `"missing"` when the file does not exist, `""` otherwise.
  - Decoding: `load_preview(abspath, ext, edge, largest=edge > PREVIEW_EDGE)` then `_to_qimage`, on a private `QThreadPool` of 2; LRU of `CACHE_SIZE` entries keyed `(key, edge)`.

- [ ] **Step 1: Failing tests**

```python
def test_request_then_cache_hit(qtbot, tmp_path):
    p = tmp_path / "a.jpg"; Image.new("RGB", (900, 600)).save(p)
    loader = PreviewLoader()
    with qtbot.waitSignal(loader.ready) as got: loader.request(1, p, ".jpg", "image")
    assert got.args[0].image.size() == QSize(900, 600) and got.args[0].error == ""
    assert loader.cached(1, PREVIEW_EDGE) is not None

def test_missing_file_reports_missing(qtbot, tmp_path): ...  # error == "missing", image None
def test_video_is_not_read(qtbot, tmp_path): ...  # media_type "video" -> image None, error ""
def test_prefetch_fills_cache_quietly(qtbot, tmp_path): ...  # no ready signal; waitUntil cached(...) is not None
def test_cache_is_bounded(qtbot, tmp_path): ...  # 8 requests -> only the last CACHE_SIZE cached
```
plus in `tests/unit/test_thumb_cache.py`: `test_load_preview_largest_asks_jpgfromraw_first` (fake `subprocess.run` records the first tag: `"-JpgFromRaw"`).
- [ ] **Step 2: Run** both test files — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "Add the viewer's cached preview loader with prefetch"`

---

### Task 5: The Studio window (replaces MediaViewer)

**Files:**
- Create: `smart_gallery/gui/viewer/window.py`
- Modify: `smart_gallery/gui/library/viewer.py` (delete the old `MediaViewer`, `fitted_size`, `FIT_*`, `MIN_AREA`, `_metadata`, `FIELDS`; keep `format_size`/`format_duration` re-exported from `viewer/info.py`, `_to_qimage`, `show_in_explorer`, `open_folder`; add `from smart_gallery.gui.viewer.window import MediaViewer`), `smart_gallery/gui/library/view.py` (pass `holder=lambda row: self.grid_model.holders.get(row.id)`)
- Test: `tests/gui/test_viewer_window.py`; Modify `tests/gui/test_library_view.py` (drop `test_fitted_size_follows_the_image_shape` and `test_viewer_window_matches_each_photo_shape`; keep the double-click test)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `class MediaViewer(QWidget)`:
  - `__init__(ctx, root: Path, rows: list[LibraryRow], index: int, parent: QWidget | None = None, *, holder: Callable[[LibraryRow], str | None] | None = None)`
  - kept API: `row()`, `abspath()`, `show_index(i)`, `show_previous()`, `show_next()`, `field(name) -> str`, `has_preview() -> bool` (a sharp image is shown), `prev_btn`/`next_btn` (the stage's arrows), `rows`, `_index`.
  - `field(name)` names: `"Path"` (relpath), `"Taken"`, `"Camera"` (camera model), `"Position"` (`"12 / 340"`).
  - new: `stage: PhotoStage`, `info_panel: QWidget` (visible ⇔ info open), `title_label`, `position_label`, `status_label` (empty QLabel in the top bar for subclasses), toolbar buttons `zoom_btn`, `faces_btn`, `folder_btn`, `full_btn`, `info_btn` (checkable), `close_btn`, `open_btn` (videos only, opens with `QDesktopServices.openUrl`).
  - hooks for subclasses: `boxes() -> list[Box]` (default: every face of the photo with its name when `faces_btn` is checked, else none); `on_shown() -> None` called after each `show_index` (default no-op).
  - Keys (`QShortcut`, `Qt.WindowShortcut`): Left/Right, Home/End, I, S (no-op until Task 6), Z, F, Esc (leave full screen, else close).
  - Loading order in `show_index`: set labels + position; `stage.set_nav`; if the row is offloaded → message `f"Kept on {label} only."` / `"Kept on a mirror drive only."`; else show the cached thumbnail at once (`thumb_path(root, id, mtime_ns, ext)` if the file exists, else `ctx.thumbs.request(...)` and use its `ready`), `stage.set_loading(True)`, `PreviewLoader.request(row.id, ...)`, then `prefetch` index±1; `RepoQuery` loads `repo.get_by_id` + `info.photo_people` and fills the info panel. Results whose key is not the current row's id are ignored. Error `"missing"` → `"Drive not connected."` when the root is missing, else `"No preview for this file."`; video → its thumbnail with a play glyph overlay and `open_btn` shown.
  - Zooming in requests `ZOOM_EDGE` for the current row and swaps it in when it arrives (same `image_rect` centre).
  - State: `load_state` on open → geometry via `place(...)`, info/strip toggles; `save_state` on close.
  - Closes on `ctx.gallery_changed`.

- [ ] **Step 1: Failing tests** (`tests/gui/test_viewer_window.py`, real JPEGs written with Pillow into `repo.drive_root`, `ctx` like `tests/gui/test_library_view.py`, `SG_APP_DATA` monkeypatched to `tmp_path`)

```python
def test_controls_stay_put_between_shapes(qtbot, ...):  # portrait then landscape
    v = _open(rows=[portrait, wide], index=0); qtbot.waitUntil(v.has_preview)
    before = [w.mapTo(v, QPoint()) for w in (v.prev_btn, v.next_btn, v.info_btn, v.close_btn)] + [v.size()]
    v.show_next(); qtbot.waitUntil(lambda: v.field("Path") == "a/wide.jpg" and v.has_preview())
    assert [w.mapTo(v, QPoint()) for w in (...)] + [v.size()] == before

def test_fields_and_position(qtbot, ...): ...  # field("Camera") == "Canon EOS R6"; field("Position") == "1 / 2"; prev disabled at 0
def test_key_burst_ends_on_last_photo(qtbot, ...):  # Review Focus 1
    for _ in range(9): v.show_next()
    qtbot.waitUntil(lambda: v.has_preview()); assert v.field("Path") == rows[9].relpath and v.field("Position") == "10 / 10"
def test_missing_file_shows_message_and_keeps_nav(qtbot, ...):  # Review Focus 2
    ...; assert v.stage message == "No preview for this file."; v.show_next() works
def test_offloaded_row_message(qtbot, ...): ...  # holder -> "Kept on Backup D only."
def test_closes_on_gallery_change(qtbot, ...): ...  # Review Focus 5
def test_info_toggle_remembered(qtbot, ...): ...  # press I -> panel hidden; close; reopen -> hidden
def test_esc_leaves_full_screen_then_closes(qtbot, ...): ...
def test_thumbnail_shown_before_preview(qtbot, ...):  # cached thumb file exists, PreviewLoader.request patched to never answer
    assert v.stage has an image and not v.has_preview()
```
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_viewer_window.py -q` — FAIL.
- [ ] **Step 3: Implement `window.py`;** layout: `QVBoxLayout(top bar fixed 40 px, QHBoxLayout(stage stretch, info_panel fixed 260 px), filmstrip slot)`. Info panel renders `InfoSection`s: label (uppercase, 10 px, `PlaceholderText` colour), `heading` with `theme.serif_font(15)`, `lines` as selectable QLabels (first normal, others secondary colour), chips as rounded QLabels (accent border for `accent_chips`). Update `library/viewer.py` and `library/view.py` as listed.
- [ ] **Step 4: Run** `uv run pytest tests/gui -q` — PASS (fix `test_library_view.py` as listed).
- [ ] **Step 5: Commit** `git commit -m "Replace the photo viewer with the fixed-layout Studio window"`

---

### Task 6: Filmstrip

**Files:**
- Create: `smart_gallery/gui/viewer/filmstrip.py`
- Modify: `smart_gallery/gui/viewer/window.py` (slot it in; S key, `stage.strip_tab`, remembered `strip_open`)
- Test: `tests/gui/test_viewer_filmstrip.py`

**Interfaces:**
- Consumes: `ctx.thumbs` (`request(root, media_id, mtime_ns, relpath, ext, media_type)`, `ready(root, id, path)`), `thumb_path`.
- Produces: `class Filmstrip(QListView)`: `STRIP_HEIGHT = 72`; signal `picked(int)` (row index); `set_rows(root: Path, rows: list[LibraryRow]) -> None`; `set_current(index: int) -> None` (accent outline, `scrollTo` centre). Requests thumbnails for visible items only (debounced like `PhotoGrid._request_visible`), never calls `ctx.thumbs.cancel_pending()` (the Library grid owns that).
- Window: `MediaViewer.strip` attribute; `stage.set_strip_handle(not strip_open)`; toggling changes only the stage height.

- [ ] **Step 1: Failing tests**

```python
def test_strip_hidden_by_default_and_toggles(qtbot, ...):
    v = _open(...); assert not v.strip.isVisible()
    top_before = v.prev_btn.mapTo(v, QPoint()).x(), v.info_btn.mapTo(v, QPoint())
    QTest.keyClick(v, Qt.Key.Key_S); assert v.strip.isVisible()
    assert (v.prev_btn.mapTo(v, QPoint()).x(), v.info_btn.mapTo(v, QPoint())) == top_before
def test_click_in_strip_jumps(qtbot, ...): ...  # strip.picked.emit(3) -> field("Position") == "4 / N"
def test_strip_state_remembered(qtbot, ...): ...
def test_strip_follows_current(qtbot, ...): ...  # show_next -> strip.currentIndex().row() == 1
```
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/gui -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Add the viewer filmstrip, hidden by default"`

---

### Task 7: Callers: face viewer, RAW picker, people boxes

**Files:**
- Modify: `smart_gallery/gui/people/faces_grid.py` (`FaceViewer`), `smart_gallery/gui/storage/raw_view.py` (`_PickViewer`), `smart_gallery/gui/people/person_view.py` (import only, if needed)
- Test: `tests/gui/test_person_view.py`, `tests/gui/test_raw_view.py`

**Interfaces:**
- Consumes: `MediaViewer.boxes()`, `on_shown()`, `status_label`, `faces_btn`, `Box`, `face_rect`.
- Produces:
  - `FaceViewer.boxes()` → `[Box(face_rect(self.tile(), image_size))]` when `faces_btn` is checked (checked by default in the face viewer) and `face_rect` is not None; `_decorate` is removed.
  - `_PickViewer`: drops its own Left/Right handling (base shortcuts work unfocused); Space via `QShortcut`; `status_label` shows `PICKED`/`NOT_PICKED`; window title is the file name only.

- [ ] **Step 1: Update tests:** in `test_person_view.py`, replace `test_face_viewer_outlines_the_face` with one asserting `viewer.boxes()` has one box at `QRectF(100, 100, 80, 80)` for a 400×400 preview of a 200×200 scan with bbox `(50, 50, 40, 40)`, and none after `viewer.faces_btn.setChecked(False)`. In `test_raw_view.py`, replace `"Picked" in viewer.windowTitle()` with `viewer.status_label.text() == PICKED` (and `NOT_PICKED`), and add a Space test while a toolbar button has focus.
- [ ] **Step 2: Run** `uv run pytest tests/gui/test_person_view.py tests/gui/test_raw_view.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/gui -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Move the face viewer and RAW picker onto the Studio viewer"`

---

### Task 8: Docs and full check

**Files:**
- Modify: `docs/USER_GUIDE.md` (viewer section near line 263: layout, keys table from the spec, filmstrip hidden by default, zoom), `docs/TECHNICAL_REFERENCE.md` (viewer module list), `CLAUDE.md` (one line under the Desktop app section: `gui/viewer/` is the photo viewer every page opens)

- [ ] **Step 1:** Update the three docs.
- [ ] **Step 2: Run** `uv run pytest -q -m "not exiftool"` and `uv run pytest -q -m exiftool` — all pass; `uv run ruff check smart_gallery/gui/viewer tests/gui/test_viewer_*.py` clean.
- [ ] **Step 3:** Launch the app (`uv run smart-gallery gui`) offscreen-render the viewer on a landscape and a portrait photo and look at the screenshots; fix anything that looks off.
- [ ] **Step 4: Commit** `git commit -m "Document the Studio photo viewer"`
