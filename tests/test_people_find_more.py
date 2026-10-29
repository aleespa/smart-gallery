"""find_more: unclassified faces ranked by likeness to one person."""

import pytest

from smart_gallery.gui.people.model import find_more
from tests.test_file_faces import _near, _unit
from tests.test_file_faces import Catalog


@pytest.fixture
def cat(repo, make_item):
    return Catalog(repo, make_item)


def _reject(repo, face, pid):
    repo.conn.execute(
        "INSERT INTO face_rejections (face_id, person_id) VALUES (?, ?)", (face, pid)
    )
    repo.conn.commit()


def test_find_more_ranks_unclassified_by_similarity(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    close = cat.face(_near(_unit(0), 0.9), "new")
    mid = cat.face(_near(_unit(0), 0.6, axis=501), "hidden")
    cat.face(_unit(1), "new")
    tiles = find_more(repo, alice)
    assert [t.face_id for t in tiles] == [close, mid]
    assert tiles[0].score == pytest.approx(0.9, abs=0.01)


def test_find_more_includes_unnamed_groups_not_named_people(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    bob = cat.person("Bob")
    grp = cat.person()
    hid = cat.person(hidden=True)
    in_grp = cat.face(_near(_unit(0), 0.9, axis=500), "cluster", grp)
    cat.face(_near(_unit(0), 0.9, axis=501), "auto", bob)
    cat.face(_near(_unit(0), 0.9, axis=502), "hidden", hid)
    assert [t.face_id for t in find_more(repo, alice)] == [in_grp]


def test_find_more_skips_user_ignored_rejected_and_own(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    cat.face(_near(_unit(0), 0.9, axis=500), "user", cat.person("Bob"))
    cat.face(_near(_unit(0), 0.9, axis=501), "ignored")
    rej = cat.face(_near(_unit(0), 0.9, axis=502), "new")
    _reject(repo, rej, alice)
    cat.face(_near(_unit(0), 0.9, axis=503), "auto", alice)
    ok = cat.face(_near(_unit(0), 0.9, axis=504), "new")
    assert [t.face_id for t in find_more(repo, alice)] == [ok]


def test_find_more_limit(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    ids = [
        cat.face(_near(_unit(0), 0.9 - 0.05 * i, axis=500 + i), "new") for i in range(5)
    ]
    assert [t.face_id for t in find_more(repo, alice, limit=3)] == ids[:3]


def test_find_more_without_examples_is_empty(cat, repo):
    grp = cat.person()
    cat.face(_unit(0), "new")
    assert find_more(repo, grp) == []


def test_person_examples_matches_filing_inputs(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    cat.face(_unit(1), "user", alice)
    repo.conn.execute(
        "UPDATE persons SET reference_centroid=? WHERE id=?",
        (_unit(2).tobytes(), alice),
    )
    repo.conn.commit()
    _, cands = repo.load_filing_inputs()
    want = next(c for c in cands if c.person_id == alice).examples
    got = repo.person_examples(alice)
    assert got.shape == (3, 512)
    assert (got == want).all()
    assert repo.person_examples(9999).shape == (0, 512)
