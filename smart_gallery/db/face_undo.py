"""Undo store: face corrections recorded as batches that can be reverted."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

MAX_UNDO_BATCHES = 20

_BLANK = (None, None, None, None)


class UndoConflict(ValueError):
    """The catalog changed since the batch was recorded, so it can't be undone."""


@dataclass(frozen=True)
class FaceSnapshot:
    # face_id -> (state, person_id, suggested_person_id, match_score)
    faces: dict[int, tuple]
    rejections: set[tuple[int, int]]
    # person_id -> (name, hidden)
    persons: dict[int, tuple[str | None, int]]


@dataclass(frozen=True)
class UndoInfo:
    batch_id: int
    summary: str
    created_at: str


class FaceUndoMixin:
    def face_snapshot(self) -> FaceSnapshot:
        c = self.conn
        faces = {
            r[0]: (r[1], r[2], r[3], r[4])
            for r in c.execute(
                "SELECT id, state, person_id, suggested_person_id, match_score "
                "FROM faces"
            )
        }
        rejections = {
            (r[0], r[1])
            for r in c.execute("SELECT face_id, person_id FROM face_rejections")
        }
        persons = {
            r[0]: (r[1], r[2])
            for r in c.execute("SELECT id, name, hidden FROM persons")
        }
        return FaceSnapshot(faces, rejections, persons)

    def record_undo(self, summary: str, before: FaceSnapshot) -> int | None:
        """Store the difference between ``before`` and now as one batch."""
        after = self.face_snapshot()
        changed = sorted(
            fid
            for fid in before.faces.keys() | after.faces.keys()
            if before.faces.get(fid) != after.faces.get(fid)
        )
        rej_added = sorted(after.rejections - before.rejections)
        rej_removed = sorted(before.rejections - after.rejections)
        made = sorted(after.persons.keys() - before.persons.keys())
        gone = sorted(before.persons.keys() - after.persons.keys())
        if not (changed or rej_added or rej_removed or made or gone):
            return None
        c = self.conn
        try:
            bid = c.execute(
                "INSERT INTO face_undo_batches (summary) VALUES (?)", (summary,)
            ).lastrowid
            c.executemany(
                "INSERT INTO face_undo_faces VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        bid,
                        fid,
                        *before.faces.get(fid, _BLANK),
                        *after.faces.get(fid, _BLANK),
                    )
                    for fid in changed
                ],
            )
            c.executemany(
                "INSERT INTO face_undo_rejections VALUES (?,?,?,?)",
                [(bid, f, p, 1) for f, p in rej_added]
                + [(bid, f, p, 0) for f, p in rej_removed],
            )
            c.executemany(
                "INSERT INTO face_undo_persons VALUES (?,?,?,?,?)",
                [(bid, p, *after.persons[p], 1) for p in made]
                + [(bid, p, *before.persons[p], 0) for p in gone],
            )
            c.execute(
                "DELETE FROM face_undo_batches WHERE id NOT IN "
                "(SELECT id FROM face_undo_batches ORDER BY id DESC LIMIT ?)",
                (MAX_UNDO_BATCHES,),
            )
            c.commit()
        except Exception:
            c.rollback()
            raise
        return bid

    def last_undo(self) -> UndoInfo | None:
        """The newest batch. ``None`` when there is none or the catalog predates v8."""
        try:
            row = self.conn.execute(
                "SELECT id, summary, created_at FROM face_undo_batches "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e):
                raise
            return None
        return UndoInfo(row[0], row[1], row[2]) if row else None

    def undo_last(self) -> tuple[UndoInfo, int]:
        """Revert the newest batch; returns it and how many faces were restored."""
        info = self.last_undo()
        if info is None:
            raise ValueError("Nothing to undo.")
        c = self.conn
        bid = info.batch_id
        try:
            faces = c.execute(
                "SELECT face_id, before_state, before_person, before_suggested, "
                "before_score, after_state, after_person, after_suggested, "
                "after_score FROM face_undo_faces WHERE batch_id=?",
                (bid,),
            ).fetchall()
            rejs = c.execute(
                "SELECT face_id, person_id, added FROM face_undo_rejections "
                "WHERE batch_id=?",
                (bid,),
            ).fetchall()
            persons = c.execute(
                "SELECT person_id, name, hidden, created FROM face_undo_persons "
                "WHERE batch_id=?",
                (bid,),
            ).fetchall()

            now = self.face_snapshot()
            bad = sum(1 for r in faces if now.faces.get(r[0], _BLANK) != tuple(r[5:9]))
            bad += sum(
                1 for f, p, added in rejs if ((f, p) in now.rejections) != bool(added)
            )
            bad += sum(
                1 for p, _n, _h, created in persons if not created and p in now.persons
            )
            # Restored values must point at people that will exist, and
            # restored rejections at faces that still do.
            revived = {p for p, _n, _h, created in persons if not created}
            alive = now.persons.keys() | revived
            for r in faces:
                if r[0] in now.faces:
                    bad += sum(
                        1 for v in (r[2], r[3]) if v is not None and v not in alive
                    )
            for f, p, added in rejs:
                if not added and (p not in alive or f not in now.faces):
                    bad += 1
            if bad:
                raise UndoConflict(
                    f'Can\'t undo "{info.summary}": {bad:,} of its faces '
                    "changed since. Nothing was changed."
                )

            for p, name, hidden, created in persons:
                if not created:
                    c.execute(
                        "INSERT INTO persons (id, name, hidden) VALUES (?,?,?)",
                        (p, name, hidden),
                    )
            restored = 0
            for r in faces:
                if r[0] not in now.faces:
                    continue  # the batch itself added it; it no longer exists to restore
                restored += c.execute(
                    "UPDATE faces SET state=?, person_id=?, suggested_person_id=?, "
                    "match_score=? WHERE id=?",
                    (*tuple(r[1:5]), r[0]),
                ).rowcount
            for f, p, added in rejs:
                if added:
                    c.execute(
                        "DELETE FROM face_rejections WHERE face_id=? AND person_id=?",
                        (f, p),
                    )
                else:
                    c.execute(
                        "INSERT OR IGNORE INTO face_rejections VALUES (?, ?)", (f, p)
                    )
            for p, _n, _h, created in persons:
                if created:
                    c.execute(
                        "DELETE FROM persons WHERE id=? AND NOT EXISTS "
                        "(SELECT 1 FROM faces WHERE person_id=?)",
                        (p, p),
                    )
            c.execute("DELETE FROM face_undo_batches WHERE id=?", (bid,))
            c.commit()
        except Exception:
            c.rollback()
            raise

        touched = {p for r in faces for p in (r[2], r[6]) if p is not None}
        touched |= {p for _f, p, _a in rejs}
        touched |= {r[0] for r in persons}
        for p in sorted(touched):
            if c.execute("SELECT 1 FROM persons WHERE id=?", (p,)).fetchone():
                self.recompute_person(p)
        return info, restored
