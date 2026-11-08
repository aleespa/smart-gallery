# UI consistency pass — design

Approved 2026-11-08. Built from a full page survey (Library, Stats, Storage
and its three tools, People, History, Advanced, Import and Export windows).
The theme layer is sound (tokenised colours, few literals); the drift is in
structure, button hierarchy, wording and number formats.

## 1. Button roles and places

- **Primary** (`theme.primary`, coral fill): at most one *visible* per page or
  window.
- **Secondary**: plain button. Everything else, including *Clear all filters*
  and the Library's *Export…*.
- **Destructive** (`theme.danger`, new): `ERROR`-coloured outline and text,
  no fill; filled `ERROR` only on hover. Used for the Storage tools' Recycle
  Bin actions.
- Places: a page-level action sits in the page header (right); an action on
  the current selection sits in a bottom **action bar**, right-aligned; in a
  window the primary button sits bottom-right, never full width.
- Toggles (segments, the sort-direction button) are **not** coral fills:
  a checked segment gets a soft accent tint (`ACCENT_FILL` at alpha 60) with
  normal text and a 2px `ACCENT` bottom line, like the area tabs. The sort
  direction becomes an icon button (arrow up/down) without a checked state.

## 2. Page skeleton

- Spacing scale 4 / 8 / 12 / 16 / 24 px. Page content lines up with the page
  title: `MainWindow` gives the page stack the header's side margin (24 px),
  12 px above, 16 px below; area pages' own root layouts have zero margins.
- Standard side panel widths: 260 px (folder tree, people list, command
  list), 320 px for the filter panel, 420 px for the Storage tree (it has
  columns).
- Every side panel's bottom row is a `PanelFooter` (same height, 8 px
  padding, left-aligned controls).

## 3. Page header

- A page may expose `summary_changed = Signal(str)`. The header shows the
  latest text as a muted line right of the title, baseline-aligned. Library
  (`84,355 files · 781 GB`), Storage (`781 GB in 84,355 files` → same format
  as Library: `{files} · {size}`), People (`79 photos not scanned yet`) use it;
  their old labels in `header_controls` go.
- `header_controls` order: secondary buttons, then the primary, at the right.

## 4. Sections and surfaces

- `SectionHeader(title, subtitle="")`: serif `HEADER_PT` title plus muted
  subtitle. Replaces the bold-sans titles in Storage tools and Advanced.
- `QGroupBox` everywhere renders as a **card**: `palette(base)` background,
  1px `palette(mid)` border, 6px radius, serif title inside the card's top
  padding (no Windows notch). One style sheet in `theme.CARD_STYLE`, applied by
  `MainWindow` and `FormWindow`.
- Surface tokens: page (`Window`), panel/card (`Base`), raised (`AlternateBase`).

## 5. Shared states

- `EmptyState`: optional icon, one line, optional hint, optional action
  button; the button is hidden unless an action is given. Replaces the
  message labels of Library, Stats, Storage and People (fixes People's empty
  grey button while loading).
- One thumbnail placeholder (`thumbs.placeholder.placeholder_pixmap(kind,
  size)`), the Library's glyph tile, used by Bursts and RAW → JPEG too.

## 6. Wording and formats

- `smart_gallery/format.py` (Qt-free): `format_size` (1024-based, three
  significant figures: `781 GB`, `45.6 GB`, `7.43 MB`), `format_count(n,
  noun)` (`84,355 files`, `1 file`), `format_duration(seconds)` (`15h 54m
  59s`, `4m 3s`, `12s`). Every size/count/duration label in the GUI and the
  Stats tiles goes through it. `storage.tree.format_bytes` and
  `viewer.info.format_size` become aliases or are removed.
- Glossary: **photos / videos / other files**; **files**, never "items";
  sentence case everywhere (Stats tiles: `Total files`, `Photos`, `Videos`,
  `Total size`, `Video length`).
- Storage tools: **selected** and **Clear selection** in all three
  (footers `{n} selected · frees {size}`; notes `Unselected pairs are left
  alone`, `Unselected bursts are left alone`; leave guard `You have {n}
  selections not applied yet. Discard them?`).

## 7. Pages

- **History**: status as a coloured pill (`done` green `KEEP`, `failed`
  `ERROR`, `cancelled` neutral); Started shows `Today 17:01`, `Yesterday
  09:12`, else `2026-11-07 17:01`, column sized to contents; headers
  left-aligned; the log pane shows an `EmptyState` ("Select a job to see its
  log") until a row is selected; bottom `ActionBar`.
- **Advanced**: list shows readable labels grouped under non-selectable
  headings Catalog / Mirror / Files / Faces / People, CLI name muted to the
  right; title is a `SectionHeader` (label + help as subtitle).
- **Import / Export windows**: both use `FormWindow`'s header (serif title +
  summary) and footer (`ActionBar`: dry run left, primary right). Import's
  sections become cards; its Commands box becomes the same collapsible
  `Disclosure("Command")` Export has.
- **Stats**: the filter panel collapses with the same « button and strip as
  the Library (`CollapsibleFilters`, shared).
- **Icons**: `viewer/icons.py` moves to `gui/icons.py` and gains `sort-up`,
  `sort-down`, `refresh`, `undo`, `browse`; used on the sort toggle, Import's
  Refresh, People's Undo and every Browse… button (icon + text).
