from datetime import date, datetime
from pathlib import Path

import pytest

from smart_gallery.cli import build_filter_query, parse_args
from smart_gallery.gui.exports.model import (
    ExportSettings,
    build_export_job,
    default_folder_name,
    example_path,
    export_summary,
    filter_argv,
)
from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.organize.filters import FilterOptions

NOW = datetime(2026, 10, 28, 9, 5)


def _gallery(tmp_path, name="E", connected=True):
    db = tmp_path / name / ".smart_gallery" / "gallery.db"
    if connected:
        db.parent.mkdir(parents=True)
        db.touch()
    return Gallery(name, str(db), name)


def _parsed(argv):
    return parse_args(["export", "--from", "E:/", "--to", "out", *argv])


@pytest.mark.parametrize(
    "filters",
    [
        FilterOptions(),
        FilterOptions(filetypes=["image", "video"], extensions=[".jpg", ".cr3"]),
        FilterOptions(date_range=(date(2025, 1, 1), None)),
        FilterOptions(date_range=(None, date(2025, 12, 31))),
        FilterOptions(cameras=["Canon EOS R6"], lenses=["RF24-70mm F2.8 L IS USM"]),
        FilterOptions(aperture_range=(1.8, 8.0), iso_range=(100, 3200)),
        FilterOptions(shutter_speed_range=("1/1000", "2s")),
        FilterOptions(people=["Ana", "Luis"], people_match="all"),
        FilterOptions(people=["Ana"]),
        FilterOptions(person_ids=[3, 7]),
    ],
)
def test_filter_argv_round_trips_through_the_cli(filters):
    assert build_filter_query(_parsed(filter_argv(filters))) == filters


def test_build_export_job_reorganized(tmp_path):
    gallery = _gallery(tmp_path)
    settings = ExportSettings(
        dest="F:/Share",
        structure=["Year", "Model"],
        by_media_type=False,
        on_exist="skip",
        jpeg_only=True,
        manifest=False,
        portable_db=True,
        folder=" Trip to Rome ",
    )
    job = build_export_job(gallery, settings, FilterOptions(people=["Ana"]))
    (argv,) = job.argvs
    assert argv[:4] == ("export", "--from", str(gallery.root), "--to")
    assert Path(argv[4]) == Path("F:/Share") / "Trip to Rome"
    args = parse_args(list(argv))
    assert args.structure == ["Year", "Model"] and not args.mirror
    assert not args.by_media_type and args.on_exist == "skip"
    assert args.jpeg_only and not args.manifest and args.portable_db
    assert not args.dry_run and args.people == ["Ana"]
    assert job.title == "Export · E" and not job.writes_catalog


def test_build_export_job_keeps_folders_and_dry_run(tmp_path):
    settings = ExportSettings(dest="F:/x", keep_folders=True, dry_run=True)
    job = build_export_job(_gallery(tmp_path), settings, None)
    args = parse_args(list(job.argvs[0]))
    assert args.mirror and args.dry_run and args.manifest
    assert "--structure" not in job.argvs[0]
    assert job.title.endswith("(dry run)")


def test_default_folder_name():
    assert default_folder_name(NOW) == "Export 20261028 0905"


def test_build_export_job_names_an_empty_folder_after_now(tmp_path):
    settings = ExportSettings(dest="F:/x", folder="  ")
    job = build_export_job(_gallery(tmp_path), settings, None, now=NOW)
    args = parse_args(list(job.argvs[0]))
    assert args.dest == Path("F:/x") / "Export 20261028 0905"


@pytest.mark.parametrize(
    "gallery, settings, message",
    [
        (None, ExportSettings(dest="x"), "Open a gallery"),
        ("missing", ExportSettings(dest="x"), "not connected"),
        ("ok", ExportSettings(dest="  "), "destination"),
        ("ok", ExportSettings(dest="x", structure=[]), "folder level"),
        ("ok", ExportSettings(dest="x", folder="a:b"), "folder name"),
        ("ok", ExportSettings(dest="x", folder="a/b"), "folder name"),
        ("ok", ExportSettings(dest="x", folder=".."), "folder name"),
        ("ok", ExportSettings(dest="x", folder="trip."), "folder name"),
    ],
)
def test_build_export_job_says_what_is_missing(tmp_path, gallery, settings, message):
    if gallery is not None:
        gallery = _gallery(tmp_path, connected=gallery == "ok")
    with pytest.raises(ValueError, match=message):
        build_export_job(gallery, settings, None)


def test_example_path():
    assert (
        example_path(ExportSettings(), NOW)
        == "Export 20261028 0905/Photos/2026/06/IMG_0001.CR3"
    )
    assert (
        example_path(
            ExportSettings(
                structure=["Model"], by_media_type=False, jpeg_only=True, folder="Rome"
            ),
            NOW,
        )
        == "Rome/Canon EOS R6/IMG_0001.jpg"
    )
    assert example_path(ExportSettings(keep_folders=True, folder="Rome"), NOW) == (
        "Rome/(the gallery's folders)/IMG_0001.CR3"
    )


def test_export_summary(repo, make_item):
    repo.upsert_many(
        [
            make_item("a/IMG_1.CR3", size_bytes=30),
            make_item("a/IMG_1.JPG", size_bytes=10),
            make_item("a/IMG_2.CR3", size_bytes=30),
            make_item("a/clip.mp4", media_type="video", size_bytes=100),
        ]
    )
    summary = export_summary(repo, None)
    assert (summary.matched, summary.size_bytes) == (4, 170)
    assert (summary.as_is, summary.raws, summary.left_out) == (2, 1, 1)
    only_video = export_summary(repo, FilterOptions(filetypes=["video"]))
    assert (only_video.matched, only_video.as_is, only_video.left_out) == (1, 1, 0)
