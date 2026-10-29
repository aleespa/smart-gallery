"""label-faces: user corrections to how faces are filed."""

import json

import pytest

from smart_gallery.cli import _HANDLERS, build_parser
from smart_gallery.services.file_faces import file_faces
from smart_gallery.services.label_faces import (
    LabelEntry,
    describe,
    label_faces,
    read_label_list,
    write_label_list,
)
from smart_gallery.services.undo_faces import undo_faces
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


def test_single_action_with_unknown_person_fails_and_writes_nothing(cat, repo, drive):
    f = cat.face(_unit(0))
    before = cat.snapshot()
    args = build_parser().parse_args(
        ["label-faces", str(drive), "--faces", str(f), "--confirm", "424242"]
    )
    with pytest.raises(ValueError):
        _HANDLERS["label-faces"](args)
    assert cat.snapshot() == before
    assert _rejections(repo) == set()


def test_list_skips_entries_whose_person_is_gone(cat, repo, drive, tmp_path):
    alice = cat.person("Alice")
    f, g = cat.face(_unit(0)), cat.face(_unit(1))
    path = tmp_path / "labels.json"
    write_label_list(
        path,
        [
            LabelEntry("confirm", (f,), person_id=alice),
            LabelEntry("confirm", (g,), person_id=424242),
        ],
    )
    report = label_faces(repo, read_label_list(path), skip_missing=True)
    assert report.missing_people == 1
    assert (cat.row(f)["state"], cat.row(f)["person_id"]) == ("user", alice)
    assert (cat.row(g)["state"], cat.row(g)["person_id"]) == ("new", None)

    args = build_parser().parse_args(["label-faces", str(drive), "--list", str(path)])
    _HANDLERS["label-faces"](args)  # the CLI's list mode skips too


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


def test_label_then_undo_restores_everything_refile_moved(cat, repo):
    # The incident: few confirmed examples, many auto faces; rejecting look-alikes
    # makes the re-filing strip the auto faces. Undo must bring all of them back.
    alice = cat.person("Alice")
    cat.face(_unit(0), "user", alice)
    autos = [cat.face(_near(_unit(0), 0.8, axis=100), "auto", alice) for _ in range(20)]
    look = [
        cat.face(_near(_unit(0), 0.8, axis=100), "suggested", None, suggested=alice)
        for _ in range(5)
    ]
    repo.recompute_person(alice)
    before = repo.face_snapshot()
    label_faces(repo, [LabelEntry("reject", tuple(look), person_id=alice)], refile=True)
    assert any(cat.row(a)["person_id"] is None for a in autos)
    assert repo.face_snapshot() != before
    report = undo_faces(repo)
    assert report.summary == "Marked 5 faces as not Alice"
    assert repo.face_snapshot() == before


def test_describe_phrases():
    names = {1: "Alice"}
    e = LabelEntry
    assert describe([e("confirm", (1,), person_id=1)], names) == (
        "Confirmed 1 face as Alice"
    )
    assert describe([e("reject", (1, 2), person_id=1)], names) == (
        "Marked 2 faces as not Alice"
    )
    assert describe([e("move", (1, 2), person_id=9)], names) == (
        "Moved 2 faces to a group"
    )
    assert describe([e("new_person", (1,), name="Bob")], names) == (
        "Moved 1 face to Bob"
    )
    assert describe([e("ignore", (1, 2, 3))], names) == "Ignored 3 faces"
    same = [e("move", (1,), person_id=1), e("move", (2, 3), person_id=1)]
    assert describe(same, names) == "Moved 3 faces to Alice"
    assert describe([e("ignore", (1,)), e("move", (2,), person_id=1)], names) == (
        "2 corrections"
    )


def test_label_without_changes_records_nothing(cat, repo):
    alice = cat.person("Alice")
    label_faces(repo, [LabelEntry("confirm", (9999,), person_id=alice)])
    assert repo.last_undo() is None


def test_cli_undo_faces(cat, repo, drive):
    alice = cat.person("Alice")
    f = cat.face(_unit(0), "suggested", None, suggested=alice)
    p = build_parser()
    _HANDLERS["label-faces"](
        p.parse_args(
            ["label-faces", str(drive), "--faces", str(f), "--confirm", str(alice)]
        )
    )
    assert cat.row(f)["state"] == "user"
    _HANDLERS["undo-faces"](p.parse_args(["undo-faces", str(drive)]))
    row = cat.row(f)
    assert (row["state"], row["person_id"], row["suggested_person_id"]) == (
        "suggested",
        None,
        alice,
    )


def test_cli_undo_faces_nothing_to_undo_raises(repo, drive):
    args = build_parser().parse_args(["undo-faces", str(drive)])
    with pytest.raises(ValueError):
        _HANDLERS["undo-faces"](args)


def test_cli_label_faces_refile_flag_parses(drive):
    p = build_parser()
    a = p.parse_args(["label-faces", str(drive), "--list", "x.json", "--refile"])
    assert a.refile is True
    assert p.parse_args(["label-faces", str(drive), "--list", "x.json"]).refile is False


def test_undo_faces_refuses_on_mirror(repo):
    from smart_gallery.mirror.types import Peer

    repo.add_peer("mirror", Peer("u" * 32, "x.db"))
    with pytest.raises(ValueError, match="mirror"):
        undo_faces(repo)
