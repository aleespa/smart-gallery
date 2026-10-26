"""Face filing storage: the inputs the filing step reads and the decisions it writes."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Optional, Sequence

import numpy as np

from smart_gallery.config import FACE_EMBEDDING_DIM

if TYPE_CHECKING:
    from smart_gallery.analysis.filing import Candidate, Decision, FaceIn
    from smart_gallery.services.label_faces import LabelEntry, LabelReport


def _vec(blob) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _stack(rows: list[np.ndarray]) -> np.ndarray:
    if not rows:
        return np.empty((0, FACE_EMBEDDING_DIM), dtype=np.float32)
    return np.stack(rows).astype(np.float32)


class FaceLabelsMixin:
    """Methods of ``GalleryRepository`` for sorting faces into people.

    Uses ``self.conn`` like the repository itself.
    """

    def load_filing_inputs(self) -> "tuple[list[FaceIn], list[Candidate]]":
        """Faces the filing step may move, and the people they can go to."""
        from smart_gallery.analysis.filing import Candidate, FaceIn

        rejected_from: dict[int, set[int]] = defaultdict(set)
        rejected_embs: dict[int, list[np.ndarray]] = defaultdict(list)
        for r in self.conn.execute(
            "SELECT r.face_id, r.person_id, f.embedding FROM face_rejections r "
            "JOIN faces f ON f.id = r.face_id"
        ):
            rejected_from[r["face_id"]].add(r["person_id"])
            rejected_embs[r["person_id"]].append(_vec(r["embedding"]))

        faces = [
            FaceIn(
                face_id=r["id"],
                embedding=_vec(r["embedding"]),
                state=r["state"],
                person_id=r["person_id"],
                short_side=float(min(r["bbox_w"], r["bbox_h"])),
                det_score=r["det_score"],
                rejected_from=frozenset(rejected_from.get(r["id"], ())),
            )
            for r in self.conn.execute(
                "SELECT id, embedding, state, person_id, bbox_w, bbox_h, det_score "
                "FROM faces WHERE state NOT IN ('user', 'ignored') ORDER BY id"
            )
        ]

        user_embs: dict[int, list[np.ndarray]] = defaultdict(list)
        for r in self.conn.execute(
            "SELECT person_id, embedding FROM faces "
            "WHERE state='user' AND person_id IS NOT NULL"
        ):
            user_embs[r["person_id"]].append(_vec(r["embedding"]))

        candidates = []
        for p in self.conn.execute(
            "SELECT id, name, hidden, centroid, reference_centroid "
            "FROM persons ORDER BY id"
        ):
            pid = p["id"]
            if p["hidden"]:
                kind = "hidden"
            elif p["name"]:
                kind = "named"
            else:
                kind = "group"
            if kind == "named":
                ex = list(user_embs.get(pid, ()))
                if p["reference_centroid"] is not None:
                    ex.append(_vec(p["reference_centroid"]))
                if not ex and p["centroid"] is not None:
                    ex.append(_vec(p["centroid"]))
            else:
                ex = [_vec(p["centroid"])] if p["centroid"] is not None else []
            if not ex:
                continue
            candidates.append(
                Candidate(pid, kind, _stack(ex), _stack(rejected_embs.get(pid, [])))
            )
        return faces, candidates

    def apply_filing(
        self, decisions: "list[Decision]", new_groups: list[list[int]]
    ) -> None:
        """Write decisions and new groups in one transaction, then refresh
        every person whose faces changed."""
        touched: set[int] = set()
        try:
            old = {
                r["id"]: r["person_id"]
                for r in self.conn.execute("SELECT id, person_id FROM faces")
            }
            for d in decisions:
                self.conn.execute(
                    "UPDATE faces SET state=?, person_id=?, suggested_person_id=?, "
                    "match_score=? WHERE id=?",
                    (
                        d.state,
                        d.person_id,
                        d.suggested_person_id,
                        None if d.score is None else float(d.score),
                        d.face_id,
                    ),
                )
                if old.get(d.face_id) != d.person_id:
                    touched.update(p for p in (old.get(d.face_id), d.person_id) if p)
            for members in new_groups:
                cur = self.conn.execute("INSERT INTO persons (hidden) VALUES (0)")
                pid = int(cur.lastrowid)
                self.conn.executemany(
                    "UPDATE faces SET state='cluster', person_id=?, "
                    "suggested_person_id=NULL WHERE id=?",
                    [(pid, int(fid)) for fid in members],
                )
                touched.add(pid)
                touched.update(p for p in (old.get(int(f)) for f in members) if p)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        for pid in sorted(touched):
            self.recompute_person(pid)

    def person_named(self, name: str) -> Optional[int]:
        """The id of the one person with this name (any case), or None.
        Raises ValueError when several people share it."""
        ids = [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM persons WHERE name = ? COLLATE NOCASE", (name.strip(),)
            )
        ]
        if len(ids) > 1:
            raise ValueError(f"Several people are named {name.strip()!r}.")
        return ids[0] if ids else None

    def set_person_hidden(self, person_id: int, hidden: bool) -> None:
        """Hide an unnamed group of strangers (its faces become ``ignored``),
        or show it again (its ignored/hidden faces go back to ``cluster``)."""
        row = self.conn.execute(
            "SELECT name FROM persons WHERE id=?", (person_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No person with id {person_id}.")
        if row["name"]:
            raise ValueError(
                f"Person {person_id} is named {row['name']!r}; only unnamed "
                "groups can be hidden."
            )
        run = self.conn.execute
        try:
            run(
                "UPDATE persons SET hidden=?, updated_at=datetime('now') WHERE id=?",
                (1 if hidden else 0, person_id),
            )
            if hidden:
                run(
                    "UPDATE faces SET state='ignored', suggested_person_id=NULL "
                    "WHERE person_id=?",
                    (person_id,),
                )
            else:
                run(
                    "UPDATE faces SET state='cluster' "
                    "WHERE person_id=? AND state IN ('ignored', 'hidden')",
                    (person_id,),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self.recompute_person(person_id)

    def load_person_faces(
        self, person_id: int
    ) -> "tuple[np.ndarray, np.ndarray, list[str]]":
        """``(face_ids, embeddings, states)`` of one person's faces, by id."""
        rows = self.conn.execute(
            "SELECT id, embedding, state FROM faces WHERE person_id=? ORDER BY id",
            (person_id,),
        ).fetchall()
        ids = np.array([r["id"] for r in rows], dtype=np.int64)
        return (
            ids,
            _stack([_vec(r["embedding"]) for r in rows]),
            [r["state"] for r in rows],
        )

    def apply_split(
        self, person_id: int, new_groups: list[list[int]], noise: list[int]
    ) -> list[int]:
        """Move faces out of ``person_id`` in one transaction: each group into
        a new unnamed group (``cluster``), ``noise`` back to the pool. Every
        moved face is marked as not ``person_id``. Returns the new group ids."""
        run = self.conn.execute
        created: list[int] = []
        try:
            for members in new_groups:
                pid = int(run("INSERT INTO persons (hidden) VALUES (0)").lastrowid)
                created.append(pid)
                self.conn.executemany(
                    "UPDATE faces SET state='cluster', person_id=?, cluster_id=NULL, "
                    "suggested_person_id=NULL, match_score=NULL WHERE id=?",
                    [(pid, int(f)) for f in members],
                )
            self.conn.executemany(
                "UPDATE faces SET state='new', person_id=NULL, cluster_id=NULL, "
                "suggested_person_id=NULL, match_score=NULL WHERE id=?",
                [(int(f),) for f in noise],
            )
            moved = [int(f) for g in new_groups for f in g] + [int(f) for f in noise]
            self.conn.executemany(
                "INSERT OR IGNORE INTO face_rejections VALUES (?, ?)",
                [(f, person_id) for f in moved],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        for pid in [person_id, *created]:
            self.recompute_person(pid)
        return created

    def apply_labels(self, entries: "Sequence[LabelEntry]") -> "LabelReport":
        """Apply validated corrections in one transaction, then refresh every
        person whose faces changed."""
        from smart_gallery.services.label_faces import LabelReport

        report = LabelReport()
        touched: set[int] = set()
        try:
            for e in entries:
                person_id = e.person_id
                if e.action == "new_person":
                    person_id = self.person_named(e.name)
                    if person_id is None:
                        cur = self.conn.execute(
                            "INSERT INTO persons (name, hidden) VALUES (?, 0)",
                            (e.name.strip(),),
                        )
                        person_id = int(cur.lastrowid)
                        report.people_created += 1
                    action = "move"
                else:
                    action = e.action
                if person_id is not None:
                    touched.add(person_id)
                for fid in dict.fromkeys(e.faces):
                    row = self.conn.execute(
                        "SELECT person_id, suggested_person_id FROM faces WHERE id=?",
                        (fid,),
                    ).fetchone()
                    if row is None:
                        report.missing_faces += 1
                        continue
                    report.faces_changed += 1
                    old = row["person_id"]
                    if old:
                        touched.add(old)
                    self._label_face(action, fid, person_id, old, row)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        for pid in sorted(touched):
            self.recompute_person(pid)
        return report

    def _label_face(self, action: str, fid: int, pid, old, row) -> None:
        run = self.conn.execute
        if action == "ignore":
            run(
                "UPDATE faces SET state='ignored', person_id=NULL, "
                "suggested_person_id=NULL, match_score=NULL WHERE id=?",
                (fid,),
            )
        elif action == "reject":
            run("INSERT OR IGNORE INTO face_rejections VALUES (?, ?)", (fid, pid))
            if pid in (old, row["suggested_person_id"]):
                run(
                    "UPDATE faces SET state='new', person_id=NULL, "
                    "suggested_person_id=NULL, match_score=NULL WHERE id=?",
                    (fid,),
                )
        else:  # confirm / move
            if action == "move" and old and old != pid:
                run("INSERT OR IGNORE INTO face_rejections VALUES (?, ?)", (fid, old))
            run(
                "DELETE FROM face_rejections WHERE face_id=? AND person_id=?",
                (fid, pid),
            )
            run(
                "UPDATE faces SET state='user', person_id=?, "
                "suggested_person_id=NULL, match_score=NULL WHERE id=?",
                (pid, fid),
            )
