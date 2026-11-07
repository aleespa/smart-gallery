# Photo viewer redesign ("Studio")

Date: 2026-11-07. Replaces the current `gui/library/viewer.py:MediaViewer`.
The mockup the owner approved is option A of the brainstorm screens
(`viewer-studio-v2.html`): photo stage, info panel on the right, filmstrip
hidden by default.

## Why

The viewer opens from the Library, a person's photos, the face grids (face
viewer) and the RAW picker. Today it resizes its window to each photo's shape
(`_fit_to` / `fitted_size`), so going from a landscape to a portrait photo
moves the Previous/Next buttons and the owner loses their place while
stepping through photos. It also looks like a plain dialog: a form of fields
and a row of push buttons. The owner wants a professional photo viewer in the
spirit of Lightroom's loupe view, matching the app's dark theme and Sunset
accent.

## Goals

- **Nothing moves between photos.** The window keeps the size the owner gives
  it; each photo is fitted (letterboxed) inside a fixed stage. Arrows, top bar
  and panels stay exactly where they are, whatever the photo's shape.
- **A professional look:** a neutral dark stage, slim chrome, the app's
  fonts (Segoe UI Variable for text, Georgia for the date heading) and the one
  accent colour (`palette(highlight)` / `theme.ACCENT`), no hard-coded
  colours outside `colors.py`.
- **Fast stepping:** the next/previous photos are prepared ahead, and a photo
  shows its cached thumbnail immediately, sharpening when the full preview is
  ready, instead of a "Loading…" flash.
- **One viewer everywhere.** The Library, person photos, the face viewer and
  the RAW picker all use it, keeping what each adds today.

## Non-goals

- Editing, rating, flags, deleting or moving files.
- Video playback (videos keep showing their thumbnail; see Videos below).
- A slideshow.
- Opening the viewer inside the main window instead of its own window.

## Anti-goals (a failure even if it works)

- Any control that shifts position when the photo changes.
- Showing a face box in the wrong place (better none than a wrong one; this
  rule already holds in `faces_grid.face_rect`).
- Blocking the GUI thread on file reads, decoding or catalog queries.

## The page

```
┌──────────────────────────────────────────────────────────────────┐
│ IMG_4512.CR3   12 / 340                 ⌕  ☺  ▤  ⤢  ⓘ  ✕        │ top bar
├───────────────────────────────────────────────┬──────────────────┤
│                                               │ 14 June 2026     │
│  ‹          [ photo, fitted, centred ]     ›  │ Saturday · 17:42 │
│                                               │ EXPOSURE         │
│                                               │ (f/2.8)(1/250 s) │
│                                               │ (ISO 400)(50 mm) │
│                                               │ CAMERA           │
│                                               │ Canon EOS R6     │
│                                               │ RF 24-105mm F4 L │
│                                               │ PEOPLE           │
│                                               │ (Ana)(Luis)(+1)  │
│                                               │ FILE             │
│                  [▴ Filmstrip]                │ 6000 × 4000 · …  │
├───────────────────────────────────────────────┴──────────────────┤
│ (filmstrip, hidden by default)                                   │
└──────────────────────────────────────────────────────────────────┘
```

### Window

- A separate top-level window, as today (not modal; the owner can keep using
  the app). It opens at the size and position it had when last closed
  (remembered in the app data dir, `state_store.save_json`); the first time,
  about 80 % of the screen, centred. It never resizes itself when the photo
  changes.
- It follows the app's look: the app is frameless with its own title bar;
  whether the viewer uses the system frame or the app's `TitleBar` is the
  builder's call, but the top bar below is the viewer's own and is always
  there.

### Top bar (fixed height)

- Left: the file name (bold), then the position, `12 / 340`, in the secondary
  text colour.
- Right, icon buttons with tooltips naming their key:
  zoom (Z), face boxes (shown only for photos with faces), Show in Explorer,
  full screen (F), info panel (I, shown as checked while the panel is open),
  close (Esc).
