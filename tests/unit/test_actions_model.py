import pytest

from smart_gallery.cli import parse_args
from smart_gallery.forms import build_argv, default_values
from smart_gallery.gui.actions import model

CANON_IMPORT = [
    "import",
    "F:\\",
    "--to",
    "E:\\",
    "--output",
    "E:\\Photos\\Canon",
    "--file-types",
    "image",
    "--extensions",
    ".cr3",
    ".jpg",
    "--no-by-media-type",
    "--structure",
    "Year",
    "Month",
    "--on-exist",
    "skip",
]

ROUNDTRIPS = [
    ["sync", "E:/"],
    CANON_IMPORT,
    [
        "export",
        "--from",
        "E:/",
        "--to",
        "D:/out",
        "--cameras",
        "Canon EOS R6",
        "--min-iso",
        "400",
    ],
    ["import", "C:\\My Photos", "D:\\x y", "--to", "E:/", "--structure", "Model"],
    ["cluster-faces", "E:/", "--incremental", "--algo", "dbscan", "--eps", "0.3"],
    ["convert-cr3", "F:\\DCIM"],
    ["export", "--from", "E:/", "--to", "D:/o", "--date-start", "2024-01-02"],
]


@pytest.mark.parametrize("argv", ROUNDTRIPS)
def test_values_from_argv_roundtrip(argv):
    forms = model.gui_forms()
    name = parse_args(argv).command
    name = "convert-raw" if name == "convert-cr3" else name
    form = forms[name]
    values = model.values_from_argv(form, argv)
    rebuilt = build_argv(form, values)
    assert rebuilt.ok, rebuilt
    expected = vars(parse_args(argv))
    actual = vars(parse_args(rebuilt.argv))
    expected.pop("command")
    actual.pop("command")
    assert actual == expected


def test_values_from_argv_value_types_match_defaults():
    form = model.gui_forms()["cluster-faces"]
    values = model.values_from_argv(form, ["cluster-faces", "E:/", "--rebuild"])
    assert values["mode"] == "--rebuild"
    assert set(values) == set(default_values(form))
    assert model.values_from_argv(form, ["cluster-faces", "E:/"])["mode"] is None


def test_values_from_argv_rejects_bad_argv(capsys):
    form = model.gui_forms()["sync"]
    with pytest.raises(ValueError):
        model.values_from_argv(form, ["sync", "--nope"])
    assert capsys.readouterr().err == ""


def test_gui_forms_hide_launchers():
    forms = model.gui_forms()
    assert "gui" not in forms and "ui" not in forms and "dashboard" not in forms
    assert list(forms)[:3] == ["init", "import", "sync"]
    assert "figures" in forms["report"].fields


def test_frozen_build_drops_report_figures(monkeypatch):
    monkeypatch.setattr(model, "is_frozen", lambda: True)
    report = model.gui_forms()["report"]
    assert "figures" not in report.fields
    assert all(f.key != "figures" for s in report.sections for f in s.fields)


def test_initial_values_prefills_drive():
    forms = model.gui_forms()
    assert model.initial_values(forms["sync"], "E:\\")["drive"] == "E:\\"
    assert model.initial_values(forms["export"], "E:\\")["drive"] == "E:\\"
    assert model.initial_values(forms["sync"], None) == default_values(forms["sync"])
    compare = forms["compare"]
    assert model.initial_values(compare, "E:\\") == default_values(compare)


def test_face_commands_unavailable_without_insightface(monkeypatch):
    monkeypatch.setattr(model, "faces_available", lambda: False)
    for command in ("scan-faces", "identify-faces", "cluster-faces", "split-person"):
        assert model.unavailable_reason(command) == (
            "Face features aren't installed in this build"
        )
    assert model.unavailable_reason("sync") is None
    monkeypatch.setattr(model, "faces_available", lambda: True)
    assert model.unavailable_reason("scan-faces") is None
