from datetime import date

import pytest

from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.types import BaselineRow, Peer
from smart_gallery.organize.filters import FilterOptions

TABLES = ("mirror_baseline", "mirror_offloaded", "mirror_peers")


def _downgrade(repo):
    for t in TABLES:
        repo.conn.execute(f"DROP TABLE {t}")
    repo.set_meta("schema_version", "6")


def test_migrate_v6_catalog_adds_mirror_tables(drive):
    with GalleryRepository.create(drive) as r:
        _downgrade(r)
    for _ in range(2):
        with GalleryRepository.open(drive) as r:
            assert r.schema_version == 7
            names = {
                x["name"] for x in r.conn.execute("SELECT name FROM sqlite_master")
            }
            assert set(TABLES) <= names


def test_readers_tolerate_unmigrated_read_only_catalog(drive):
    with GalleryRepository.create(drive) as r:
        _downgrade(r)
    with GalleryRepository.open(drive, read_only=True) as r:
        assert r.schema_version == 6
        assert r.offloaded_relpaths() == set()
        assert r.offloaded_on("u1") == set()
        assert r.baseline("u1") == {}
        assert r.get_pairing() is None
        assert r.face_source_uuid() is None


def test_pairing_round_trip(repo):
    d1 = Peer("d1", "D:/.smart_gallery/gallery.db")
    f1 = Peer("f1", "F:/.smart_gallery/gallery.db")
    repo.add_peer("working", d1)
    repo.add_peer("working", f1)
    rules = [
        FilterOptions(filetypes=["video"]),
        FilterOptions(date_range=(date(2020, 1, 1), date(2020, 12, 31))),
    ]
    repo.set_offload_rules(rules)
    p = repo.get_pairing()
    assert p.role == "working"
    assert p.peers == [d1, f1]
    assert p.offload_rules == rules


def test_mirror_accepts_only_one_working_peer(repo):
    repo.add_peer("mirror", Peer("e1", "E:/x.db"))
    with pytest.raises(ValueError):
        repo.add_peer("mirror", Peer("e2", "E:/y.db"))
    with pytest.raises(ValueError):
        repo.add_peer("working", Peer("e3", "E:/z.db"))


def test_remove_peer_drops_its_baseline_and_holders(repo):
    for u in ("d1", "f1"):
        repo.add_peer("working", Peer(u, f"{u}.db"))
        repo.update_baseline(u, [BaselineRow("a.jpg", 1, 2, 3)], [])
        repo.add_offloaded(["a.jpg"], u)
    repo.remove_peer("d1")
    assert repo.baseline("d1") == {}
    assert len(repo.baseline("f1")) == 1
    assert repo.offloaded_on("d1") == set()
    assert repo.offloaded_on("f1") == {"a.jpg"}
    repo.remove_peer("f1")
    assert repo.get_pairing() is None


def test_baselines_are_per_peer(repo):
    for u in ("d1", "f1"):
        repo.add_peer("working", Peer(u, f"{u}.db"))
    repo.update_baseline("d1", [BaselineRow("a.jpg", 1, 2, 3)], [])
    assert "a.jpg" in repo.baseline("d1")
    assert repo.baseline("f1") == {}


def test_baseline_and_offloaded_are_case_insensitive(repo):
    for u in ("d1", "f1"):
        repo.add_peer("working", Peer(u, f"{u}.db"))
    repo.update_baseline("d1", [BaselineRow("IMG_1.JPG", 1, 2, 3)], [])
    assert "img_1.jpg" in repo.baseline("d1")
    repo.update_baseline("d1", [BaselineRow("img_1.jpg", 9, 2, 3)], [])
    assert repo.baseline("d1")["img_1.jpg"].size_bytes == 9
    repo.update_baseline("d1", [], ["Img_1.Jpg"])
    assert repo.baseline("d1") == {}
    repo.add_offloaded(["A.JPG"], "d1")
    repo.add_offloaded(["b.jpg"], "f1")
    assert {r.lower() for r in repo.offloaded_on("d1")} == {"a.jpg"}
    assert {r.lower() for r in repo.offloaded_relpaths()} == {"a.jpg", "b.jpg"}
    repo.remove_offloaded(["a.jpg"])
    assert {r.lower() for r in repo.offloaded_relpaths()} == {"b.jpg"}


def test_set_hashes_survives_until_row_changes(repo, make_item):
    repo.upsert_many([make_item("a.jpg")])
    repo.set_hashes([("a.jpg", "abc")])
    assert repo.mirror_rows()["a.jpg"].content_hash == "abc"
    repo.upsert_many([make_item("a.jpg")])
    assert repo.mirror_rows()["a.jpg"].content_hash is None
