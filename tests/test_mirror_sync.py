import os
import shutil

import pytest

from smart_gallery.db import GalleryRepository
from smart_gallery.mirror import hashing
from smart_gallery.mirror.peers import choose_mirror
from smart_gallery.mirror.types import BaselineRow, MirrorError
from smart_gallery.services import init_drive, pair_galleries, plan_mirror_sync


def _put(root, rel, content="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _mirror_copy(e, d, rel, content="x"):
    shutil.copy2(_put(e, rel, content), d / rel)


def _paired(e, d):
    w = GalleryRepository.open(e)
    m = GalleryRepository.open(d)
    pair_galleries(w, m)
    return w, m


def test_pair_then_plan_with_identical_drives_is_empty(two_drives):
    e, d = two_drives
    for i in range(3):
        _mirror_copy(e, d, f"a{i}.txt", str(i))
    w, m = _paired(e, d)
    with w, m:
        pv = plan_mirror_sync(w, m)
    assert pv.plan.is_empty or not (pv.plan.copy_new or pv.plan.trash)
    assert pv.working_files == pv.mirror_files == 3


def test_pair_refuses_same_catalog_twice(two_drives):
    e, _ = two_drives
    with GalleryRepository.open(e) as a, GalleryRepository.open(e) as b:
        with pytest.raises(MirrorError):
            pair_galleries(a, b)


def test_pair_second_mirror_is_allowed_and_baselines_stay_separate(
    two_drives, tmp_path
):
    e, d = two_drives
    d2 = tmp_path / "D2"
    d2.mkdir()
    init_drive(d2)
    _mirror_copy(e, d, "a.txt")
    shutil.copy2(e / "a.txt", d2 / "a.txt")
    w, m = _paired(e, d)
    m2 = GalleryRepository.open(d2)
    with w, m, m2:
        pair_galleries(w, m2)
        assert len(w.get_pairing().peers) == 2
        u2 = m2.get_meta("db_uuid")
        w.update_baseline(u2, [BaselineRow("a.txt", 1, 1, 1)], [])
        plan_mirror_sync(w, m)
        assert set(w.baseline(u2)) == {"a.txt"}
        assert w.baseline(u2)["a.txt"].working_mtime_ns == 1
        with pytest.raises(MirrorError):
            choose_mirror(w.get_pairing())
        assert (
            choose_mirror(w.get_pairing(), d2 / ".smart_gallery" / "gallery.db").uuid
            == u2
        )


def test_choose_mirror_requires_override_with_two_mirrors(two_drives, tmp_path):
    e, d = two_drives
    d2 = tmp_path / "D2"
    d2.mkdir()
    init_drive(d2)
    w, m = _paired(e, d)
    m2 = GalleryRepository.open(d2)
    with w, m, m2:
        pair_galleries(w, m2)
        with pytest.raises(MirrorError, match="several mirrors"):
            choose_mirror(w.get_pairing())


def test_first_plan_hashes_only_mismatched_files(two_drives, monkeypatch):
    e, d = two_drives
    for i in range(10):
        _mirror_copy(e, d, f"f{i}.txt", str(i))
    for i in (3, 7):
        os.utime(d / f"f{i}.txt", ns=(1_000_000_000, 1_000_000_000))
    seen = []
    real = hashing.hash_file
    monkeypatch.setattr(
        hashing, "hash_file", lambda p: (seen.append(p.name), real(p))[1]
    )
    w, m = _paired(e, d)
    with w, m:
        pv = plan_mirror_sync(w, m)
    assert sorted(seen) == ["f3.txt", "f3.txt", "f7.txt", "f7.txt"]
    assert len(pv.plan.adopt) == 10


def test_new_file_on_working_shows_in_copy_new(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt")
    _put(e, "new.txt")
    w, m = _paired(e, d)
    with w, m:
        pv = plan_mirror_sync(w, m)
    assert pv.plan.copy_new == ["new.txt"]
    assert pv.copy_bytes == 1


def test_delete_on_working_outside_the_app_is_detected(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt")
    _mirror_copy(e, d, "b.txt")
    w, m = _paired(e, d)
    with w, m:
        plan_mirror_sync(w, m)
        u = m.get_meta("db_uuid")
        rows = []
        for r in ("a.txt", "b.txt"):
            w_row = w.mirror_rows()[r]
            m_row = m.mirror_rows()[r]
            rows.append(
                BaselineRow(r, w_row.size_bytes, w_row.mtime_ns, m_row.mtime_ns)
            )
        w.update_baseline(u, rows, [])
        (e / "a.txt").unlink()
        pv = plan_mirror_sync(w, m)
    assert pv.plan.trash == ["a.txt"]


def test_unmounted_working_root_refuses(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt")
    w, m = _paired(e, d)
    with w, m:
        plan_mirror_sync(w, m)
        before = m.mirror_rows()
        shutil.rmtree(e, ignore_errors=True)  # Windows keeps the open catalog
        with pytest.raises(MirrorError):
            plan_mirror_sync(w, m)
        w.drive_root = e / "gone"  # root entirely absent
        with pytest.raises(MirrorError, match="not available"):
            plan_mirror_sync(w, m)
        assert m.mirror_rows() == before
    assert (d / "a.txt").exists()


def test_empty_working_root_refuses(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt")
    w, m = _paired(e, d)
    with w, m:
        plan_mirror_sync(w, m)
        (e / "a.txt").unlink()
        with pytest.raises(MirrorError):
            plan_mirror_sync(w, m)
    assert (d / "a.txt").exists()


def test_pairing_uuid_mismatch_refuses(two_drives, tmp_path):
    e, d = two_drives
    other = tmp_path / "O"
    other.mkdir()
    init_drive(other)
    _mirror_copy(e, d, "a.txt")
    w, m = _paired(e, d)
    o = GalleryRepository.open(other)
    with w, m, o:
        with pytest.raises(MirrorError):
            plan_mirror_sync(w, o)


def _hundred(e, d):
    for i in range(100):
        _mirror_copy(e, d, f"f{i:03}.txt", str(i))
    w, m = _paired(e, d)
    with w, m:
        plan_mirror_sync(w, m)
        u = m.get_meta("db_uuid")
        wr, mr = w.mirror_rows(), m.mirror_rows()
        w.update_baseline(
            u,
            [
                BaselineRow(r.relpath, r.size_bytes, r.mtime_ns, mr[k].mtime_ns)
                for k, r in wr.items()
            ],
            [],
        )
    return w, m


@pytest.mark.parametrize("n,flag", [(6, True), (4, False)])
def test_large_delete_flag(two_drives, n, flag):
    e, d = two_drives
    _hundred(e, d)
    for i in range(n):
        (e / f"f{i:03}.txt").unlink()
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        pv = plan_mirror_sync(w, m)
    assert len(pv.plan.trash) == n
    assert pv.large_delete is flag


def test_plan_does_not_modify_mirror_files(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt")
    _put(e, "new.txt")
    _put(d, "extra.txt")
    snap = lambda: {  # noqa: E731
        p.name: (p.stat().st_size, p.stat().st_mtime_ns)
        for p in d.iterdir()
        if p.is_file()
    }
    before = snap()
    w, m = _paired(e, d)
    with w, m:
        plan_mirror_sync(w, m)
    assert snap() == before


def test_first_plan_reads_catalog_rows_once_per_side(two_drives, monkeypatch):
    e, d = two_drives
    for i in range(8):
        _mirror_copy(e, d, f"f{i}.txt", str(i))
        os.utime(d / f"f{i}.txt", ns=(1_000_000_000, 1_000_000_000))
    w, m = _paired(e, d)
    calls = []
    real = GalleryRepository.mirror_rows

    def spy(self):
        calls.append(self.drive_root)
        return real(self)

    monkeypatch.setattr(GalleryRepository, "mirror_rows", spy)
    with w, m:
        pv = plan_mirror_sync(w, m)
    assert len(pv.plan.adopt) == 8
    assert len(calls) <= 2
