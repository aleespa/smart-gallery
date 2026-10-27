"""Copying face data to a mirror, and keeping face commands off it."""

import pytest

from smart_gallery.db import GalleryRepository
from smart_gallery.mirror import faces as mirror_faces
from smart_gallery.mirror.faces import sync_face_data
from smart_gallery.mirror.types import Peer
from smart_gallery.services.file_faces import file_faces
from smart_gallery.services.label_faces import label_faces
from smart_gallery.services.scan_faces import scan_faces

INSERT = (
    "INSERT INTO faces (id, media_id, bbox_x, bbox_y, bbox_w, bbox_h, det_score,"
    " embedding, person_id, state) VALUES (?,?,?,?,?,?,?,?,?,?)"
)


def _mid(repo, relpath):
    return repo.conn.execute(
        "SELECT id FROM media_items WHERE relpath=?", (relpath,)
    ).fetchone()[0]


def _face(repo, fid, relpath, emb=b"\x01\x02", person=None, state="new"):
    repo.conn.execute(
        INSERT, (fid, _mid(repo, relpath), 1, 2, 30, 40, 0.9, emb, person, state)
    )
    repo.conn.commit()


def _person(repo, pid, name=None):
    repo.conn.execute("INSERT INTO persons (id, name) VALUES (?, ?)", (pid, name))
    repo.conn.commit()


@pytest.fixture
def pair(two_drives, make_item):
    e, d = two_drives
    w = GalleryRepository.open(e)
    m = GalleryRepository.open(d)
    # different insertion order so media ids differ between catalogs
    w.upsert_many([make_item("a.jpg"), make_item("b.jpg"), make_item("c.jpg")])
    m.upsert_many(
        [make_item("z.jpg"), make_item("c.jpg"), make_item("b.jpg"), make_item("a.jpg")]
    )
    yield w, m
    w.close()
    m.close()


def _dump(repo):
    return [
        tuple(r)
        for r in repo.conn.execute(
            "SELECT f.id, lower(m.relpath), f.person_id, f.state, f.embedding "
            "FROM faces f JOIN media_items m ON m.id=f.media_id ORDER BY f.id"
        )
    ]


def test_first_transfer_replaces_mirror_faces_with_overlapping_ids(pair):
    w, m = pair
    _person(w, 7, "Ann")
    _face(w, 1, "a.jpg", b"\xaa", person=7, state="user")
    _face(w, 2, "b.jpg", b"\xbb")
    _person(m, 1, "Old")
    for i, rel in ((1, "z.jpg"), (2, "c.jpg"), (3, "b.jpg")):
        _face(m, i, rel, b"\x00", person=1)

    report = sync_face_data(w, m)

    assert report.reset is True
    assert _dump(m) == _dump(w)
    assert [r[0] for r in m.conn.execute("SELECT id FROM persons")] == [7]
    assert m.face_source_uuid() == w.get_meta("db_uuid")


def test_second_run_is_a_noop(pair, monkeypatch):
    w, m = pair
    _face(w, 1, "a.jpg", b"\xaa")
    sync_face_data(w, m)
    reads = []
    real = mirror_faces._read_embeddings
    monkeypatch.setattr(
        mirror_faces,
        "_read_embeddings",
        lambda repo, ids: reads.append(ids) or real(repo, ids),
    )
    report = sync_face_data(w, m)
    assert (report.inserted, report.updated, report.deleted) == (0, 0, 0)
    assert report.reset is False
    assert reads == []


def test_renaming_a_person_updates_the_mirror(pair):
    w, m = pair
    _person(w, 7, "Ann")
    _face(w, 1, "a.jpg", person=7)
    sync_face_data(w, m)
    w.conn.execute("UPDATE persons SET name='Anna' WHERE id=7")
    w.conn.commit()
    report = sync_face_data(w, m)
    assert report.updated == 0
    assert m.conn.execute("SELECT name FROM persons WHERE id=7").fetchone()[0] == "Anna"
    assert _dump(m)[0][2] == 7  # the face kept its person


def test_face_for_missing_relpath_is_skipped(pair, make_item):
    w, m = pair
    w.upsert_many([make_item("only_here.jpg")])
    _face(w, 1, "a.jpg")
    _face(w, 2, "only_here.jpg")
    report = sync_face_data(w, m)
    assert report.inserted == 1
    assert [r[0] for r in _dump(m)] == [1]


def test_media_id_is_remapped_by_relpath(pair):
    w, m = pair
    assert _mid(w, "a.jpg") != _mid(m, "a.jpg")
    _face(w, 5, "A.JPG".lower())
    sync_face_data(w, m)
    row = m.conn.execute("SELECT media_id FROM faces WHERE id=5").fetchone()
    assert row[0] == _mid(m, "a.jpg")


def test_face_commands_refuse_on_mirror(pair):
    w, m = pair
    for repo in (w, m):  # no pairing: the guard lets them through
        file_faces(repo, group=False)
        label_faces(repo, [])
    m.add_peer("mirror", Peer("u" * 32, "x.db"))
    with pytest.raises(ValueError, match="mirror"):
        scan_faces(m)
    with pytest.raises(ValueError, match="mirror"):
        file_faces(m)
    with pytest.raises(ValueError, match="mirror"):
        label_faces(m, [])
