"""People area: find photos by who is in them.

The left column lists named people and unnamed groups (``PeopleList``); the
right side is a stack holding the message page (empty and missing states),
the person page, the several-people page and the hidden faces. The catalog is
read off the GUI thread (``people_list`` through ``RepoQuery``). Every change
(find, merge, rename, split, hide, delete, face corrections) is a CLI job;
nothing here writes the catalog.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.config import drive_root_of
from smart_gallery.gui import paths
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.jobspec import JOB_RUNNING, JobSpec
from smart_gallery.gui.people import model
from smart_gallery.gui.people.list_view import PeopleList
from smart_gallery.gui.people.person_view import (
    HiddenFacesPage,
    PersonPage,
    SeveralPeoplePage,
)
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.services.label_faces import LabelEntry, write_label_list

FIND_TITLE = "Find faces"
FIND_BUTTON = "Find new faces"
PENDING = "{n:,} images not scanned yet"
NO_GALLERY = "Open or create a gallery to see its people."
DISCONNECTED = "Drive not connected. Plug in {label} to see its people."
READ_ERROR = "Could not read the catalog: {error}"
NEEDS_UPGRADE = "This gallery needs a face update. Find faces updates it."
NO_FACES = "No faces found yet."
NOT_GROUPED = "Faces found, but not grouped into people yet. Find faces groups them."
NO_EXTRA = "Face recognition is not installed. Install it with:\nuv sync --extra faces"
PICK = "Choose someone on the left to see their photos."
MERGE_TITLE = "Merge people"
WHICH_NAME = "Which name should the merged person keep?"
RENAME_TITLE = "Rename"
RENAME_LABEL = "Name:"
MERGE_WITH = "Merge with the existing {name}?"
SPLIT_TITLE = "Split group"
HIDE_TITLE = "Hide group"
UNHIDE_TITLE = "Show group"
DELETE_TITLE = "Delete person"
DELETE_ASK = "Delete {name}? Their faces become unassigned."
FIX_TITLE = "Fix faces"
SEND_DELAY_MS = 400  # a burst of clicks becomes one job
LABELS_DIR = "face-labels"
LEAVE_ASK = "Send the face corrections you made before leaving?"
LIST_ERROR = "Could not save the face corrections: {error}"


class PeopleView(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.data: model.PeopleList | None = None
        self.loaded = False  # True once the latest load has been shown
        self._root = Path()
        self._loading_root = Path()
        self._message_callback: Callable[[], None] | None = None
        self._pending: list[LabelEntry] = []  # corrections not sent yet
        self._send_timer = QTimer(self)
        self._send_timer.setSingleShot(True)
        self._send_timer.setInterval(SEND_DELAY_MS)
        self._send_timer.timeout.connect(self._send_pending)
        self._query = RepoQuery(ctx, self)

        # Shown on the page header's right (MainWindow takes header_controls).
        self.status = QLabel()
        self.find_button = QPushButton(FIND_BUTTON)
        self.header_controls = QWidget()
        header = QHBoxLayout(self.header_controls)
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.status)
        header.addWidget(self.find_button)

        self.list = PeopleList(ctx)

        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.message_action = QPushButton()
        self.message_page = QWidget()
        page = QVBoxLayout(self.message_page)
        page.addStretch(1)
        page.addWidget(self.message)
        page.addWidget(self.message_action, 0, Qt.AlignmentFlag.AlignHCenter)
        page.addStretch(1)
        self.person_page = PersonPage(ctx)
        self.several_page = SeveralPeoplePage(ctx)
        self.hidden_page = HiddenFacesPage(ctx)

        self.right = QStackedWidget()
        self.right.addWidget(self.message_page)
        self.right.addWidget(self.person_page)
        self.right.addWidget(self.several_page)
        self.right.addWidget(self.hidden_page)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.list)
        self.splitter.addWidget(self.right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([260, 740])
        QVBoxLayout(self).addWidget(self.splitter)

        self.find_button.clicked.connect(self.find_faces)
        self.message_action.clicked.connect(self._on_message_action)
        self.list.selection_changed.connect(self._on_selection)
        self.list.merge_requested.connect(self.merge)
        self.list.rename_requested.connect(self.rename)
        self.list.split_requested.connect(self.split)
        self.list.hide_requested.connect(self.hide_person)
        self.list.delete_requested.connect(self.delete)
        self.list.show_hidden_requested.connect(self.show_hidden_faces)
        self.person_page.labels.connect(self.send_labels)
        self.person_page.rename_requested.connect(self.rename)
        self.person_page.name_entered.connect(self.name_person)
        self.hidden_page.labels.connect(self.send_labels)
        self.hidden_page.unhide_requested.connect(
            lambda pid: self.hide_person(pid, False)
        )
        self._query.finished.connect(self._on_data)
        self._query.failed.connect(self._on_failed)
        ctx.gallery_changed.connect(self._on_gallery_changed)
        ctx.catalog_updated.connect(self.reload)
        finished = getattr(ctx.jobs, "finished", None)
        if finished is not None:
            finished.connect(self._send_pending)
        ctx.add_leave_guard(self._confirm_leave)
        self.destroyed.connect(lambda *_: ctx.remove_leave_guard(self._confirm_leave))
        self.reload()

    # ── loading ─────────────────────────────────────────────────────────────
    def reload(self, *_args) -> None:
        current = self.ctx.current
        self._update_find()
        if current is None:
            self._query.cancel()
            self._clear()
            self.show_message(NO_GALLERY)
            self.loaded = True
            return
        self._loading_root = drive_root_of(Path(current.db_path))
        self._query.run(model.people_list)

    def _on_gallery_changed(self, *_args) -> None:
        # Person ids belong to one catalog: never carry a selection across.
        self._clear()
        self.reload()

    def _clear(self) -> None:
        self.data = None
        self.status.setText("")
        self.list.set_people(None)

    def _on_data(self, result: model.PeopleList) -> None:
        self.data = result
        self._root = self._loading_root
        self.list.set_people(result, self._root)
        self.person_page.set_people(result)
        self.hidden_page.set_people(result)
        n = result.pending_images
        self.status.setText(PENDING.format(n=n) if n > 0 else "")
        if not result.has_faces:
            if paths.faces_available():
                self._find_message(NO_FACES)
            else:
                self.show_message(NO_EXTRA)
        elif not (result.named or result.groups):
            self._find_message(NOT_GROUPED)
        else:
            self._on_selection(self.list.selected_ids())
        self.loaded = True

    def _on_failed(self, error) -> None:
        self._clear()
        if isinstance(error, model.NeedsUpgrade):
            self._root = self._loading_root
            if paths.faces_available():
                self._find_message(NEEDS_UPGRADE)
            else:
                self.show_message(NO_EXTRA)
        elif self.ctx.current is None:
            self.show_message(NO_GALLERY)
        elif isinstance(error, FileNotFoundError):
            label = getattr(self.ctx.current, "label", "") or str(self._loading_root)
            self.show_message(DISCONNECTED.format(label=label))
        else:
            self.show_message(READ_ERROR.format(error=error))
        self.loaded = True

    def _update_find(self) -> None:
        self.find_button.setEnabled(
            paths.faces_available() and self.ctx.current is not None
        )

    # ── right side ──────────────────────────────────────────────────────────
    def show_message(
        self,
        text: str,
        action_label: str | None = None,
        action: Callable[[], None] | None = None,
    ) -> None:
        """Show ``text`` on the right, with a button running ``action``."""
        self.message.setText(text)
        self._message_callback = action
        self.message_action.setText(action_label or "")
        self.message_action.setHidden(not action_label)
        self.message_action.setEnabled(True)
        self.right.setCurrentWidget(self.message_page)

    def _find_message(self, text: str) -> None:
        self.show_message(text, FIND_TITLE, self.find_faces)
        self.message_action.setEnabled(paths.faces_available())

    def _on_message_action(self) -> None:
        if self._message_callback is not None:
            self._message_callback()

    def _on_selection(self, ids: list) -> None:
        if self.data is None or not (self.data.named or self.data.groups):
            return
        if len(ids) == 1:
            self.person_page.show_person(ids[0])
            self.right.setCurrentWidget(self.person_page)
        elif ids:
            rows = [row for row in map(self._row, ids) if row is not None]
            self.several_page.show_people(rows)
            self.right.setCurrentWidget(self.several_page)
        elif self.right.currentWidget() is self.hidden_page:
            self.hidden_page.show_hidden(self.data.hidden_groups)  # a reload
        else:
            self.show_message(PICK)

    def show_hidden_faces(self) -> None:
        if self.data is None:
            return
        self.list.list.clearSelection()
        self.hidden_page.show_hidden(self.data.hidden_groups)
        self.right.setCurrentWidget(self.hidden_page)

    def send_labels(self, entries: list) -> None:
        """Hide the corrected faces now and send the corrections as one job."""
        entries = list(entries)
        if not entries:
            return
        gone = {face for entry in entries for face in entry.faces}
        for grid in (
            self.person_page.faces_grid,
            self.person_page.review_grid,
            self.hidden_page.faces_grid,
        ):
            grid.remove_faces(gone)
        self._pending.extend(entries)
        self._send_timer.start()

    def _send_pending(self, *_args) -> bool:
        """Start the job for the queued corrections; False when none went."""
        if not self._pending:
            return False
        self._send_timer.stop()
        jobs = self.ctx.jobs
        if jobs is None or self.ctx.current is None or jobs.running:
            return False  # sent when the running job finishes
        root = str(drive_root_of(Path(self.ctx.current.db_path)))
        try:
            path = self._new_list_path()
            write_label_list(path, self._pending)
        except OSError as exc:
            QMessageBox.warning(self, FIX_TITLE, LIST_ERROR.format(error=exc))
            return False
        spec = JobSpec(
            FIX_TITLE,
            (
                ("label-faces", root, "--list", str(path)),
                ("file-faces", root, "--no-group"),
            ),
        )
        if not jobs.start(spec):
            return False  # raced with another job: retry on its finish
        self._pending = []
        return True

    @staticmethod
    def _new_list_path() -> Path:
        folder = paths.app_data_dir() / LABELS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = folder / f"{stamp}.json"
        n = 1
        while path.exists():  # never overwrite a list a job may still read
            n += 1
            path = folder / f"{stamp}-{n}.json"
        return path

    def _confirm_leave(self) -> bool:
        if not self._pending:
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self,
            FIX_TITLE,
            LEAVE_ASK,
            buttons.Yes | buttons.No | buttons.Cancel,
        )
        if answer == buttons.Cancel:
            return False
        if answer == buttons.Yes:
            self._send_pending()
        else:
            self._pending = []
        self._send_timer.stop()
        return True

    # ── jobs ────────────────────────────────────────────────────────────────
    def _start(self, title: str, *argvs: tuple[str, ...]) -> None:
        jobs = self.ctx.jobs
        if jobs is None:
            return
        if not jobs.start(JobSpec(title, tuple(argvs))):
            QMessageBox.information(self, title, JOB_RUNNING)

    def _row(self, pid: int) -> model.PersonRow | None:
        if self.data is None:
            return None
        for row in (*self.data.named, *self.data.groups, *self.data.hidden_groups):
            if row.id == pid:
                return row
        return None

    def find_faces(self) -> None:
        if self.ctx.current is None:
            return
        root = str(drive_root_of(Path(self.ctx.current.db_path)))
        self._start(FIND_TITLE, ("find-faces", root))

    def merge(self, dst: int, srcs: list) -> None:
        srcs = [int(s) for s in srcs if int(s) != dst]
        if not srcs:
            return
        names: list[str] = []
        for pid in (dst, *srcs):
            row = self._row(pid)
            name = (row.name or "").strip() if row else ""
            if name and name.lower() not in {n.lower() for n in names}:
                names.append(name)
        argvs = [("merge-persons", str(self._root), str(dst), *map(str, srcs))]
        if len(names) > 1:
            choice, ok = QInputDialog.getItem(
                self, MERGE_TITLE, WHICH_NAME, names, 0, False
            )
            if not ok:
                return
            dst_row = self._row(dst)
            if choice != (dst_row.name if dst_row else None):
                argvs.append(("name-person", str(self._root), str(dst), choice))
        self._start(MERGE_TITLE, *argvs)

    def rename(self, pid: int) -> None:
        row = self._row(pid)
        current = (row.name or "") if row else ""
        text, ok = QInputDialog.getText(
            self, RENAME_TITLE, RENAME_LABEL, QLineEdit.EchoMode.Normal, current
        )
        name = text.strip() if ok else ""
        if not name or name == current:
            return
        self.name_person(pid, name)

    def name_person(self, pid: int, name: str) -> None:
        """Name ``pid``; a name someone else has offers a merge into them."""
        argv = ("name-person", str(self._root), str(pid))
        owner = model.name_owner(self.data, name, pid) if self.data else None
        if owner is not None:
            answer = QMessageBox.question(
                self, RENAME_TITLE, MERGE_WITH.format(name=owner.name)
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._start(RENAME_TITLE, (*argv, owner.name, "--merge"))
            return
        self._start(RENAME_TITLE, (*argv, name))

    def split(self, pid: int) -> None:
        self._start(SPLIT_TITLE, ("split-person", str(self._root), str(pid)))

    def hide_person(self, pid: int, hide: bool = True) -> None:
        argv = ("hide-person", str(self._root), str(pid))
        if hide:
            self._start(HIDE_TITLE, argv)
        else:
            self._start(UNHIDE_TITLE, (*argv, "--unhide"))

    def delete(self, pid: int) -> None:
        row = self._row(pid)
        name = (row.name if row else None) or "this person"
        answer = QMessageBox.question(self, DELETE_TITLE, DELETE_ASK.format(name=name))
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(DELETE_TITLE, ("delete-person", str(self._root), str(pid)))
