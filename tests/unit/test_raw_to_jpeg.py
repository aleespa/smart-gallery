"""The raw-to-jpeg job: a CR3 becomes its camera JPEG, keeping its faces.

ExifTool is faked: ``extract_embedded`` writes a small real JPEG with Pillow,
``_copy_metadata`` copies the CR3's (faked) Orientation, ``_read_tags`` returns
fixed sizes and orientations, and ``analyze_paths`` builds catalog items from
``stat``. The Recycle Bin is a recorder.
"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from smart_gallery import cli
from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.face_crops import crop_path
from smart_gallery.faces_move import rotate_box
from smart_gallery.mirror.types import Peer
from smart_gallery.models import Face, Person
from smart_gallery.progress import CONVERTING, Cancelled
from smart_gallery.services import raw_to_jpeg
from smart_gallery.services.raw_to_jpeg import replace_raws
from smart_gallery.services.recycle import RecycleEntry, write_list

W, H = 60, 40


class Fake:
    """The ExifTool and Recycle Bin stand-ins, configurable per test."""

    def __init__(self):
        self.recycled: list[Path] = []
        self.cr3_orientation = 1
        self.preview_orientation = None  # the embedded JPEG's own tag
        self.embedded_size: dict[str, tuple[int, int] | None] = {}  # by CR3 name
        self.fail_recycle: set[str] = set()
        self.extract_calls = 0

    def move(self, path):
        if path.name in self.fail_recycle:
            raise OSError("Recycle Bin refused")
        self.recycled.append(path)
        path.unlink()

    def extract(self, sources, suffix=".jpg"):
        self.extract_calls += 1
        for source in sources:
            size = self.embedded_size.get(source.name, (W, H))
            out = source.with_suffix(suffix)
            if size is None or out.exists():  # -w never overwrites
                continue
            exif = Image.Exif()
            if self.preview_orientation is not None:
                exif[0x0112] = self.preview_orientation
            Image.new("RGB", size, "gray").save(out, "JPEG", exif=exif)
        return ""

    def copy_metadata(self, pairs):
        for _source, output in pairs:
            with Image.open(output) as img:
                img.load()
                exif = img.getexif()
                exif[0x0112] = self.cr3_orientation
                img.save(output, "JPEG", exif=exif)
        return []

    def read_tags(self, paths):
        out = {}
        for p in paths:
            if p.suffix.lower() == ".cr3":
                out[p] = {
                    "ImageWidth": W,
                    "ImageHeight": H,
                    "Orientation": self.cr3_orientation,
                }
            elif self.preview_orientation is not None:
                out[p] = {"Orientation": self.preview_orientation}
            else:
                out[p] = {}
        return out


@pytest.fixture
def fake(monkeypatch, make_item):
    f = Fake()

    def fake_analyze(paths, drive_root=None, **_kw):
        items = []
        for p in paths:
            st = Path(p).stat()
            rel = Path(p).relative_to(drive_root).as_posix()
            items.append(make_item(rel, size_bytes=st.st_size, mtime_ns=st.st_mtime_ns))
        return items

    monkeypatch.setattr(raw_to_jpeg, "supports_recycle_bin", lambda p: True)
    monkeypatch.setattr(raw_to_jpeg, "recycle_bin_capacity", lambda p: None)
    monkeypatch.setattr(raw_to_jpeg, "move_to_recycle_bin", f.move)
    monkeypatch.setattr(raw_to_jpeg, "extract_embedded", f.extract)
    monkeypatch.setattr(raw_to_jpeg, "_copy_metadata", f.copy_metadata)
    monkeypatch.setattr(raw_to_jpeg, "_read_tags", f.read_tags)
    monkeypatch.setattr(raw_to_jpeg, "analyze_paths", fake_analyze)
    return f


def _catalogued(repo, drive, make_item, relpath, content=b"r" * 64):
    path = drive / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    st = path.stat()
    repo.upsert_many(
        [make_item(relpath, size_bytes=st.st_size, mtime_ns=st.st_mtime_ns)]
    )
    return path, RecycleEntry(relpath, st.st_size, st.st_mtime_ns)


def _media_id(repo, relpath):
    row = repo.conn.execute(
        "SELECT id FROM media_items WHERE relpath=?", (relpath,)
    ).fetchone()
    return None if row is None else row[0]


def _add_face(repo, media_id, box=(10, 5, 8, 6), img_size=(W, H)):
    emb = np.ones(512, dtype=np.float32) / np.sqrt(512)
    x, y, w, h = box  # from_detection takes corners
    face = Face.from_detection(media_id, (x, y, x + w, y + h), 0.9, emb)
    repo.add_scan_results([(media_id, [face], img_size)], FACE_DET_VERSION)
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
    return face.id, pid, other


def _face(repo, fid):
    return repo.conn.execute(
        "SELECT media_id, bbox_x, bbox_y, bbox_w, bbox_h, person_id, state "
        "FROM faces WHERE id=?",
        (fid,),
    ).fetchone()


def _scan_dims(repo, media_id):
    row = repo.conn.execute(
        "SELECT img_w, img_h FROM face_scan_state WHERE media_id=?", (media_id,)
    ).fetchone()
    return None if row is None else tuple(row)


def test_converts_and_moves_faces(repo, drive, make_item, fake):
    fake.cr3_orientation = 6  # preview has no tag: boxes rotate
    cr3, entry = _catalogued(repo, drive, make_item, "a/IMG_1.CR3")
    src = _media_id(repo, "a/IMG_1.CR3")
    fid, pid, other = _add_face(repo, src)
    crop = crop_path(drive, fid)
    crop.parent.mkdir(parents=True, exist_ok=True)
    crop.write_bytes(b"old crop")
    events = []

    report = replace_raws(repo, [entry], progress_callback=events.append)

    jpeg = drive / "a/IMG_1.JPG"
    assert (report.converted, report.skipped, report.failures) == (1, 0, [])
    assert report.faces_moved == 1
    assert jpeg.is_file() and not cr3.exists()
    assert fake.recycled == [cr3]
    assert jpeg.stat().st_mtime_ns == entry.mtime_ns
    assert _media_id(repo, "a/IMG_1.CR3") is None
    dst = _media_id(repo, "a/IMG_1.JPG")
    assert dst is not None
    row = _face(repo, fid)
    assert row["media_id"] == dst
    box = (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"])
    assert box == rotate_box((10, 5, 8, 6), 6, W, H)
    assert (row["person_id"], row["state"]) == (pid, "user")
    assert (
        repo.conn.execute(
            "SELECT person_id FROM face_rejections WHERE face_id=?", (fid,)
        ).fetchone()[0]
        == other
    )
    assert _scan_dims(repo, dst) == (H, W)
    assert not crop.exists()
    assert report.freed_bytes == entry.size_bytes - jpeg.stat().st_size
    assert events[-1].step == CONVERTING and events[-1].done == events[-1].total == 1
    assert events[-1].outcomes == (("converted", 1), ("skipped", 0), ("failed", 0))


def test_preview_with_own_orientation_keeps_boxes(repo, drive, make_item, fake):
    fake.cr3_orientation = 6
    fake.preview_orientation = 6  # the scan already saw the oriented frame
    _, entry = _catalogued(repo, drive, make_item, "IMG_2.CR3")
    src = _media_id(repo, "IMG_2.CR3")
    fid, _, _ = _add_face(repo, src, box=(3, 4, 5, 6), img_size=(H, W))

    report = replace_raws(repo, [entry])

    assert report.converted == 1 and report.failures == []
    dst = _media_id(repo, "IMG_2.JPG")
    row = _face(repo, fid)
    assert row["media_id"] == dst
    assert (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]) == (
        3,
        4,
        5,
        6,
    )
    assert _scan_dims(repo, dst) == (H, W)


def test_face_frame_mismatch_keeps_cr3(repo, drive, make_item, fake):
    fake.cr3_orientation = 6  # no preview tag: the scan should be W x H ...
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_3.CR3")
    src = _media_id(repo, "IMG_3.CR3")
    fid, _, _ = _add_face(repo, src, img_size=(H, W))  # ... but it says H x W

    report = replace_raws(repo, [entry])

    assert report.converted == 0
    assert len(report.failures) == 1 and "face frame mismatch" in report.failures[0]
    assert cr3.exists() and fake.recycled == []
    assert not (drive / "IMG_3.JPG").exists()
    assert _media_id(repo, "IMG_3.JPG") is None
    assert _face(repo, fid)["media_id"] == src


def test_existing_jpeg_on_disk_is_skipped(repo, drive, make_item, fake):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_4.CR3")
    existing = drive / "img_4.jpeg"
    existing.write_bytes(b"keep me")

    report = replace_raws(repo, [entry])

    assert (report.converted, report.skipped) == (0, 1)
    assert existing.read_bytes() == b"keep me"
    assert cr3.exists() and fake.recycled == []
    assert _media_id(repo, "IMG_4.CR3") is not None
    assert not (drive / "IMG_4.JPG").exists()
    assert fake.extract_calls == 0


def test_non_cr3_and_changed_files_are_skipped(repo, drive, make_item, fake):
    jpg, a = _catalogued(repo, drive, make_item, "x.CR2")
    cr3, b = _catalogued(repo, drive, make_item, "y.CR3")
    cr3.write_bytes(b"changed")

    report = replace_raws(repo, [a, b])

    assert (report.converted, report.skipped) == (0, 2)
    assert fake.recycled == [] and fake.extract_calls == 0


def test_missing_embedded_image_keeps_cr3(repo, drive, make_item, fake):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_5.CR3")
    fake.embedded_size["IMG_5.CR3"] = None

    report = replace_raws(repo, [entry])

    assert report.converted == 0
    assert len(report.failures) == 1 and "IMG_5.CR3" in report.failures[0]
    assert cr3.exists() and fake.recycled == []
    assert list(drive.glob("IMG_5.*")) == [cr3]


def test_wrong_size_jpeg_keeps_cr3(repo, drive, make_item, fake):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_6.CR3")
    good, other = _catalogued(repo, drive, make_item, "IMG_7.CR3")
    fake.embedded_size["IMG_6.CR3"] = (W // 2, H // 2)  # a small preview

    report = replace_raws(repo, [entry, other])

    assert report.converted == 1
    assert len(report.failures) == 1 and "IMG_6.CR3" in report.failures[0]
    assert cr3.exists() and not (drive / "IMG_6.JPG").exists()
    assert fake.recycled == [good]


def test_recycle_failure_rolls_back(repo, drive, make_item, fake):
    fake.cr3_orientation = 6
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_8.CR3")
    src = _media_id(repo, "IMG_8.CR3")
    fid, pid, _ = _add_face(repo, src)
    before = tuple(_face(repo, fid))
    fake.fail_recycle.add("IMG_8.CR3")

    report = replace_raws(repo, [entry])

    assert report.converted == 0
    assert len(report.failures) == 1 and "Recycle Bin refused" in report.failures[0]
    assert cr3.exists() and not (drive / "IMG_8.JPG").exists()
    assert _media_id(repo, "IMG_8.CR3") == src
    assert _media_id(repo, "IMG_8.JPG") is None
    assert tuple(_face(repo, fid)) == before
    assert _scan_dims(repo, src) == (W, H)


def test_cancel_mid_batch_leaves_no_stray_jpegs(repo, drive, make_item, fake):
    paths, entries = zip(
        *(_catalogued(repo, drive, make_item, f"IMG_{i}.CR3") for i in range(3))
    )

    def cb(event):
        if event.done >= 1:
            raise Cancelled()

    with pytest.raises(Cancelled):
        replace_raws(repo, list(entries), progress_callback=cb)

    assert fake.recycled == [paths[0]]
    assert (drive / "IMG_0.JPG").exists()
    assert not (drive / "IMG_1.JPG").exists() and not (drive / "IMG_2.JPG").exists()
    assert paths[1].exists() and paths[2].exists()
    assert repo.index_for_sync().keys() == {"IMG_0.JPG", "IMG_1.CR3", "IMG_2.CR3"}


def test_xmp_goes_with_cr3(repo, drive, make_item, fake):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_9.CR3")
    xmp, _ = _catalogued(repo, drive, make_item, "IMG_9.xmp", b"<x/>")

    report = replace_raws(repo, [entry])

    assert report.converted == 1
    assert fake.recycled == [cr3, xmp]
    assert repo.index_for_sync().keys() == {"IMG_9.JPG"}


def test_refuses_on_mirror(repo, drive, make_item, fake):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_10.CR3")
    repo.add_peer("mirror", Peer("u" * 32, "x.db"))

    with pytest.raises(ValueError, match="mirror"):
        replace_raws(repo, [entry])

    assert cr3.exists() and fake.extract_calls == 0


def test_refuses_without_recycle_bin(repo, drive, make_item, fake, monkeypatch):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_11.CR3")
    monkeypatch.setattr(raw_to_jpeg, "supports_recycle_bin", lambda p: False)

    with pytest.raises(ValueError, match="Recycle Bin is not available"):
        replace_raws(repo, [entry])

    assert cr3.exists() and fake.extract_calls == 0


def test_too_big_for_recycle_bin_fails(repo, drive, make_item, fake, monkeypatch):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_12.CR3")
    monkeypatch.setattr(raw_to_jpeg, "recycle_bin_capacity", lambda p: 10)

    report = replace_raws(repo, [entry])

    assert report.converted == 0 and "too big" in report.failures[0]
    assert cr3.exists() and fake.extract_calls == 0


def test_cli_deletes_list_once_started(
    repo, drive, make_item, fake, monkeypatch, tmp_path
):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_13.CR3")
    list_file = tmp_path / "raw.txt"
    write_list(list_file, [entry])
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "logs")

    cli.main(["raw-to-jpeg", str(drive), "--list", str(list_file)])

    assert not list_file.exists()
    assert not cr3.exists() and (drive / "IMG_13.JPG").exists()


def test_cli_keeps_list_when_refused(
    repo, drive, make_item, fake, monkeypatch, tmp_path
):
    cr3, entry = _catalogued(repo, drive, make_item, "IMG_14.CR3")
    list_file = tmp_path / "raw.txt"
    write_list(list_file, [entry])
    monkeypatch.setattr(raw_to_jpeg, "supports_recycle_bin", lambda p: False)
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "logs")

    with pytest.raises(SystemExit) as exc:
        cli.main(["raw-to-jpeg", str(drive), "--list", str(list_file)])

    assert exc.value.code == 1
    assert list_file.exists() and cr3.exists()
