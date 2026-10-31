"""The Sync options button in the Library header.

One menu, built from the open gallery's pairing: sync to each mirror, free up
space, bring files back, or pair with another gallery. Every write runs as a
background job (``sync-mirror``, ``free-up``, ``bring-back``, ``pair``); a sync
and a free-up first run a dry run that writes its plan (``--plan-out``) and
show it, and the real run passes that file back (``--expect-plan``) so it does
nothing the preview did not show.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu, QMessageBox, QPushButton

from smart_gallery.config import drive_root_of
from smart_gallery.gui.context import AppContext
from smart_gallery.gui.jobspec import JOB_RUNNING, JobSpec
from smart_gallery.gui.mirror.dialogs import FreeUpDialog, PairDialog, SyncPreviewDialog
from smart_gallery.gui.paths import app_data_dir
from smart_gallery.storage.tree import format_bytes

PLANS_DIR = "mirror"  # app_data_dir() / PLANS_DIR / f"{timestamp}.json"
BUTTON_TEXT = "Sync options"
SYNC_ENTRY = "Sync to {label}…"
FREE_UP_ENTRY = "Free up space…"
BRING_BACK_ENTRY = "Bring back…"
PAIR_ENTRY = "Pair with another gallery…"
MIRROR_ENTRY = "Open {label} to sync"
PLAN_TITLE = "Check sync with {label}"
SYNC_TITLE = "Sync to {label}"
FREE_UP_TITLE = "Free up space"
FREE_UP_PREVIEW_TITLE = "Check free up space"
BRING_BACK_TITLE = "Bring back"
PAIR_TITLE = "Pair galleries"
JOB_FAILED = "The job did not finish: {summary}"
PLAN_ERROR = "Could not read the sync plan: {error}"
FREE_UP_PLAN_ERROR = "Could not read the free up preview: {error}"
FREE_UP_RESULT = "Would free {files} ({size})."
FREE_UP_SKIPPED = (
    "{files} skipped (no verified copy on the mirror, faces not scanned, "
    "or changed)."
)
FREE_UP_CONFIRM = (
    "{result}\n\nThese files will be deleted from this drive; the mirror keeps "
    "its copies and you can bring them back later. Free up the space now?"
)
NOTHING_TO_FREE = "Nothing was found to free up."

logger = logging.getLogger(__name__)


def peer_label(galleries, peer) -> str:
    """The known gallery's label for ``peer``, else its drive root."""
    for gallery in galleries:
        if gallery.db_uuid == peer.uuid and gallery.label:
            return gallery.label
    return str(drive_root_of(peer.db_path))


