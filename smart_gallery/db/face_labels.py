"""Face filing storage: the inputs the filing step reads and the decisions it writes."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np

from smart_gallery.config import FACE_EMBEDDING_DIM

if TYPE_CHECKING:
    from smart_gallery.analysis.filing import Candidate, Decision, FaceIn


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
