"""Storage area: where the space goes, RAW + JPEG pairs stored twice, bursts.

The left column is the size tree (``StorageTreeView``). The right shows one of
two lists, switched at its top: the pairs and their keep choices
(``PairsView``) or the bursts and their marks (``BurstsView``); a burst opens
in ``BurstReview``, which takes over the page. Clicking a folder on the left
scopes both lists. The catalog is read off the GUI thread (``load_storage``);
the picture check runs on its own worker (``PairChecker``), only for the pairs
in the selected folder; remembered verdicts show everywhere. Confirmed choices
become a ``recycle`` job; nothing here writes the catalog or touches a file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from smart_gallery.config import drive_root_of
from smart_gallery.db.repository import GalleryRepository
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.jobspec import JOB_RUNNING, JobSpec
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.gui.repo_query import RepoQuery
from smart_gallery.gui.segment import segment
from smart_gallery.gui.state_store import load_json, save_json
from smart_gallery.gui.storage.burst_review import BurstReview
from smart_gallery.gui.storage.bursts_view import BurstsView
from smart_gallery.gui.storage.checker import PairChecker
from smart_gallery.gui.storage.pairs_view import LEAVE, LEAVE_TITLE, PairsView
from smart_gallery.gui.storage.tree_view import (
    GROUPING_LABELS,
    NODE_ROLE,
    StorageTreeView,
)
from smart_gallery.services.recycle import RecycleEntry, write_list
from smart_gallery.storage.bursts import (
    DEFAULT_GAP,
    MAX_GAP,
    MIN_GAP,
    Shot,
    load_shots,
)
from smart_gallery.storage.pairs import FileRef, Pair, find_pairs
from smart_gallery.storage.tree import (
    StorageTree,
    build_storage,
    folder_sizes,
    format_bytes,
)

# {"grouping": "folder"|"type", "split": [int, int],
#  "right": "pairs"|"bursts", "burst_gap": int}
STATE_FILE = "storage.json"
RIGHT_LABELS = {"pairs": "RAW + JPEG pairs", "bursts": "Bursts"}
SAVE_DELAY_MS = 300  # a splitter drag is saved once it settles
CHECKS_DIR = "pair_checks"  # app_data_dir() / CHECKS_DIR / f"{db_uuid}.json"
LISTS_DIR = "recycle_lists"  # app_data_dir() / LISTS_DIR / f"{timestamp}.tsv"
CAPTION = "{root} · {size} in {files:,} files"
JOB_TITLE = "Move to Recycle Bin"
NO_GALLERY = "Open or create a gallery to see its storage."
DISCONNECTED = "Drive not connected. Plug in {label} to see its storage."
READ_ERROR = "Could not read the catalog: {error}"
EMPTY = "This gallery's catalog has no files yet."
LIST_ERROR = "Could not write the list of files to move: {error}"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageData:
    tree: StorageTree | None
    pairs: list[Pair]
    root_name: str
    shots: list[Shot]


def load_storage(repo: GalleryRepository) -> StorageData:
    """Size trees, pairs and shots, from the catalog alone. No Qt."""
    root = drive_root_of(repo.db_path)
    root_name = root.name or root.as_posix()
    df = repo.query_df()
    tree = build_storage(df[["relpath", "ext", "size_bytes"]], root_name)
    return StorageData(tree, find_pairs(repo), root_name, load_shots(repo))


def _valid_split(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        and all(v > 0 for v in value)
    )


def _valid_gap(value) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and MIN_GAP <= value <= MAX_GAP
    )


class StorageView(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.data: StorageData | None = None
        self._root = Path()
        self._loading_root = Path()
        # The gallery whose choices are held; a different one starts afresh.
        self._gallery_uuid = getattr(ctx.current, "db_uuid", None)
        self._fresh = True  # next data starts on all folders
        self._query = RepoQuery(ctx, self)

        # Shown on the page header's right (MainWindow takes header_controls).
        self.caption = QLabel()
        self.header_controls = self.caption

        self.tree_view = StorageTreeView()
        self.pairs_view = PairsView(ctx)
        self.bursts_view = BurstsView(ctx)
        self.review = BurstReview(self.bursts_view, Path())
        self.checker = PairChecker(self)

        # The right column: the list switch above the list it shows.
        self.right_box = QWidget()
        switch, _group, self.right_switch = segment(*RIGHT_LABELS.values())
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self.pairs_view)
        self.right_stack.addWidget(self.bursts_view)
        top = QHBoxLayout()
        top.addWidget(switch)
        top.addStretch(1)
        column = QVBoxLayout(self.right_box)
        column.setContentsMargins(0, 0, 0, 0)
        column.addLayout(top)
        column.addWidget(self.right_stack, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.tree_view)
        self.splitter.addWidget(self.right_box)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        self.state_path = app_data_dir() / STATE_FILE
        state = load_json(self.state_path)
        grouping = state.get("grouping")
        if isinstance(grouping, str) and grouping in GROUPING_LABELS:
            self.tree_view.set_grouping(grouping)
        gap = state.get("burst_gap")
        if _valid_gap(gap) and gap != DEFAULT_GAP:
            self.bursts_view.set_gap(gap)  # no marks yet: no question
        if state.get("right") == "bursts":
            self.right_switch[1].setChecked(True)
            self.right_stack.setCurrentWidget(self.bursts_view)
        split = state.get("split")
        # Applied on the first show, once the splitter has its width.
        self._split: list[int] | None = split if _valid_split(split) else None
        self._split_applied = False

        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.splitter)
        self.stack.addWidget(self.message)
        self.stack.addWidget(self.review)
        QVBoxLayout(self).addWidget(self.stack)

        self.tree_view.folder_selected.connect(self.pairs_view.set_scope)
        self.tree_view.folder_selected.connect(self.bursts_view.set_scope)
        for i, name in enumerate(RIGHT_LABELS):
            self.right_switch[i].clicked.connect(
                lambda _checked=False, n=name: self._show_right(n)
            )
        self.bursts_view.show_all.clicked.connect(self._clear_tree_selection)
        self.bursts_view.move_requested.connect(self._on_move)
        self.bursts_view.gap_changed.connect(self._save_state)
        self.bursts_view.review_requested.connect(self._open_review)
        self.review.closed.connect(self._close_review)
        self.tree_view.message.connect(self.caption.setText)
        self.tree_view.grouping_changed.connect(self._on_grouping)
        self.pairs_view.show_all.clicked.connect(self._clear_tree_selection)
        self.pairs_view.move_requested.connect(self._on_move)
        self.checker.checked.connect(self.pairs_view.set_verdict)
        self.checker.progress.connect(self.pairs_view.set_progress)
        self.checker.finished.connect(self.pairs_view.check_finished)
        self.checker.failed.connect(self.pairs_view.check_failed)
        self.pairs_view.scope_changed.connect(self._check_scope)
        self._loading = False  # _on_data checks the scope once, at its end
        # A drag moves the splitter on every pixel: save once it settles.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save_state)
        self.splitter.splitterMoved.connect(self._save_timer.start)

        self._query.finished.connect(self._on_data)
        self._query.failed.connect(self._on_failed)
        ctx.gallery_changed.connect(self._on_gallery_changed)
        ctx.catalog_updated.connect(self.reload)

        guard = self._confirm_leave
        ctx.add_leave_guard(guard)
        checker = self.checker

        def teardown(*_args) -> None:
            # Before the children go: the checker's pool waits for its task.
            checker.cancel_all()
            ctx.remove_leave_guard(guard)

        self.destroyed.connect(teardown)
        self.reload()

    def _confirm_leave(self) -> bool:
        """One question for the unapplied pair choices and burst marks."""
        n = len(self.pairs_view.choices()) + len(self.bursts_view.marks())
        if n == 0:
            return True
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        answer = QMessageBox.question(
            self, LEAVE_TITLE, LEAVE.format(n=n), yes | no, no
        )
        if answer != yes:
            return False
        self.pairs_view.clear_choices()
        self.bursts_view.clear_marks()
        return True

    def _show_right(self, name: str) -> None:
        view = self.bursts_view if name == "bursts" else self.pairs_view
        self.right_stack.setCurrentWidget(view)
        self._save_state()

    def _open_review(self, burst, shot) -> None:
        self.review.open_at(burst, shot)
        if self.review.current() is not None:
            self.stack.setCurrentWidget(self.review)

    def _close_review(self, burst) -> None:
        self.stack.setCurrentWidget(self.splitter)
        if burst is not None:
            self.bursts_view.set_expanded(burst)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        self.pairs_view.forget_thumb_requests()
        if self._split_applied:
            return
        self._split_applied = True
        if self._split is not None:
            self.splitter.setSizes(self._split)
        else:
            total = sum(self.splitter.sizes()) or self.splitter.width()
            self.splitter.setSizes([total // 2, total - total // 2])

    # ── loading ─────────────────────────────────────────────────────────────
    def reload(self, *_args) -> None:
        current = self.ctx.current
        if current is None:
            self._query.cancel()
            self.checker.cancel_all()
            self._show_message(NO_GALLERY)
            return
        self._loading_root = drive_root_of(current.db_path)
        self._query.run(load_storage)

    def _on_gallery_changed(self, gallery=None, *_args) -> None:
        uuid = getattr(gallery, "db_uuid", None)
        if uuid is not None and uuid == self._gallery_uuid:
            # The same gallery again (its drive came back or moved): reload it
            # like a catalog update and keep the choices and the scope.
            self.reload()
            return
        self._gallery_uuid = uuid
        self.checker.cancel_all()
        self.pairs_view.clear_choices()
        self.bursts_view.clear_marks()
        self._fresh = True  # a new gallery starts on all folders
        self.pairs_view.set_scope(None)
        self.bursts_view.set_scope(None)
        self.reload()

    def _on_data(self, result: StorageData) -> None:
        self.data = result
        root = self._loading_root
        if result.tree is None:
            self.checker.cancel_all()
            self.pairs_view.set_pairs([], root)
            self.bursts_view.set_shots([], root)
            self._root = root
            self.review._root = root
            self._show_message(EMPTY)
            return
        # Keep the folder only when the same gallery reloaded and it is still there.
        previous = None if self._fresh else self._scope_folder()
        self._fresh = False
        self._root = root
        self.review._root = root
        self.checker.cancel()  # its pairs are gone; the new scope decides
        # Remembered verdicts for every pair, read on the worker: cheap.
        self.checker.load_cached(result.pairs, self._cache_path())
        self._loading = True
        try:
            self.tree_view.set_tree(result.tree, root)
            self.pairs_view.set_pairs(result.pairs, root, folder_sizes(result.tree))
            self.pairs_view.set_scope(None)
            self.bursts_view.set_scope(None)
            if previous is not None:
                index = self._find_folder(previous)
                if index is not None:
                    self.tree_view.tree.setCurrentIndex(index)  # scopes the pairs
            # After the scope is restored: a scope change clears the notice of
            # marks the new data dropped.
            self.bursts_view.set_shots(result.shots, root)
        finally:
            self._loading = False
        self._check_scope()
        if self.stack.currentWidget() is self.review:
            self.review.refresh()
        self.caption.setText(
            CAPTION.format(
                root=result.root_name,
                size=format_bytes(result.tree.total_bytes),
                files=result.tree.total_files,
            )
        )
        if self.stack.currentWidget() is not self.review:
            self.stack.setCurrentWidget(self.splitter)

    def _check_scope(self, *_args) -> None:
        """Check the pictures of the folder in scope, and nothing else."""
        if self._loading:
            return
        self.checker.cancel()
        self.pairs_view.check_cancelled()
        todo = self.pairs_view.unchecked_in_scope()  # [] with no folder
        if todo:
            self.pairs_view.check_started(p.key for p in todo)
            self.checker.start(todo, self._root, self._cache_path())

    def _on_failed(self, error) -> None:
        self.checker.cancel_all()
        if self.ctx.current is None:
            text = NO_GALLERY
        elif isinstance(error, FileNotFoundError):
            label = getattr(self.ctx.current, "label", "") or str(self._loading_root)
            text = DISCONNECTED.format(label=label)
        else:
            text = READ_ERROR.format(error=error)
        self._show_message(text)

    def _show_message(self, text: str) -> None:
        self.caption.setText("")
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)

    def _cache_path(self) -> Path:
        uuid = getattr(self.ctx.current, "db_uuid", None) or "gallery"
        return app_data_dir() / CHECKS_DIR / f"{uuid}.json"

    # ── tree and scope ──────────────────────────────────────────────────────
    def _scope_folder(self) -> str | None:
        node = self.tree_view.tree.currentIndex().siblingAtColumn(0).data(NODE_ROLE)
        return node.relpath if node is not None and node.relpath else None

    def _find_folder(self, relpath: str) -> QModelIndex | None:
        """The first tree row that opens ``relpath`` (ignoring case)."""
        model = self.tree_view.tree.model()
        if model is None:
            return None
        wanted = relpath.casefold()
        pending = [model.index(0, 0)]
        while pending:
            index = pending.pop(0)
            if not index.isValid():
                continue
            node = index.data(NODE_ROLE)
            if node is not None and (node.relpath or "").casefold() == wanted:
                return index
            pending.extend(
                model.index(r, 0, index) for r in range(model.rowCount(index))
            )
        return None

    def _clear_tree_selection(self) -> None:
        selection = self.tree_view.tree.selectionModel()
        if selection is not None:
            selection.clear()  # emits folder_selected(None)
        self.pairs_view.set_scope(None)
        self.bursts_view.set_scope(None)

    def _on_grouping(self, *_args) -> None:
        self._clear_tree_selection()
        self._save_state()

    def _save_state(self, *_args) -> None:
        self._save_timer.stop()  # this save covers a pending one
        right = (
            "bursts"
            if self.right_stack.currentWidget() is self.bursts_view
            else "pairs"
        )
        state = {
            "grouping": self.tree_view.grouping(),
            "split": self.splitter.sizes(),
            "right": right,
            "burst_gap": self.bursts_view.gap(),
        }
        try:
            save_json(self.state_path, state)
        except OSError as exc:  # e.g. the file is locked; the next save retries
            logger.warning("Could not save %s: %s", self.state_path, exc)

    # ── moving ──────────────────────────────────────────────────────────────
    def _on_move(self, files: list[FileRef]) -> None:
        jobs = self.ctx.jobs
        if not files or jobs is None:
            return
        if getattr(jobs, "running", False):
            # Checked before writing: a running job may still read its list.
            QMessageBox.information(self, JOB_TITLE, JOB_RUNNING)
            return
        entries = [RecycleEntry(f.relpath, f.size_bytes, f.mtime_ns) for f in files]
        try:
            path = self._new_list_path()
            write_list(path, entries)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, JOB_TITLE, LIST_ERROR.format(error=exc))
            return
        argv = ("recycle", str(self._root), "--list", str(path))
        if not jobs.start(JobSpec(JOB_TITLE, (argv,))):
            QMessageBox.information(self, JOB_TITLE, JOB_RUNNING)

    @staticmethod
    def _new_list_path() -> Path:
        folder = app_data_dir() / LISTS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = folder / f"{stamp}.tsv"
        n = 1
        while path.exists():  # never overwrite a list a job may still read
            n += 1
            path = folder / f"{stamp}-{n}.tsv"
        return path
