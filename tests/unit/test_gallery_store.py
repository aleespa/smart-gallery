import json
import shutil
from pathlib import Path

import pytest

from smart_gallery.db import GalleryRepository
from smart_gallery.gui.galleries.store import (
    Gallery,
    GalleryStore,
    is_connected,
    locate,
)


@pytest.fixture
def store(tmp_path) -> GalleryStore:
    return GalleryStore(tmp_path / "appdata" / "galleries.json")


def test_add_reads_uuid_and_label(repo, store):
    g = store.add(repo.drive_root)
    assert g.db_uuid == repo.get_meta("db_uuid")
    assert g.label == "test"
    assert Path(g.db_path) == repo.db_path
    assert g.root == repo.drive_root
    assert store.all() == [g]


def test_label_falls_back_to_root(tmp_path, store):
    root = tmp_path / "unlabelled"
    root.mkdir()
    GalleryRepository.create(root).close()
    g = store.add(root)
    assert g.label == str(root.resolve())


def test_add_same_catalog_twice_dedupes(repo, store):
    store.add(repo.drive_root)
    store.add(repo.db_path)
    assert len(store.all()) == 1


def test_add_does_not_migrate(repo, store):
    repo.conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    repo.conn.commit()
    before = repo.get_meta("schema_version")
    store.add(repo.drive_root)
    assert repo.get_meta("schema_version") == before


def test_add_missing_catalog_raises(tmp_path, store):
    with pytest.raises(FileNotFoundError):
        store.add(tmp_path)
    assert store.all() == []


def test_remount_updates_path(tmp_path, store):
    old = tmp_path / "old"
    old.mkdir()
    GalleryRepository.create(old, label="photos").close()
    first = store.add(old)
    new = tmp_path / "new"
    shutil.copytree(old, new)
    moved = store.add(new)
    assert moved.db_uuid == first.db_uuid
    assert len(store.all()) == 1
    assert (
        Path(store.all()[0].db_path)
        == (new / ".smart_gallery" / "gallery.db").resolve()
    )


def test_disconnected_gallery_is_reported(tmp_path, store):
    root = tmp_path / "card"
    root.mkdir()
    GalleryRepository.create(root, label="card").close()
    g = store.add(root)
    assert is_connected(g)
    shutil.rmtree(root)
    assert not is_connected(g)
    assert store.all() == [g]


def test_persists_across_instances_and_remembers_last(repo, tmp_path):
    path = tmp_path / "galleries.json"
    first = GalleryStore(path)
    g = first.add(repo.drive_root)
    first.set_last(g.db_uuid)
    again = GalleryStore(path)
    assert again.all() == [g]
    assert again.last() == g.db_uuid
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["last"] == g.db_uuid
    assert [e["db_uuid"] for e in data["galleries"]] == [g.db_uuid]
    assert not list(tmp_path.glob("*.tmp"))


def test_remove(repo, store):
    g = store.add(repo.drive_root)
    store.set_last(g.db_uuid)
    store.remove(g.db_uuid)
    assert store.all() == []
    assert store.last() is None


def test_corrupt_file_reads_as_empty(tmp_path):
    path = tmp_path / "galleries.json"
    path.write_text("{not json", encoding="utf-8")
    assert GalleryStore(path).all() == []


def test_locate_finds_matching_catalog_on_another_drive(tmp_path, store):
    old = tmp_path / "olddrive" / "photos"
    old.mkdir(parents=True)
    GalleryRepository.create(old, label="photos").close()
    g = store.add(old)
    # Simulate a remount: the stored path's anchor is replaced by another "drive".
    anchor = Path(g.db_path).anchor
    rel = Path(g.db_path).relative_to(anchor)
    fake = Gallery(g.db_uuid, str(Path("Z:/") / rel), g.label)
    found = locate(fake, [str(Path(anchor)), "Z:\\"])
    assert found is not None
    assert Path(found.db_path) == Path(g.db_path)
    assert found.db_uuid == g.db_uuid


def test_locate_rejects_other_catalog(tmp_path, store):
    root = tmp_path / "other"
    root.mkdir()
    GalleryRepository.create(root).close()
    g = store.add(root)
    anchor = Path(g.db_path).anchor
    stranger = Gallery("f" * 32, g.db_path, "stranger")
    assert locate(stranger, [anchor]) is None
    assert locate(stranger, []) is None


def test_existing_drives_uses_listdrives_when_available(monkeypatch):
    import os

    from smart_gallery.gui.galleries.store import existing_drives

    monkeypatch.setattr(os, "listdrives", lambda: ["C:\\", "Q:\\"], raising=False)
    assert existing_drives() == ["C:\\", "Q:\\"]
