"""label-faces: user corrections to how faces are filed."""

import json

import pytest

from smart_gallery.cli import _HANDLERS, build_parser
from smart_gallery.services.file_faces import file_faces
from smart_gallery.services.label_faces import (
    LabelEntry,
    label_faces,
    read_label_list,
    write_label_list,
)
from tests.test_file_faces import Catalog, _near, _unit


@pytest.fixture
def cat(repo, make_item):
    return Catalog(repo, make_item)


def _rejections(repo):
    return {
        tuple(r)
        for r in repo.conn.execute("SELECT face_id, person_id FROM face_rejections")
    }


def test_confirm(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "suggested", None, suggested=alice)
    repo.conn.execute("INSERT INTO face_rejections VALUES (?, ?)", (f, alice))
    repo.conn.commit()
    report = label_faces(repo, [LabelEntry("confirm", (f,), person_id=alice)])
    row = cat.row(f)
    assert (row["state"], row["person_id"], row["suggested_person_id"]) == (
        "user",
        alice,
        None,
    )
    assert _rejections(repo) == set()
    assert report.faces_changed == 1
    assert repo.get_person(alice)["face_count"] == 1


def test_reject_suggestion_returns_to_pool(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "suggested", None, suggested=alice)
    label_faces(repo, [LabelEntry("reject", (f,), person_id=alice)])
    row = cat.row(f)
    assert (row["state"], row["person_id"], row["suggested_person_id"]) == (
        "new",
        None,
        None,
    )
    assert _rejections(repo) == {(f, alice)}


def test_reject_assigned_returns_to_pool(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "auto", alice)
    label_faces(repo, [LabelEntry("reject", (f,), person_id=alice)])
    row = cat.row(f)
    assert (row["state"], row["person_id"]) == ("new", None)
    assert _rejections(repo) == {(f, alice)}


def test_reject_unrelated_person_leaves_face(cat, repo):
    alice, bob = cat.person("Alice"), cat.person("Bob")
    f = cat.face(_unit(0), "auto", alice)
    label_faces(repo, [LabelEntry("reject", (f,), person_id=bob)])
    row = cat.row(f)
    assert (row["state"], row["person_id"]) == ("auto", alice)
    assert _rejections(repo) == {(f, bob)}


def test_move(cat, repo):
    alice, bob = cat.person("Alice"), cat.person("Bob")
    f = cat.face(_unit(0), "auto", alice)
    repo.conn.execute("INSERT INTO face_rejections VALUES (?, ?)", (f, bob))
    repo.conn.commit()
    label_faces(repo, [LabelEntry("move", (f,), person_id=bob)])
    row = cat.row(f)
    assert (row["state"], row["person_id"]) == ("user", bob)
    assert _rejections(repo) == {(f, alice)}
    assert repo.get_person(bob)["face_count"] == 1
    assert repo.get_person(alice)["face_count"] == 0


def test_move_from_no_person_adds_no_rejection(cat, repo):
    bob = cat.person("Bob")
    f = cat.face(_unit(0))
    label_faces(repo, [LabelEntry("move", (f,), person_id=bob)])
    assert _rejections(repo) == set()


def test_new_person(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "auto", alice)
    report = label_faces(repo, [LabelEntry("new_person", (f,), name="Carol")])
    row = cat.row(f)
    assert row["state"] == "user"
    assert repo.get_person(row["person_id"])["name"] == "Carol"
    assert report.people_created == 1
    assert _rejections(repo) == {(f, alice)}


def test_new_person_reuses_existing_name(cat, repo):
    carol = cat.person("Carol")
    f = cat.face(_unit(0))
    report = label_faces(repo, [LabelEntry("new_person", (f,), name="carol")])
    assert cat.row(f)["person_id"] == carol
    assert report.people_created == 0


def test_new_person_ambiguous_name_raises(cat, repo):
    cat.person("Carol")
    cat.person("CAROL")
    f = cat.face(_unit(0))
    with pytest.raises(ValueError):
        label_faces(repo, [LabelEntry("new_person", (f,), name="Carol")])
    assert cat.row(f)["state"] == "new"


