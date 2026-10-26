"""Tests for the pure face filing decisions."""

from __future__ import annotations

import numpy as np

from smart_gallery.analysis.filing import (
    Candidate,
    FaceIn,
    FilingConfig,
    decide,
    score_matrix,
)

CFG = FilingConfig()


def _at(sim: float, axis: int = 0, other: int = 100) -> np.ndarray:
    """Unit vector whose cosine with basis vector `axis` is `sim`."""
    v = np.zeros(512, dtype=np.float32)
    v[axis] = sim
    v[other] = float(np.sqrt(max(0.0, 1.0 - sim * sim)))
    return v


def _basis(axis: int) -> np.ndarray:
    v = np.zeros(512, dtype=np.float32)
    v[axis] = 1.0
    return v


def _cand(pid, axis, kind="named", rejected=None) -> Candidate:
    rej = np.zeros((0, 512), dtype=np.float32) if rejected is None else rejected
    return Candidate(pid, kind, _basis(axis)[None, :], rej)


def _face(emb, **kw) -> FaceIn:
    base = dict(
        face_id=1,
        embedding=emb,
        state="new",
        person_id=None,
        short_side=100.0,
        det_score=0.9,
        rejected_from=frozenset(),
    )
    base.update(kw)
    return FaceIn(**base)


def _one(face, cands, cfg=CFG):
    (d,) = decide([face], cands, cfg)
    return d


def test_confident_named_match_is_auto():
    d = _one(_face(_at(0.8)), [_cand(1, 0)])
    assert (d.state, d.person_id, d.suggested_person_id) == ("auto", 1, None)
    assert abs(d.score - 0.8) < 1e-5


def test_close_match_is_suggested_not_assigned():
    d = _one(_face(_at(0.42)), [_cand(1, 0)])
    assert (d.state, d.person_id, d.suggested_person_id) == ("suggested", None, 1)


def test_no_match_is_leftover():
    d = _one(_face(_at(0.1)), [_cand(1, 0)])
    assert d.state == "new" and d.person_id is None
    d = _one(_face(_at(0.9)), [])
    assert d.state == "new" and d.score is None


def test_ambiguous_between_two_named_is_not_auto():
    a = Candidate(1, "named", _at(0.80)[None, :], np.zeros((0, 512), np.float32))
    b = Candidate(2, "named", _at(0.78)[None, :], np.zeros((0, 512), np.float32))
    # The face is e0, so the scores are 0.80 and 0.78: a lead of 0.02 < margin.
    d = _one(_face(_basis(0)), [a, b])
    assert (d.state, d.person_id, d.suggested_person_id) == ("suggested", None, 1)


def test_rejected_person_is_never_chosen():
    d = _one(_face(_basis(0), rejected_from=frozenset({1})), [_cand(1, 0)])
    assert d.state == "new" and d.suggested_person_id is None


def test_closer_to_rejected_faces_drops_candidate():
    rej = _at(0.95)[None, :]
    c = _cand(1, 0, rejected=rej)
    face = _face(_at(0.6, axis=0, other=100))
    # sim to rejected row (0.95 at e0, rest e100) is larger than to example
    d = _one(face, [c])
    assert d.state == "new" and d.suggested_person_id is None


def test_small_or_weak_face_is_hidden():
    for kw in ({"short_side": 30.0}, {"det_score": 0.4}):
        d = _one(_face(_at(0.9), **kw), [_cand(1, 0)])
        assert (d.state, d.person_id) == ("hidden", None)
    assert _one(_face(_at(0.9), det_score=None), [_cand(1, 0)]).state == "auto"


def test_auto_face_is_sticky_when_nobody_clearly_wins():
    face = _face(_at(0.3), state="auto", person_id=1, short_side=10.0)
    d = _one(face, [_cand(1, 0), _cand(2, 5)])
    assert (d.state, d.person_id) == ("auto", 1)


def test_auto_face_moves_when_named_person_clearly_wins():
    emb = 0.9 * _basis(5) + 0.3 * _basis(0)
    emb = emb / np.linalg.norm(emb)
    face = _face(emb, state="auto", person_id=1)
    d = _one(face, [_cand(1, 0), _cand(2, 5)])
    assert (d.state, d.person_id) == ("auto", 2)


def test_cluster_face_joins_named_person_that_clearly_wins():
    face = _face(_basis(5), state="cluster", person_id=9)
    d = _one(face, [_cand(9, 0, kind="group"), _cand(2, 5)])
    assert (d.state, d.person_id) == ("auto", 2)


def test_match_to_group_is_cluster():
    d = _one(_face(_at(0.8)), [_cand(7, 0, kind="group")])
    assert (d.state, d.person_id) == ("cluster", 7)


def test_match_to_hidden_group_is_hidden_with_group_id():
    d = _one(_face(_at(0.8)), [_cand(8, 0, kind="hidden")])
    assert (d.state, d.person_id) == ("hidden", 8)


def test_score_matrix_uses_top_k_mean():
    ex = np.stack([_at(0.9), _at(0.8), _at(0.1)]).astype(np.float32)
    c = Candidate(1, "named", ex, np.zeros((0, 512), dtype=np.float32))
    # Query is e0: similarities are the e0 components 0.9, 0.8, 0.1.
    m = score_matrix(_basis(0)[None, :], [c], k=2)
    assert m.shape == (1, 1)
    assert abs(m[0, 0] - 0.85) < 1e-5
