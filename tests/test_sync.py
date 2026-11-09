from smart_gallery.db import GalleryRepository
from smart_gallery.services import init_drive, sync_drive


def test_sync_add_remove_change(drive, drive_file):
    drive_file(drive, "a.txt")
    drive_file(drive, "sub/b.txt")
    init_drive(drive)

    with GalleryRepository.open(drive) as repo:
        assert repo.counts()["total"] == 2

        drive_file(drive, "c.txt")  # add
        (drive / "a.txt").unlink()  # remove
        drive_file(drive, "sub/b.txt", content=b"y" * 999)  # change (size differs)

        report = sync_drive(repo)
        assert report.added == 1
        assert report.deleted == 1
        assert report.updated == 1
        assert set(repo.query_relpaths()) == {"sub/b.txt", "c.txt"}


def test_sync_idempotent(drive, drive_file):
    drive_file(drive, "a.txt")
    init_drive(drive)
    with GalleryRepository.open(drive) as repo:
        report = sync_drive(repo)
        assert report.changed == 0
        assert report.unchanged == 1


def test_sync_only_analyzes_changed(drive, drive_file, monkeypatch):
    drive_file(drive, "a.txt")
    drive_file(drive, "b.txt")
    init_drive(drive)

    import smart_gallery.services.sync as syncmod

    seen = []
    original = syncmod.analyze_paths

    def spy(paths, **kwargs):
        paths = list(paths)
        seen.append(paths)
        return original(paths, **kwargs)

    monkeypatch.setattr(syncmod, "analyze_paths", spy)

    with GalleryRepository.open(drive) as repo:
        drive_file(drive, "c.txt")  # only this is new
        sync_drive(repo)

    assert len(seen) == 1
    assert {p.name for p in seen[0]} == {"c.txt"}


def test_sync_keeps_offloaded_rows(drive, drive_file):
    from smart_gallery.mirror.types import Peer

    drive_file(drive, "a.txt")
    init_drive(drive)
    (drive / "a.txt").unlink()
    with GalleryRepository.open(drive) as repo:
        assert sync_drive(repo, dry_run=True).deleted == 1
        repo.add_peer("working", Peer("d1", "D:/g.db"))
        repo.add_offloaded(["A.txt"], "d1")
        assert sync_drive(repo).deleted == 0
        assert repo.counts()["total"] == 1


def test_sync_clears_offloaded_flag_when_file_returns(drive, drive_file):
    from smart_gallery.mirror.types import Peer

    drive_file(drive, "a.txt")
    init_drive(drive)
    with GalleryRepository.open(drive) as repo:
        repo.add_peer("working", Peer("d1", "D:/g.db"))
        repo.add_offloaded(["a.txt"], "d1")
        sync_drive(repo, dry_run=True)
        assert repo.offloaded_relpaths() == {"a.txt"}
        sync_drive(repo)
        assert repo.offloaded_relpaths() == set()


def test_sync_keeps_offloaded_flag_when_different_file_returns(drive, drive_file):
    from smart_gallery.mirror.types import Peer

    drive_file(drive, "a.txt", content=b"x" * 16)
    init_drive(drive)
    (drive / "a.txt").unlink()
    with GalleryRepository.open(drive) as repo:
        repo.add_peer("working", Peer("d1", "D:/g.db"))
        repo.add_offloaded(["a.txt"], "d1")
        drive_file(drive, "a.txt", content=b"someone else's file")
        report = sync_drive(repo)
        assert report.updated == 0
        assert repo.offloaded_relpaths() == {"a.txt"}
        assert repo.index_for_sync()["a.txt"][0] == 16


def test_sync_keeps_offloaded_flag_when_same_size_different_file_returns(
    drive, drive_file
):
    import os

    from smart_gallery.mirror.types import Peer

    drive_file(drive, "a.txt", content=b"x" * 16)
    init_drive(drive)
    (drive / "a.txt").unlink()
    with GalleryRepository.open(drive) as repo:
        before = repo.index_for_sync()["a.txt"]
        repo.add_peer("working", Peer("d1", "D:/g.db"))
        repo.add_offloaded(["a.txt"], "d1")
        path = drive_file(drive, "a.txt", content=b"y" * 16)  # same size
        os.utime(path, ns=(before[1] + 5_000_000_000, before[1] + 5_000_000_000))
        report = sync_drive(repo)
        assert report.updated == 0
        assert repo.offloaded_relpaths() == {"a.txt"}
        assert repo.index_for_sync()["a.txt"] == before


def test_sync_keeps_offloaded_flag_when_different_file_returns_in_other_case(
    drive, drive_file
):
    from smart_gallery.mirror.types import Peer

    drive_file(drive, "a.txt", content=b"x" * 16)
    init_drive(drive)
    (drive / "a.txt").unlink()
    with GalleryRepository.open(drive) as repo:
        repo.add_peer("working", Peer("d1", "D:/g.db"))
        repo.add_offloaded(["a.txt"], "d1")
        drive_file(drive, "A.txt", content=b"someone else's file")
        report = sync_drive(repo)
        assert report.added == 0 and report.updated == 0
        assert repo.offloaded_relpaths() == {"a.txt"}
        assert repo.index_for_sync() == {
            "a.txt": (16, repo.index_for_sync()["a.txt"][1])
        }


