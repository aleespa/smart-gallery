"""The photo viewer ("Studio"): a fixed top bar, the stage with its arrows,
an info panel on the right and a filmstrip below, hidden by default.

The window keeps the size the owner gives it; only the photo inside the
stage changes from one item to the next, so nothing the owner clicks moves.
Every page opens this one viewer: the Library, a person's photos, the face
viewer and the RAW picker (subclasses add what they need through
``boxes()``, ``on_shown()`` and ``status_label``).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QFont,
    QGuiApplication,
    QImage,
    QPalette,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from smart_gallery.config import to_abspath
from smart_gallery.gui import paths, theme
from smart_gallery.gui.backdrop import BackdropMixin
from smart_gallery.gui.library.model import LibraryRow
from smart_gallery.gui.titlebar import (
    RESIZE_MARGIN,
    ResizeFrame,
    TitleBar,
    WindowButtons,
    round_corners,
)
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.gui.thumbs.cache import thumb_path
from smart_gallery.gui.viewer import icons
from smart_gallery.gui.viewer.filmstrip import Filmstrip
from smart_gallery.gui.viewer.info import InfoSection, build_info, photo_people
from smart_gallery.gui.viewer.previews import PREVIEW_EDGE, ZOOM_EDGE, PreviewLoader
from smart_gallery.gui.viewer.stage import Box, PhotoStage, face_rect
from smart_gallery.gui.viewer.state import (
    STATE_FILE,
    ViewerState,
    load_state,
    place,
    save_state,
)

NO_PREVIEW = "No preview for this file."
DISCONNECTED = "Drive not connected."
OFFLOADED = "Kept on {label} only."
OFFLOADED_SOMEWHERE = "Kept on a mirror drive only."
TOP_BAR_HEIGHT = 40
INFO_WIDTH = 260
CHIP_STYLE = "border: 1px solid palette(mid); border-radius: 9px; padding: 1px 8px;"
ACCENT_CHIP_STYLE = (
    f"border: 1px solid {theme.ACCENT}; border-radius: 9px; padding: 1px 8px;"
)
_TINT = theme.qcolor(theme.ACCENT)
TOOL_STYLE = (
    "QToolButton { border: none; border-radius: 6px; padding: 4px; }"
    " QToolButton:hover { background: palette(midlight); }"
    # A soft accent tint for a toggled button, not a solid block of colour.
    f" QToolButton:checked {{ background: rgba({_TINT.red()}, {_TINT.green()},"
    f" {_TINT.blue()}, 60); border-bottom: 2px solid {theme.ACCENT}; }}"
)
TIPS = {
    "zoom": "Zoom: fit or 100 % (Z)",
    "faces": "Show faces",
    "folder": "Show in Explorer",
    "open": "Open in the default app",
    "full": "Full screen (F)",
    "info": "Info (I)",
}


def show_in_explorer(abspath: Path) -> None:
    """Open Explorer with the file selected (a file manager elsewhere)."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(abspath)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(abspath.parent)))


