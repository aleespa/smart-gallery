"""label-faces --refile files again only the faces a batch can have moved.

On a catalog that is fully filed, the scoped refile must leave every face
exactly as a full ``file_faces(group=False)`` after the same corrections
(the match_score of a hidden face aside: the scoped refile leaves it).
"""

import importlib
import sqlite3

import numpy as np
import pytest

from smart_gallery.db import GalleryRepository
from smart_gallery.services.file_faces import file_faces
from smart_gallery.services.label_faces import LabelEntry, label_faces
from tests.test_file_faces import Catalog

file_faces_module = importlib.import_module("smart_gallery.services.file_faces")

SUB = 12  # embeddings live in a small subspace, so scores spread widely


def _unit(v):
    out = np.zeros(512, dtype=np.float32)
    out[:SUB] = v
    return (out / np.linalg.norm(out)).astype(np.float32)


def _build(cat, repo, rng):
    """Named people with confirmed faces, groups, a hidden group, and a pool
    of faces around them at every distance, some too small to keep."""
    centers = [rng.normal(size=SUB) for _ in range(7)]

    def around(c, spread):
        return _unit(c + rng.normal(scale=spread, size=SUB))

    named = [cat.person(n) for n in ("Alice", "Bob", "Cara")]
    for pid, c in zip(named, centers):
        for _ in range(int(rng.integers(1, 8))):
            cat.face(around(c, 0.3), "user", pid)
    for c in centers[3:5]:  # unnamed groups
        g = cat.person()
        for _ in range(4):
            cat.face(around(c, 0.3), "cluster", g)
        repo.recompute_person(g)
    hidden = cat.person(hidden=True)
    for _ in range(3):
        cat.face(around(centers[5], 0.3), "ignored", hidden)
    repo.recompute_person(hidden)
    for pid in named:
        repo.recompute_person(pid)
    for _ in range(160):
        c = centers[int(rng.integers(len(centers)))]
        size = 30.0 if rng.random() < 0.1 else 60.0
        cat.face(around(c, float(rng.uniform(0.2, 1.6))), size=size)
    return named


def _settle(repo):
    """File until a full pass changes nothing, scores included: a group's
    centroid moves after a pass that moved its faces, so its scores settle
    one pass later than the states."""
    before = None
    for _ in range(12):
        file_faces(repo, group=False)
        now = _faces(repo)
        if now == before:
            return True
        before = now
    return False


def _copy(repo, tmp_path) -> GalleryRepository:
    target = tmp_path / "copy" / ".smart_gallery" / "gallery.db"
    target.parent.mkdir(parents=True)
    dst = sqlite3.connect(target)
    repo.conn.backup(dst)
    dst.close()
    return GalleryRepository.open(target)


def _faces(repo):
    return [
        tuple(r)
        for r in repo.conn.execute(
            "SELECT id, state, person_id, suggested_person_id, match_score "
            "FROM faces ORDER BY id"
        )
    ]


def _ids(repo, where, params=()):
    return [
        r[0] for r in repo.conn.execute(f"SELECT id FROM faces WHERE {where}", params)
    ]


def _entries(repo, named, rng, kind):
    """A batch of one kind of correction, picked from the filed catalog."""
    alice, bob, _cara = named
    pool = _ids(repo, "state IN ('new', 'suggested', 'cluster')")
    auto_alice = _ids(repo, "state='auto' AND person_id=?", (alice,))
    some = [int(f) for f in rng.permutation(pool)[:4]]
    if kind == "confirm":
        return [LabelEntry("confirm", tuple(some), person_id=alice)]
    if kind == "reject":
        faces = tuple(auto_alice[:2] or some[:2])
        return [LabelEntry("reject", faces, person_id=alice)]
    if kind == "move":
        faces = tuple(auto_alice[:2] or some[:2])
        return [LabelEntry("move", faces, person_id=bob)]
    if kind == "ignore":
        return [LabelEntry("ignore", tuple(some[:2]))]
    if kind == "new_person":
        return [LabelEntry("new_person", tuple(some[:3]), name="Dan")]
    if kind == "mixed":
        return [
            LabelEntry("confirm", tuple(some[:2]), person_id=bob),
            LabelEntry("reject", tuple(some[2:3]), person_id=alice),
            LabelEntry("ignore", tuple(some[3:])),
        ]
    raise AssertionError(kind)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize(
    "kind", ["confirm", "reject", "move", "ignore", "new_person", "mixed"]
)
def test_scoped_refile_matches_full_refile(repo, make_item, tmp_path, seed, kind):
    rng = np.random.default_rng(seed)
    cat = Catalog(repo, make_item)
    named = _build(cat, repo, rng)
    assert _settle(repo), "the catalog never settled"
    entries = _entries(repo, named, rng, kind)

    with _copy(repo, tmp_path) as full:
        label_faces(full, entries)
        file_faces(full, group=False)
        expected = _faces(full)
    label_faces(repo, entries, refile=True)
    got = _faces(repo)

    assert [r[:4] for r in got] == [r[:4] for r in expected]
    for g, e in zip(got, expected):
        if g[1] == "hidden":
            continue  # a hidden face keeps its old match_score: nothing reads it
        if g[4] is None or e[4] is None:
            assert g[4] == e[4], g
        else:
            assert g[4] == pytest.approx(e[4], abs=1e-5), g


def test_scoped_refile_skips_faces_it_cannot_move(repo, make_item, monkeypatch):
    rng = np.random.default_rng(1)
    cat = Catalog(repo, make_item)
    _build(cat, repo, rng)
    assert _settle(repo)
    filed = []
    real = file_faces_module.decide

    def counting(faces, candidates, cfg):
        filed.append(len(faces))
        return real(faces, candidates, cfg)

    monkeypatch.setattr(file_faces_module, "decide", counting)
    total = len(repo.load_filing_inputs()[0])
    face = _ids(repo, "state='new' OR state='suggested'")[:1]
    label_faces(repo, [LabelEntry("ignore", tuple(face))], refile=True)
    assert filed and filed[0] < total / 2
