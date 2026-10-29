import sqlite3
from dataclasses import replace

import pytest

from smart_gallery.db.repository import GalleryRepository
from smart_gallery.gui.people.model import (
    NeedsUpgrade,
    hidden_faces,
    library_filter,
    name_owner,
    people_list,
    person_faces,
    person_summary,
    photos_filter,
    suggestions_for,
)
from smart_gallery.organize.filters import FilterOptions

_BLOB = b"\x00" * 8


class Cat:
    def __init__(self, repo, make_item):
        self.repo = repo
        self.make_item = make_item

    def media(self, relpath, date="2026-06-01"):
        self.repo.upsert_many([self.make_item(relpath, date_taken=date)])
        return self.repo.conn.execute(
            "SELECT id FROM media_items WHERE relpath=?", (relpath,)
        ).fetchone()[0]

    def person(self, name=None, hidden=False, cover=None):
        cur = self.repo.conn.execute(
            "INSERT INTO persons (name, hidden, cover_face_id) VALUES (?,?,?)",
            (name, int(hidden), cover),
        )
        self.repo.conn.commit()
        return cur.lastrowid

    def face(self, media, person=None, state="auto", score=None, suggested=None):
        cur = self.repo.conn.execute(
            "INSERT INTO faces (media_id, bbox_x, bbox_y, bbox_w, bbox_h,"
            " embedding, person_id, state, suggested_person_id, match_score)"
            " VALUES (?,1,2,3,4,?,?,?,?,?)",
            (media, _BLOB, person, state, suggested, score),
        )
        self.repo.conn.commit()
        return cur.lastrowid


@pytest.fixture
def cat(repo, make_item):
    return Cat(repo, make_item)


def test_people_list_last_undo(cat, repo):
    alice = cat.person("Alice")
    cat.face(cat.media("a.jpg"), alice)
    assert people_list(repo).last_undo is None
    snap = repo.face_snapshot()
    repo.conn.execute("UPDATE faces SET state = 'user'")
    repo.conn.commit()
    repo.record_undo("Renamed Alice", snap)
    assert people_list(repo).last_undo == "Renamed Alice"


def test_people_list_sections_and_order(cat, repo):
    m = [cat.media(f"p{i}.jpg") for i in range(4)]
    bob = cat.person("bob")
    amy = cat.person("Amy")
    zed = cat.person("zed")
    grp = cat.person(None)
    grp2 = cat.person("")
    hid = cat.person(None, hidden=True)
    f = cat.face(m[0], bob)
    cat.face(m[1], bob)
    cat.face(m[0], amy)
    cat.face(m[1], amy)
    cat.face(m[0], zed)
    cat.face(m[0], grp)
    cat.face(m[1], grp)
    cat.face(m[2], grp)
    cat.face(m[0], grp2)
    cat.face(m[0], hid, state="ignored")
    repo.conn.execute("UPDATE persons SET cover_face_id=? WHERE id=?", (f, bob))
    repo.conn.commit()
    pl = people_list(repo)
    assert [p.name for p in pl.named] == ["Amy", "bob", "zed"]
    assert [p.id for p in pl.groups] == [grp, grp2]
    assert [p.id for p in pl.hidden_groups] == [hid]
    assert pl.hidden_groups[0].hidden
    assert pl.has_faces
    assert pl.pending_images == 4  # no scan state rows
    assert next(p for p in pl.named if p.id == bob).cover.face_id == f
    assert next(p for p in pl.named if p.id == amy).cover is None


def test_photo_count_is_distinct_media(cat, repo):
    m = cat.media("a.jpg")
    p = cat.person("ann")
    cat.face(m, p)
    cat.face(m, p)
    row = people_list(repo).named[0]
    assert (row.photos, row.faces) == (1, 2)


def test_suggestion_count_per_person(cat, repo):
    m = cat.media("a.jpg")
    p = cat.person("ann")
    q = cat.person("bo")
    cat.face(m, None, state="suggested", suggested=p, score=0.5)
    cat.face(m, None, state="suggested", suggested=p, score=0.7)
    cat.face(m, None, state="new", suggested=q)
    pl = people_list(repo)
    by = {r.id: r for r in pl.named}
    assert by[p].suggestions == 2
    assert by[q].suggestions == 0
    assert [t.score for t in suggestions_for(repo, p)] == [0.7, 0.5]


