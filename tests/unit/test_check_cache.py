import json
from dataclasses import replace

from smart_gallery.storage.check_cache import CheckCache
from smart_gallery.storage.pairs import FileRef, Pair


def _pair():
    raw = FileRef(1, "Shots/IMG_1.CR3", ".cr3", 100, 5, "image")
    jpeg = FileRef(2, "Shots/IMG_1.JPG", ".jpg", 50, 6, "image")
    return Pair("Shots", "IMG_1", raw, jpeg, None)


def test_hit_when_keys_match(tmp_path):
    path = tmp_path / "c.json"
    cache = CheckCache(path)
    cache.put(_pair(), "match")
    cache.save()
    assert CheckCache(path).get(_pair()) == "match"
    assert not path.with_name("c.json.tmp").exists()


def test_miss_after_size_or_mtime_change(tmp_path):
    path = tmp_path / "c.json"
    cache = CheckCache(path)
    pair = _pair()
    cache.put(pair, "different")
    cache.save()
    cache = CheckCache(path)
    assert cache.get(replace(pair, raw=replace(pair.raw, mtime_ns=6))) is None
    assert cache.get(replace(pair, jpeg=replace(pair.jpeg, size_bytes=51))) is None
    assert cache.get(pair) == "different"


def test_bad_file_is_empty(tmp_path):
    path = tmp_path / "c.json"
    for text in ("{oops", '{"checks": []}', '{"checks": {"k": "maybe"}}'):
        path.write_text(text, encoding="utf-8")
        assert CheckCache(path).get(_pair()) is None


def test_unknown_verdicts_dropped_but_valid_kept(tmp_path):
    path = tmp_path / "c.json"
    pair = _pair()
    good = CheckCache(tmp_path / "x.json")
    good.put(pair, "match")
    key = next(iter(good._checks))
    path.write_text(
        json.dumps({"version": 1, "checks": {key: "match", "z": "maybe"}}),
        encoding="utf-8",
    )
    assert CheckCache(path).get(pair) == "match"
