import time

from smart_gallery.mirror.types import Peer
from smart_gallery.storage.bursts import (
    Shot,
    burst_removals,
    can_mark,
    group_bursts,
    load_shots,
    reconcile_marks,
)
from smart_gallery.storage.pairs import FileRef


def _add(repo, make_item, *rows):
    items = []
    for relpath, ts, *rest in rows:
        camera = rest[0] if rest else "R8"
        kw = {"media_type": "video"} if relpath.lower().endswith(".mov") else {}
        items.append(make_item(relpath, taken_ts=ts, camera=camera, **kw))
    repo.upsert_many(items)


def _bursts(repo, **kw):
    return group_bursts(load_shots(repo), **kw)


def _ts(burst):
    return [s.taken_ts for s in burst.shots]


def test_burst_needs_gap_within_limit(repo, make_item):
    _add(repo, make_item, ("a/1.jpg", 100), ("a/2.jpg", 102), ("a/3.jpg", 105))
    assert [_ts(b) for b in _bursts(repo)] == [[100, 102]]


def test_span_cap_splits_chains(repo, make_item):
    _add(repo, make_item, *[(f"a/{t}.jpg", t) for t in range(0, 16, 2)])
    bursts = sorted(_bursts(repo), key=lambda b: b.shots[0].taken_ts)
    assert [(_ts(b)[0], _ts(b)[-1]) for b in bursts] == [(0, 10), (12, 14)]


def test_same_folder_and_camera_only(repo, make_item):
    _add(repo, make_item, ("a/1.jpg", 5), ("b/2.jpg", 5))
    assert _bursts(repo) == []
    _add(repo, make_item, ("c/1.jpg", 5, "R8"), ("c/2.jpg", 5, "T6"))
    assert _bursts(repo) == []


def test_cameras_interleaved_in_time_stay_apart(repo, make_item):
    rows = []
    for t in range(3):
        rows += [(f"a/r{t}.jpg", t, "R8"), (f"a/t{t}.jpg", t, "T6")]
    _add(repo, make_item, *rows)
    bursts = _bursts(repo)
    assert [len(b.shots) for b in bursts] == [3, 3]
    assert all(len({s.camera for s in b.shots}) == 1 for b in bursts)


def test_null_camera_is_its_own_value(repo, make_item):
    _add(repo, make_item, ("a/1.jpg", 0, None), ("a/2.jpg", 1, None))
    assert len(_bursts(repo)) == 1
    _add(repo, make_item, ("b/1.jpg", 0, None), ("b/2.jpg", 1, "R8"))
    assert [b.folder for b in _bursts(repo)] == ["a"]


def test_same_second_ordered_by_relpath(repo, make_item):
    _add(repo, make_item, ("a/IMG_2.JPG", 50), ("a/IMG_1.JPG", 50))
    (burst,) = _bursts(repo)
    assert [s.stem for s in burst.shots] == ["IMG_1", "IMG_2"]


def test_raw_and_jpeg_are_one_shot(repo, make_item):
    _add(
        repo,
        make_item,
        ("a/IMG_1.CR3", 10),
        ("a/IMG_1.JPG", 10),
        ("a/IMG_2.CR3", 11),
    )
    repo.upsert_many([make_item("a/IMG_1.xmp", media_type="other")])
    (burst,) = _bursts(repo)
    assert len(burst.shots) == 2
    shot1 = burst.shots[0]
    assert shot1.formats == "CR3+JPG"
    assert shot1.sidecar.relpath == "a/IMG_1.xmp"
    assert [f.relpath for f in burst_removals([shot1])] == [
        "a/IMG_1.CR3",
        "a/IMG_1.JPG",
        "a/IMG_1.xmp",
    ]


def test_other_formats_join_the_shot(repo, make_item):
    _add(repo, make_item, ("a/IMG_1.JPG", 5), ("a/IMG_1.HEIC", 5))
    assert len(load_shots(repo)) == 1
    assert _bursts(repo) == []


def test_non_candidates_never_join(repo, make_item):
    _add(
        repo,
        make_item,
        ("a/IMG_1.CR3", 5),
        ("a/IMG_1.MOV", 5),
        ("a/IMG_1.JPG", None),
    )
    (shot,) = load_shots(repo)
    assert [f.relpath for f in shot.files] == ["a/IMG_1.CR3"]


def test_shot_takes_earliest_ts_and_first_camera(repo, make_item):
    _add(repo, make_item, ("a/IMG_1.CR3", 10, None), ("a/IMG_1.JPG", 11, "R8"))
    (shot,) = load_shots(repo)
    assert shot.taken_ts == 10
    assert shot.camera == "R8"


