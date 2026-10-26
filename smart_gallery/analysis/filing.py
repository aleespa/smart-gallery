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
_COLS = 4096  # candidate examples per matrix product


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


def _offsets(sizes: np.ndarray) -> np.ndarray:
    return np.concatenate(([0], np.cumsum(sizes)[:-1])).astype(np.int64)


@dataclass(frozen=True)
class _Batch:
    """Candidates whose examples are scored with one matrix product."""

    cols: np.ndarray  # candidate indices
    examples: np.ndarray  # their examples, stacked
    starts: np.ndarray  # first example row of each candidate
    sizes: np.ndarray  # example count of each candidate (float32)
    big: list[int]  # positions in ``cols`` with more than k examples
    rej_pos: np.ndarray  # positions in ``cols`` that have rejected faces
    rejected: np.ndarray  # their rejected faces, stacked
    rej_starts: np.ndarray


def _batches(candidates: Sequence[Candidate], k: int) -> list[_Batch]:
    groups: list[list[int]] = []
    width = 0
    for j, c in enumerate(candidates):
        n = len(c.examples)
        if not groups or width + n > _COLS:
            groups.append([])
            width = 0
        groups[-1].append(j)
        width += n
    out = []
    for cols in groups:
        exs = [np.asarray(candidates[j].examples, np.float32) for j in cols]
        sizes = np.array([len(e) for e in exs], dtype=np.int64)
        rej_pos = [i for i, j in enumerate(cols) if len(candidates[j].rejected)]
        rejs = [np.asarray(candidates[cols[i]].rejected, np.float32) for i in rej_pos]
        out.append(
            _Batch(
                cols=np.array(cols, dtype=np.int64),
                examples=np.concatenate(exs),
                starts=_offsets(sizes),
                sizes=sizes.astype(np.float32),
                big=[i for i, n in enumerate(sizes) if n > k],
                rej_pos=np.array(rej_pos, dtype=np.int64),
                rejected=np.concatenate(rejs) if rejs else np.empty((0, 0)),
                rej_starts=_offsets(np.array([len(r) for r in rejs], np.int64)),
            )
        )
    return out


def _block_scores(
    block: np.ndarray, batches: list[_Batch], p: int, k: int
) -> tuple[np.ndarray, np.ndarray]:
    """(score, rejected-wins) matrices of one block of faces, both (b, P)."""
    scores = np.empty((len(block), p), dtype=np.float32)
    worse = np.zeros((len(block), p), dtype=bool)
    for b in batches:
        sims = block @ b.examples.T
        means = np.add.reduceat(sims, b.starts, axis=1) / b.sizes
        for i in b.big:
            lo = int(b.starts[i])
            means[:, i] = _top_k_mean(sims[:, lo : lo + int(b.sizes[i])], k)
        scores[:, b.cols] = means
        if len(b.rej_pos):
            best_rej = np.maximum.reduceat(block @ b.rejected.T, b.rej_starts, axis=1)
            best_ex = np.maximum.reduceat(sims, b.starts, axis=1)[:, b.rej_pos]
            worse[:, b.cols[b.rej_pos]] = best_rej > best_ex
    return scores, worse


