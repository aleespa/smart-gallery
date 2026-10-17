import sys

import pytest

from smart_gallery.gui.galleries.store import Gallery
from smart_gallery.gui.imports.model import build_import_job, list_removable_drives
from smart_gallery.import_profiles import expand, starter_profiles


def _gallery(tmp_path, name, connected=True):
    root = tmp_path / name
    db = root / ".smart_gallery" / "gallery.db"
    if connected:
        db.parent.mkdir(parents=True)
        db.touch()
    return Gallery(name, str(db), name)


def test_build_import_job_title_and_steps(tmp_path):
    profile = starter_profiles()[0]
    galleries = [_gallery(tmp_path, "a"), _gallery(tmp_path, "b")]
    job = build_import_job(profile, str(tmp_path / "card"), galleries)
    assert job.title == "Import · Canon SD card"
    expected = expand(profile, str(tmp_path / "card"), [str(g.root) for g in galleries])
    assert job.argvs == tuple(tuple(a) for a in expected)
    assert len(job.argvs) == 4  # two rules x two galleries


def test_build_import_job_rejects_disconnected_gallery(tmp_path):
    profile = starter_profiles()[0]
    gone = _gallery(tmp_path, "gone", connected=False)
    with pytest.raises(ValueError, match="not connected"):
        build_import_job(profile, "card", [gone])


def test_build_import_job_rejects_empty_source_and_no_galleries(tmp_path):
    profile = starter_profiles()[0]
    with pytest.raises(ValueError):
        build_import_job(profile, "  ", [_gallery(tmp_path, "a")])
    with pytest.raises(ValueError):
        build_import_job(profile, "card", [])


def test_list_removable_drives_never_raises():
    drives = list_removable_drives()
    assert isinstance(drives, list)
    if sys.platform != "win32":
        assert drives == []