def test_person_faces_least_sure_first_user_last(cat, repo):
    m = cat.media("a.jpg")
    p = cat.person("ann")
    u = cat.face(m, p, state="user", score=0.1)
    hi = cat.face(m, p, score=0.9)
    lo = cat.face(m, p, score=0.2)
    nul = cat.face(m, p, score=None)
    got = [t.face_id for t in person_faces(repo, p, "least_sure")]
    assert got == [nul, lo, hi, u]


def test_person_faces_newest(cat, repo):
    p = cat.person("ann")
    old = cat.face(cat.media("o.jpg", "2020-01-01"), p)
    new = cat.face(cat.media("n.jpg", "2025-01-01"), p)
    none = cat.face(cat.media("x.jpg", None), p)
    got = [t.face_id for t in person_faces(repo, p, "newest")]
    assert got == [new, old, none]


def test_person_summary_year_range(cat, repo):
    p = cat.person("ann")
    cat.face(cat.media("o.jpg", "2019-05-01"), p)
    cat.face(cat.media("n.jpg", "2024-05-01"), p)
    cat.face(cat.media("x.jpg", None), p)
    s = person_summary(repo, p)
    assert (s.first_year, s.last_year) == (2019, 2024)
    assert s.row.photos == 3
    assert person_summary(repo, 999) is None
    e = cat.person("empty")
    s = person_summary(repo, e)
    assert (s.first_year, s.last_year) == (None, None)


def test_hidden_faces(cat, repo):
    p = cat.person("ann")
    a = cat.face(cat.media("a.jpg", "2020-01-01"), None, state="hidden")
    b = cat.face(cat.media("b.jpg", "2021-01-01"), None, state="ignored")
    cat.face(cat.media("c.jpg"), p, state="hidden")
    cat.face(cat.media("d.jpg"), None, state="new")
    assert [t.face_id for t in hidden_faces(repo)] == [b, a]


def test_tile_fields(cat, repo):
    m = cat.media("a.jpg")
    p = cat.person("ann")
    f = cat.face(m, p, score=0.4)
    repo.conn.execute(
        "INSERT INTO face_scan_state (media_id, img_w, img_h) VALUES (?,100,50)", (m,)
    )
    repo.conn.commit()
    t = person_faces(repo, p, "newest")[0]
    assert (t.face_id, t.media_id, t.relpath, t.ext) == (f, m, "a.jpg", ".jpg")
    assert (t.state, t.score, t.bbox, t.img_size) == (
        "auto", 0.4, (1.0, 2.0, 3.0, 4.0), (100, 50),
    )  # fmt: skip


def test_photos_filter_and_library_filter_keep_other_filters():
    f = photos_filter([3, 4], "all")
    assert (f.person_ids, f.people_match) == ([3, 4], "all")
    base = FilterOptions(cameras=["X"], person_ids=[1])
    out = library_filter(base, ["ann", "bo"], "all")
    assert out.cameras == ["X"]
    assert (out.people, out.people_match, out.person_ids) == (
        ["ann", "bo"], "all", None,
    )  # fmt: skip
    assert base == replace(base)


def test_name_owner_case_insensitive(cat, repo):
    ann = cat.person("Ann")
    cat.person(None)
    pl = people_list(repo)
    assert name_owner(pl, " ann ", 0).id == ann
    assert name_owner(pl, "ANN", ann) is None
    assert name_owner(pl, "zzz", 0) is None
    assert name_owner(pl, "", 0) is None


def test_no_face_tables_is_needs_upgrade(tmp_path):
    db = tmp_path / "d" / ".smart_gallery" / "gallery.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE media_items (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    with GalleryRepository.open(db, read_only=True) as r:
        with pytest.raises(NeedsUpgrade):
            people_list(r)


def test_old_catalog_raises_needs_upgrade(tmp_path, repo, drive):
    # Rebuild faces without the v5 columns, as a v4 catalog has it.
    repo.conn.execute("DROP TABLE faces")
    repo.conn.execute(
        "CREATE TABLE faces (id INTEGER PRIMARY KEY, media_id INTEGER,"
        " bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL, det_score REAL,"
        " embedding BLOB, person_id INTEGER, cluster_id INTEGER)"
    )
    repo.conn.commit()
    with GalleryRepository.open(drive, read_only=True) as ro:
        cols = {r["name"] for r in ro.conn.execute("PRAGMA table_info(faces)")}
        assert "state" not in cols
        with pytest.raises(NeedsUpgrade):
            people_list(ro)
