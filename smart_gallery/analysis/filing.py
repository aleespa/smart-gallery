"""Decide where each face belongs: pure numpy, no database access.

Callers load faces and candidate people into ``FaceIn``/``Candidate`` rows and
apply the returned ``Decision`` objects. Embeddings are L2-normalized, so a dot
product is the cosine similarity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence

import numpy as np

CHUNK = 4096  # faces per similarity block; bounds memory on large catalogs


@dataclass(frozen=True)
class FilingConfig:
    auto_thresh: float = 0.50  # = cluster_faces.DEFAULT_MATCH_THRESH
    suggest_thresh: float = 0.35
    margin: float = 0.05
    k: int = 5  # nearest examples averaged per person
    min_face_px: float = 40.0  # short side of the box, decoded-image pixels
    min_det_score: float = 0.60
    min_group_size: int = 3


@dataclass(frozen=True)
class Candidate:
    person_id: int
    kind: Literal["named", "group", "hidden"]
    examples: np.ndarray  # (n, 512) L2-normalized, n >= 1
    rejected: np.ndarray  # (m, 512) faces rejected from this person, m >= 0


@dataclass(frozen=True)
class FaceIn:
    face_id: int
    embedding: np.ndarray  # (512,)
    state: str  # never in USER_STATES
    person_id: Optional[int]
    short_side: float
    det_score: Optional[float]
    rejected_from: frozenset[int]


@dataclass(frozen=True)
class Decision:
    face_id: int
    state: str  # "auto" | "suggested" | "cluster" | "hidden" | "new"
    person_id: Optional[int]
    suggested_person_id: Optional[int]
    score: Optional[float]


def _top_k_mean(sims: np.ndarray, k: int) -> np.ndarray:
    """Mean of the top-min(k, n) columns of each row of ``sims``."""
    n = sims.shape[1]
    kk = min(k, n)
    if kk >= n:
        return sims.mean(axis=1)
    top = np.partition(sims, n - kk, axis=1)[:, n - kk :]
    return top.mean(axis=1)


def _scores(
    embs: np.ndarray, candidates: Sequence[Candidate], k: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return (score matrix, rejected-wins matrix), both (N, P)."""
    embs = np.asarray(embs, dtype=np.float32).reshape(-1, embs.shape[-1])
    n, p = len(embs), len(candidates)
    scores = np.empty((n, p), dtype=np.float32)
    worse = np.zeros((n, p), dtype=bool)
    for start in range(0, n, CHUNK):
        block = embs[start : start + CHUNK]
        for j, c in enumerate(candidates):
            sims = block @ np.asarray(c.examples, dtype=np.float32).T
            scores[start : start + CHUNK, j] = _top_k_mean(sims, k)
            rej = np.asarray(c.rejected, dtype=np.float32)
            if len(rej):
                best_rej = (block @ rej.T).max(axis=1)
                worse[start : start + CHUNK, j] = best_rej > sims.max(axis=1)
    return scores, worse


def score_matrix(
    embs: np.ndarray, candidates: Sequence[Candidate], k: int
) -> np.ndarray:
    """(N, P): per candidate, mean of the top-min(k, n) cosine similarities."""
    return _scores(embs, candidates, k)[0]


def decide(
    faces: Sequence[FaceIn], candidates: Sequence[Candidate], cfg: FilingConfig
) -> list[Decision]:
    if not faces:
        return []
    embs = np.stack([f.embedding for f in faces]).astype(np.float32)
    scores, worse = _scores(embs, candidates, cfg.k)
    pids = [c.person_id for c in candidates]
    index = {pid: j for j, pid in enumerate(pids)}
    return [
        _decide_one(f, candidates, scores[i], worse[i], index, cfg)
        for i, f in enumerate(faces)
    ]


def _decide_one(
    face: FaceIn,
    candidates: Sequence[Candidate],
    row: np.ndarray,
    worse: np.ndarray,
    index: dict[int, int],
    cfg: FilingConfig,
) -> Decision:
    # 1. Allowed candidates.
    allowed = [
        j
        for j, c in enumerate(candidates)
        if c.person_id not in face.rejected_from and not worse[j]
    ]
    # 2. Rank.
    ranked = sorted(allowed, key=lambda j: float(row[j]), reverse=True)
    best_j = ranked[0] if ranked else None
    best = float(row[best_j]) if ranked else None
    runner_up = float(row[ranked[1]]) if len(ranked) > 1 else -1.0
    lead = best - runner_up if ranked else 0.0

    def make(state, person_id=None, suggested=None) -> Decision:
        return Decision(face.face_id, state, person_id, suggested, best)

    # 3. Sticky.
    if face.state in ("auto", "cluster") and face.person_id is not None:
        cur_j = index.get(face.person_id)
        cur = float(row[cur_j]) if cur_j in allowed else -1.0
        for j in ranked:
            c = candidates[j]
            if c.kind != "named" or c.person_id == face.person_id:
                continue
            s = float(row[j])
            if s >= cfg.auto_thresh and s - cur >= cfg.margin:
                return make("auto", c.person_id)
            break  # ranked descending: later named ones score lower
        return make(face.state, face.person_id)

    # 4. Quality gate.
    if face.short_side < cfg.min_face_px or (
        face.det_score is not None and face.det_score < cfg.min_det_score
    ):
        return make("hidden")

    # 5. Confident, unambiguous best.
    if ranked and best >= cfg.auto_thresh and lead >= cfg.margin:
        c = candidates[best_j]
        if c.kind == "named":
            return make("auto", c.person_id)
        if c.kind == "group":
            return make("cluster", c.person_id)
        return make("hidden", c.person_id)

    # 6. Suggestion from the best allowed named candidate.
    for j in ranked:
        if candidates[j].kind == "named":
            if float(row[j]) >= cfg.suggest_thresh:
                return make("suggested", None, candidates[j].person_id)
            break

    # 7. Leftover.
    return make("new")