- Subclasses can add text to the top bar (the RAW picker's "Picked / Not
  picked"; see Callers).

### Stage

- Background: a neutral dark grey (a new `VIEWER_STAGE` colour in
  `colors.py`), in light and dark app themes alike, as photo tools do.
- The photo is fitted inside the stage with an even margin and a soft
  shadow, centred. Never upscaled beyond 100 % when fitting.
- Previous / next: large chevron buttons on the stage's left and right edges,
  vertically centred, on a translucent dark pill. They are always in the same
  place; at the first/last photo the matching one is disabled (dimmed), not
  hidden.
- States in the stage centre, replacing the photo: loading (only if neither
  thumbnail nor preview arrives within ~150 ms: a small spinner, no text
  flash), "No preview for this file.", "Drive not connected.", and for an
  offloaded file "Kept on <mirror label> only." (the Library already knows the
  holder label; opened from elsewhere, without one: "Kept on a mirror drive
  only.").

### Info panel (right, fixed width ~260 px)

Open by default; **I** or the ⓘ button hides/shows it, remembered. Hiding it
widens the stage; nothing else moves. Sections, each with a small uppercase
label in the secondary colour; a section with no data is left out:

1. Date: `14 June 2026` in Georgia, then `Saturday · 17:42` below. No date:
   "Date unknown".
2. Exposure: chips `f/2.8`, `1/250 s`, `ISO 400`, `50 mm` (reuse
   `format_shutter`).
3. Camera: camera model, then lens in the secondary colour.
4. People: one chip per named person in the photo (accent-tinted border),
   then `+N unnamed` for faces in unnamed groups or not filed. Read from
   `faces`/`persons` for the media id, off the GUI thread; a catalog without
   face tables shows no section. Clicking a chip is not required (builder's
   call; if added it opens that person's page).
5. Video only: duration, codec, frame rate.
6. File: `6000 × 4000 · 28.4 MB`, then the relpath in the secondary colour,
   selectable. Location (lat/long) if present, as text.

Text in the panel is selectable.

### Filmstrip (bottom, hidden by default)

- A row of thumbnails of the rows the viewer was opened with, the current one
  outlined in the accent and kept in view. Click jumps there.
- Shown/hidden by **S** or a small "▴ Filmstrip" tab centred on the stage's
  bottom edge; the state is remembered. Opening it makes the stage shorter;
  nothing else moves.
- Thumbnails come from the shared `ctx.thumbs` loader (visible ones only,
  like the Library grid).

### Zoom

- Fit (default) and 100 %. **Z**, the zoom button, or a double-click on the
  photo toggles; at 100 % the point clicked (or the centre, for Z) stays
  under the cursor. Ctrl + mouse wheel zooms in steps around the cursor
  between fit and 200 %.
- When zoomed, drag pans; the cursor shows a hand.
- Going to another photo returns to fit.
- 100 % means the preview's pixels. Previews are capped at `PREVIEW_EDGE`
  (2048) today; when zoomed, the builder should load a larger preview for the
  current photo (the file itself for JPEG/PNG, the largest embedded preview,
  `JpgFromRaw`, for RAW) so 100 % shows real detail.

### Keys

| Key | Action |
|---|---|
| ← / → , mouse wheel over the stage (no Ctrl) | previous / next (over the info panel or filmstrip the wheel scrolls them) |
| Home / End | first / last |
| I | info panel |
| S | filmstrip |
| Z, double-click | fit ↔ 100 % |
| F | full screen |
| Esc | leave full screen; otherwise close |

Keys must work whatever has focus inside the viewer (the RAW picker today
re-implements ←/→ because shortcuts did not fire unfocused; fix that in the
base so subclasses only add their own keys).

### Speed

- On showing photo *n*, load *n*'s preview first, then *n+1* and *n-1* in the
  background; keep a small cache (a few decoded previews) so stepping back and
  forth is instant.
- Show the cached thumbnail (from `ctx.thumbs` / the on-drive cache) scaled
  to the stage at once, and swap in the preview when it arrives. A stale
  result (the owner already moved on) is dropped, as `RepoQuery` does today.
- Decoding stays off the GUI thread (`RepoQuery`/a pool, as now); the
  orientation fix for RAW previews (`thumbs/cache.py:_open_raw_preview`
  turning them by the RAW's Orientation tag) is relied on.

## Callers (keep what each adds)

- **Library** (`library/view.py:open_viewer`): rows = the grid's rows, as now.
- **Person photos** (`people/person_view.py:PhotoGrid.open_viewer`): same.
- **Face viewer** (`people/faces_grid.py:FaceViewer`): steps through faces,
  outlines the current face (the `_decorate` hook today; it must keep working
  at fit and at 100 %, and with the thumbnail placeholder it should draw only
  once the preview is shown). The face-box toggle in the top bar shows/hides
  this outline, and for other callers shows all faces of the photo with names.
- **RAW picker** (`storage/raw_view.py:_PickViewer`): Space picks/unpicks;
  "Picked / Not picked" shown in the top bar (today it is in the window
  title).

Public API used by callers and tests today: constructor
`(ctx, root, rows, index, parent)`, `row()`, `abspath()`, `show_index()`,
`show_previous()`, `show_next()`, `field(name)`, `has_preview()`,
`_decorate(image)`, `prev_btn`/`next_btn`. Keep these names working (tests in
`tests/gui/test_library_view.py`, `test_person_view.py`, `test_raw_view.py`
use them), or update the tests with the change. `fitted_size` and its test go
away with the window resizing.

## Constraints

- PySide6, inside `smart_gallery/gui/` (the `gui` extra); nothing Qt in the
  core. Colours only from `colors.py` / `theme.py`; style sheets use
  `palette(highlight)` for the accent.
- Read-only: the viewer never writes the catalog or the files.
- Videos: no playback. Show the video's thumbnail fitted on the stage with a
  play glyph; the top bar gains "Open" (default app). Their info panel shows
  the video section.
- GUI tests stay headless (`QT_QPA_PLATFORM=offscreen`).

## Left to the builder

- Widget structure (a `QGraphicsView` or a custom painted widget for the stage
  are both fine), icon set (the app has no icon font today; simple painted or
  SVG icons in the theme colours), animation (a short fade/slide for the
  filmstrip and between photos is welcome, never required).
- Exact sizes, margins and the spinner.
- System frame or the app's frameless title bar for the viewer window.
- Whether people chips are clickable.
- Where the remembered state lives (one JSON file in the app data dir, like
  the Storage and Export pages).

## Calls made in the design (not stated by the owner; check these)

The owner approved the layout (option A, filmstrip hidden by default), the
key list, remembering the window size, and one viewer for every caller. These
details were filled in by the designer:

- Window: separate, non-modal; first open at ~80 % of the screen.
- Stage stays dark even in a light app theme.
- Arrows disabled (dimmed) at the ends rather than hidden.
- Info panel: open by default and remembered; sections left out when empty;
  location shown as text only.
- Face-box button: in the face viewer it toggles the current face's outline;
  elsewhere it shows every face in the photo with names.
- Zoom: Ctrl + wheel up to 200 %, and a larger preview loaded when zoomed so
  100 % shows real detail.
- Videos: thumbnail with a play glyph and an "Open" button (default app).
- Esc leaves full screen first, then closes.
- The RAW picker's "Picked / Not picked" moves from the window title into the
  top bar.
