import os
import shutil
from datetime import datetime

import pytest

from smart_gallery.db import GalleryRepository
from smart_gallery.mirror import hashing
from smart_gallery.mirror.peers import choose_mirror
from smart_gallery.mirror.types import BaselineRow, MirrorError
from smart_gallery.organize import organize as organize_mod
from smart_gallery.services import (
    MirrorChoices,
    apply_mirror_sync,
    init_drive,
    pair_galleries,
    plan_mirror_sync,
    purge_trash,
)
from smart_gallery.services import mirror as mirror_service


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


# -- apply -------------------------------------------------------------------


def _sync(e, d, choices=None):
    """Plan and apply one sync; return (report, preview)."""
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        pv = plan_mirror_sync(w, m)
        rep = apply_mirror_sync(w, m, pv, choices or MirrorChoices())
    return rep, pv


def _plan(e, d):
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        return plan_mirror_sync(w, m)


def _files(root):
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and ".smart_gallery" not in p.parts
    }


def _trash(root, name):
    return sorted((root / ".smart_gallery" / "trash").glob(f"*/{name}"))


def _setup_pair(e, d, *rels):
    for rel in rels:
        _mirror_copy(e, d, rel, rel)
    w, m = _paired(e, d)
    w.close()
    m.close()
    _sync(e, d)


CONFIRM = MirrorChoices(confirm_large_delete=True)


def test_apply_copies_new_overwrites_and_trashes(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "keep.txt", "edit.txt", "gone.txt")
    _put(e, "sub/new.txt", "new")
    os.utime(e / "sub/new.txt", ns=(2_000_000_000, 2_000_000_000))
    _put(e, "edit.txt", "edited on E")
    (e / "gone.txt").unlink()
    before = _files(e)
    rep, pv = _sync(e, d, CONFIRM)
    assert pv.plan.copy_new == ["sub/new.txt"]
    assert (rep.copied, rep.overwritten, rep.trashed) == (1, 1, 1)
    assert rep.failures == []
    assert (d / "sub/new.txt").read_text() == "new"
    assert (d / "sub/new.txt").stat().st_mtime_ns == 2_000_000_000
    assert (d / "edit.txt").read_text() == "edited on E"
    assert [p.read_text() for p in _trash(d, "edit.txt")] == ["edit.txt"]
    assert [p.read_text() for p in _trash(d, "gone.txt")] == ["gone.txt"]
    assert not (d / "gone.txt").exists()
    with GalleryRepository.open(d) as m:
        rows = m.mirror_rows()
    assert "gone.txt" not in rows
    assert rows["sub/new.txt"].mtime_ns == 2_000_000_000
    assert _files(e) == before
    assert _plan(e, d).plan.is_empty


def test_second_sync_is_a_noop_and_trash_names_do_not_collide(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "a.txt", "b.txt")
    (e / "a.txt").unlink()
    _sync(e, d, CONFIRM)
    assert not (d / "a.txt").exists()
    _mirror_copy(e, d, "a.txt", "again")
    _sync(e, d, CONFIRM)
    (e / "a.txt").unlink()
    rep, _ = _sync(e, d, CONFIRM)
    assert rep.trashed == 1
    trashed = sorted((d / ".smart_gallery" / "trash").rglob("a*.txt"))
    assert sorted(p.read_text() for p in trashed) == ["a.txt", "again"]
    assert _plan(e, d).plan.is_empty


def test_interrupted_copy_leaves_no_partial_file_and_resumes(two_drives, monkeypatch):
    e, d = two_drives
    for rel in ("a.txt", "b.txt", "c.txt"):
        _put(e, rel, rel * 8)
    w, m = _paired(e, d)
    w.close()
    m.close()
    monkeypatch.setattr(organize_mod, "COPY_CHUNK", 4)
    real = mirror_service.copy_atomic
    calls = []

    def flaky_check(*_a, **_k):
        if len(calls) == 2:
            raise OSError("drive unplugged")

    def counting(src, dest):
        calls.append(dest)
        return real(src, dest)

    monkeypatch.setattr(organize_mod.progress, "check_cancelled", flaky_check)
    monkeypatch.setattr(mirror_service, "copy_atomic", counting)
    rep, _ = _sync(e, d)
    assert len(rep.failures) == 1 and "b.txt" in rep.failures[0]
    assert rep.copied == 2
    assert not (d / "b.txt").exists()
    assert not [p for p in d.rglob("*") if p.name.endswith(".sg-partial")]
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        assert set(w.baseline(m.get_meta("db_uuid"))) == {"a.txt", "c.txt"}
    monkeypatch.undo()
    rep, _ = _sync(e, d)
    assert rep.failures == [] and rep.copied == 1
    assert (d / "b.txt").read_text() == "b.txt" * 8
    assert _plan(e, d).plan.is_empty


