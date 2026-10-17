from pathlib import Path

from smart_gallery.db import GalleryRepository
from smart_gallery.organize import FilterOptions, Options
from smart_gallery.services import export_media


def _seed(repo, drive, make_item, rel, **kw):
    path = drive / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"img")
    repo.upsert_many([make_item(rel, **kw)])


def test_export_filtered_reorganized(repo, drive, tmp_path, make_item):
    _seed(repo, drive, make_item, "Photos/2026/06/a.jpg", camera="Canon EOS R6")
    _seed(repo, drive, make_item, "Photos/2026/06/b.jpg", camera="Sony A7")

    dest = tmp_path / "out"
    report = export_media(
        repo,
        dest,
        filters=FilterOptions(cameras=["Canon EOS R6"]),
        options=Options(by_media_type=True, structure=["Year", "Month"]),
    )
    assert report.matched == 1
    assert report.copied == 1
    assert (dest / "Photos" / "2026" / "06" / "a.jpg").exists()
    assert not (dest / "Photos" / "2026" / "06" / "b.jpg").exists()
    assert report.manifest_path.exists()


def test_export_mirror_preserves_structure(repo, drive, tmp_path, make_item):
    rel = "Photos/Canon/2026/x.jpg"
    _seed(repo, drive, make_item, rel)
    dest = tmp_path / "mirror"
    report = export_media(repo, dest, mirror=True, manifest=False)
    assert report.copied == 1
    assert (dest / rel).exists()


def test_export_portable_db(repo, drive, tmp_path, make_item):
    _seed(repo, drive, make_item, "Photos/a.jpg", date_taken="2026-06-01")
    dest = tmp_path / "portable"
    report = export_media(
        repo, dest, options=Options(structure=["Year", "Month"]), portable_db=True
    )
    assert report.portable_db_path.exists()

    with GalleryRepository.open(dest, read_only=True) as portable:
        assert portable.counts()["total"] == 1
        rels = portable.query_relpaths()
        # relpath is now relative to the export destination, reorganized by date
        assert rels == ["Photos/2026/06/a.jpg"]


def test_export_cancel_writes_manifest_for_placed(repo, drive, tmp_path, make_item):
    import pytest

    from smart_gallery.progress import COPYING, Cancelled

    for i in range(5):
        _seed(repo, drive, make_item, f"Photos/f{i}.jpg")
    dest = tmp_path / "out"

    events = []

    def cancel_on_third_copy(event):
        if event.step == COPYING:
            events.append(event)
            if len(events) == 3:
                raise Cancelled()

    with pytest.raises(Cancelled):
        export_media(
            repo,
            dest,
            mirror=True,
            portable_db=True,
            progress_callback=cancel_on_third_copy,
        )
    copied = sorted(p.name for p in (dest / "Photos").iterdir())
    assert len(copied) == 3
    assert not list(dest.rglob("*.sg-partial"))
    manifest = (dest / "_smart_gallery_manifest.csv").read_text(encoding="utf-8")
    assert (
        sorted(
            line.split(",")[0].rsplit("/", 1)[-1] for line in manifest.splitlines()[1:]
        )
        == copied
    )
    with GalleryRepository.open(dest, read_only=True) as portable:
        assert sorted(Path(r).name for r in portable.query_relpaths()) == copied


def test_resumed_export_describes_the_whole_export(repo, drive, tmp_path, make_item):
    import pytest

    from smart_gallery.progress import COPYING, Cancelled

    for i in range(5):
        _seed(repo, drive, make_item, f"Photos/f{i}.jpg")
    dest = tmp_path / "out"

    def cancel_on_third_copy(event):
        if event.step == COPYING and event.done == 3:
            raise Cancelled()

    with pytest.raises(Cancelled):
        export_media(
            repo,
            dest,
            mirror=True,
            on_exist="skip",
            portable_db=True,
            progress_callback=cancel_on_third_copy,
        )
    report = export_media(repo, dest, mirror=True, on_exist="skip", portable_db=True)
    assert (report.copied, report.skipped) == (2, 3)

    every = [f"Photos/f{i}.jpg" for i in range(5)]
    manifest = (dest / "_smart_gallery_manifest.csv").read_text(encoding="utf-8")
    assert sorted(line.split(",")[0] for line in manifest.splitlines()[1:]) == every
    with GalleryRepository.open(dest, read_only=True) as portable:
        assert sorted(portable.query_relpaths()) == every
