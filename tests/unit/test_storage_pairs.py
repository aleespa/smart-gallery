from smart_gallery.storage.pairs import (
    KEEP_BOTH,
    KEEP_JPEG,
    KEEP_RAW,
    FileRef,
    Pair,
    find_pairs,
    in_scope,
    resolve_removals,
)


def _put(repo, make_item, *relpaths):
    repo.upsert_many([make_item(r) for r in relpaths])


def _ref(relpath, i):
    ext = "." + relpath.rsplit(".", 1)[1].lower()
    return FileRef(i, relpath, ext, 10, 1, "image")


def _pair(raw, jpeg, sidecar=None, folder=None):
    folder = raw.rsplit("/", 1)[0] if folder is None and "/" in raw else folder or ""
    stem = raw.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return Pair(
        folder,
        stem,
        _ref(raw, hash(raw) % 10**6),
        _ref(jpeg, hash(jpeg) % 10**6),
        _ref(sidecar, hash(sidecar) % 10**6) if sidecar else None,
    )


def test_pairs_same_folder_only(repo, make_item):
    _put(
        repo,
        make_item,
        "a/IMG_1.CR3",
        "a/IMG_1.JPG",
        "b/IMG_1.JPG",
        "a/IMG_2.CR3",
        "a/IMG_3.JPG",
    )
    pairs = find_pairs(repo)
    assert [(p.folder, p.stem, p.raw.relpath, p.jpeg.relpath) for p in pairs] == [
        ("a", "IMG_1", "a/IMG_1.CR3", "a/IMG_1.JPG")
    ]


def test_pairs_ignore_case(repo, make_item):
    _put(repo, make_item, "Canon/img_5.cr3", "canon/IMG_5.JPEG")
    (pair,) = find_pairs(repo)
    assert pair.folder == "Canon"
    assert pair.stem == "img_5"
    assert pair.key == ("canon/img_5.cr3", "canon/img_5.jpeg")


def test_cr2_pairs(repo, make_item):
    _put(repo, make_item, "x/A.CR2", "x/A.jpg")
    assert len(find_pairs(repo)) == 1


def test_no_same_format_pairs(repo, make_item):
    _put(repo, make_item, "x/A.CR2", "x/A.CR3", "x/B.JPG", "x/B.jpeg")
    assert find_pairs(repo) == []


def test_one_raw_two_jpegs(repo, make_item):
    _put(repo, make_item, "x/A.CR3", "x/A.JPG", "x/A.jpeg")
    pairs = find_pairs(repo)
    assert len(pairs) == 2
    assert {p.raw.relpath for p in pairs} == {"x/A.CR3"}
    assert [p.jpeg.relpath for p in pairs] == ["x/A.JPG", "x/A.jpeg"]


def test_sidecar_attached(repo, make_item):
    _put(repo, make_item, "x/A.CR3", "x/A.JPG", "x/A.XMP")
    pairs = find_pairs(repo)
    assert pairs[0].sidecar.relpath == "x/A.XMP"


def test_null_size_becomes_zero(repo, make_item):
    repo.upsert_many(
        [make_item("x/A.CR3", size_bytes=None), make_item("x/A.JPG", size_bytes=5)]
    )
    (pair,) = find_pairs(repo)
    assert pair.raw.size_bytes == 0
    assert pair.jpeg.size_bytes == 5


def test_root_level_pair(repo, make_item):
    _put(repo, make_item, "IMG_1.CR3", "IMG_1.JPG")
    (pair,) = find_pairs(repo)
    assert pair.folder == ""
    assert in_scope(pair, None) and in_scope(pair, "")
    assert not in_scope(pair, "Canon")


def test_scope_includes_subfolders():
    pairs = [
        _pair(f"{f}/A.CR3", f"{f}/A.JPG")
        for f in ("Canon", "Canon/2026", "Canonical", "Drone")
    ]
    assert [p.folder for p in pairs if in_scope(p, "canon")] == [
        "Canon",
        "Canon/2026",
    ]


def test_resolve_basic():
    p1 = _pair("a/1.CR3", "a/1.JPG")
    p2 = _pair("a/2.CR3", "a/2.JPG")
    p3 = _pair("a/3.CR3", "a/3.JPG")
    p4 = _pair("a/4.CR3", "a/4.JPG")
    choices = {p1.key: KEEP_RAW, p2.key: KEEP_JPEG, p3.key: KEEP_BOTH}
    assert [f.relpath for f in resolve_removals([p1, p2, p3, p4], choices)] == [
        "a/1.JPG",
        "a/2.CR3",
    ]


def test_resolve_keep_wins_for_shared_file():
    p1 = _pair("x/A.CR3", "x/A.JPG")
    p2 = _pair("x/A.CR3", "x/A.jpeg")
    # same RAW object identity by id: build p2 from p1's raw
    p2 = Pair(p1.folder, p1.stem, p1.raw, p2.jpeg, None)
    choices = {p1.key: KEEP_JPEG, p2.key: KEEP_RAW}
    assert [f.relpath for f in resolve_removals([p1, p2], choices)] == ["x/A.jpeg"]


def test_resolve_counts_each_file_once():
    p1 = _pair("x/A.CR3", "x/A.JPG")
    p2 = Pair(p1.folder, p1.stem, p1.raw, _ref("x/A.jpeg", 77), None)
    choices = {p1.key: KEEP_JPEG, p2.key: KEEP_JPEG}
    assert [f.relpath for f in resolve_removals([p1, p2], choices)] == ["x/A.CR3"]


def test_resolve_ignores_unknown_keys():
    p1 = _pair("x/A.CR3", "x/A.JPG")
    assert resolve_removals([p1], {("nope", "nope"): KEEP_JPEG}) == []


def test_sidecar_follows_raw():
    p = _pair("x/A.CR3", "x/A.JPG", "x/A.XMP")
    out = resolve_removals([p], {p.key: KEEP_JPEG})
    assert [f.relpath for f in out] == ["x/A.CR3", "x/A.XMP"]
    out = resolve_removals([p], {p.key: KEEP_RAW})
    assert [f.relpath for f in out] == ["x/A.JPG"]


def test_sidecar_stays_while_any_raw_kept():
    jpg = _ref("x/A.JPG", 1)
    xmp = _ref("x/A.XMP", 2)
    p1 = Pair("x", "A", _ref("x/A.CR2", 3), jpg, xmp)
    p2 = Pair("x", "A", _ref("x/A.CR3", 4), jpg, xmp)
    choices = {p1.key: KEEP_JPEG, p2.key: KEEP_RAW}
    assert [f.relpath for f in resolve_removals([p1, p2], choices)] == ["x/A.CR2"]


def test_sidecar_stays_when_a_raw_is_undecided():
    jpg = _ref("x/A.JPG", 1)
    xmp = _ref("x/A.XMP", 2)
    p1 = Pair("x", "A", _ref("x/A.CR2", 3), jpg, xmp)
    p2 = Pair("x", "A", _ref("x/A.CR3", 4), jpg, xmp)
    out = resolve_removals([p1, p2], {p1.key: KEEP_JPEG})
    assert [f.relpath for f in out] == ["x/A.CR2"]
