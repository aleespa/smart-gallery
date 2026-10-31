"""Moving a photo's faces to another media row (CR3 -> embedded JPEG)."""

import numpy as np
import pytest
from PIL import Image

from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.faces_move import move_faces, rotate_box
from smart_gallery.models import Face, Person


@pytest.mark.parametrize(
    "o,expected",
    [
        (None, (10, 20, 30, 40)),
        (1, (10, 20, 30, 40)),
        (3, (6000 - 10 - 30, 4000 - 20 - 40, 30, 40)),
        (6, (4000 - 20 - 40, 10, 40, 30)),
        (8, (20, 6000 - 10 - 30, 40, 30)),
    ],
)
def test_rotate_box(o, expected):
    assert rotate_box((10, 20, 30, 40), o, 6000, 4000) == expected


@pytest.mark.parametrize("o", [2, 4, 5, 7])
def test_rotate_box_refuses_mirrored(o):
    with pytest.raises(ValueError):
        rotate_box((1, 2, 3, 4), o, 100, 50)


def test_six_then_crop_center_lands_on_same_pixel():
    w, h = 60, 40
    img = Image.new("L", (w, h), 0)
    x, y, bw, bh = 10, 5, 8, 6
    img.putpixel((x + bw // 2, y + bh // 2), 255)
    # Orientation 6 displays as a 90 CW rotation (PIL ROTATE_270)
    out = img.transpose(Image.Transpose.ROTATE_270)
    py, px = np.argwhere(np.array(out) == 255)[0]
    rx, ry, rw, rh = rotate_box((x, y, bw, bh), 6, w, h)
    assert rx <= px < rx + rw and ry <= py < ry + rh
    assert (out.width, out.height) == (h, w)


def _media(repo, make_item, relpath):
    repo.upsert_many([make_item(relpath)])
    return repo.conn.execute(
        "SELECT id FROM media_items WHERE relpath=?", (relpath,)
    ).fetchone()[0]


def _setup(repo, make_item):
    src = _media(repo, make_item, "a/IMG.CR3")
    dst = _media(repo, make_item, "a/IMG.JPG")
    emb = np.ones(512, dtype=np.float32) / np.sqrt(512)
    repo.add_scan_results(
        [(src, [Face.from_detection(src, (10, 20, 40, 60), 0.9, emb)], None)],
        FACE_DET_VERSION,
    )
    repo.conn.execute(
        "UPDATE face_scan_state SET img_w=6000, img_h=4000 WHERE media_id=?", (src,)
    )
    pid = repo.create_person(Person(cluster_id=1))
    repo.set_person_name(pid, "Ann")
    fid = repo.conn.execute("SELECT id FROM faces").fetchone()[0]
    repo.conn.execute(
        "UPDATE faces SET person_id=?, state='user' WHERE id=?", (pid, fid)
    )
    other = repo.create_person(Person(cluster_id=2))
    repo.conn.execute(
        "INSERT INTO face_rejections(face_id, person_id) VALUES (?,?)", (fid, other)
    )
    repo.conn.execute(
        "INSERT INTO face_carryover(media_id,bbox_x,bbox_y,bbox_w,bbox_h,img_w,img_h,"
        "state) VALUES (?,?,?,?,?,?,?,?)",
        (src, 10, 20, 30, 40, 6000, 4000, "user"),
    )
    repo.conn.commit()
    return src, dst, fid, pid, other


def _box(row):
    return (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"])


def test_move_keeps_ids_person_state_and_rejections(repo, make_item):
    src, dst, fid, pid, other = _setup(repo, make_item)
    assert move_faces(repo.conn, src, dst, 1, 6000, 4000) == [fid]
    row = repo.conn.execute("SELECT * FROM faces WHERE id=?", (fid,)).fetchone()
    assert row["media_id"] == dst
    assert (row["person_id"], row["state"]) == (pid, "user")
    assert _box(row) == (10, 20, 30, 40)
    rej = repo.conn.execute(
        "SELECT person_id FROM face_rejections WHERE face_id=?", (fid,)
    ).fetchone()
    assert rej[0] == other
    assert not repo.conn.execute(
        "SELECT 1 FROM faces WHERE media_id=?", (src,)
    ).fetchall()


def test_move_updates_scan_state_and_carryover_for_portrait(repo, make_item):
    src, dst, fid, *_ = _setup(repo, make_item)
    # a stale scan row on dst must be replaced
    repo.conn.execute(
        "INSERT INTO face_scan_state(media_id, n_faces) VALUES (?,0)", (dst,)
    )
    move_faces(repo.conn, src, dst, 6, 6000, 4000)
    expected = (4000 - 20 - 40, 10, 40, 30)
    row = repo.conn.execute("SELECT * FROM faces WHERE id=?", (fid,)).fetchone()
    assert _box(row) == expected
    st = repo.conn.execute("SELECT * FROM face_scan_state").fetchall()
    assert len(st) == 1 and st[0]["media_id"] == dst
    assert (st[0]["img_w"], st[0]["img_h"]) == (4000, 6000)
    assert st[0]["det_version"] == FACE_DET_VERSION and st[0]["n_faces"] == 1
    co = repo.conn.execute("SELECT * FROM face_carryover").fetchone()
    assert co["media_id"] == dst and (co["img_w"], co["img_h"]) == (4000, 6000)
    assert _box(co) == expected


def test_move_does_not_commit(repo, make_item):
    src, dst, fid, *_ = _setup(repo, make_item)
    move_faces(repo.conn, src, dst, 6, 6000, 4000)
    repo.conn.rollback()
    row = repo.conn.execute("SELECT * FROM faces WHERE id=?", (fid,)).fetchone()
    assert row["media_id"] == src and row["bbox_x"] == 10
    st = repo.conn.execute("SELECT * FROM face_scan_state").fetchone()
    assert st["media_id"] == src and st["img_w"] == 6000
    co = repo.conn.execute("SELECT * FROM face_carryover").fetchone()
    assert co["media_id"] == src and co["img_w"] == 6000
