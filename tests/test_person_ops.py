"""Person operations (merge, hide, name, split, rebuild, identify) keep the
user's decisions."""

import numpy as np
import pytest

from smart_gallery.cli import _HANDLERS, build_parser
from tests.test_file_faces import Catalog, _near, _unit


@pytest.fixture
def cat(repo, make_item):
    return Catalog(repo, make_item)


def _rejections(repo):
    return {
        tuple(r)
        for r in repo.conn.execute("SELECT face_id, person_id FROM face_rejections")
    }


def _person(repo, pid):
    row = repo.conn.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def _jitter(base, rng, n, scale=0.01):
    out = []
    for _ in range(n):
        v = base + scale * rng.standard_normal(512).astype(np.float32)
        out.append((v / np.linalg.norm(v)).astype(np.float32))
    return out


# ── merge ────────────────────────────────────────────────────────────────────
def test_merge_moves_rejections_and_user_assignment_wins(cat, repo):
    alice, src = cat.person("Alice"), cat.person()
    u = cat.face(_unit(0), "user", src)  # the user put this face in src...
    a = cat.face(_unit(1), "auto", src)
    s = cat.face(_unit(2), "suggested", None, suggested=src)
    other = cat.face(_unit(3), "new")
    run = repo.conn.execute
    run("INSERT INTO face_rejections VALUES (?, ?)", (u, alice))  # ...but not Alice
    run("INSERT INTO face_rejections VALUES (?, ?)", (other, src))
    run("INSERT INTO face_rejections VALUES (?, ?)", (other, alice))
    repo.conn.commit()

    repo.merge_persons(alice, [src])

    assert _person(repo, src) is None
    assert cat.row(u)["person_id"] == alice
    assert cat.row(a)["person_id"] == alice
    assert cat.row(s)["suggested_person_id"] == alice
    # The source's rejections move to Alice (the duplicate collapses); the
    # rejection of a face the user filed in the merged person is dropped.
    assert _rejections(repo) == {(other, alice)}
    assert repo.get_person(alice)["face_count"] == 2


def test_merge_unnamed_target_takes_source_name(cat, repo):
    dst, bob, grp = cat.person(), cat.person("Bob"), cat.person()
    cat.face(_unit(0), "cluster", dst)
    cat.face(_unit(1), "auto", bob)
    cat.face(_unit(2), "cluster", grp)
    repo.merge_persons(dst, [bob, grp])
    assert _person(repo, dst)["name"] == "Bob"

    # Two named sources: the unnamed target stays unnamed.
    dst2, c, d = cat.person(), cat.person("Carol"), cat.person("Dan")
    cat.face(_unit(3), "cluster", dst2)
    repo.merge_persons(dst2, [c, d])
    assert _person(repo, dst2)["name"] is None


# ── hide ─────────────────────────────────────────────────────────────────────
def test_hide_and_unhide_group(cat, repo):
    grp = cat.person()
    f1 = cat.face(_unit(0), "cluster", grp)
    f2 = cat.face(_unit(1), "hidden", grp)
    repo.set_person_hidden(grp, True)
    assert _person(repo, grp)["hidden"] == 1
    assert {cat.row(f1)["state"], cat.row(f2)["state"]} == {"ignored"}
    assert cat.row(f1)["person_id"] == grp

    repo.set_person_hidden(grp, False)
    assert _person(repo, grp)["hidden"] == 0
    assert {cat.row(f1)["state"], cat.row(f2)["state"]} == {"cluster"}


def test_hide_named_person_refused(cat, repo, drive):
    alice = cat.person("Alice")
    with pytest.raises(ValueError):
        repo.set_person_hidden(alice, True)
    assert _person(repo, alice)["hidden"] == 0

    grp = cat.person()
    cat.face(_unit(0), "cluster", grp)
    p = build_parser()
    _HANDLERS["hide-person"](p.parse_args(["hide-person", str(drive), str(grp)]))
    assert _person(repo, grp)["hidden"] == 1
    args = p.parse_args(["hide-person", str(drive), str(grp), "--unhide"])
    _HANDLERS["hide-person"](args)
    assert _person(repo, grp)["hidden"] == 0


# ── name ─────────────────────────────────────────────────────────────────────
def test_name_person_duplicate_refused_without_merge(cat, repo, drive):
    alice, grp = cat.person("Alice"), cat.person()
    cat.face(_unit(0), "cluster", grp)
    p = build_parser()
    with pytest.raises(ValueError):
        _HANDLERS["name-person"](
            p.parse_args(["name-person", str(drive), str(grp), "alice"])
        )
    assert _person(repo, grp)["name"] is None
    # Renaming a person to its own name (any case) is not a duplicate.
    _HANDLERS["name-person"](
        p.parse_args(["name-person", str(drive), str(alice), "ALICE"])
    )
    assert _person(repo, alice)["name"] == "ALICE"


