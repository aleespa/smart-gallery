import shutil
from collections import namedtuple

import pytest

from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.types import MirrorError
from smart_gallery.organize.filters import FilterOptions
from smart_gallery.services import (
    apply_mirror_sync,
    bring_back,
    free_up_space,
    offload_rule_matches,
    pair_galleries,
    plan_mirror_sync,
)
from smart_gallery.services import mirror as mirror_service

OTHER = FilterOptions(filetypes=["other"])


def _put(root, rel, content="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _mirror_copy(e, d, rel, content="x"):
    src = _put(e, rel, content)
    (d / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, d / rel)


def _paired(e, d):
    w = GalleryRepository.open(e)
    m = GalleryRepository.open(d)
    pair_galleries(w, m)
    return w, m


def _catalog_image(repo, make_item, root, rel):
    """An image-typed row for a real file, matching its stat so sync keeps it."""
    st = (root / rel).stat()
    repo.upsert_many([make_item(rel, size_bytes=st.st_size, mtime_ns=st.st_mtime_ns)])


def _mark_scanned(repo, rel, version=FACE_DET_VERSION):
    repo.conn.execute(
        "INSERT INTO face_scan_state(media_id, n_faces, det_version) "
        "VALUES((SELECT id FROM media_items WHERE relpath=?), 0, ?)",
        (rel, version),
    )
    repo.conn.commit()


def _files(root):
    """Every file on a drive outside its catalog folder, with size and mtime."""
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and ".smart_gallery" not in p.parts
    }


def _relpaths(repo):
    return set(repo.query_relpaths())


