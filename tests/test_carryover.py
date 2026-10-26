"""Face corrections survive a re-scan; scan saves crops; find-faces is wired."""

from types import SimpleNamespace

import numpy as np
import pytest

from smart_gallery.cli import _HANDLERS, DETAIL_LOG_COMMANDS, build_parser
from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.face_crops import crop_path
from smart_gallery.models import Face, Person

SIZE = (1000, 800)
BOX_A = (100, 100, 200, 220)  # x1, y1, x2, y2
BOX_B = (500, 300, 600, 420)


def _unit(i: int) -> np.ndarray:
    v = np.zeros(512, dtype=np.float32)
    v[i] = 1.0
    return v


def _media(repo, make_item, relpath="a.jpg") -> int:
    repo.upsert_many([make_item(relpath)])
    return repo.conn.execute(
        "SELECT id FROM media_items WHERE relpath=?", (relpath,)
    ).fetchone()[0]


def _scan(repo, mid, boxes, size=SIZE, version=FACE_DET_VERSION):
    faces = [Face.from_detection(mid, b, 0.9, _unit(i)) for i, b in enumerate(boxes)]
    repo.add_scan_results([(mid, faces, size)], version)
    return faces


def _face_at(repo, mid, x):
    return repo.conn.execute(
        "SELECT * FROM faces WHERE media_id=? AND bbox_x=?", (mid, float(x))
    ).fetchone()


def _set(repo, face_id, state, person_id):
    repo.conn.execute(
        "UPDATE faces SET state=?, person_id=? WHERE id=?", (state, person_id, face_id)
    )
    repo.conn.commit()


def _carryover_rows(repo) -> int:
    return repo.conn.execute("SELECT COUNT(*) FROM face_carryover").fetchone()[0]


def test_scan_sets_ids_and_image_size(repo, make_item):
    mid = _media(repo, make_item)
    faces = _scan(repo, mid, [BOX_A, BOX_B])
    ids = [r[0] for r in repo.conn.execute("SELECT id FROM faces ORDER BY id")]
    assert [f.id for f in faces] == ids
    row = repo.conn.execute(
        "SELECT img_w, img_h FROM face_scan_state WHERE media_id=?", (mid,)
    ).fetchone()
    assert tuple(row) == SIZE


def test_metadata_edit_keeps_user_ignored_and_rejection(repo, make_item):
    mid = _media(repo, make_item)
    alice = repo.create_person(Person(name="Alice"))
    bob = repo.create_person(Person(name="Bob"))
    a, b = _scan(repo, mid, [BOX_A, BOX_B])
    _set(repo, a.id, "user", alice)
    _set(repo, b.id, "ignored", None)
    repo.conn.execute("INSERT INTO face_rejections VALUES (?, ?)", (a.id, bob))
    repo.conn.commit()

    repo.invalidate_faces_by_relpath(["a.jpg"])
    assert repo.faces_count() == 0
    assert _carryover_rows(repo) == 2

    _scan(repo, mid, [BOX_A, BOX_B])
    assert repo.last_carryover == (2, 0)
    new_a, new_b = _face_at(repo, mid, BOX_A[0]), _face_at(repo, mid, BOX_B[0])
    assert (new_a["state"], new_a["person_id"]) == ("user", alice)
    assert (new_b["state"], new_b["person_id"]) == ("ignored", None)
    rejections = repo.conn.execute(
        "SELECT face_id, person_id FROM face_rejections"
    ).fetchall()
    assert [tuple(r) for r in rejections] == [(new_a["id"], bob)]
    assert _carryover_rows(repo) == 0
    assert repo.get_person(alice)["face_count"] == 1


def test_moved_face_is_dropped_and_counted(repo, make_item):
    mid = _media(repo, make_item)
    alice = repo.create_person(Person(name="Alice"))
    (a,) = _scan(repo, mid, [BOX_A])
    _set(repo, a.id, "user", alice)

    repo.invalidate_faces_by_relpath(["a.jpg"])
    _scan(repo, mid, [(160, 160, 260, 280)])  # IoU with BOX_A < 0.5
    assert repo.last_carryover == (0, 1)
    row = repo.conn.execute("SELECT state, person_id FROM faces").fetchone()
    assert tuple(row) == ("new", None)
    assert _carryover_rows(repo) == 0