def test_name_person_merge_flag_merges(cat, repo, drive):
    alice, grp = cat.person("Alice"), cat.person()
    f = cat.face(_unit(0), "cluster", grp)
    p = build_parser()
    args = p.parse_args(["name-person", str(drive), str(grp), "alice", "--merge"])
    _HANDLERS["name-person"](args)
    assert _person(repo, grp) is None
    assert cat.row(f)["person_id"] == alice
    assert _person(repo, alice)["name"] == "Alice"


# ── split ────────────────────────────────────────────────────────────────────
@pytest.mark.faces
def test_split_keeps_closest_subgroup_and_rejects_the_rest(cat, repo):
    pytest.importorskip("sklearn")
    from smart_gallery.services.cluster_faces import split_person

    rng = np.random.default_rng(3)
    alice = cat.person("Alice")
    user = cat.face(_unit(200), "user", alice)  # Alice looks like the 2nd group
    a_ids = [cat.face(v, "auto", alice) for v in _jitter(_unit(0), rng, 6)]
    b_ids = [cat.face(v, "auto", alice) for v in _jitter(_unit(200), rng, 6)]
    stray = cat.face(_unit(400), "auto", alice)
    repo.recompute_person(alice)

    report = split_person(repo, alice, algo="dbscan", eps=0.3, min_samples=2)

    assert report.persons_created == 1
    assert cat.row(user)["person_id"] == alice
    assert {cat.row(f)["person_id"] for f in b_ids} == {alice}
    assert {cat.row(f)["state"] for f in b_ids} == {"auto"}
    new = {cat.row(f)["person_id"] for f in a_ids}
    assert len(new) == 1 and alice not in new
    (new_id,) = new
    assert _person(repo, new_id)["name"] is None
    assert {cat.row(f)["state"] for f in a_ids} == {"cluster"}
    # A face that fits no sub-group stays with the person, unrejected.
    assert (cat.row(stray)["state"], cat.row(stray)["person_id"]) == ("auto", alice)
    assert _rejections(repo) == {(f, alice) for f in a_ids}


@pytest.mark.faces
def test_split_with_only_noise_changes_nothing(cat, repo):
    pytest.importorskip("sklearn")
    from smart_gallery.services.cluster_faces import split_person

    alice = cat.person("Alice")
    for axis in (0, 100, 200, 300):
        cat.face(_unit(axis), "auto", alice)
    repo.recompute_person(alice)
    before = cat.snapshot()
    report = split_person(repo, alice, algo="dbscan", eps=0.3, min_samples=2)
    assert report.persons_created == 0 and report.noise == 0
    assert cat.snapshot() == before
    assert _rejections(repo) == set()


@pytest.mark.faces
def test_split_never_deletes_person_and_keeps_user_faces(cat, repo):
    pytest.importorskip("sklearn")
    from smart_gallery.services.cluster_faces import split_person

    rng = np.random.default_rng(4)
    grp = cat.person()
    user = cat.face(_unit(0), "user", grp)
    big = [cat.face(v, "cluster", grp) for v in _jitter(_unit(100), rng, 6)]
    small = [cat.face(v, "cluster", grp) for v in _jitter(_unit(300), rng, 3)]
    repo.recompute_person(grp)
    split_person(repo, grp, algo="dbscan", eps=0.3, min_samples=2)
    assert _person(repo, grp) is not None
    assert (cat.row(user)["state"], cat.row(user)["person_id"]) == ("user", grp)
    # Neither sub-group is near the user face; the closer one stays.
    stays = {cat.row(f)["person_id"] for f in big} | {
        cat.row(f)["person_id"] for f in small
    }
    assert grp in stays and len(stays) == 2

    # Only user faces: nothing to split, nothing deleted.
    only = cat.person("Solo")
    cat.face(_unit(5), "user", only)
    report = split_person(repo, only)
    assert report.persons_created == 0
    assert _person(repo, only) is not None


