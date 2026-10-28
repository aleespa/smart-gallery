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


def _fake_convert(monkeypatch, fail=()):
    """Stand in for ExifTool: write ``<stem>.jpg`` beside each RAW, as
    ``convert_cr3`` does, except for RAWs whose name is in ``fail``."""
    import smart_gallery.services.export as exportmod

    calls = []

    def convert(sources):
        sources = list(sources)
        calls.append(sources)
        for raw in sources:
            if raw.name not in fail:
                raw.with_suffix(".jpg").write_bytes(b"jpeg of " + raw.read_bytes())

    monkeypatch.setattr(exportmod, "convert_cr3", convert)
    return calls


def test_split_for_jpeg_export():
    from smart_gallery.services.export import split_for_jpeg_export

    as_is, raws, left_out = split_for_jpeg_export(
        [
            "a/IMG_1.CR3",
            "a/img_1.JPG",  # twin of IMG_1.CR3: the RAW is left out
            "a/IMG_2.cr2",
            "b/IMG_1.CR3",  # same name, other folder: not a twin
            "a/clip.MP4",
            "a/pic.png",
        ]
    )
    assert as_is == ["a/img_1.JPG", "a/clip.MP4"]
    assert raws == ["a/IMG_2.cr2", "b/IMG_1.CR3"]
    assert left_out == ["a/IMG_1.CR3", "a/pic.png"]


def test_jpeg_only_export_converts_raws_keeps_videos_leaves_out_others(
    repo, drive, tmp_path, make_item, monkeypatch
):
    calls = _fake_convert(monkeypatch)
    _seed(repo, drive, make_item, "Photos/IMG_1.CR3")
    _seed(repo, drive, make_item, "Photos/IMG_2.JPG")
    _seed(repo, drive, make_item, "Photos/IMG_2.CR3")  # twin of IMG_2.JPG
    _seed(repo, drive, make_item, "Photos/pic.png")
    _seed(repo, drive, make_item, "Videos/clip.mp4", media_type="video")
    dest = tmp_path / "out"

    report = export_media(
        repo,
        dest,
        options=Options(structure=["Year", "Month"]),
        jpeg_only=True,
        portable_db=True,
    )

    folder = dest / "Photos" / "2026" / "06"
    assert sorted(p.name for p in folder.iterdir()) == ["IMG_1.jpg", "IMG_2.JPG"]
    assert (folder / "IMG_1.jpg").read_bytes() == b"jpeg of img"
    assert (report.matched, report.copied, report.converted) == (5, 3, 1)
    assert report.left_out == 2  # the twin RAW and the PNG
    assert len(calls) == 1
    assert (dest / "Videos" / "2026" / "06" / "clip.mp4").exists()
    assert not (dest / ".smart_gallery_raw_staging").exists()
    assert not (drive / "Photos" / "IMG_1.jpg").exists()  # source untouched
    with GalleryRepository.open(dest, read_only=True) as portable:
        assert sorted(portable.query_relpaths()) == [
            "Photos/2026/06/IMG_1.jpg",
            "Photos/2026/06/IMG_2.JPG",
            "Videos/2026/06/clip.mp4",
        ]


def test_jpeg_only_export_renames_on_name_clash(
    repo, drive, tmp_path, make_item, monkeypatch
):
    _fake_convert(monkeypatch)
    _seed(repo, drive, make_item, "a/IMG_1.CR3")
    _seed(repo, drive, make_item, "b/IMG_1.CR3")
    _seed(repo, drive, make_item, "c/IMG_1.jpg")
    dest = tmp_path / "out"

    report = export_media(
        repo, dest, options=Options(structure=["Year"]), jpeg_only=True
    )

    folder = dest / "Photos" / "2026"
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["IMG_1.jpg", "IMG_1_1.jpg", "IMG_1_2.jpg"]
    assert (folder / "IMG_1.jpg").read_bytes() == b"img"  # the JPEG keeps its name
    assert report.converted == 2


def test_jpeg_only_export_skips_existing_and_reports_failures(
    repo, drive, tmp_path, make_item, monkeypatch
):
    _fake_convert(monkeypatch, fail={"IMG_2.CR3"})
    _seed(repo, drive, make_item, "IMG_1.CR3")
    _seed(repo, drive, make_item, "IMG_2.CR3")
    _seed(repo, drive, make_item, "IMG_3.CR3")
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "IMG_3.jpg").write_bytes(b"done before")

    report = export_media(
        repo, dest, mirror=True, on_exist="skip", jpeg_only=True, manifest=False
    )

    assert (dest / "IMG_1.jpg").exists()
    assert not (dest / "IMG_2.jpg").exists()
    assert (dest / "IMG_3.jpg").read_bytes() == b"done before"
    assert (report.copied, report.skipped, report.converted) == (1, 1, 1)
    assert len(report.excluded) == 1 and "IMG_2.CR3" in report.excluded[0]
    assert sorted(p.name for p in dest.iterdir()) == ["IMG_1.jpg", "IMG_3.jpg"]


def test_jpeg_only_dry_run_converts_nothing(
    repo, drive, tmp_path, make_item, monkeypatch
):
    calls = _fake_convert(monkeypatch)
    _seed(repo, drive, make_item, "IMG_1.CR3")
    dest = tmp_path / "out"

    report = export_media(repo, dest, mirror=True, jpeg_only=True, dry_run=True)

    assert (report.copied, report.converted) == (1, 1)
    assert calls == [] and not dest.exists()


def test_jpeg_only_export_batches_raws(repo, drive, tmp_path, make_item, monkeypatch):
    import smart_gallery.services.export as exportmod

    monkeypatch.setattr(exportmod, "RAW_BATCH", 2)
    calls = _fake_convert(monkeypatch)
    for i in range(5):
        _seed(repo, drive, make_item, f"IMG_{i}.CR3")

    report = export_media(repo, tmp_path / "out", mirror=True, jpeg_only=True)

    assert [len(c) for c in calls] == [2, 2, 1]
    assert report.converted == 5


def test_export_cli_passes_jpeg_only(repo, drive, tmp_path, make_item, monkeypatch):
    from smart_gallery import cli

    _fake_convert(monkeypatch)
    _seed(repo, drive, make_item, "IMG_1.CR3")
    _seed(repo, drive, make_item, "clip.mp4", media_type="video")
    dest = tmp_path / "out"
    repo.close()

    cli.main(
        [
            "export",
            "--from",
            str(drive),
            "--to",
            str(dest),
            "--mirror",
            "--jpeg-only",
            "--no-manifest",
        ]
    )

    assert sorted(p.name for p in dest.iterdir()) == ["IMG_1.jpg", "clip.mp4"]
