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


def test_sticky_face_leaves_a_person_it_was_rejected_from():
    # Merged into Alice although the user said it is not Alice: the sticky rule
    # must not keep it there; it is decided again without Alice.
    face = _face(_at(0.9), state="auto", person_id=1, rejected_from=frozenset({1}))
    d = _one(face, [_cand(1, 0), _cand(7, 0, kind="group")])
    assert (d.state, d.person_id) == ("cluster", 7)


def test_sticky_face_leaves_a_person_closer_to_its_rejected_faces():
    rej = _at(0.95)[None, :]
    face = _face(_at(0.6), state="cluster", person_id=1)
    d = _one(face, [_cand(1, 0, kind="group", rejected=rej)])
    assert (d.state, d.person_id) == ("new", None)


def test_hidden_face_stays_hidden():
    # A one-off face the grouping hid is not pulled back into the pool.
    d = _one(_face(_at(0.1), state="hidden"), [_cand(1, 0)])
    assert (d.state, d.person_id, d.suggested_person_id) == ("hidden", None, None)
    d = _one(_face(_at(0.8), state="hidden", person_id=8), [_cand(8, 0, "hidden")])
    assert (d.state, d.person_id) == ("hidden", 8)


def _reference(face, cands, cfg):
    """The filing rules, one face at a time, written as plainly as possible."""
    rows = []
    for j, c in enumerate(cands):
        sims = np.asarray(c.examples, np.float32) @ face.embedding
        kk = min(cfg.k, len(sims))
        score = float(np.sort(sims)[::-1][:kk].mean())
        rej = np.asarray(c.rejected, np.float32)
        worse = bool(len(rej)) and float((rej @ face.embedding).max()) > sims.max()
        if c.person_id not in face.rejected_from and not worse:
            rows.append((score, j))
    ranked = sorted(rows, key=lambda r: (-r[0], r[1]))
    best = ranked[0][0] if ranked else None

    def make(state, pid=None, sug=None):
        return (state, pid, sug)

    if face.state == "hidden":
        return make("hidden", face.person_id)
    allowed_pids = {cands[j].person_id for _, j in ranked}
    if face.state in ("auto", "cluster") and face.person_id is not None:
        known = any(c.person_id == face.person_id for c in cands)
        if face.person_id in allowed_pids or not (
            known or face.person_id in face.rejected_from
        ):
            cur = next(
                (s for s, j in ranked if cands[j].person_id == face.person_id), -1.0
            )
            for s, j in ranked:
                c = cands[j]
                if c.kind == "named" and c.person_id != face.person_id:
                    if s >= cfg.auto_thresh and s - cur >= cfg.margin:
                        return make("auto", c.person_id)
                    break
            return make(face.state, face.person_id)
    if face.short_side < cfg.min_face_px or (
        face.det_score is not None and face.det_score < cfg.min_det_score
    ):
        return make("hidden")
    if ranked:
        lead = best - (ranked[1][0] if len(ranked) > 1 else -1.0)
        if best >= cfg.auto_thresh and lead >= cfg.margin:
            c = cands[ranked[0][1]]
            state = {"named": "auto", "group": "cluster", "hidden": "hidden"}
            return make(state[c.kind], c.person_id)
    for s, j in ranked:
        if cands[j].kind == "named":
            if s >= cfg.suggest_thresh:
                return make("suggested", None, cands[j].person_id)
            break
    return make("new")


def test_decide_matches_the_plain_rules_across_chunks(monkeypatch):
    import smart_gallery.analysis.filing as filing

    monkeypatch.setattr(filing, "CHUNK", 7)
    monkeypatch.setattr(filing, "_COLS", 5)
    rng = np.random.default_rng(3)
    centres = rng.standard_normal((6, 512)).astype(np.float32)

    def unit(v):
        return (v / np.linalg.norm(v, axis=-1, keepdims=True)).astype(np.float32)

    def near(i, n, spread=0.9):
        return unit(centres[i] + spread * rng.standard_normal((n, 512)) / 20)

    empty = np.zeros((0, 512), np.float32)
    cands = [
        Candidate(1, "named", near(0, 8), near(1, 2)),
        Candidate(2, "named", near(1, 3), empty),
        Candidate(3, "named", near(2, 1), empty),
        Candidate(4, "group", near(3, 1), near(0, 1)),
        Candidate(5, "group", near(2, 1), empty),
        Candidate(6, "hidden", near(4, 1), empty),
    ]
    states = ["new", "suggested", "auto", "cluster", "hidden"]
    faces = []
    for i in range(60):
        state = states[i % 5]
        pid = None
        if state in ("auto", "cluster"):
            pid = int(rng.integers(1, 8))  # 7 is not a candidate
        rejected = frozenset(int(p) for p in rng.choice(7, 1) if p) if i % 3 else ()
        faces.append(
            FaceIn(
                i,
                near(int(rng.integers(0, 6)), 1, spread=float(rng.uniform(5, 25)))[0],
                state,
                pid,
                float(rng.uniform(20, 100)),
                float(rng.uniform(0.4, 1.0)),
                frozenset(rejected),
            )
        )
    got = [
        (d.state, d.person_id, d.suggested_person_id) for d in decide(faces, cands, CFG)
    ]
    want = [_reference(f, cands, CFG) for f in faces]
    assert got == want
    assert len({g[0] for g in got}) >= 4  # the data exercises several rules
