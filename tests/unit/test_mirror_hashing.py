import os

from smart_gallery.mirror import hashing
from smart_gallery.mirror.hashing import ensure_hashes, hash_file
from smart_gallery.mirror.types import key


def test_hash_file_is_content_based(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    a.write_bytes(b"hello world")
    b.write_bytes(b"hello world")
    c.write_bytes(b"hello worle")
    os.utime(b, ns=(1, 1))
    assert hash_file(a) == hash_file(b)
    assert hash_file(a) != hash_file(c)
    assert len(hash_file(a)) == 40


def _add(repo, make_item, drive_file, drive, relpath, content):
    drive_file(drive, relpath, content=content)
    repo.upsert_many([make_item(relpath)])


def test_ensure_hashes_fills_only_missing(
    repo, drive, drive_file, make_item, monkeypatch
):
    _add(repo, make_item, drive_file, drive, "A/one.jpg", b"one")
    _add(repo, make_item, drive_file, drive, "A/two.jpg", b"two")
    repo.set_hashes([("A/one.jpg", "sentinel")])
    calls = []
    real = hashing.hash_file
    monkeypatch.setattr(hashing, "hash_file", lambda p: calls.append(p) or real(p))
    got = ensure_hashes(repo, ["A/one.jpg", "A/two.jpg"])
    assert len(calls) == 1
    assert got[key("A/one.jpg")] == "sentinel"
    assert got[key("A/two.jpg")] == real(drive / "A/two.jpg")
    assert repo.mirror_rows()[key("A/two.jpg")].content_hash == got[key("A/two.jpg")]


def test_ensure_hashes_skips_unreadable(repo, drive, drive_file, make_item):
    _add(repo, make_item, drive_file, drive, "A/ok.jpg", b"ok")
    _add(repo, make_item, drive_file, drive, "A/gone.jpg", b"gone")
    (drive / "A/gone.jpg").unlink()
    got = ensure_hashes(repo, ["A/ok.jpg", "A/gone.jpg"])
    assert set(got) == {key("A/ok.jpg")}


def test_ensure_hashes_with_rows_does_not_reread_catalog(
    repo, drive, drive_file, make_item, monkeypatch
):
    _add(repo, make_item, drive_file, drive, "A/one.jpg", b"one")
    rows = repo.mirror_rows()

    def boom():
        raise AssertionError("mirror_rows called")

    monkeypatch.setattr(repo, "mirror_rows", boom)
    out = ensure_hashes(repo, ["A/one.jpg"], rows=rows)
    assert set(out) == {key("A/one.jpg")}


def test_ensure_hashes_force_rereads_stored_hashes(repo, drive, drive_file, make_item):
    _add(repo, make_item, drive_file, drive, "a.jpg", b"real")
    repo.set_hashes([("a.jpg", "stale")])
    assert ensure_hashes(repo, ["a.jpg"])[key("a.jpg")] == "stale"
    fresh = ensure_hashes(repo, ["a.jpg"], force=True)[key("a.jpg")]
    assert fresh == hash_file(drive / "a.jpg")
    assert repo.mirror_rows()[key("a.jpg")].content_hash == fresh


def test_ensure_hashes_commits_in_batches_and_counts_on(
    repo, drive, drive_file, make_item, monkeypatch
):
    for i in range(5):
        _add(repo, make_item, drive_file, drive, f"f{i}.jpg", bytes([i]))
    monkeypatch.setattr(hashing, "HASH_BATCH", 2)
    commits = []
    real = repo.set_hashes
    monkeypatch.setattr(
        repo, "set_hashes", lambda pairs: (commits.append(len(pairs)), real(pairs))
    )
    events = []
    rels = [f"f{i}.jpg" for i in range(5)]
    ensure_hashes(repo, rels, events.append, done_before=3, total=10)
    assert commits == [2, 2, 1]
    assert [(ev.done, ev.total) for ev in events] == [(n, 10) for n in range(4, 9)]