def test_free_up_removes_files_from_working_and_marks_offloaded(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt", "aaa")
    _mirror_copy(e, d, "sub/b.txt", "bb")
    _put(e, "keep.dat", "k")  # not selected: stays
    w, m = _paired(e, d)
    with w, m:
        d_before = _files(d)
        report = free_up_space(w, m, FilterOptions(extensions=[".txt"]))
        uuid = m.get_meta("db_uuid")
        assert report.files == 2
        assert report.bytes == 5
        assert report.skipped == []
        assert not (e / "a.txt").exists() and not (e / "sub/b.txt").exists()
        assert (e / "keep.dat").exists()
        assert (d / "a.txt").read_text() == "aaa"
        assert (d / "sub/b.txt").read_text() == "bb"
        assert w.offloaded_on(uuid) == {"a.txt", "sub/b.txt"}
        assert {"a.txt", "sub/b.txt"} <= _relpaths(w)
        rules = w.get_pairing().offload_rules
        assert rules == [FilterOptions(extensions=[".txt"])]
        assert _files(d) == d_before
        assert m.offloaded_relpaths() == set()


def test_free_up_skips_file_without_verified_copy(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "same.txt", "same")
    _put(e, "differs.txt", "working")
    _put(d, "differs.txt", "mirror!")  # same size, different content
    _put(e, "missing.txt", "only on E")
    w, m = _paired(e, d)
    with w, m:
        report = free_up_space(w, m, OTHER)
        assert report.files == 1
        assert sorted(report.skipped) == [
            "differs.txt: no verified copy on mirror",
            "missing.txt: no verified copy on mirror",
        ]
        assert not (e / "same.txt").exists()
        assert (e / "differs.txt").read_text() == "working"
        assert (e / "missing.txt").exists()
        assert w.offloaded_relpaths() == {"same.txt"}


def test_free_up_skips_unscanned_images(two_drives, make_item):
    e, d = two_drives
    _mirror_copy(e, d, "scanned.jpg", "img1")
    _mirror_copy(e, d, "unscanned.jpg", "img2")
    _mirror_copy(e, d, "oldscan.jpg", "img3")
    w, m = _paired(e, d)
    with w, m:
        for rel in ("scanned.jpg", "unscanned.jpg", "oldscan.jpg"):
            _catalog_image(w, make_item, e, rel)
            _catalog_image(m, make_item, d, rel)
        _mark_scanned(w, "scanned.jpg")
        _mark_scanned(w, "oldscan.jpg", version="old-model")
        report = free_up_space(w, m, FilterOptions(filetypes=["image"]))
        assert report.files == 1
        assert sorted(report.skipped) == [
            "oldscan.jpg: faces not scanned",
            "unscanned.jpg: faces not scanned",
        ]
        assert not (e / "scanned.jpg").exists()
        assert (e / "unscanned.jpg").exists() and (e / "oldscan.jpg").exists()
        # The scan state was transferred to the mirror before E's copy went.
        n = m.conn.execute(
            "SELECT COUNT(*) FROM face_scan_state s JOIN media_items mi "
            "ON mi.id = s.media_id WHERE mi.relpath='scanned.jpg'"
        ).fetchone()[0]
        assert n == 1


def test_free_up_dry_run_changes_nothing(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt", "aaa")
    w, m = _paired(e, d)
    with w, m:
        report = free_up_space(w, m, OTHER, dry_run=True)
        assert report.files == 1 and report.bytes == 3
        assert (e / "a.txt").exists() and (d / "a.txt").exists()
        assert w.offloaded_relpaths() == set()
        assert w.get_pairing().offload_rules == []


def test_free_up_refuses_an_empty_selection(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt")
    w, m = _paired(e, d)
    with w, m:
        with pytest.raises(MirrorError, match="Choose what to free up"):
            free_up_space(w, m, FilterOptions())
        assert (e / "a.txt").exists()


def test_free_up_skips_a_file_that_changed_before_removal(two_drives, monkeypatch):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt", "aaa")
    w, m = _paired(e, d)
    real = mirror_service.sync_face_data

    def touch_then_sync(working, mirror, cb=None):
        (e / "a.txt").write_text("changed on E")
        return real(working, mirror, cb)

    monkeypatch.setattr(mirror_service, "sync_face_data", touch_then_sync)
    with w, m:
        report = free_up_space(w, m, OTHER)
        assert report.files == 0
        assert report.skipped == ["a.txt: changed on the working drive"]
        assert (e / "a.txt").read_text() == "changed on E"
        assert w.offloaded_relpaths() == set()


def test_free_up_skips_already_offloaded_files(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt")
    w, m = _paired(e, d)
    with w, m:
        assert free_up_space(w, m, OTHER).files == 1
        _put(e, "b.txt")
        shutil.copy2(e / "b.txt", d / "b.txt")
        report = free_up_space(w, m, OTHER)
        assert report.files == 1 and report.skipped == []
        assert w.get_pairing().offload_rules == [OTHER]  # not added twice


def test_sync_after_free_up_does_not_trash_on_mirror(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt", "aaa")
    _mirror_copy(e, d, "b.txt", "bbb")
    w, m = _paired(e, d)
    with w, m:
        pv = plan_mirror_sync(w, m)
        apply_mirror_sync(w, m, pv)
        free_up_space(w, m, FilterOptions(extensions=[".txt"]), folders=["a.txt"])
        pv = plan_mirror_sync(w, m)
        assert pv.plan.trash == []
        apply_mirror_sync(w, m, pv)
        assert (d / "a.txt").read_text() == "aaa"
        assert "a.txt" in _relpaths(m) and "a.txt" in _relpaths(w)
        assert w.offloaded_relpaths() == {"a.txt"}


def test_bring_back_restores_file_and_clears_flag(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt", "aaa")
    _mirror_copy(e, d, "b.txt", "bbb")
    w, m = _paired(e, d)
    with w, m:
        uuid = m.get_meta("db_uuid")
        pv = plan_mirror_sync(w, m)
        apply_mirror_sync(w, m, pv)
        free_up_space(w, m, OTHER)
        assert w.offloaded_relpaths() == {"a.txt", "b.txt"}
        report = bring_back(w, m, OTHER)
        assert report.files == 2 and report.bytes == 6 and report.skipped == []
        assert (e / "a.txt").read_text() == "aaa"
        d_mtime = (d / "a.txt").stat().st_mtime_ns
        assert (e / "a.txt").stat().st_mtime_ns == d_mtime
        assert w.offloaded_relpaths() == set()
        assert w.get_pairing().offload_rules == []
        base = w.baseline(uuid)["a.txt"]
        assert base.working_mtime_ns == d_mtime == base.mirror_mtime_ns
        row = w.mirror_rows()["a.txt"]
        assert (row.size_bytes, row.mtime_ns) == (3, d_mtime)
        pv = plan_mirror_sync(w, m)
        assert pv.plan.is_empty or not (pv.plan.copy_new or pv.plan.trash)


def test_bring_back_checks_free_space(two_drives, monkeypatch):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt", "aaa")
    w, m = _paired(e, d)
    Usage = namedtuple("Usage", "total used free")
    with w, m:
        free_up_space(w, m, OTHER)
        monkeypatch.setattr(
            mirror_service.shutil, "disk_usage", lambda p: Usage(10, 9, 1)
        )
        with pytest.raises(MirrorError, match="free"):
            bring_back(w, m, OTHER)
        assert not (e / "a.txt").exists()
        assert w.offloaded_relpaths() == {"a.txt"}


def test_free_up_by_folder_prefix_is_case_insensitive_and_adds_no_rule(two_drives):
    e, d = two_drives
    _mirror_copy(e, d, "Trips/2019/a.txt")
    _mirror_copy(e, d, "Trips/2019b/b.txt")
    _mirror_copy(e, d, "Other/c.txt")
    w, m = _paired(e, d)
    with w, m:
        report = free_up_space(w, m, FilterOptions(), folders=["trips\\2019/"])
        assert report.files == 1
        assert not (e / "Trips/2019/a.txt").exists()
        assert (e / "Trips/2019b/b.txt").exists()
        assert (e / "Other/c.txt").exists()
        assert w.get_pairing().offload_rules == []
        report = bring_back(w, m, FilterOptions(), folders=["TRIPS"])
        assert report.files == 1
        assert (e / "Trips/2019/a.txt").exists()


def test_offload_rule_matches_any_rule(make_item):
    item = make_item("a.mp4", media_type="video", ext=".mp4")
    assert offload_rule_matches([OTHER, FilterOptions(filetypes=["video"])], item)
    assert not offload_rule_matches([OTHER], item)
    assert not offload_rule_matches([], item)


def test_free_up_case_differing_pair_transfers_faces_before_delete(
    two_drives, make_item
):
    e, d = two_drives
    _put(e, "Photos/IMG_1.JPG", "img")
    (d / "photos").mkdir()
    shutil.copy2(e / "Photos/IMG_1.JPG", d / "photos/img_1.jpg")
    w, m = _paired(e, d)
    with w, m:
        _catalog_image(w, make_item, e, "Photos/IMG_1.JPG")
        _catalog_image(m, make_item, d, "photos/img_1.jpg")
        _mark_scanned(w, "Photos/IMG_1.JPG")
        w.conn.execute(
            "INSERT INTO faces (id, media_id, bbox_x, bbox_y, bbox_w, bbox_h, "
            "det_score, embedding, state) VALUES "
            "(1, (SELECT id FROM media_items WHERE relpath=?), 1, 2, 3, 4, 0.9, "
            "?, 'new')",
            ("Photos/IMG_1.JPG", b"\xaa"),
        )
        w.conn.commit()

        report = free_up_space(w, m, FilterOptions(filetypes=["image"]))

        assert report.files == 1 and report.skipped == []
        assert not (e / "Photos/IMG_1.JPG").exists()
        assert (d / "photos/img_1.jpg").exists()
        d_id = m.conn.execute(
            "SELECT id FROM media_items WHERE relpath='photos/img_1.jpg'"
        ).fetchone()[0]
        assert (
            m.conn.execute("SELECT media_id FROM faces WHERE id=1").fetchone()[0]
            == d_id
        )
        assert (
            m.conn.execute(
                "SELECT det_version FROM face_scan_state WHERE media_id=?", (d_id,)
            ).fetchone()[0]
            == FACE_DET_VERSION
        )


def test_free_up_skips_image_whose_scan_state_did_not_reach_mirror(
    two_drives, make_item, monkeypatch
):
    e, d = two_drives
    _mirror_copy(e, d, "a.jpg", "img")
    w, m = _paired(e, d)
    monkeypatch.setattr(mirror_service, "sync_face_data", lambda *a, **k: None)
    with w, m:
        _catalog_image(w, make_item, e, "a.jpg")
        _catalog_image(m, make_item, d, "a.jpg")
        _mark_scanned(w, "a.jpg")
        report = free_up_space(w, m, FilterOptions(filetypes=["image"]))
        assert report.files == 0
        assert report.skipped == ["a.jpg: faces not scanned"]
        assert (e / "a.jpg").exists()


_KILL_DURING_FREE_UP = """
import os, sys
from pathlib import Path
from smart_gallery.db import GalleryRepository
from smart_gallery.organize.filters import FilterOptions
from smart_gallery.services import free_up_space
import smart_gallery.services.mirror as ms

real, n = os.remove, [0]

def remove(path):
    real(path)
    n[0] += 1
    if n[0] == 3:
        os._exit(9)  # like a Force stop, a power loss or E unplugged

ms.os.remove = remove
e, d = Path(sys.argv[1]), Path(sys.argv[2])
with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
    free_up_space(w, m, FilterOptions(extensions=[".txt"]))
"""


def test_killed_free_up_keeps_every_removed_file_offloaded(two_drives):
    import subprocess
    import sys

    e, d = two_drives
    for i in range(5):
        _mirror_copy(e, d, f"p{i}.txt", f"photo {i}")
    w, m = _paired(e, d)
    with w, m:
        apply_mirror_sync(w, m, plan_mirror_sync(w, m))
    rc = subprocess.call([sys.executable, "-c", _KILL_DURING_FREE_UP, str(e), str(d)])
    assert rc == 9
    gone = {f"p{i}.txt" for i in range(5) if not (e / f"p{i}.txt").exists()}
    assert len(gone) == 3
    with GalleryRepository.open(e) as w, GalleryRepository.open(d) as m:
        assert gone <= w.offloaded_relpaths()
        pv = plan_mirror_sync(w, m)
        assert pv.plan.trash == []
        # A flag on a file still on E is harmless: the sync cleared it.
        assert w.offloaded_relpaths() == gone
    for i in range(5):
        assert (d / f"p{i}.txt").read_text() == f"photo {i}"


def test_free_up_unflags_a_file_it_could_not_delete(two_drives, monkeypatch):
    e, d = two_drives
    _mirror_copy(e, d, "a.txt", "aaa")
    w, m = _paired(e, d)

    def locked(path):
        raise PermissionError("in use")

    monkeypatch.setattr(mirror_service.os, "remove", locked)
    with w, m:
        report = free_up_space(w, m, OTHER)
        assert report.files == 0
        assert report.skipped == ["a.txt: in use"]
        assert (e / "a.txt").exists()
        assert w.offloaded_relpaths() == set()
