"""Undo store: face corrections recorded as batches and reverted."""

import pytest

from smart_gallery.db.face_undo import MAX_UNDO_BATCHES, UndoConflict
from smart_gallery.db.repository import GalleryRepository
from tests.test_file_faces import Catalog, _unit

UNDO_TABLES = (
    "face_undo_batches",
    "face_undo_faces",
    "face_undo_rejections",
    "face_undo_persons",
)


@pytest.fixture
def cat(repo, make_item):
    return Catalog(repo, make_item)


def _set_state(repo, face_id, state):
    repo.conn.execute("UPDATE faces SET state=? WHERE id=?", (state, face_id))
    repo.conn.commit()


def test_record_and_undo_restores_faces_rejections_and_scores(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "suggested", None, suggested=alice)
    g = cat.face(_unit(1), "auto", alice)
    before = repo.face_snapshot()
    repo.conn.execute(
        "UPDATE faces SET state='new', suggested_person_id=NULL WHERE id=?", (f,)
    )
    repo.conn.execute("UPDATE faces SET match_score=0.9 WHERE id=?", (g,))
    repo.conn.execute("INSERT INTO face_rejections VALUES (?, ?)", (f, alice))
    repo.conn.commit()
    bid = repo.record_undo("Marked 1 face as not Alice", before)
    assert repo.last_undo().batch_id == bid
    info, n = repo.undo_last()
    assert info.summary == "Marked 1 face as not Alice" and n == 2
    assert repo.face_snapshot() == before
    assert repo.last_undo() is None


def test_record_nothing_changed_returns_none(cat, repo):
    cat.face(_unit(0), "new")
    before = repo.face_snapshot()
    assert repo.record_undo("Nothing", before) is None
    assert repo.last_undo() is None


def test_undo_recreates_deleted_group(cat, repo):
    alice = cat.person("Alice")
    group = cat.person(None)
    f = cat.face(_unit(0), "cluster", group)
    repo.recompute_person(group)
    before = repo.face_snapshot()
    repo.conn.execute(
        "UPDATE faces SET state='user', person_id=? WHERE id=?", (alice, f)
    )
    repo.conn.commit()
    repo.recompute_person(group)  # empty and unnamed: deleted
    assert repo.get_person(group) is None
    repo.record_undo("Moved a face", before)
    repo.undo_last()
    assert repo.get_person(group) is not None
    row = cat.row(f)
    assert (row["state"], row["person_id"]) == ("cluster", group)


def test_undo_deletes_person_the_batch_created(cat, repo):
    f = cat.face(_unit(0), "new")
    before = repo.face_snapshot()
    carol = cat.person("Carol")
    repo.conn.execute(
        "UPDATE faces SET state='user', person_id=? WHERE id=?", (carol, f)
    )
    repo.conn.commit()
    repo.record_undo("Named Carol", before)
    repo.undo_last()
    assert repo.get_person(carol) is None
    assert repo.face_snapshot() == before


def test_undo_refuses_when_a_face_changed_since(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "suggested", None, suggested=alice)
    before = repo.face_snapshot()
    _set_state(repo, f, "new")
    repo.record_undo("Dismissed", before)
    _set_state(repo, f, "ignored")
    current = repo.face_snapshot()
    with pytest.raises(UndoConflict):
        repo.undo_last()
    assert repo.face_snapshot() == current
    assert repo.last_undo() is not None


def test_undo_refuses_when_face_deleted(cat, repo):
    f = cat.face(_unit(0), "new")
    before = repo.face_snapshot()
    _set_state(repo, f, "ignored")
    repo.record_undo("Ignored", before)
    repo.conn.execute("DELETE FROM faces WHERE id=?", (f,))
    repo.conn.commit()
    with pytest.raises(UndoConflict):
        repo.undo_last()


def test_undo_refuses_when_deleted_person_id_reused(cat, repo):
    alice = cat.person("Alice")
    group = cat.person(None)
    f = cat.face(_unit(0), "cluster", group)
    before = repo.face_snapshot()
    repo.conn.execute(
        "UPDATE faces SET state='user', person_id=? WHERE id=?", (alice, f)
    )
    repo.conn.commit()
    repo.recompute_person(group)
    repo.record_undo("Moved a face", before)
    repo.conn.execute(
        "INSERT INTO persons (id, name, hidden) VALUES (?, 'X', 0)", (group,)
    )
    repo.conn.commit()
    with pytest.raises(UndoConflict):
        repo.undo_last()


def test_batches_are_capped(cat, repo):
    f = cat.face(_unit(0), "new")
    for i in range(MAX_UNDO_BATCHES + 1):
        before = repo.face_snapshot()
        _set_state(repo, f, "ignored" if i % 2 == 0 else "new")
        repo.record_undo(f"change {i}", before)
    rows = repo.conn.execute(
        "SELECT summary FROM face_undo_batches ORDER BY id"
    ).fetchall()
    assert len(rows) == MAX_UNDO_BATCHES == 20
    assert rows[0][0] == "change 1"


def test_undo_twice_walks_back(cat, repo):
    f = cat.face(_unit(0), "new")
    g = cat.face(_unit(1), "new")
    original = repo.face_snapshot()
    _set_state(repo, f, "ignored")
    repo.record_undo("first", original)
    middle = repo.face_snapshot()
    _set_state(repo, g, "ignored")
    repo.record_undo("second", middle)
    assert repo.undo_last()[0].summary == "second"
    assert repo.face_snapshot() == middle
    assert repo.undo_last()[0].summary == "first"
    assert repo.face_snapshot() == original
    with pytest.raises(ValueError, match="Nothing to undo."):
        repo.undo_last()


def test_last_undo_tolerates_missing_table(cat, repo):
    for t in UNDO_TABLES:
        repo.conn.execute(f"DROP TABLE {t}")
    repo.conn.commit()
    assert repo.last_undo() is None


def test_migrate_v7_catalog_adds_undo_tables(drive):
    with GalleryRepository.create(drive) as repo:
        db = repo.db_path
        for t in reversed(UNDO_TABLES):
            repo.conn.execute(f"DROP TABLE {t}")
        repo.conn.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
        repo.conn.commit()
    with GalleryRepository.open(db) as repo:
        names = {
            r[0]
            for r in repo.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert set(UNDO_TABLES) <= names
        assert repo.schema_version == 8
