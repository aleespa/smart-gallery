"""The viewer's info panel content and the people in a photo (no Qt)."""

from smart_gallery.gui.viewer.info import (
    PhotoPeople,
    build_info,
    format_size,
    photo_people,
)

_BLOB = b"\x00" * 8


def _sections(item, people=None):
    return {s.key: s for s in build_info(item, people)}


def test_info_sections_for_a_photo(make_item):
    item = make_item(
        "2026/06/IMG_4512.CR3",
        date_taken="2026-06-14",
        time_taken="17:42:05",
        camera="Canon EOS R6",
        lens="RF 24-105mm F4 L",
        aperture=2.8,
        shutter_speed_sec=0.004,
        shutter_speed_text=None,
        iso=400,
        focal_length=50.0,
        width=6000,
        height=4000,
        size_bytes=29_779_558,
    )
    sections = build_info(item, PhotoPeople(("Ana", "Luis"), 1, ()))
    s = {x.key: x for x in sections}
    assert [x.key for x in sections] == ["date", "exposure", "camera", "people", "file"]
    assert s["date"].heading == "14 June 2026"
    assert s["date"].lines == ("Sunday · 17:42",)
    assert s["exposure"].chips == ("f/2.8", "1/250 s", "ISO 400", "50 mm")
    assert s["camera"].lines == ("Canon EOS R6", "RF 24-105mm F4 L")
    assert s["people"].accent_chips == ("Ana", "Luis")
    assert s["people"].chips == ("+1 unnamed",)
    assert s["file"].lines == ("6000 × 4000 · 28.4 MB", "2026/06/IMG_4512.CR3")


def test_shutter_text_from_exif_gets_a_space(make_item):
    s = _sections(make_item("a.jpg", shutter_speed_text="1/250s"))
    assert "1/250 s" in s["exposure"].chips


def test_info_without_metadata(make_item):
    item = make_item(
        "a.jpg",
        date_taken=None,
        camera=None,
        lens=None,
        aperture=None,
        iso=None,
        shutter_speed_sec=None,
        shutter_speed_text=None,
    )
    s = _sections(item)
    assert s["date"].heading == "Date unknown" and s["date"].lines == ()
    assert "exposure" not in s and "camera" not in s and "people" not in s
    assert s["file"].lines == ("977 KB", "a.jpg")


def test_people_section_only_unnamed(make_item):
    s = _sections(make_item("a.jpg"), PhotoPeople((), 2, ()))
    assert s["people"].accent_chips == () and s["people"].chips == ("+2 unnamed",)
    assert "people" not in _sections(make_item("a.jpg"), PhotoPeople((), 0, ()))


def test_info_video_section(make_item):
    video = make_item(
        "a.mp4",
        media_type="video",
        ext=".mp4",
        duration_ms=83_000,
        codec="h264",
        frame_rate=29.97,
    )
    s = _sections(video)
    assert s["video"].lines == ("1:23", "h264 · 29.97 fps")
    long = _sections(make_item("b.mp4", media_type="video", duration_ms=3_723_000))
    assert long["video"].lines[0] == "1:02:03"


def test_location_line(make_item):
    s = _sections(make_item("a.jpg", latitude=19.4326077, longitude=-99.133208))
    assert s["file"].lines[-1] == "19.43261, -99.13321"


def test_format_size():
    assert format_size(None) == "—"
    assert format_size(512) == "512 B"
    assert format_size(29_779_558) == "28.4 MB"


def _face(repo, media, person, state):
    repo.conn.execute(
        "INSERT INTO faces (media_id, bbox_x, bbox_y, bbox_w, bbox_h, embedding,"
        " person_id, state) VALUES (?,1,2,3,4,?,?,?)",
        (media, _BLOB, person, state),
    )


def test_photo_people_counts(repo, make_item):
    repo.upsert_many([make_item("p.jpg")])
    media = repo.conn.execute("SELECT id FROM media_items").fetchone()[0]
    run = repo.conn.execute

    def person(name, hidden=0):
        sql = "INSERT INTO persons (name, hidden) VALUES (?, ?)"
        return run(sql, (name, hidden)).lastrowid

    ana, zoe = person("Ana"), person("Zoe")
    group, strangers = person(None), person(None, hidden=1)
    _face(repo, media, zoe, "auto")
    _face(repo, media, ana, "user")
    _face(repo, media, ana, "auto")  # Ana twice: named once
    _face(repo, media, group, "cluster")  # unnamed
    _face(repo, media, None, "new")  # unnamed
    _face(repo, media, None, "ignored")  # not counted
    _face(repo, media, strangers, "ignored")  # hidden group: not counted
    run(
        "INSERT INTO face_scan_state (media_id, det_version, n_faces, img_w, img_h)"
        " VALUES (?, 'v', 7, 200, 100)",
        (media,),
    )
    repo.conn.commit()
    p = photo_people(repo, media)
    assert p.names == ("Ana", "Zoe")
    assert p.unnamed == 2
    assert len(p.faces) == 5
    named = [f for f in p.faces if f.name == "Ana"]
    assert len(named) == 2 and named[0].img_size == (200, 100)
    assert named[0].bbox == (1, 2, 3, 4)


def test_photo_people_none_without_face_tables(repo, make_item):
    repo.conn.execute("DROP TABLE faces")
    assert photo_people(repo, 1) is None


def test_old_scan_faces_fall_back_to_the_photo_size(repo, make_item):
    """A scan from before image sizes were recorded: the photo's own size
    stands in (the viewer still refuses a box whose orientation disagrees)."""
    repo.upsert_many([make_item("p.jpg", width=3456, height=5184)])
    media = repo.conn.execute("SELECT id FROM media_items").fetchone()[0]
    _face(repo, media, None, "new")
    repo.conn.execute(
        "INSERT INTO face_scan_state (media_id, det_version, n_faces) VALUES (?, 'v', 1)",
        (media,),
    )
    repo.conn.commit()
    (face,) = photo_people(repo, media).faces
    assert face.img_size == (3456, 5184)
