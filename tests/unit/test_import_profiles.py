import pytest

from smart_gallery import import_profiles as ip
from smart_gallery.cli import build_parser


def _parse(argv):
    return build_parser().parse_args(argv)


def _by_name(name):
    return next(p for p in ip.starter_profiles() if p.name == name)


def test_canon_profile_matches_script():
    argvs = ip.expand(_by_name("Canon SD card"), "F:\\", ["E:\\", "D:\\"])
    assert len(argvs) == 4
    ns = [_parse(a) for a in argvs]
    assert [str(n.output) for n in ns] == [
        r"E:\Photos\Canon",
        r"D:\Photos\Canon",
        r"E:\Videos",
        r"D:\Videos",
    ]
    assert [list(n.file_types) for n in ns] == [["image"]] * 2 + [["video"]] * 2
    for n in ns:
        assert n.by_media_type is False
        assert list(n.structure) == ["Year", "Month"]
        assert n.on_exist == "skip"
    assert [e.lower() for e in ns[0].extensions] == [".cr3", ".jpg"]
    assert [e.lower() for e in ns[2].extensions] == [".mp4"]


def test_source_and_output_with_spaces():
    source = r"C:\Users\A\Pictures\Pixel 03.08.2026"
    argvs = ip.expand(_by_name("Pixel dump"), source, ["E:\\"])
    ns = _parse(argvs[0])
    assert str(ns.output) == r"E:\Photos\Other cameras"
    assert [str(s) for s in ns.sources] == [source]


def test_save_load_roundtrip_and_seed_only_once(tmp_path):
    ip.seed_starter_profiles(tmp_path)
    loaded = ip.load_profiles(tmp_path)
    assert [p.name for p in loaded] == ["Canon SD card", "Pixel dump"]
    before = ip.expand(loaded[0], "F:\\", ["E:\\"])
    assert before == ip.expand(_by_name("Canon SD card"), "F:\\", ["E:\\"])

    ip.delete_profile(tmp_path, "Pixel dump")
    ip.seed_starter_profiles(tmp_path)
    assert [p.name for p in ip.load_profiles(tmp_path)] == ["Canon SD card"]

    (tmp_path / "broken.json").write_text("{nope")
    assert len(ip.load_profiles(tmp_path)) == 1

    other = ip.ImportProfile("canon sd card!", [])
    path = ip.save_profile(tmp_path, other)
    assert path.name == "canon-sd-card-2.json"
    assert ip.save_profile(tmp_path, other) == path


def test_unknown_option_key_raises():
    rule = ip.ImportRule("image", [".jpg"], "Photos", {"bogus": 1})
    with pytest.raises(ValueError):
        ip.expand(ip.ImportProfile("x", [rule]), "F:\\", ["E:\\"])
    rule = ip.ImportRule("image", [".jpg"], "Photos", {"drive": "D:\\"})
    with pytest.raises(ValueError):
        ip.expand(ip.ImportProfile("x", [rule]), "F:\\", ["E:\\"])
