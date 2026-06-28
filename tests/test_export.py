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
        repo, dest,
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