def _scores(
    embs: np.ndarray, candidates: Sequence[Candidate], k: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return (score matrix, rejected-wins matrix), both (N, P)."""
    embs = np.asarray(embs, dtype=np.float32)
    embs = embs.reshape(-1, embs.shape[-1])
    return _block_scores(embs, _batches(candidates, k), len(candidates), k)


def score_matrix(
    embs: np.ndarray, candidates: Sequence[Candidate], k: int
) -> np.ndarray:
    """(N, P): per candidate, mean of the top-min(k, n) cosine similarities."""
    return _scores(embs, candidates, k)[0]


def decide(
    faces: Sequence[FaceIn], candidates: Sequence[Candidate], cfg: FilingConfig
) -> list[Decision]:
    """File every face. Faces are scored CHUNK at a time, so memory stays at
    one (CHUNK, P) block whatever the size of the catalog."""
    if not faces:
        return []
    batches = _batches(candidates, cfg.k)
    index = {c.person_id: j for j, c in enumerate(candidates)}
    named = np.array([c.kind == "named" for c in candidates], dtype=bool)
    out: list[Decision] = []
    for start in range(0, len(faces), CHUNK):
        chunk = faces[start : start + CHUNK]
        embs = np.stack([f.embedding for f in chunk]).astype(np.float32)
        scores, worse = _block_scores(embs, batches, len(candidates), cfg.k)
        allowed = ~worse
        for r, f in enumerate(chunk):
            for pid in f.rejected_from:
                j = index.get(pid)
                if j is not None:
                    allowed[r, j] = False
        out.extend(_decide_chunk(chunk, candidates, scores, allowed, named, index, cfg))
    return out


_NEG = np.float32(-np.inf)


@dataclass(frozen=True)
class _Ranked:
    """Per face of a chunk: the ranking facts the rules need."""

    best_j: np.ndarray  # best allowed candidate (ties: lower index)
    best: np.ndarray  # its score, -inf when nothing is allowed
    runner: np.ndarray  # second-best allowed score, -inf when none
    named_j: np.ndarray  # best allowed named candidate
    named_s: np.ndarray  # its score, -inf when none
    own_j: np.ndarray  # index of the face's own person, -1 when none
    rival_j: np.ndarray  # best allowed named candidate other than its own
    rival_s: np.ndarray  # its score, -inf when none


def _rank(scores, allowed, named, own_j) -> _Ranked:
    rows = np.arange(len(scores))
    masked = np.where(allowed, scores, _NEG)
    best_j = masked.argmax(axis=1)
    best = masked[rows, best_j]
    masked[rows, best_j] = _NEG
    runner = masked.max(axis=1)
    masked[rows, best_j] = best
    named_m = np.where(named, masked, _NEG)
    named_j = named_m.argmax(axis=1)
    named_s = named_m[rows, named_j]
    has_own = own_j >= 0
    named_m[rows[has_own], own_j[has_own]] = _NEG
    rival_j = named_m.argmax(axis=1)
    return _Ranked(
        best_j, best, runner, named_j, named_s, own_j, rival_j, named_m[rows, rival_j]
    )


def _decide_chunk(
    faces: Sequence[FaceIn],
    candidates: Sequence[Candidate],
    scores: np.ndarray,
    allowed: np.ndarray,
    named: np.ndarray,
    index: dict[int, int],
    cfg: FilingConfig,
) -> list[Decision]:
    own_j = np.array(
        [-1 if f.person_id is None else index.get(f.person_id, -1) for f in faces],
        dtype=np.int64,
    )
    rk = _rank(scores, allowed, named, own_j) if candidates else None
    out = []
    for r, face in enumerate(faces):
        has = rk is not None and rk.best[r] != _NEG
        best = float(rk.best[r]) if has else None

        def make(state, person_id=None, suggested=None) -> Decision:
            return Decision(face.face_id, state, person_id, suggested, best)

        out.append(
            _rules(face, r, rk, has, best, make, candidates, scores, allowed, cfg)
        )
    return out


def _rules(face, r, rk, has, best, make, candidates, scores, allowed, cfg):
    # 0. A face hidden earlier (too small or weak, or a one-off) stays hidden.
    if face.state == "hidden":
        return make("hidden", face.person_id)

    # 3. Sticky, unless the user has since ruled it out of its person
    #    (rejected, or closer to that person's rejected faces).
    if face.state in ("auto", "cluster") and face.person_id is not None:
        j = int(rk.own_j[r]) if rk is not None else -1
        ruled_out = face.person_id in face.rejected_from or (
            j >= 0 and not allowed[r, j]
        )
        if not ruled_out:
            cur = float(scores[r, j]) if j >= 0 else -1.0
            if rk is not None and rk.rival_s[r] != _NEG:
                s = float(rk.rival_s[r])
                if s >= cfg.auto_thresh and s - cur >= cfg.margin:
                    return make("auto", candidates[int(rk.rival_j[r])].person_id)
            return make(face.state, face.person_id)

    # 4. Quality gate.
    if face.short_side < cfg.min_face_px or (
        face.det_score is not None and face.det_score < cfg.min_det_score
    ):
        return make("hidden")

    if not has:
        return make("new")

    # 5. Confident, unambiguous best.
    runner_up = float(rk.runner[r]) if rk.runner[r] != _NEG else -1.0
    if best >= cfg.auto_thresh and best - runner_up >= cfg.margin:
        c = candidates[int(rk.best_j[r])]
        if c.kind == "named":
            return make("auto", c.person_id)
        if c.kind == "group":
            return make("cluster", c.person_id)
        return make("hidden", c.person_id)

    # 6. Suggestion from the best allowed named candidate.
    if rk.named_s[r] != _NEG and float(rk.named_s[r]) >= cfg.suggest_thresh:
        return make("suggested", None, candidates[int(rk.named_j[r])].person_id)

    # 7. Leftover.
    return make("new")