# A push button rather than a tool button: it draws its menu arrow with room to
# spare, the same as the Sort by box beside it.
class MirrorButton(QPushButton):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.pairing = None
        self._galleries: list = []
        self._readable = False
        # argvs of a job this button started -> what to do when it ends
        self._pending: dict[tuple, tuple] = {}
        self.setText(BUTTON_TEXT)
        self.setToolTip("Sync with a mirror drive, free up space, bring files back")
        self.setMenu(QMenu(self))
        ctx.gallery_changed.connect(self.refresh)
        ctx.catalog_updated.connect(self.refresh)
        if ctx.jobs is not None:
            ctx.jobs.finished.connect(self._on_job_finished)
        self.refresh()

    # ── state ───────────────────────────────────────────────────────────────
    def refresh(self, *_args) -> None:
        current = self.ctx.current
        self.pairing = None
        self._galleries = []
        self._readable = False
        if current is not None:
            store = self.ctx.galleries
            self._galleries = list(store.all()) if store is not None else []
            try:
                with self.ctx.open_repo() as repo:
                    self.pairing = repo.get_pairing()
                self._readable = True
            except (OSError, sqlite3.Error) as exc:
                logger.debug("No pairing read for %s: %s", current.db_path, exc)
        self._build_menu()

    def _others(self) -> list:
        current = self.ctx.current
        uuid = getattr(current, "db_uuid", None)
        paired = {p.uuid for p in self.pairing.peers} if self.pairing else set()
        return [
            g for g in self._galleries if g.db_uuid != uuid and g.db_uuid not in paired
        ]

    def _label(self, peer) -> str:
        return peer_label(self._galleries, peer)

    def _build_menu(self) -> None:
        menu = self.menu()
        menu.clear()
        pairing = self.pairing
        if self.ctx.current is None or not self._readable:
            self.setVisible(False)  # no gallery, or its drive is not connected
            return
        if pairing is not None and pairing.role == "mirror":
            for peer in pairing.peers[:1]:
                entry = menu.addAction(MIRROR_ENTRY.format(label=self._label(peer)))
                entry.setEnabled(False)
            self.setVisible(bool(pairing.peers))
            return
        if pairing is not None and pairing.peers:
            for peer in pairing.peers:
                entry = menu.addAction(SYNC_ENTRY.format(label=self._label(peer)))
                entry.triggered.connect(lambda _=False, p=peer: self.sync(p))
            menu.addSeparator()
            menu.addAction(FREE_UP_ENTRY).triggered.connect(self.free_up)
            menu.addAction(BRING_BACK_ENTRY).triggered.connect(self.bring_back)
            menu.addSeparator()
            pair = menu.addAction(PAIR_ENTRY)
            pair.triggered.connect(self.pair)
            pair.setEnabled(bool(self._others()))
            self.setVisible(True)
            return
        if self._others():
            menu.addAction(PAIR_ENTRY).triggered.connect(self.pair)
            self.setVisible(True)
            return
        self.setVisible(False)

    # ── jobs ────────────────────────────────────────────────────────────────
    def _drive(self) -> str:
        return str(self.ctx.current.db_path)

    def _start(self, title: str, argv: tuple, then=None, writes=True) -> bool:
        jobs = self.ctx.jobs
        if jobs is None:
            return False
        if getattr(jobs, "running", False):
            QMessageBox.information(self, title, JOB_RUNNING)
            return False
        argvs = (tuple(argv),)
        if then is not None:
            # Before start: ``finished`` may come before start returns.
            self._pending[argvs] = then
        if not jobs.start(JobSpec(title, argvs, writes_catalog=writes)):
            self._pending.pop(argvs, None)
            QMessageBox.information(self, title, JOB_RUNNING)
            return False
        return True

    def _on_job_finished(self, record) -> None:
        argvs = tuple(tuple(a) for a in record.argvs)
        then = self._pending.pop(argvs, None)
        if then is None:
            return  # not one of ours
        if record.status == "failed":
            summary = record.summary or record.status
            QMessageBox.warning(self, record.title, JOB_FAILED.format(summary=summary))
            return
        if record.status != "ok":
            return  # cancelled
        # After this signal returns: the runner still emits catalog_written.
        QTimer.singleShot(0, lambda: then(record))

    def _mirror_choices(self) -> list[tuple[str, str]]:
        peers = self.pairing.peers if self.pairing else []
        return [(self._label(p), p.db_path) for p in peers]

    # ── sync ────────────────────────────────────────────────────────────────
    def sync(self, peer) -> None:
        label = self._label(peer)
        title = PLAN_TITLE.format(label=label)
        if getattr(self.ctx.jobs, "running", False):
            QMessageBox.information(self, title, JOB_RUNNING)
            return
        try:
            plan_path = _new_plan_path()
        except OSError as exc:
            QMessageBox.warning(self, title, PLAN_ERROR.format(error=exc))
            return
        drive = self._drive()
        argv = (
            "sync-mirror",
            drive,
            "--mirror",
            peer.db_path,
            "--dry-run",
            "--plan-out",
            str(plan_path),
        )
        self._start(
            title, argv, then=lambda _r: self._preview(drive, peer, label, plan_path)
        )

    def _preview(self, drive: str, peer, label: str, plan_path: Path) -> None:
        title = SYNC_TITLE.format(label=label)
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, title, PLAN_ERROR.format(error=exc))
            return
        dialog = SyncPreviewDialog(plan, self, mirror_label=label)
        if not dialog.exec():
            return
        argv = (
            "sync-mirror",
            drive,
            "--mirror",
            peer.db_path,
            *dialog.flags(),
            "--expect-plan",
            str(plan_path),
        )
        self._start(title, argv)

    # ── free up / bring back ────────────────────────────────────────────────
    def free_up(self) -> None:
        dialog = FreeUpDialog(self, mirrors=self._mirror_choices())
        if not dialog.exec():
            return
        try:
            plan_path = _new_plan_path()
        except OSError as exc:
            error = FREE_UP_PLAN_ERROR.format(error=exc)
            QMessageBox.warning(self, FREE_UP_TITLE, error)
            return
        drive, flags = self._drive(), list(dialog.argv())
        argv = ("free-up", drive, *flags, "--dry-run", "--plan-out", str(plan_path))
        self._start(
            FREE_UP_PREVIEW_TITLE,
            argv,
            then=lambda _r: self._confirm_free_up(drive, flags, plan_path),
        )

    def _confirm_free_up(self, drive: str, flags: list[str], plan_path: Path) -> None:
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            files, size = int(plan["files"]), int(plan["bytes"])
            skipped = len(plan.get("skipped") or [])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            error = FREE_UP_PLAN_ERROR.format(error=exc)
            QMessageBox.warning(self, FREE_UP_TITLE, error)
            return
        notes = [FREE_UP_SKIPPED.format(files=_files(skipped))] if skipped else []
        if not files:
            text = "\n\n".join([NOTHING_TO_FREE, *notes])
            QMessageBox.information(self, FREE_UP_TITLE, text)
            return
        result = FREE_UP_RESULT.format(files=_files(files), size=format_bytes(size))
        result = "\n".join([result, *notes])
        answer = QMessageBox.question(
            self,
            FREE_UP_TITLE,
            FREE_UP_CONFIRM.format(result=result),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        argv = ("free-up", drive, *flags, "--expect-plan", str(plan_path))
        self._start(FREE_UP_TITLE, argv)

    def bring_back(self) -> None:
        dialog = FreeUpDialog(
            self,
            mirrors=self._mirror_choices(),
            title=BRING_BACK_TITLE,
            ok_text=BRING_BACK_TITLE,
        )
        if not dialog.exec():
            return
        self._start(BRING_BACK_TITLE, ("bring-back", self._drive(), *dialog.argv()))

    # ── pairing ─────────────────────────────────────────────────────────────
    def pair(self) -> None:
        dialog = PairDialog(self._others(), self)
        if not dialog.exec():
            return
        mirror = dialog.selected()
        if mirror is None:
            return
        self._start(PAIR_TITLE, ("pair", self._drive(), mirror.db_path))


def _files(n: int) -> str:
    return f"{n:,} file" if n == 1 else f"{n:,} files"


def _new_plan_path() -> Path:
    """A fresh file under the app data folder; ``--plan-out`` needs the folder."""
    folder = app_data_dir() / PLANS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}.json"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{stamp}-{n}.json"
    return path