class FlowLayout(QLayout):
    """Lays its items out left to right, wrapping onto new lines (chips)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 (Qt API)
        self._items.append(item)

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802 (Qt API)
        self.addChildWidget(widget)
        self.addItem(QWidgetItem(widget))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 (Qt API)
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 (Qt API)
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        return self._arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt API)
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt API)
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, move: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line:
                x, y, line = rect.x(), y + line + self._gap, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line = max(line, hint.height())
        return y + line - rect.y()


class MediaViewer(BackdropMixin, QWidget):
    closed = Signal()  # the window was closed

    def __init__(
        self,
        ctx,
        root: Path,
        rows: list[LibraryRow],
        index: int,
        parent: QWidget | None = None,
        *,
        holder: Callable[[LibraryRow], str | None] | None = None,
    ):
        super().__init__(parent, Qt.WindowType.Window)
        # The app's own title bar (the top bar), as in the main window:
        # Acrylic behind it and the info panel on Windows 11 (the Windows
        # title bar hidden), elsewhere no system frame at all.
        if not self.init_backdrop(own_title_bar=True):
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.ctx = ctx
        self.root = Path(root)
        self.rows = rows
        self._index = index
        self._holder = holder
        self._fields: dict[str, str] = {}
        self._people = None
        self._state_path = paths.app_data_dir() / STATE_FILE
        self._state = load_state(self._state_path)
        self._previews = PreviewLoader(self)
        self._previews.ready.connect(self._on_preview)
        self._query = RepoQuery(ctx, self)
        self._query.finished.connect(self._on_info)
        self._query.failed.connect(self._on_info_failed)

        self._build()
        if self.backdrop:  # let the backdrop show behind the info panel
            self.info_panel.setStyleSheet("QScrollArea { background: transparent; }")
            self.info_panel.viewport().setAutoFillBackground(False)
            self.info_body.setAutoFillBackground(False)  # setWidget turned it on
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(place(self._state.geometry, self._home_screen()))
        self._set_info_open(self._state.info_open)
        self._set_strip_open(self._state.strip_open)

        thumbs = getattr(ctx, "thumbs", None)
        if thumbs is not None:
            thumbs.ready.connect(self._on_thumb)
        # The rows belong to the gallery that was open; another has other ids.
        ctx.gallery_changed.connect(self.close)
        self.finish_backdrop(before_show=False)  # parented inside the main window
        self.show_index(index)

    # ── building ────────────────────────────────────────────────────────────
    def _tool(self, name: str, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        color = self.palette().color(QPalette.ColorRole.WindowText)
        button.setIcon(icons.icon(name, color))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(TIPS.get(name, ""))
        button.setCheckable(checkable)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setStyleSheet(TOOL_STYLE)
        return button

    def _build(self) -> None:
        self.top_bar = TitleBar()
        self.top_bar.setFixedHeight(TOP_BAR_HEIGHT)
        self.title_label = QLabel()
        # Its own width, but a long name is cut rather than pushing the
        # centred tools aside.
        self.title_label.setMinimumWidth(40)
        bold = QFont(self.title_label.font())
        bold.setBold(True)
        self.title_label.setFont(bold)
        self.position_label = QLabel()
        self.position_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.status_label = QLabel()
        self.zoom_btn = self._tool("zoom")
        self.faces_btn = self._tool("faces", checkable=True)
        self.folder_btn = self._tool("folder")
        self.open_btn = self._tool("open")
        self.full_btn = self._tool("full")
        self.info_btn = self._tool("info", checkable=True)
        # Left: the name and position; middle: the tools, centred in the
        # window; right: minimize, maximize and close. The two sides stretch
        # alike, which keeps the middle centred.
        left = QWidget()
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(14, 0, 0, 0)
        left_row.setSpacing(10)
        left_row.addWidget(self.title_label)
        left_row.addWidget(self.position_label)
        left_row.addWidget(self.status_label)
        left_row.addStretch(1)
        self.tools = QWidget()
        tool_row = QHBoxLayout(self.tools)
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.setSpacing(10)
        for button in (
            self.zoom_btn,
            self.faces_btn,
            self.folder_btn,
            self.open_btn,
            self.full_btn,
            self.info_btn,
        ):
            tool_row.addWidget(button)
        right = QWidget()
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.addStretch(1)
        self.window_buttons = WindowButtons(self)
        right_row.addWidget(self.window_buttons, 0, Qt.AlignmentFlag.AlignTop)
        for side in (left, right):
            side.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bar = QHBoxLayout(self.top_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(0)
        bar.addWidget(left, 1)
        bar.addWidget(self.tools)
        bar.addWidget(right, 1)

        self.stage = PhotoStage()
        self.prev_btn = self.stage.prev_btn
        self.next_btn = self.stage.next_btn

        self.info_body = QWidget()
        self.info_layout = QVBoxLayout(self.info_body)
        self.info_layout.setContentsMargins(14, 14, 14, 14)
        self.info_layout.setSpacing(14)
        self.info_panel = QScrollArea()
        self.info_panel.setFixedWidth(INFO_WIDTH)
        self.info_panel.setWidgetResizable(True)
        self.info_panel.setFrameShape(QScrollArea.Shape.NoFrame)
        self.info_panel.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.info_panel.setWidget(self.info_body)
        self.info_panel.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # arrows page photos

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addWidget(self.stage, 1)
        middle.addWidget(self.info_panel)
        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        self.body.addWidget(self.top_bar)
        self.body.addLayout(middle, 1)
        self.strip = Filmstrip(self.ctx)
        self.strip.set_rows(self.root, self.rows)
        self.body.addWidget(self.strip)
        self.frame = ResizeFrame(content)  # the edge margin resizes the window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)

        self.stage.previous.connect(self.show_previous)
        self.stage.next.connect(self.show_next)
        self.stage.zoom_changed.connect(self._on_zoom)
        self.stage.strip_toggle.connect(self.toggle_strip)
        self.strip.picked.connect(self.show_index)
        self.zoom_btn.clicked.connect(lambda: self.stage.toggle_zoom())
        self.faces_btn.toggled.connect(lambda _on: self._apply_boxes())
        self.folder_btn.clicked.connect(lambda: show_in_explorer(self.abspath()))
        self.open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.abspath())))
        )
        self.full_btn.clicked.connect(self.toggle_full_screen)
        self.info_btn.toggled.connect(self._set_info_open)

    def _key_actions(self) -> dict:
        """Key → action. Handled in ``keyPressEvent``, so they work whatever
        has focus inside the window (children pass unused keys up)."""
        return {
            Qt.Key.Key_Left: self.show_previous,
            Qt.Key.Key_Right: self.show_next,
            Qt.Key.Key_Home: lambda: self.show_index(0),
            Qt.Key.Key_End: lambda: self.show_index(len(self.rows) - 1),
            Qt.Key.Key_I: self.info_btn.toggle,
            Qt.Key.Key_S: self.toggle_strip,
            Qt.Key.Key_Z: lambda: self.stage.toggle_zoom(),
            Qt.Key.Key_F: self.toggle_full_screen,
            Qt.Key.Key_Escape: self._escape,
        }

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        action = None
        if not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            action = self._key_actions().get(event.key())
        if action is None:
            super().keyPressEvent(event)
            return
        action()
        event.accept()

    def _home_screen(self) -> QRect:
        """The available area of the screen the window was left on (a
        monitor unplugged since: the window's own, else the primary)."""
        saved = self._state.geometry
        screen = None
        if saved is not None:
            screen = QGuiApplication.screenAt(QRect(*saved).center())
        screen = screen or self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    # ── navigation ──────────────────────────────────────────────────────────
    def row(self) -> LibraryRow:
        return self.rows[self._index]

    def abspath(self) -> Path:
        return to_abspath(self.row().relpath, self.root)

    def show_previous(self) -> None:
        if self._index > 0:
            self.show_index(self._index - 1)

    def show_next(self) -> None:
        if self._index < len(self.rows) - 1:
            self.show_index(self._index + 1)

    def show_index(self, index: int) -> None:
        if not 0 <= index < len(self.rows):
            return
        self._index = index
        row = self.row()
        name = PurePosixPath(row.relpath).name
        position = f"{index + 1} / {len(self.rows)}"
        self.setWindowTitle(name)
        self.title_label.setText(name)
        self.position_label.setText(position)
        self._fields = {"Path": row.relpath, "Position": position}
        self._people = None
        self.faces_btn.hide()
        self.open_btn.setVisible(row.media_type == "video")
        self.stage.set_nav(index > 0, index < len(self.rows) - 1)
        self.strip.set_current(index)
        self.stage.set_boxes([])
        self.stage.set_play_glyph(False)
        self._show_sections([])

        # Decodes queued for photos passed on the way would delay this one.
        self._previews.cancel_pending()
        if row.offloaded:
            label = self._holder(row) if self._holder else None
            self.stage.set_message(
                OFFLOADED.format(label=label) if label else OFFLOADED_SOMEWHERE
            )
        else:
            self._show_picture(row)
        self._query.run(
            lambda repo, mid=row.id: (mid, repo.get_by_id(mid), photo_people(repo, mid))
        )
        self.on_shown()

    def _show_picture(self, row: LibraryRow) -> None:
        thumb = self._cached_thumbnail(row)
        if row.media_type == "video":
            if thumb is None:
                self.stage.set_message(NO_PREVIEW)
            else:
                self.stage.set_image(thumb, sharp=False)
                self.stage.set_play_glyph(True)
            return
        if thumb is not None:
            self.stage.set_image(thumb, sharp=False)
        else:
            self.stage.set_message("")
        self.stage.set_loading(True)
        self._previews.request(row.id, self.abspath(), row.ext, row.media_type)
        self._previews.prefetch(
            [
                (r.id, to_abspath(r.relpath, self.root), r.ext, r.media_type)
                for r in (self._neighbour(1), self._neighbour(-1))
                if r is not None and not r.offloaded
            ]
        )

    def _neighbour(self, step: int) -> LibraryRow | None:
        i = self._index + step
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def _cached_thumbnail(self, row: LibraryRow) -> QImage | None:
        path = thumb_path(self.root, row.id, row.mtime_ns, row.ext)
        if path.is_file():
            image = QImage(str(path))
            if not image.isNull():
                return image
        thumbs = getattr(self.ctx, "thumbs", None)
        if thumbs is not None:
            thumbs.request(
                self.root, row.id, row.mtime_ns, row.relpath, row.ext, row.media_type
            )
        return None

    # ── results ─────────────────────────────────────────────────────────────
    def _on_thumb(self, root: str, media_id: int, path: str) -> None:
        if root != str(self.root) or not path or media_id != self.row().id:
            return
        row = self.row()
        if self.stage.has_sharp_image() or row.offloaded:
            return
        if self.stage.message() and row.media_type != "video":
            return  # the preview already said there is none
        image = QImage(path)
        if image.isNull():
            return
        self.stage.set_image(image, sharp=False)
        self.stage.set_play_glyph(row.media_type == "video")
        if (
            row.media_type != "video"
            and self._previews.cached(row.id, PREVIEW_EDGE) is None
        ):
            self.stage.set_loading(True)

    def _on_preview(self, result) -> None:
        if result.key != self.row().id:
            return  # the owner has moved on
        if result.edge == ZOOM_EDGE:
            if result.image is not None and self.stage.is_zoomed():
                self.stage.set_image(result.image, sharp=True, keep_view=True)
                self._apply_boxes()
            return
        if result.image is not None:
            self.stage.set_image(result.image, sharp=True)
            self._apply_boxes()
        elif result.error == "missing" and not self.root.exists():
            self.stage.set_message(DISCONNECTED)
        else:
            self.stage.set_message(NO_PREVIEW)

    def _on_zoom(self, zoomed: bool) -> None:
        row = self.row()
        if zoomed and row.media_type == "image" and not row.offloaded:
            self._previews.request(
                row.id, self.abspath(), row.ext, row.media_type, ZOOM_EDGE
            )

    def _on_info(self, result) -> None:
        media_id, item, people = result
        if media_id != self.row().id or item is None:
            return
        sections = build_info(item, people)
        heading = next((s.heading for s in sections if s.key == "date"), "")
        self._fields.update(Taken=heading, Camera=item.camera or "")
        self._show_sections(sections)
        self._people = people
        has_faces = bool(people and people.faces) and item.media_type == "image"
        self.faces_btn.setVisible(has_faces)
        self._apply_boxes()

    def _on_info_failed(self, error) -> None:
        if isinstance(error, FileNotFoundError) and not self.root.exists():
            if not self.stage.has_sharp_image():
                self.stage.set_message(DISCONNECTED)

    # ── info panel ──────────────────────────────────────────────────────────
    def _show_sections(self, sections: list[InfoSection]) -> None:
        while self.info_layout.count():
            item = self.info_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for section in sections:
            self.info_layout.addWidget(self._section_widget(section))
        self.info_layout.addStretch(1)

    def _section_widget(self, section: InfoSection) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        if section.key != "date":
            label = QLabel(section.label.upper())
            small = QFont(label.font())
            small.setPointSizeF(max(7.5, small.pointSizeF() - 1.5))
            small.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
            label.setFont(small)
            label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
            layout.addWidget(label)
        if section.heading:
            heading = QLabel(section.heading)
            heading.setFont(theme.serif_font(15))
            layout.addWidget(heading)
        for i, text in enumerate(section.lines):
            line = QLabel(text)
            line.setWordWrap(True)
            line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if i or section.heading:
                line.setForegroundRole(QPalette.ColorRole.PlaceholderText)
            layout.addWidget(line)
        if section.chips or section.accent_chips:
            chips = QWidget()
            flow = FlowLayout(chips)
            for text in section.accent_chips:
                flow.addWidget(self._chip(text, ACCENT_CHIP_STYLE))
            for text in section.chips:
                flow.addWidget(self._chip(text, CHIP_STYLE))
            chips.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
            )
            layout.addWidget(chips)
        return box

    @staticmethod
    def _chip(text: str, style: str) -> QLabel:
        chip = QLabel(text)
        chip.setStyleSheet(style)
        return chip

    def info_text(self) -> str:
        """All the info panel's text, for tests."""
        return "\n".join(label.text() for label in self.info_body.findChildren(QLabel))

    def _set_info_open(self, on: bool) -> None:
        self.info_panel.setVisible(on)
        if self.info_btn.isChecked() != on:
            self.info_btn.setChecked(on)
        self._state.info_open = on

    def toggle_strip(self) -> None:
        self._set_strip_open(not self.strip.isVisibleTo(self))

    def _set_strip_open(self, on: bool) -> None:
        self.strip.setVisible(on)
        self.stage.set_strip_handle(not on)
        self._state.strip_open = on

    # ── boxes ───────────────────────────────────────────────────────────────
    def boxes(self) -> list[Box]:
        """The outlines to draw on the shown image: every face with its name
        while the faces button is on."""
        people = self._people
        if not self.faces_btn.isChecked() or people is None:
            return []
        size = self.stage.image_size()
        out = []
        for face in people.faces:
            rect = face_rect(face, size)
            if rect is not None:
                out.append(Box(rect, face.name or ""))
        return out

    def _apply_boxes(self) -> None:
        self.stage.set_boxes(self.boxes())

    def on_shown(self) -> None:
        """Called after each ``show_index``; for subclasses."""

    # ── window ──────────────────────────────────────────────────────────────
    def toggle_full_screen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.close()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        if not self.backdrop:
            round_corners(self)  # frameless: rounded like any Windows 11 window

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.WindowStateChange:
            # No resize border while the window fills the screen.
            full = self.isMaximized() or self.isFullScreen()
            self.frame.set_margin(0 if full else RESIZE_MARGIN)
        super().changeEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if not self.isVisible():  # already closed (hidden): nothing to do
            event.ignore()
            return
        geometry = self.normalGeometry() if self.isFullScreen() else self.geometry()
        self._state.geometry = (
            geometry.x(),
            geometry.y(),
            geometry.width(),
            geometry.height(),
        )
        save_state(self._state_path, self._state)
        self._previews.cancel_pending()
        # Hide rather than close: Qt destroys a closed window's native
        # window, and for this translucent window parented inside the main
        # window that repainted a grey patch over the main window's corner.
        # The native window goes when the owner deletes the viewer.
        event.ignore()
        self.hide()
        self.closed.emit()

    # ── for tests and callers ───────────────────────────────────────────────
    def field(self, name: str) -> str:
        return self._fields.get(name, "")

    def has_preview(self) -> bool:
        return self.stage.has_sharp_image()


__all__ = ["MediaViewer", "ViewerState", "NO_PREVIEW", "DISCONNECTED"]
