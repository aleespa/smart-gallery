from smart_gallery.mirror.plan import build_plan
from smart_gallery.mirror.types import BaselineRow, MirrorRow, key


class Hashes:
    def __init__(self, table=None):
        self.table = table or {}
        self.calls = []

    def __call__(self, k):
        self.calls.append(k)
        return self.table.get(k)


def row(rel, size=10, mtime=1):
    return MirrorRow(rel, size, mtime, None)


def base(rel, size=10, w=1, m=1):
    return BaselineRow(rel, size, w, m)


def plan(working=(), mirror=(), baseline=(), offloaded=(), held=(), hw=None, hm=None):
    hw = hw or Hashes()
    hm = hm or Hashes()
    p = build_plan(
        {key(r.relpath): r for r in working},
        {key(r.relpath): r for r in mirror},
        {key(r.relpath): r for r in baseline},
        {key(x) for x in offloaded},
        {key(x) for x in held},
        hw,
        hm,
    )
    return p, hw, hm


def test_new_on_working_is_copied():
    p, _, _ = plan(working=[row("a.jpg")])
    assert p.copy_new == ["a.jpg"] and not p.is_empty


def test_deleted_on_working_is_trashed():
    p, _, _ = plan(mirror=[row("a.jpg")], baseline=[base("a.jpg")])
    assert p.trash == ["a.jpg"] and p.delete_count == 1


def test_deleted_on_mirror_is_copied_again():
    p, _, _ = plan(working=[row("a.jpg")], baseline=[base("a.jpg")])
    assert p.copy_new == ["a.jpg"]


def test_unchanged_in_baseline_is_a_noop_without_hashing():
    p, hw, hm = plan([row("a.jpg")], [row("a.jpg")], [base("a.jpg")])
    assert p.is_empty and not hw.calls and not hm.calls


def test_same_size_edit_on_working_overwrites():
    hw, hm = Hashes({"a.jpg": "x"}), Hashes({"a.jpg": "y"})
    p, _, _ = plan(
        [row("a.jpg", mtime=5)], [row("a.jpg")], [base("a.jpg")], hw=hw, hm=hm
    )
    assert p.overwrite == ["a.jpg"] and not p.adopt


def test_size_edit_on_working_overwrites_without_hashing():
    p, hw, _ = plan([row("a.jpg", size=11)], [row("a.jpg")], [base("a.jpg")])
    assert p.overwrite == ["a.jpg"] and not hw.calls


def test_touched_but_identical_is_adopted():
    hw, hm = Hashes({"a.jpg": "x"}), Hashes({"a.jpg": "x"})
    p, _, _ = plan(
        [row("a.jpg", mtime=5)], [row("a.jpg", mtime=2)], [base("a.jpg")], hw=hw, hm=hm
    )
    assert p.adopt == [BaselineRow("a.jpg", 10, 5, 2)] and p.is_empty is False
    assert not p.overwrite


def test_unknown_hash_is_not_equal():
    p, _, _ = plan([row("a.jpg", mtime=5)], [row("a.jpg")], [base("a.jpg")])
    assert p.overwrite == ["a.jpg"]


def test_edit_on_mirror_only_is_asked():
    hw, hm = Hashes({"a.jpg": "x"}), Hashes({"a.jpg": "y"})
    p, _, _ = plan(
        [row("a.jpg")], [row("a.jpg", mtime=9)], [base("a.jpg")], hw=hw, hm=hm
    )
    assert p.changed_on_mirror == ["a.jpg"]


def test_edit_on_both_is_conflict():
    p, _, _ = plan([row("a.jpg", size=11)], [row("a.jpg", size=12)], [base("a.jpg")])
    assert p.conflicts == ["a.jpg"]


def test_first_sight_equal_size_and_mtime_adopts_without_hashing():
    p, hw, hm = plan([row("a.jpg")], [row("a.jpg")])
    assert p.adopt == [BaselineRow("a.jpg", 10, 1, 1)] and not hw.calls and not hm.calls


def test_first_sight_mtime_differs_hash_equal_adopts():
    hw, hm = Hashes({"a.jpg": "x"}), Hashes({"a.jpg": "x"})
    p, _, _ = plan([row("a.jpg")], [row("a.jpg", mtime=3)], hw=hw, hm=hm)
    assert p.adopt == [BaselineRow("a.jpg", 10, 1, 3)]