# ── rebuild ──────────────────────────────────────────────────────────────────
def test_rebuild_keeps_named_people_and_user_faces(cat, repo):
    alice, grp, hid = cat.person("Alice"), cat.person(), cat.person(hidden=True)
    u = cat.face(_unit(0), "user", alice)
    a = cat.face(_unit(1), "auto", alice)
    c = cat.face(_unit(2), "cluster", grp)
    s = cat.face(_unit(3), "suggested", None, suggested=alice)
    ig = cat.face(_unit(4), "ignored", hid)
    h = cat.face(_unit(5), "hidden", None)
    repo.conn.execute("INSERT INTO face_rejections VALUES (?, ?)", (a, alice))
    repo.conn.commit()

    repo.clear_persons()

    assert _person(repo, alice) is not None
    assert _person(repo, hid) is not None
    assert _person(repo, grp) is None
    assert (cat.row(u)["state"], cat.row(u)["person_id"]) == ("user", alice)
    assert (cat.row(ig)["state"], cat.row(ig)["person_id"]) == ("ignored", hid)
    for f in (a, c, s, h):
        row = cat.row(f)
        assert (row["state"], row["person_id"], row["suggested_person_id"]) == (
            "new",
            None,
            None,
        )
    assert _rejections(repo) == {(a, alice)}
    assert repo.get_person(alice)["face_count"] == 1


# ── identify ─────────────────────────────────────────────────────────────────
def test_identify_reassign_leaves_user_faces(cat, repo):
    alice, bob = cat.person("Alice"), cat.person("Bob")
    u = cat.face(_unit(1), "user", alice)  # unlike the reference, but the user said so
    a = cat.face(_unit(2), "auto", alice)  # unlike the reference: reset
    ig = cat.face(_unit(0), "ignored")
    rej = cat.face(_near(_unit(0), 0.9), "new")
    n = cat.face(_near(_unit(0), 0.9, axis=400), "new")
    repo.conn.execute("INSERT INTO face_rejections VALUES (?, ?)", (rej, alice))
    repo.conn.commit()

    refs = {"Alice": _unit(0), "Bob": _unit(300)}
    result = repo.identify_faces_from_samples(
        refs, threshold=0.5, margin=0.05, reassign=True
    )

    assert (cat.row(u)["state"], cat.row(u)["person_id"]) == ("user", alice)
    assert (cat.row(a)["state"], cat.row(a)["person_id"]) == ("new", None)
    assert (cat.row(ig)["state"], cat.row(ig)["person_id"]) == ("ignored", None)
    assert cat.row(rej)["person_id"] is None
    assert (cat.row(n)["state"], cat.row(n)["person_id"]) == ("auto", alice)
    assert result["assigned"] == 1
    assert repo.get_person(bob) is not None


# ── naming turns group faces into the person's faces ─────────────────────────
def test_naming_a_group_makes_its_faces_auto(cat, repo):
    grp = cat.person()
    c = cat.face(_unit(0), "cluster", grp)
    u = cat.face(_unit(1), "user", grp)
    repo.set_person_name(grp, "Alice")
    assert (cat.row(c)["state"], cat.row(u)["state"]) == ("auto", "user")


def test_merging_a_group_into_a_named_person_makes_its_faces_auto(cat, repo):
    alice, grp = cat.person("Alice"), cat.person()
    c = cat.face(_unit(0), "cluster", grp)
    repo.merge_persons(alice, [grp])
    assert (cat.row(c)["state"], cat.row(c)["person_id"]) == ("auto", alice)


def test_merging_into_an_unnamed_group_keeps_cluster(cat, repo):
    grp, other = cat.person(), cat.person()
    c = cat.face(_unit(0), "cluster", other)
    repo.merge_persons(grp, [other])
    assert cat.row(c)["state"] == "cluster"


def test_group_taking_a_named_sources_name_makes_faces_auto(cat, repo):
    grp, bob = cat.person(), cat.person("Bob")
    c = cat.face(_unit(0), "cluster", grp)
    repo.merge_persons(grp, [bob])
    assert cat.row(c)["state"] == "auto"


# ── people command ───────────────────────────────────────────────────────────
def test_people_command_marks_hidden_groups(cat, repo, drive, capsys):
    shown, hid = cat.person("Alice"), cat.person(hidden=True)
    cat.face(_unit(0), "auto", shown)
    cat.face(_unit(1), "ignored", hid)
    repo.recompute_person(shown)
    repo.recompute_person(hid)
    _HANDLERS["people"](build_parser().parse_args(["people", str(drive)]))
    lines = capsys.readouterr().out.splitlines()
    (alice,) = [ln for ln in lines if f"[{shown:>4}]" in ln]
    (hidden,) = [ln for ln in lines if f"[{hid:>4}]" in ln]
    assert "(hidden)" in hidden and "(hidden)" not in alice


def test_people_command_points_to_find_faces(repo, drive, monkeypatch):
    import smart_gallery.cli as cli

    said = []
    monkeypatch.setattr(cli.logger, "info", lambda msg, *a, **k: said.append(msg))
    _HANDLERS["people"](build_parser().parse_args(["people", str(drive)]))
    assert any("find-faces" in m for m in said)
    assert not any("cluster-faces" in m for m in said)
