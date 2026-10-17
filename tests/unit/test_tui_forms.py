"""The TUI's forms are generated from the CLI parser; these tests pin how options
become fields and how filled-in fields become a command line."""

from datetime import date
from pathlib import Path

import pytest

from smart_gallery.cli import build_parser, parse_args
from smart_gallery.tui_forms import (
    build_argv,
    command_forms,
    default_values,
    field_rows,
    format_command,
)

FORMS = command_forms(build_parser())


def _field(command, key):
    return FORMS[command].fields[key]


# ── form structure ───────────────────────────────────────────────────────────
def test_aliases_and_the_ui_itself_are_not_listed():
    assert "export" in FORMS
    assert "convert-raw" in FORMS
    for hidden in ("convert-cr3", "ui", "tui"):
        assert hidden not in FORMS


def test_export_sections_in_display_order():
    form = FORMS["export"]
    assert [s.title for s in form.sections] == [
        "Required",
        "Layout",
        "Filters",
        "Extras",
        "Options",
    ]
    required = form.sections[0]
    assert [f.key for f in required.fields] == ["drive", "dest"]
    assert all(f.required for f in required.fields)


def test_large_sections_start_collapsed():
    sections = {s.title: s for s in FORMS["export"].sections}
    assert sections["Filters"].collapsed is True
    assert sections["Layout"].collapsed is False
    assert sections["Required"].collapsed is False


def test_optional_positional_is_not_required():
    form = FORMS["name-person"]
    assert form.fields["name"].required is False
    assert form.fields["name"] not in form.sections[0].fields


@pytest.mark.parametrize(
    "command, key, kind",
    [
        ("export", "drive", "path"),
        ("export", "min_iso", "int"),
        ("export", "min_aperture", "float"),
        ("export", "date_start", "date"),
        ("export", "cameras", "list"),
        ("export", "person_ids", "int_list"),
        ("export", "file_types", "multi"),
        ("export", "structure", "ordered"),
        ("export", "on_exist", "choice"),
        ("export", "mirror", "switch"),
        ("import", "sources", "path_list"),
        ("cluster-faces", "mode", "mode"),
        ("init", "label", "text"),
    ],
)
def test_option_kinds(command, key, kind):
    assert _field(command, key).kind == kind


def test_paired_store_true_and_store_false_become_one_switch():
    form = FORMS["import"]
    by_type = [f for f in form.fields.values() if "by_media_type" in f.key]
    assert len(by_type) == 1
    assert by_type[0].kind == "switch"
    assert by_type[0].default is True


def test_mutually_exclusive_flags_become_one_mode_field():
    mode = _field("cluster-faces", "mode")
    assert mode.choices == ("--rebuild", "--incremental")
    assert "rebuild" not in FORMS["cluster-faces"].fields
    assert "incremental" not in FORMS["cluster-faces"].fields


def test_every_field_has_a_hint():
    missing = [
        f"{name}.{key}"
        for name, form in FORMS.items()
        for key, field in form.fields.items()
        if not field.hint.strip()
    ]
    assert missing == []


# ── values -> command line ───────────────────────────────────────────────────


def _build(command, extra="", **values):
    form = FORMS[command]
    filled = default_values(form)
    filled.update(values)
    return build_argv(form, filled, extra)


def test_only_required_values_when_nothing_else_changes():
    result = _build("export", drive="E:/", dest="D:/x")
    assert result.argv == ["export", "--from", "E:/", "--to", "D:/x"]
    assert result.errors == {}
    assert result.missing == []
    assert result.ok


def test_values_equal_to_the_default_are_left_out():
    base = dict(drive="E:/", samples="S:/faces")
    unchanged = _build("identify-faces", threshold="0.50", **base)
    assert unchanged.argv == ["identify-faces", "E:/", "--samples", "S:/faces"]
    changed = _build("identify-faces", threshold="0.6", **base)
    assert changed.argv[-2:] == ["--threshold", "0.6"]


def test_positionals_come_first_and_keep_spaces():
    result = _build(
        "import",
        sources='F:/DCIM "G:/My dump"',
        drive="E:/My Photos",
        extensions=".cr3 .jpg",
    )
    assert result.argv == [
        "import",
        "F:/DCIM",
        "G:/My dump",
        "--to",
        "E:/My Photos",
        "--extensions",
        ".cr3",
        ".jpg",
    ]


def test_windows_backslashes_are_not_escapes():
    result = _build("convert-raw", sources=r'E:\DCIM "D:\My Dump"')
    assert result.argv == ["convert-raw", r"E:\DCIM", r"D:\My Dump"]