def test_sync_clears_offloaded_flag_when_a_hand_copied_original_returns(
    drive, drive_file
):
    """Copying the freed-up file back keeps its size and mtime: it is the original."""
    import shutil

    from smart_gallery.mirror.types import Peer

    drive_file(drive, "a.txt", content=b"x" * 16)
    init_drive(drive)
    keep = drive.parent / "kept-a.txt"
    shutil.copy2(drive / "a.txt", keep)
    (drive / "a.txt").unlink()
    with GalleryRepository.open(drive) as repo:
        repo.add_peer("working", Peer("d1", "D:/g.db"))
        repo.add_offloaded(["a.txt"], "d1")
        shutil.copy2(keep, drive / "a.txt")
        sync_drive(repo)
        assert repo.offloaded_relpaths() == set()


# ── orientation backfill ────────────────────────────────────────────────────
def _orient_setup(repo, drive, drive_file, make_item, names, **kw):
    items = []
    for name in names:
        p = drive_file(drive, name)
        st = p.stat()
        items.append(
            make_item(
                name, size_bytes=st.st_size, mtime_ns=st.st_mtime_ns, **kw.get(name, {})
            )
        )
    repo.upsert_many(items)


def _row(repo, rel):
    return dict(
        repo.conn.execute(
            "SELECT * FROM media_items WHERE relpath=?", (rel,)
        ).fetchone()
    )


def test_sync_backfills_orientation_only(
    repo, drive, drive_file, make_item, monkeypatch
):
    import smart_gallery.services.sync as syncmod
    from smart_gallery.mirror.types import Peer

    _orient_setup(
        repo,
        drive,
        drive_file,
        make_item,
        ["a.jpg", "b.jpg", "c.jpg"],
        **{"b.jpg": {"orientation": 1}},
    )
    repo.conn.execute("UPDATE media_items SET updated_at='2020-01-01 00:00:00'")
    repo.conn.commit()
    repo.add_peer("working", Peer("d1", "D:/g.db"))
    repo.add_offloaded(["c.jpg"], "d1")
    (drive / "c.jpg").unlink()
    a_id = _row(repo, "a.jpg")["id"]
    repo.conn.execute(
        "INSERT INTO faces(media_id, bbox_x, bbox_y, bbox_w, bbox_h, embedding) "
        "VALUES(?,0,0,1,1,x'00')",
        (a_id,),
    )
    repo.conn.commit()
    before = _row(repo, "a.jpg")
    seen = []

    def fake(items, progress_callback=None, on_batch=None):
        seen.append([p.name for p, _ in items])
        found = {p: 6 for p, _ in items if p.name == "a.jpg"}
        on_batch(found)
        return found

    monkeypatch.setattr(syncmod, "read_orientations", fake)
    report = sync_drive(repo)
    after = _row(repo, "a.jpg")
    assert seen == [["a.jpg"]]
    assert report.rotation_read == 1
    assert after["orientation"] == 6
    assert {k: v for k, v in after.items() if k != "orientation"} == {
        k: v for k, v in before.items() if k != "orientation"
    }
    assert repo.conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 1


def test_sync_backfill_skips_rows_being_reanalysed(
    repo, drive, drive_file, make_item, monkeypatch
):
    import smart_gallery.services.sync as syncmod

    _orient_setup(repo, drive, drive_file, make_item, ["a.jpg"])
    repo.conn.execute("UPDATE media_items SET mtime_ns = mtime_ns + 5")
    repo.conn.commit()
    analysed, read = [], []
    monkeypatch.setattr(
        syncmod, "analyze_paths", lambda paths, **k: analysed.append(list(paths)) or []
    )
    monkeypatch.setattr(
        syncmod, "read_orientations", lambda items, **k: read.append(items) or {}
    )
    report = sync_drive(repo)
    assert [p.name for p in analysed[0]] == ["a.jpg"]
    assert read == [] or read == [[]]
    assert report.rotation_read == 0


def test_sync_backfill_leaves_null_when_unreadable(
    repo, drive, drive_file, make_item, monkeypatch
):
    import smart_gallery.services.sync as syncmod

    _orient_setup(repo, drive, drive_file, make_item, ["a.jpg"])
    calls = []
    monkeypatch.setattr(
        syncmod, "read_orientations", lambda items, **k: calls.append(items) or {}
    )
    sync_drive(repo)
    sync_drive(repo)
    assert _row(repo, "a.jpg")["orientation"] is None
    assert len(calls) == 2


def test_sync_dry_run_counts_rotation_reads(
    repo, drive, drive_file, make_item, monkeypatch
):
    import smart_gallery.services.sync as syncmod

    _orient_setup(repo, drive, drive_file, make_item, ["a.jpg", "b.jpg"])

    def boom(*a, **k):
        raise AssertionError("reader must not run on a dry run")

    monkeypatch.setattr(syncmod, "read_orientations", boom)
    report = sync_drive(repo, dry_run=True)
    assert report.rotation_read == 2
    assert _row(repo, "a.jpg")["orientation"] is None


def test_backfill_keeps_finished_batches_on_cancel(
    repo, drive, drive_file, make_item, monkeypatch
):
    import pytest

    import smart_gallery.services.sync as syncmod
    from smart_gallery.progress import Cancelled

    _orient_setup(repo, drive, drive_file, make_item, ["a.jpg", "b.jpg"])

    def fake(items, progress_callback=None, on_batch=None):
        on_batch({items[0][0]: 6})
        raise Cancelled()

    monkeypatch.setattr(syncmod, "read_orientations", fake)
    with pytest.raises(Cancelled):
        sync_drive(repo)
    got = sorted(_row(repo, r)["orientation"] or 0 for r in ("a.jpg", "b.jpg"))
    assert got == [0, 6]