def test_first_sight_different_is_conflict():
    hw, hm = Hashes({"a.jpg": "x"}), Hashes({"a.jpg": "y"})
    p, _, _ = plan([row("a.jpg")], [row("a.jpg", mtime=3)], hw=hw, hm=hm)
    assert p.conflicts == ["a.jpg"]
    p, hw, _ = plan([row("a.jpg")], [row("a.jpg", size=99)])
    assert p.conflicts == ["a.jpg"] and not hw.calls


def test_only_on_mirror_is_listed_not_deleted():
    p, _, _ = plan(mirror=[row("a.jpg")])
    assert p.only_on_mirror == ["a.jpg"] and not p.trash and not p.is_empty


def test_offloaded_rows_are_not_treated_as_present():
    off = [row("a.jpg"), row("b.jpg"), row("c.jpg")]
    mirror = [row("b.jpg"), row("c.jpg", mtime=7)]
    baseline = [base("a.jpg"), base("b.jpg"), base("c.jpg")]
    p, hw, _ = plan(
        off,
        mirror,
        baseline,
        offloaded=["a.jpg", "b.jpg", "c.jpg"],
        held=["a.jpg", "b.jpg", "c.jpg"],
    )
    assert p.offloaded_missing == ["a.jpg"]
    assert p.offloaded_changed == ["c.jpg"]
    assert not p.copy_new and not p.trash and p.is_empty and not hw.calls


def test_offloaded_on_another_mirror_is_ignored():
    p, _, _ = plan([row("a.jpg")], [], [], offloaded=["a.jpg"], held=[])
    assert p.is_empty and not p.offloaded_missing and not p.copy_new


def test_offloaded_present_on_mirror_without_baseline_is_adopted_if_size_matches():
    p, _, _ = plan([row("a.jpg")], [row("a.jpg", mtime=4)], offloaded=["a.jpg"])
    assert p.adopt == [BaselineRow("a.jpg", 10, 1, 4)]
    p, _, _ = plan([row("a.jpg")], [row("a.jpg", size=3)], offloaded=["a.jpg"])
    assert p.is_empty


def test_case_only_difference_is_same_file():
    p, _, _ = plan([row("Photos/A.JPG")], [row("photos/a.jpg")], [base("photos/a.jpg")])
    assert p.is_empty
    p, _, _ = plan(
        mirror=[row("photos/a.jpg")],
        baseline=[base("photos/a.jpg")],
        working=[row("Photos/A.JPG")],
    )
    assert not p.copy_new and not p.trash


def test_rename_is_a_move():
    hw, hm = Hashes({"new.jpg": "h"}), Hashes({"old.jpg": "h"})
    p, _, _ = plan([row("new.jpg")], [row("old.jpg")], [base("old.jpg")], hw=hw, hm=hm)
    assert p.moves == [("old.jpg", "new.jpg")]
    assert not p.trash and not p.copy_new


def test_same_size_different_content_is_not_a_move():
    hw, hm = Hashes({"new.jpg": "h"}), Hashes({"old.jpg": "z"})
    p, _, _ = plan([row("new.jpg")], [row("old.jpg")], [base("old.jpg")], hw=hw, hm=hm)
    assert not p.moves and p.trash == ["old.jpg"] and p.copy_new == ["new.jpg"]


def test_different_size_is_not_hashed_for_moves():
    p, hw, hm = plan([row("new.jpg", size=5)], [row("old.jpg")], [base("old.jpg")])
    assert not p.moves and not hw.calls and not hm.calls


def test_deleted_on_working_but_changed_on_mirror_is_asked():
    p, _, _ = plan(mirror=[row("a.jpg", mtime=7)], baseline=[base("a.jpg")])
    assert p.changed_on_mirror == ["a.jpg"] and p.trash == []
    p, _, _ = plan(mirror=[row("a.jpg", size=12)], baseline=[base("a.jpg")])
    assert p.changed_on_mirror == ["a.jpg"] and p.trash == []


def test_different_file_at_an_offloaded_path_is_listed():
    p = build_plan(
        {key("a.jpg"): row("a.jpg")},
        {key("a.jpg"): row("a.jpg")},
        {key("a.jpg"): base("a.jpg")},
        {key("a.jpg")},
        {key("a.jpg")},
        Hashes(),
        Hashes(),
        offloaded_conflict={key("a.jpg")},
    )
    assert p.offloaded_conflict == ["a.jpg"]
    assert not (p.trash or p.copy_new or p.overwrite or p.conflicts)