def test_cancel_propagates_and_keeps_finished_files(two_drives, monkeypatch):
    from smart_gallery.progress import Cancelled

    e, d = two_drives
    for rel in ("a.txt", "b.txt", "c.txt"):
        _put(e, rel, rel)
    w, m = _paired(e, d)
    w.close()
    m.close()
    real = mirror_service.copy_atomic
    calls = []

    def cancelling(src, dest):
        calls.append(dest)
        if len(calls) == 2:
            raise Cancelled()
        return real(src, dest)

    monkeypatch.setattr(mirror_service, "copy_atomic", cancelling)
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        pv = plan_mirror_sync(w, m)
        with pytest.raises(Cancelled):
            apply_mirror_sync(w, m, pv)
        assert set(w.baseline(m.get_meta("db_uuid"))) == {"a.txt"}
        assert "a.txt" in m.mirror_rows()
    assert len(calls) == 2


def test_large_delete_requires_confirmation(two_drives):
    e, d = two_drives
    _setup_pair(e, d, *[f"f{i:02}.txt" for i in range(20)])
    (e / "f00.txt").unlink()
    (e / "f01.txt").unlink()
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        pv = plan_mirror_sync(w, m)
        assert pv.large_delete
        with pytest.raises(MirrorError):
            apply_mirror_sync(w, m, pv)
        assert (d / "f00.txt").exists()
        rep = apply_mirror_sync(w, m, pv, CONFIRM)
    assert rep.trashed == 2
    assert not (d / "f00.txt").exists()


def test_insufficient_space_refuses(two_drives, monkeypatch):
    e, d = two_drives
    _put(e, "big.txt", "0123456789")
    w, m = _paired(e, d)
    w.close()
    m.close()
    monkeypatch.setattr(
        mirror_service.shutil,
        "disk_usage",
        lambda p: shutil._ntuple_diskusage(100, 95, 5),
    )
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        pv = plan_mirror_sync(w, m)
        with pytest.raises(MirrorError):
            apply_mirror_sync(w, m, pv)
    assert not (d / "big.txt").exists()


def test_keep_on_mirror_is_not_asked_again(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "a.txt")
    _put(d, "old/extra.txt", "only on D")
    rep, pv = _sync(e, d, MirrorChoices(only_on_mirror="keep-on-mirror"))
    assert pv.plan.only_on_mirror == ["old/extra.txt"]
    assert rep.kept_on_mirror == 1
    assert not (e / "old/extra.txt").exists()
    with GalleryRepository.open(e) as w:
        assert "old/extra.txt" in w.offloaded_relpaths()
        assert w.mirror_rows()["old/extra.txt"].size_bytes == len("only on D")
    pv = _plan(e, d)
    assert pv.plan.only_on_mirror == []
    assert pv.plan.is_empty


def test_later_keeps_asking(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "a.txt")
    _put(d, "extra.txt", "only on D")
    rep, _ = _sync(e, d)
    assert rep.skipped == 1
    assert _plan(e, d).plan.only_on_mirror == ["extra.txt"]
    assert not (e / "extra.txt").exists()


def test_copy_back_copies_to_working(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "a.txt")
    _put(d, "extra.txt", "only on D")
    rep, _ = _sync(e, d, MirrorChoices(only_on_mirror="copy-back"))
    assert rep.copied_back == 1
    assert (e / "extra.txt").read_text() == "only on D"
    assert (e / "extra.txt").stat().st_mtime_ns == (d / "extra.txt").stat().st_mtime_ns
    with GalleryRepository.open(e) as w:
        assert "extra.txt" in w.mirror_rows()
    assert _plan(e, d).plan.is_empty


