"""file-faces: load, decide, group leftovers and apply (no GPU needed)."""

import numpy as np
import pytest

from smart_gallery.cli import _HANDLERS, build_parser
from smart_gallery.models import Face, Person
from smart_gallery.services.file_faces import file_faces


def _unit(i: int) -> np.ndarray:
    v = np.zeros(512, dtype=np.float32)
    v[i] = 1.0
    return v


def _near(base: np.ndarray, sim: float, axis: int = 500) -> np.ndarray:
    """A unit vector with cosine similarity ``sim`` to ``base``."""
    v = sim * base + np.sqrt(1 - sim * sim) * _unit(axis)
    return (v / np.linalg.norm(v)).astype(np.float32)


class Catalog:
    def __init__(self, repo, make_item):
        self.repo = repo
        repo.upsert_many([make_item("a.jpg")])
        self.mid = repo.conn.execute("SELECT id FROM media_items").fetchone()[0]

    def face(self, emb, state="new", person_id=None, suggested=None, size=60.0):
        f = Face(
            self.mid, 0, 0, size, size, 0.95, emb,
            person_id=person_id, state=state, suggested_person_id=suggested,
        )  # fmt: skip
        sql = (
            "INSERT INTO faces (media_id, bbox_x, bbox_y, bbox_w, bbox_h, det_score,"
            " embedding, person_id, cluster_id, state, suggested_person_id,"
            " match_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        cur = self.repo.conn.execute(sql, f.as_params())
        self.repo.conn.commit()
        return cur.lastrowid

    def person(self, name=None, hidden=False, **kw) -> int:
        return self.repo.create_person(Person(name=name, hidden=hidden, **kw))

    def row(self, face_id):
        return dict(
            self.repo.conn.execute("SELECT * FROM faces WHERE id=?", (face_id,))
            .fetchone()
        )  # fmt: skip

    def snapshot(self):
        return [
            tuple(r)
            for r in self.repo.conn.execute(
                "SELECT id, person_id, state, suggested_person_id, match_score "
                "FROM faces ORDER BY id"
            )
        ]


@pytest.fixture
def cat(repo, make_item):
    return Catalog(repo, make_item)


def test_user_and_ignored_faces_untouched(cat, repo):
    alice = cat.person("Alice")
    u = cat.face(_unit(0), "user", alice)
    ig = cat.face(_unit(0), "ignored")
    before = [cat.row(u), cat.row(ig)]
    file_faces(repo)
    assert [cat.row(u), cat.row(ig)] == before


def test_new_face_close_to_user_examples_becomes_auto(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    f = cat.face(_near(_unit(0), 0.9))
    report = file_faces(repo)
    row = cat.row(f)
    assert (row["state"], row["person_id"]) == ("auto", alice)
    assert row["cluster_id"] is None
    assert report.auto == 1
    assert repo.get_person(alice)["face_count"] == 2


def test_named_person_without_user_faces_uses_centroid(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "auto", alice)
    repo.recompute_person(alice)  # sets the centroid
    f = cat.face(_near(_unit(0), 0.9))
    file_faces(repo)
    assert cat.row(f)["person_id"] == alice
    assert cat.row(f)["state"] == "auto"


def test_rerun_without_changes_changes_nothing(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    cat.face(_near(_unit(0), 0.9))
    cat.face(_near(_unit(0), 0.45))
    cat.face(_unit(7))
    file_faces(repo, group=False)
    first = cat.snapshot()
    file_faces(repo, group=False)
    assert cat.snapshot() == first


def test_new_user_example_turns_suggestion_into_auto(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    target = _near(_unit(0), 0.45)
    f = cat.face(target)
    file_faces(repo)
    assert cat.row(f)["state"] == "suggested"
    assert cat.row(f)["suggested_person_id"] == alice
    cat.face(_near(target, 0.9, axis=400), "user", alice)
    file_faces(repo)
    row = cat.row(f)
    assert (row["state"], row["person_id"]) == ("auto", alice)


def test_no_group_leaves_leftovers_new(cat, repo):
    ids = [cat.face(_near(_unit(3), 0.99, axis=400 + i)) for i in range(4)]
    report = file_faces(repo, group=False)
    assert all(cat.row(i)["state"] == "new" for i in ids)
    assert report.left == 4
    assert report.groups_created == 0


def _cluster(center: int, n: int, seed: int) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        v = _unit(center) + 0.01 * rng.standard_normal(512).astype(np.float32)
        out.append((v / np.linalg.norm(v)).astype(np.float32))
    return out


@pytest.mark.faces
def test_grouping_creates_unnamed_groups_and_hides_noise(cat, repo):
    pytest.importorskip("sklearn")
    pytest.importorskip("hdbscan")
    members = [cat.face(e) for e in _cluster(10, 6, 1) + _cluster(200, 6, 2)]
    noise = [cat.face(_unit(300)), cat.face(_unit(400))]
    report = file_faces(repo)
    assert report.groups_created == 2
    assert report.grouped == 12
    assert report.hidden == 2
    rows = [cat.row(i) for i in members]
    assert {r["state"] for r in rows} == {"cluster"}
    assert len({r["person_id"] for r in rows}) == 2
    for pid in {r["person_id"] for r in rows}:
        p = repo.get_person(pid)
        assert p["name"] is None and p["face_count"] == 6
    for i in noise:
        assert (cat.row(i)["state"], cat.row(i)["person_id"]) == ("hidden", None)


@pytest.mark.faces
def test_existing_group_keeps_its_id_and_gains_faces(cat, repo):
    pytest.importorskip("sklearn")
    group = cat.person()
    for e in _cluster(10, 4, 3):
        cat.face(e, "cluster", group)
    repo.recompute_person(group)
    new = [cat.face(e) for e in _cluster(10, 3, 4)]
    file_faces(repo)
    for i in new:
        assert (cat.row(i)["state"], cat.row(i)["person_id"]) == ("cluster", group)
    assert repo.get_person(group)["face_count"] == 7


def test_cli_file_faces_wired():
    args = build_parser().parse_args(["file-faces", "E:/", "--no-group"])
    assert args.command == "file-faces"
    assert args.no_group is True
    assert "file-faces" in _HANDLERS
