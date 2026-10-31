from smart_gallery.mirror.types import Peer
from smart_gallery.storage.pairs import FileRef
from smart_gallery.storage.raw_jpeg import (
    RawCandidate,
    estimate_jpeg_bytes,
    in_scope,
    load_raw_candidates,
    reconcile_picks,
    replacement_entries,
)


def _add(repo, make_item, *rows):
    repo.upsert_many([make_item(r, **kw) for r, kw in rows])


def _ref(relpath, size=100, mtime=1):
    return FileRef(1, relpath, ".cr3", size, mtime, "image")


def cand(relpath="A/IMG_1.CR3", size=100, mtime=1, sidecar=None):
    folder = relpath.rpartition("/")[0]
    return RawCandidate(folder, "IMG_1", _ref(relpath, size, mtime), sidecar, None)


def test_lone_cr3_is_a_candidate(repo, make_item):
    _add(repo, make_item, ("A/IMG_1.CR3", {}))
    (c,) = load_raw_candidates(repo)
    assert c.key == "a/img_1.cr3" and c.edited is False and c.folder == "A"


def test_cr3_with_jpeg_or_heic_is_not(repo, make_item):
    _add(
        repo,
        make_item,
        ("A/IMG_2.CR3", {}),
        ("A/IMG_2.jpg", {}),
        ("A/IMG_3.CR3", {}),
        ("A/img_3.HEIC", {}),
    )
    assert load_raw_candidates(repo) == []


def test_cr2_is_not(repo, make_item):
    _add(repo, make_item, ("A/IMG_1.CR2", {}))
    assert load_raw_candidates(repo) == []


def test_offloaded_cr3_is_not(repo, make_item):
    _add(repo, make_item, ("A/IMG_1.CR3", {}), ("A/IMG_5.CR3", {}))
    repo.add_peer("working", Peer("uuid", "D:/g.db"))
    repo.add_offloaded(["A/IMG_1.CR3"], "uuid")
    assert [c.raw.relpath for c in load_raw_candidates(repo)] == ["A/IMG_5.CR3"]


def test_sidecar_marks_edited(repo, make_item):
    _add(repo, make_item, ("A/IMG_4.CR3", {}), ("A/IMG_4.xmp", {}))
    (c,) = load_raw_candidates(repo)
    assert c.edited and c.sidecar is not None
    assert c.size_bytes == c.raw.size_bytes + c.sidecar.size_bytes


def test_order_by_time_then_relpath(repo, make_item):
    _add(
        repo,
        make_item,
        ("A/b.CR3", {"taken_ts": 5}),
        ("A/a.CR3", {"taken_ts": 9}),
        ("A/z.CR3", {"taken_ts": None}),
        ("A/c.CR3", {"taken_ts": 5}),
    )
    got = [c.raw.relpath for c in load_raw_candidates(repo)]
    assert got == ["A/b.CR3", "A/c.CR3", "A/a.CR3", "A/z.CR3"]


def test_scope_and_entries():
    a, b = cand("A/x/2.CR3"), cand("B/1.CR3")
    assert in_scope(a, None) and in_scope(a, "a") and not in_scope(b, "A")
    assert [f.relpath for f in replacement_entries([b, a])] == ["A/x/2.CR3", "B/1.CR3"]


def test_reconcile_drops_changed_and_gone():
    k, g, s = cand("A/k.CR3"), cand("A/g.CR3"), cand("A/s.CR3")
    before = {c.key: c for c in (k, g, s)}
    now = [k, cand("A/s.CR3", size=999)]
    assert reconcile_picks(now, {k.key, g.key, s.key}, before) == {k.key}
    assert reconcile_picks(now, set(), before) == set()


def test_estimate():
    assert estimate_jpeg_bytes([cand(size=20_000_000)]) == 1_000_000