def test_edited_and_offloaded_are_excluded(repo, make_item):
    _add(
        repo,
        make_item,
        ("a/IMG_1-edited.jpg", 1),
        ("a/IMG_2-EDITED.JPG", 1),
        ("a/IMG_3.jpg", 1),
        ("a/IMG_4.jpg", 1),
    )
    repo.add_peer("working", Peer("peer", "D:/g.db"))
    repo.add_offloaded(["a/IMG_4.jpg"], "peer")
    assert [s.stem for s in load_shots(repo)] == ["IMG_3"]


def test_load_shots_without_offloaded_table(repo, make_item):
    _add(repo, make_item, ("a/1.jpg", 1))
    repo.conn.execute("DROP TABLE mirror_offloaded")
    assert len(load_shots(repo)) == 1


def test_order_largest_first(repo, make_item):
    def rows(folder, stamps, size):
        return [
            make_item(f"{folder}/{t}.jpg", taken_ts=t, camera="R8", size_bytes=size)
            for t in stamps
        ]

    repo.upsert_many(
        rows("a", (100, 101), 10)
        + rows("b", (0, 1, 2), 10)
        + rows("c", (50, 51), 500)
        + rows("d", (10, 11), 500)
    )
    assert [b.folder for b in _bursts(repo)] == ["b", "d", "c", "a"]


def test_can_mark_refuses_last_kept(repo, make_item):
    _add(repo, make_item, ("a/1.jpg", 0), ("a/2.jpg", 1), ("a/3.jpg", 2))
    burst = _bursts(repo)[0]
    k = [s.key for s in burst.shots]
    marks = {k[0], k[1]}
    assert not can_mark(burst, marks, k[2])
    assert can_mark(burst, marks, k[0])
    assert can_mark(burst, {k[0]}, k[1])


def test_reconcile_drops_changed_and_orphaned_marks(repo, make_item):
    _add(repo, make_item, ("a/1.jpg", 0), ("a/2.jpg", 1), ("a/3.jpg", 2))
    _add(repo, make_item, ("b/1.jpg", 0), ("b/2.jpg", 1))
    _add(repo, make_item, ("c/1.jpg", 0), ("c/2.jpg", 1), ("c/9.jpg", 100))
    old = {s.key: s for s in load_shots(repo)}
    marks = {("a", "1"), ("a", "2"), ("b", "1"), ("b", "2"), ("c", "9"), ("c", "1")}
    repo.conn.execute("UPDATE media_items SET mtime_ns = 999 WHERE relpath = 'a/1.jpg'")
    kept, dropped = reconcile_marks(old, marks, _bursts(repo))
    # a/1 changed; b fully marked; c/9 is in no burst.
    assert kept == {("a", "2"), ("c", "1")}
    assert dropped == len(marks) - len(kept)


def _fake_shot(i):
    ref = FileRef(i, f"a/{i}.jpg", ".jpg", 10, 1, "image")
    return Shot("a", str(i), i // 3, "R8", (ref,), None, None, None, None)


def test_grouping_100k_rows_is_fast():
    shots = [_fake_shot(i) for i in range(100_000)]
    start = time.perf_counter()
    group_bursts(shots)
    assert time.perf_counter() - start < 3.0


def test_sidecar_stays_while_a_same_stem_file_is_outside_the_shot(repo, make_item):
    _add(
        repo,
        make_item,
        ("a/IMG_1.CR3", None),  # no capture time: not in the shot
        ("a/IMG_1.JPG", 10),
        ("a/IMG_2.JPG", 11),
    )
    repo.upsert_many([make_item("a/IMG_1.xmp", media_type="other")])
    shot = next(s for s in load_shots(repo) if s.stem == "IMG_1")
    assert [f.relpath for f in shot.files] == ["a/IMG_1.JPG"]
    assert shot.sidecar is None
    assert [f.relpath for f in burst_removals([shot])] == ["a/IMG_1.JPG"]


def test_sidecar_stays_while_an_offloaded_file_shares_its_stem(repo, make_item):
    _add(repo, make_item, ("a/IMG_1.CR3", 10), ("a/IMG_1.JPG", 10))
    repo.upsert_many([make_item("a/IMG_1.xmp", media_type="other")])
    repo.add_peer("working", Peer("peer", "D:/g.db"))
    repo.add_offloaded(["a/IMG_1.CR3"], "peer")
    (shot,) = load_shots(repo)
    assert [f.relpath for f in shot.files] == ["a/IMG_1.JPG"]
    assert shot.sidecar is None
    assert [f.relpath for f in burst_removals([shot])] == ["a/IMG_1.JPG"]


def test_sidecar_stays_while_another_kind_of_file_shares_its_stem(repo, make_item):
    _add(repo, make_item, ("a/IMG_1.JPG", 10), ("a/IMG_1.MOV", 10))
    repo.upsert_many([make_item("a/IMG_1.XMP", media_type="other")])
    (shot,) = load_shots(repo)
    assert shot.sidecar is None
