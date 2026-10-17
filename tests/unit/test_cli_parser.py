"""Pin the CLI's parsing behavior. The TUI builds its forms from this parser, so
regrouping options or adding help text must not change what a command line means."""

from datetime import date
from pathlib import Path

import pytest

from smart_gallery.cli import parse_args


def test_import_flags_parse():
    args = parse_args(
        [
            "import",
            "F:/",
            "G:/dump",
            "--to",
            "E:/",
            "--output",
            "E:/Photos",
            "--extensions",
            ".cr3",
            ".jpg",
            "--structure",
            "Year",
            "Month",
            "Model",
            "--on-exist",
            "skip",
            "--no-by-media-type",
            "--dry-run",
        ]
    )
    assert args.command == "import"
    assert args.sources == [Path("F:/"), Path("G:/dump")]
    assert args.drive == Path("E:/")
    assert args.output == Path("E:/Photos")
    assert args.extensions == [".cr3", ".jpg"]
    assert args.structure == ["Year", "Month", "Model"]
    assert args.on_exist == "skip"
    assert args.by_media_type is False
    assert args.dry_run is True


def test_import_defaults():
    args = parse_args(["import", "F:/", "--to", "E:/"])
    assert args.structure == ["Year", "Month"]
    assert args.on_exist == "rename"
    assert args.by_media_type is True
    assert args.dry_run is False
    assert args.output is None


def test_export_flags_parse():
    args = parse_args(
        [
            "export",
            "--from",
            "E:/",
            "--to",
            "D:/x",
            "--mirror",
            "--no-manifest",
            "--portable-db",
            "--file-types",
            "image",
            "video",
            "--min-iso",
            "100",
            "--date-start",
            "2026-01-01",
            "--person-ids",
            "3",
            "7",
        ]
    )
    assert args.drive == Path("E:/")
    assert args.dest == Path("D:/x")
    assert args.mirror is True
    assert args.manifest is False
    assert args.portable_db is True
    assert args.by_media_type is True
    assert args.file_types == ["image", "video"]
    assert args.min_iso == 100
    assert args.date_start == date(2026, 1, 1)
    assert args.person_ids == [3, 7]


def test_cluster_faces_modes_are_exclusive():
    args = parse_args(["cluster-faces", "E:/", "--incremental", "--pca", "128"])
    assert args.incremental is True
    assert args.rebuild is False
    assert args.algo == "hdbscan"
    assert args.pca == 128
    with pytest.raises(SystemExit):
        parse_args(["cluster-faces", "E:/", "--rebuild", "--incremental"])


def test_aliases_still_parse():
    assert parse_args(["convert-cr3", "x.CR3"]).command == "convert-cr3"
    assert parse_args(["tui"]).command == "tui"


def test_name_person_name_is_optional():
    assert parse_args(["name-person", "E:/", "3"]).name is None
    assert parse_args(["name-person", "E:/", "3", "Alice"]).name == "Alice"


def test_gui_command_parses():
    assert parse_args(["gui"]).command == "gui"