def test_resized_image_still_matches(repo, make_item):
    mid = _media(repo, make_item)
    alice = repo.create_person(Person(name="Alice"))
    (a,) = _scan(repo, mid, [BOX_A])
    _set(repo, a.id, "user", alice)

    repo.invalidate_faces_by_relpath(["a.jpg"])
    doubled = tuple(v * 2 for v in BOX_A)
    _scan(repo, mid, [doubled], size=(SIZE[0] * 2, SIZE[1] * 2))
    assert repo.last_carryover == (1, 0)
    row = repo.conn.execute("SELECT state, person_id FROM faces").fetchone()
    assert tuple(row) == ("user", alice)


def test_user_face_of_deleted_person_is_dropped(repo, make_item):
    mid = _media(repo, make_item)
    group = repo.create_person(Person())
    hidden = repo.create_person(Person(hidden=True))
    a, b = _scan(repo, mid, [BOX_A, BOX_B])
    _set(repo, a.id, "user", group)
    _set(repo, b.id, "ignored", hidden)

    repo.invalidate_faces_by_relpath(["a.jpg"])
    repo.conn.execute("DELETE FROM persons")
    repo.conn.commit()
    _scan(repo, mid, [BOX_A, BOX_B])
    assert repo.last_carryover == (1, 1)
    assert tuple(
        repo.conn.execute(
            "SELECT state, person_id FROM faces WHERE bbox_x=?", (float(BOX_B[0]),)
        ).fetchone()
    ) == ("ignored", None)


def test_decode_failure_keeps_faces_and_stash(repo, make_item):
    mid = _media(repo, make_item)
    other = _media(repo, make_item, "b.jpg")
    alice = repo.create_person(Person(name="Alice"))
    (a,) = _scan(repo, mid, [BOX_A])
    (b,) = _scan(repo, other, [BOX_B])
    _set(repo, a.id, "user", alice)
    _set(repo, b.id, "user", alice)

    # a.jpg: stashed by invalidate, then its decode fails.
    repo.invalidate_faces_by_relpath(["a.jpg"])
    repo.add_scan_results([(mid, [], None)], FACE_DET_VERSION)
    assert repo.last_carryover == (0, 0)
    assert _carryover_rows(repo) == 1
    assert repo.count_unscanned_images(FACE_DET_VERSION) == 0  # not retried

    # b.jpg: a version bump whose decode fails keeps the existing face.
    repo.add_scan_results([(other, [], None)], "v2")
    assert tuple(
        repo.conn.execute(
            "SELECT state, person_id FROM faces WHERE media_id=?", (other,)
        ).fetchone()
    ) == ("user", alice)
    assert _carryover_rows(repo) == 1

    # A later successful scan of a.jpg still carries the decision.
    _scan(repo, mid, [BOX_A])
    assert repo.last_carryover == (1, 0)
    assert _face_at(repo, mid, BOX_A[0])["person_id"] == alice
    assert _carryover_rows(repo) == 0


def test_rescan_keeps_named_people(repo, make_item):
    mid = _media(repo, make_item)
    alice = repo.create_person(Person(name="Alice"))
    hidden = repo.create_person(Person(hidden=True))
    group = repo.create_person(Person())
    a, b = _scan(repo, mid, [BOX_A, BOX_B])
    _set(repo, a.id, "user", alice)
    _set(repo, b.id, "cluster", group)

    repo.reset_all_faces()
    persons = {r[0] for r in repo.conn.execute("SELECT id FROM persons")}
    assert persons == {alice, hidden}
    assert repo.faces_count() == 0
    assert repo.count_unscanned_images(FACE_DET_VERSION) == 1

    _scan(repo, mid, [BOX_A, BOX_B])
    assert repo.last_carryover == (1, 0)
    row = _face_at(repo, mid, BOX_A[0])
    assert (row["state"], row["person_id"]) == ("user", alice)
    assert _face_at(repo, mid, BOX_B[0])["state"] == "new"


def test_version_bump_does_not_duplicate_faces(repo, make_item):
    mid = _media(repo, make_item)
    alice = repo.create_person(Person(name="Alice"))
    a, _ = _scan(repo, mid, [BOX_A, BOX_B], version="v1")
    _set(repo, a.id, "user", alice)

    _scan(repo, mid, [BOX_A, BOX_B], version="v2")
    assert repo.faces_count() == 2
    assert repo.last_carryover == (1, 0)
    assert _face_at(repo, mid, BOX_A[0])["person_id"] == alice
    assert repo.count_unscanned_images("v2") == 0