def test_quoted_single_value_is_unquoted():
    assert _build("sync", drive='"E:/My Photos"').argv == ["sync", "E:/My Photos"]


def test_filled_form_parses_back_to_the_chosen_values():
    result = _build(
        "export",
        drive="E:/",
        dest="D:/x",
        structure=("Month", "Year"),
        mirror=True,
        by_media_type=False,
        on_exist="skip",
        manifest=False,
        file_types=("image", "video"),
        cameras='"Canon EOS R6" "Sony A7"',
        min_iso="100",
        date_start="2026-01-01",
        person_ids="3 7",
        dry_run=True,
    )
    assert result.ok, (result.errors, result.missing)
    args = parse_args(result.argv)
    assert args.drive == Path("E:/")
    assert args.dest == Path("D:/x")
    assert args.structure == ["Month", "Year"]
    assert args.mirror is True
    assert args.by_media_type is False
    assert args.on_exist == "skip"
    assert args.manifest is False
    assert args.portable_db is False
    assert args.file_types == ["image", "video"]
    assert args.cameras == ["Canon EOS R6", "Sony A7"]
    assert args.min_iso == 100
    assert args.date_start == date(2026, 1, 1)
    assert args.person_ids == [3, 7]
    assert args.dry_run is True


def test_switches_emit_their_flag_only_away_from_default():
    on_default = _build("export", drive="E:/", dest="D:/x", manifest=True)
    assert "--no-manifest" not in on_default.argv
    flipped = _build("export", drive="E:/", dest="D:/x", by_media_type=False)
    assert "--no-by-media-type" in flipped.argv


def test_invalid_values_are_reported_per_field():
    result = _build(
        "export",
        drive="E:/",
        dest="D:/x",
        min_iso="abc",
        min_aperture="f2",
        date_start="2026-13-01",
        person_ids="3 x",
    )
    assert set(result.errors) == {"min_iso", "min_aperture", "date_start", "person_ids"}
    assert not result.ok
    for flag in ("--min-iso", "--min-aperture", "--date-start", "--person-ids"):
        assert flag not in result.argv


def test_missing_required_values_are_listed():
    result = _build("export")
    assert result.missing == ["drive", "dest"]
    assert not result.ok


def test_folder_structure_needs_at_least_one_level():
    result = _build("export", drive="E:/", dest="D:/x", structure=())
    assert "structure" in result.errors


def test_mode_emits_the_chosen_flag():
    rebuild = _build("cluster-faces", drive="E:/", mode="--rebuild")
    assert rebuild.argv == ["cluster-faces", "E:/", "--rebuild"]
    neither = _build("cluster-faces", drive="E:/")
    assert neither.argv == ["cluster-faces", "E:/"]


def test_optional_positional_is_emitted_when_filled():
    result = _build("name-person", drive="E:/", person_id="3", name="Alice Smith")
    assert result.argv == ["name-person", "E:/", "3", "Alice Smith"]


def test_extra_options_are_appended():
    result = _build("sync", extra="--dry-run", drive="E:/")
    assert result.argv == ["sync", "E:/", "--dry-run"]


def test_changed_fields_are_tracked_for_section_badges():
    result = _build(
        "export",
        drive="E:/",
        dest="D:/x",
        file_types=("image",),
        date_start="2026-01-01",
        on_exist="rename",
    )
    assert result.changed == {"drive", "dest", "file_types", "date_start"}


def test_preview_quotes_values_with_spaces():
    argv = ["sync", "E:/My Photos", "--dry-run"]
    assert format_command(argv) == 'smart-gallery sync "E:/My Photos" --dry-run'


# ── layout ───────────────────────────────────────────────────────────────────
def test_range_fields_share_a_row():
    filters = next(s for s in FORMS["export"].sections if s.title == "Filters")
    rows = [tuple(f.key for f in row.fields) for row in field_rows(filters.fields)]
    assert rows == [
        ("file_types",),
        ("extensions",),
        ("date_start", "date_end"),
        ("cameras",),
        ("lenses",),
        ("min_aperture", "max_aperture"),
        ("min_iso", "max_iso"),
        ("min_shutter_speed", "max_shutter_speed"),
        ("people",),
        ("person_ids",),
    ]


def test_unpaired_fields_keep_their_own_row_and_label():
    required = FORMS["export"].sections[0]
    rows = field_rows(required.fields)
    assert [r.label for r in rows] == ["Source drive", "Destination folder"]