def test_ignore(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "suggested", None, suggested=alice)
    g = cat.face(_unit(1), "auto", alice)
    label_faces(repo, [LabelEntry("ignore", (f, g))])
    for i in (f, g):
        row = cat.row(i)
        assert (row["state"], row["person_id"], row["suggested_person_id"]) == (
            "ignored",
            None,
            None,
        )
    assert _rejections(repo) == set()


def test_label_list_roundtrip(tmp_path):
    entries = [
        LabelEntry("confirm", (1, 2), person_id=3),
        LabelEntry("new_person", (4,), name="Dana"),
        LabelEntry("ignore", (5,)),
    ]
    path = tmp_path / "labels.json"
    write_label_list(path, entries)
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)
    assert read_label_list(path) == entries


@pytest.mark.parametrize(
    "bad",
    [
        {"action": "explode", "faces": [1]},
        {"action": "move", "faces": [1]},
        {"action": "new_person", "faces": [1]},
        {"action": "ignore", "faces": "x"},
        {"action": "ignore"},
        "nope",
    ],
)
def test_read_label_list_rejects_bad_entry(tmp_path, bad):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps([bad]), encoding="utf-8")
    with pytest.raises(ValueError):
        read_label_list(path)


def test_read_label_list_rejects_unknown_action(tmp_path):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps([{"action": "zap", "faces": [1]}]), encoding="utf-8")
    with pytest.raises(ValueError):
        read_label_list(path)


def test_missing_face_ids_are_counted_not_fatal(cat, repo):
    alice = cat.person("Alice")
    f = cat.face(_unit(0))
    report = label_faces(repo, [LabelEntry("confirm", (f, 9999), person_id=alice)])
    assert report.missing_faces == 1
    assert report.faces_changed == 1
    assert cat.row(f)["person_id"] == alice


def test_all_entries_apply_atomically(cat, repo):
    alice = cat.person("Alice")
    f, g = cat.face(_unit(0)), cat.face(_unit(1))
    before = cat.snapshot()
    with pytest.raises(ValueError):
        label_faces(
            repo,
            [
                LabelEntry("confirm", (f,), person_id=alice),
                LabelEntry("confirm", (g,), person_id=424242),
            ],
        )
    assert cat.snapshot() == before
    assert _rejections(repo) == set()


def test_rejected_face_not_resuggested_after_file_faces(cat, repo):
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    f = cat.face(_near(_unit(0), 0.9))
    file_faces(repo, group=False)
    assert cat.row(f)["person_id"] == alice
    label_faces(repo, [LabelEntry("reject", (f,), person_id=alice)])
    file_faces(repo, group=False)
    row = cat.row(f)
    assert row["person_id"] != alice
    assert row["suggested_person_id"] != alice


def test_cli_label_faces_single_action(cat, repo, drive):
    alice = cat.person("Alice")
    f = cat.face(_unit(0))
    args = build_parser().parse_args(
        ["label-faces", str(drive), "--faces", str(f), "--to", str(alice)]
    )
    assert args.command == "label-faces"
    assert "label-faces" in _HANDLERS
    _HANDLERS["label-faces"](args)
    row = cat.row(f)
    assert (row["state"], row["person_id"]) == ("user", alice)


def test_cli_label_faces_requires_exactly_one_action(drive):
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(
            ["label-faces", str(drive), "--faces", "1", "--to", "1", "--ignore"]
        )
    args = p.parse_args(["label-faces", str(drive), "--faces", "1"])
    with pytest.raises(ValueError):
        _HANDLERS["label-faces"](args)
    with pytest.raises(SystemExit):  # an action without --faces
        p.parse_args(["label-faces", str(drive), "--to", "1"])
    args = p.parse_args(["label-faces", str(drive), "--list", "x.json", "--ignore"])
    with pytest.raises(ValueError):
        _HANDLERS["label-faces"](args)
    with pytest.raises(SystemExit):
        p.parse_args(["label-faces", str(drive), "--list", "x.json", "--faces", "1"])