BOXES = [(100 + 250 * i, 100, 200 + 250 * i, 220) for i in range(3)]


def _mum_with_three_auto_faces(repo, make_item, version=FACE_DET_VERSION):
    mid = _media(repo, make_item)
    mum = repo.create_person(Person(name="Mum"))
    faces = _scan(repo, mid, BOXES, version=version)
    for f, state in zip(faces, ("auto", "auto", "cluster")):
        _set(repo, f.id, state, mum)
    repo.recompute_person(mum)
    return mid, mum


def _states_of(repo, person_id):
    return sorted(
        r[0]
        for r in repo.conn.execute(
            "SELECT state FROM faces WHERE person_id=?", (person_id,)
        )
    )


def test_rescan_gives_a_named_person_its_auto_faces_back(repo, make_item):
    from smart_gallery.services.file_faces import file_faces

    mid, mum = _mum_with_three_auto_faces(repo, make_item)
    repo.reset_all_faces()
    _scan(repo, mid, BOXES)
    assert repo.last_carryover == (3, 0)
    file_faces(repo, group=False)
    assert _states_of(repo, mum) == ["auto", "auto", "auto"]
    assert repo.get_person(mum)["face_count"] == 3


def test_version_bump_gives_a_named_person_its_auto_faces_back(repo, make_item):
    from smart_gallery.services.file_faces import file_faces

    mid, mum = _mum_with_three_auto_faces(repo, make_item, version="v1")
    _scan(repo, mid, BOXES, version="v2")
    file_faces(repo, group=False)
    assert _states_of(repo, mum) == ["auto", "auto", "auto"]
    assert repo.get_person(mum)["face_count"] == 3


def test_unnamed_group_faces_are_not_stashed(repo, make_item):
    mid = _media(repo, make_item)
    group = repo.create_person(Person())
    (a,) = _scan(repo, mid, [BOX_A])
    _set(repo, a.id, "cluster", group)
    repo.reset_all_faces()
    assert _carryover_rows(repo) == 0


def test_user_stash_wins_over_auto_stash(repo, make_item):
    mid = _media(repo, make_item)
    alice = repo.create_person(Person(name="Alice"))
    bob = repo.create_person(Person(name="Bob"))
    a, b = _scan(repo, mid, [BOX_A, (110, 110, 210, 230)])
    _set(repo, a.id, "auto", alice)
    _set(repo, b.id, "user", bob)
    repo.reset_all_faces()
    _scan(repo, mid, [BOX_A])  # one new face overlaps both old ones
    row = _face_at(repo, mid, BOX_A[0])
    assert (row["state"], row["person_id"]) == ("user", bob)


class _FakeScanner:
    provider = "fake"

    def decode(self, path):
        return np.full((SIZE[1], SIZE[0], 3), 128, dtype=np.uint8)

    def detect(self, img):
        if img is None:
            return []
        return [SimpleNamespace(bbox=BOX_A, det_score=0.9, embedding=_unit(0))]


def test_scan_writes_crops_and_carries(repo, make_item, drive, monkeypatch):
    pytest.importorskip("cv2")
    import importlib

    scan_mod = importlib.import_module("smart_gallery.services.scan_faces")

    monkeypatch.setattr(scan_mod, "FaceScanner", _FakeScanner)
    _media(repo, make_item)
    alice = repo.create_person(Person(name="Alice"))
    report = scan_mod.scan_faces(repo)
    assert report.faces_found == 1
    fid = repo.conn.execute("SELECT id FROM faces").fetchone()[0]
    assert crop_path(drive, fid).is_file()
    assert (
        tuple(repo.conn.execute("SELECT img_w, img_h FROM face_scan_state").fetchone())
        == SIZE
    )

    _set(repo, fid, "user", alice)
    report = scan_mod.scan_faces(repo, rescan=True)
    assert (report.decisions_carried, report.decisions_dropped) == (1, 0)
    assert repo.conn.execute("SELECT person_id FROM faces").fetchone()[0] == alice


def test_cli_find_faces_wired():
    args = build_parser().parse_args(["find-faces", "E:/"])
    assert args.command == "find-faces"
    assert "find-faces" in _HANDLERS
    assert "find-faces" in DETAIL_LOG_COMMANDS
