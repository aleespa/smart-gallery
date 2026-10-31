"""raw-to-jpeg against the real R8 CR3 fixture. Needs ExifTool on PATH.

The fixture's embedded JPEG has no Orientation tag of its own, so the face
scanner saw it in the stored 6000x4000 frame: its boxes must be rotated by the
CR3's Orientation (6) when they move to the JPEG.
"""

import json
import subprocess

import numpy as np
import pytest
from PIL import Image

from smart_gallery.analysis.extract import analyze_paths
from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.faces_move import rotate_box
from smart_gallery.models import Face, Person
from smart_gallery.services import raw_to_jpeg
from smart_gallery.services.raw_to_jpeg import replace_raws
from smart_gallery.services.recycle import RecycleEntry

pytestmark = pytest.mark.exiftool


def _tags(path):
    out = subprocess.run(
        ["exiftool", "-j", "-n", str(path)], capture_output=True, check=True
    )
    return json.loads(out.stdout)[0]


def test_real_cr3_portrait(repo, drive, copy_image, monkeypatch):
    recycled = []

    def fake_move(path):
        recycled.append(path)
        path.unlink()

    monkeypatch.setattr(raw_to_jpeg, "supports_recycle_bin", lambda p: True)
    monkeypatch.setattr(raw_to_jpeg, "recycle_bin_capacity", lambda p: None)
    monkeypatch.setattr(raw_to_jpeg, "move_to_recycle_bin", fake_move)

    cr3 = copy_image(drive, "test.CR3", "Shots/IMG.CR3")
    repo.upsert_many(analyze_paths([cr3], drive_root=drive))
    src_tags = _tags(cr3)
    w, h = src_tags["ImageWidth"], src_tags["ImageHeight"]
    assert (w, h, src_tags["Orientation"]) == (6000, 4000, 6)
    src = repo.conn.execute(
        "SELECT id FROM media_items WHERE relpath='Shots/IMG.CR3'"
    ).fetchone()[0]

    old_box = (1000.0, 500.0, 300.0, 400.0)
    emb = np.ones(512, dtype=np.float32) / np.sqrt(512)
    x, y, bw, bh = old_box  # from_detection takes corners
    face = Face.from_detection(src, (x, y, x + bw, y + bh), 0.9, emb)
    repo.add_scan_results([(src, [face], (w, h))], FACE_DET_VERSION)
    pid = repo.create_person(Person(cluster_id=1))
    repo.set_person_name(pid, "Ann")
    other = repo.create_person(Person(cluster_id=2))
    repo.conn.execute(
        "UPDATE faces SET person_id=?, state='user' WHERE id=?", (pid, face.id)
    )
    repo.conn.execute(
        "INSERT INTO face_rejections(face_id, person_id) VALUES (?,?)",
        (face.id, other),
    )
    repo.conn.commit()
    st = cr3.stat()

    report = replace_raws(
        repo, [RecycleEntry("Shots/IMG.CR3", st.st_size, st.st_mtime_ns)]
    )

    assert report.failures == [] and report.converted == 1
    jpeg = drive / "Shots/IMG.JPG"
    assert jpeg.is_file() and recycled == [cr3]
    assert jpeg.stat().st_mtime_ns == st.st_mtime_ns
    tags = _tags(jpeg)
    assert tags["Orientation"] == 6
    for tag in ("Model", "LensModel", "DateTimeOriginal"):
        assert tags[tag] == src_tags[tag], tag
    with Image.open(jpeg) as img:
        assert img.size == (w, h)

    row = repo.conn.execute(
        "SELECT m.relpath, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, f.person_id, "
        "f.state FROM faces f JOIN media_items m ON m.id=f.media_id WHERE f.id=?",
        (face.id,),
    ).fetchone()
    assert row["relpath"] == "Shots/IMG.JPG"
    assert (row[1], row[2], row[3], row[4]) == rotate_box(old_box, 6, w, h)
    assert (row["person_id"], row["state"]) == (pid, "user")
    assert (
        repo.conn.execute(
            "SELECT person_id FROM face_rejections WHERE face_id=?", (face.id,)
        ).fetchone()[0]
        == other
    )
    dims = repo.conn.execute(
        "SELECT s.img_w, s.img_h FROM face_scan_state s "
        "JOIN media_items m ON m.id=s.media_id WHERE m.relpath='Shots/IMG.JPG'"
    ).fetchone()
    assert tuple(dims) == (h, w)
    assert repo.index_for_sync().keys() == {"Shots/IMG.JPG"}