def test_changed_on_mirror_take_working_trashes_mirror_copy(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "a.txt")
    _put(d, "a.txt", "edited on D")
    rep, pv = _sync(e, d, MirrorChoices(changed_on_mirror="take-working"))
    assert pv.plan.changed_on_mirror == ["a.txt"]
    assert rep.overwritten == 1
    assert (d / "a.txt").read_text() == "a.txt"
    assert [p.read_text() for p in _trash(d, "a.txt")] == ["edited on D"]
    assert _plan(e, d).plan.is_empty


def test_conflict_keep_mirror_moves_working_copy_to_its_trash(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "a.txt")
    _put(d, "a.txt", "edited on D")
    _put(e, "a.txt", "edited on E too")
    rep, pv = _sync(e, d, MirrorChoices(conflicts="keep-mirror"))
    assert pv.plan.conflicts == ["a.txt"]
    assert rep.copied_back == 1
    assert (e / "a.txt").read_text() == "edited on D"
    assert [p.read_text() for p in _trash(e, "a.txt")] == ["edited on E too"]
    assert (d / "a.txt").read_text() == "edited on D"
    assert _plan(e, d).plan.is_empty


def test_rename_on_working_moves_on_mirror_without_recopying(two_drives, monkeypatch):
    e, d = two_drives
    _setup_pair(e, d, "a.txt", "b.txt")
    os.renames(e / "a.txt", e / "sub" / "renamed.txt")
    calls = []
    monkeypatch.setattr(mirror_service, "copy_atomic", lambda *a: calls.append(a))
    rep, pv = _sync(e, d)
    assert pv.plan.moves == [("a.txt", "sub/renamed.txt")]
    assert calls == []
    assert rep.moved == 1
    assert (d / "sub/renamed.txt").read_text() == "a.txt"
    assert not (d / "a.txt").exists()
    with GalleryRepository.open(d) as m:
        rows = m.mirror_rows()
    assert "sub/renamed.txt" in rows and "a.txt" not in rows
    assert _plan(e, d).plan.is_empty


def test_purge_trash_removes_old_folders_only(two_drives):
    _, d = two_drives
    trash = d / ".smart_gallery" / "trash"
    for name in ("20260901-120000", "20261020-120000", "not-a-stamp"):
        _put(trash / name, "x.txt")
    with GalleryRepository.open(d) as m:
        removed = purge_trash(m, now=datetime(2026, 10, 27, 12, 0, 0))
    assert removed == 1
    assert sorted(p.name for p in trash.iterdir()) == [
        "20261020-120000",
        "not-a-stamp",
    ]


def test_face_data_arrives_on_the_mirror(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "a.txt")
    _put(e, "b.txt", "new")
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        pv = plan_mirror_sync(w, m)
        mid = w.conn.execute(
            "SELECT id FROM media_items WHERE relpath='b.txt'"
        ).fetchone()[0]
        w.conn.execute("INSERT INTO persons (id, name) VALUES (7, 'Ana')")
        w.conn.execute(
            "INSERT INTO faces (id, media_id, bbox_x, bbox_y, bbox_w, bbox_h, "
            "det_score, embedding, person_id, state) VALUES "
            "(42, ?, 1, 2, 30, 40, 0.9, x'0102', 7, 'user')",
            (mid,),
        )
        w.conn.commit()
        rep = apply_mirror_sync(w, m, pv)
        row = m.conn.execute(
            "SELECT f.person_id, m.relpath FROM faces f "
            "JOIN media_items m ON m.id=f.media_id WHERE f.id=42"
        ).fetchone()
    assert rep.faces.inserted == 1
    assert tuple(row) == (7, "b.txt")


def test_baseline_row_for_file_gone_from_both_is_dropped(two_drives):
    e, d = two_drives
    _setup_pair(e, d, "a.txt", "b.txt")
    (e / "a.txt").unlink()
    (d / "a.txt").unlink()
    _sync(e, d)
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        assert set(w.baseline(m.get_meta("db_uuid"))) == {"b.txt"}
    _put(d, "a.txt", "a different file on D")
    pv = _plan(e, d)
    assert pv.plan.only_on_mirror == ["a.txt"]
    assert pv.plan.trash == []
