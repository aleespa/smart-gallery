"""The mirror commands, run through ``main`` against temp drives."""

import importlib
import json
from pathlib import Path

import pytest

from smart_gallery import cli
from smart_gallery.db import GalleryRepository
from smart_gallery.models import MediaItem
from smart_gallery.services import init_drive, sync_drive

import_module = importlib.import_module("smart_gallery.services.import_media")


def run(*args):
    """Run the CLI in-process; return its exit code (0 when it returns)."""
    try:
        cli.main([str(a) for a in args])
    except SystemExit as exc:
        return exc.code or 0
    return 0


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # long commands write ./logs/


@pytest.fixture(autouse=True)
def _no_exiftool(monkeypatch):
    def fake(paths, drive_root=None, progress_callback=None, **_):
        return [MediaItem.from_path(p, drive_root) for p in paths]

    monkeypatch.setattr(import_module, "analyze_paths", fake)


def _put(root, rel, text="x"):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _has(root, name):
    return any(Path(root).rglob(name))


def _catalog(root):
    sync_drive_root = GalleryRepository.open(root)
    with sync_drive_root as repo:
        sync_drive(repo)


def _paired(e, d, *names):
    for n in names:
        _put(e, n, n)
    _catalog(e)
    assert run("pair", e, d) == 0


def test_pair_then_sync_mirror_dry_run_writes_plan_json(two_drives, tmp_path):
    e, d = two_drives
    _paired(e, d, "a.txt", "sub/b.txt")
    out = tmp_path / "plan.json"
    assert run("sync-mirror", e, "--dry-run", "--plan-out", out) == 0
    plan = json.loads(out.read_text())
    assert plan["counts"]["copy_new"] == 2
    assert sorted(plan["copy_new"]) == ["a.txt", "sub/b.txt"]
    for key in (
        "overwrite",
        "trash",
        "moves",
        "only_on_mirror",
        "changed_on_mirror",
        "conflicts",
        "adopt",
        "offloaded_changed",
        "offloaded_missing",
        "offloaded_conflict",
    ):
        assert key in plan and key in plan["counts"]
    assert plan["copy_bytes"] == 14
    assert plan["large_delete"] is False
    assert {"mirror_free_bytes", "mirror_trash_bytes"} <= set(plan)
    assert not (d / "a.txt").exists()


def test_sync_mirror_with_two_mirrors_needs_mirror_flag(two_drives, tmp_path):
    e, d = two_drives
    f = tmp_path / "F"
    f.mkdir()
    init_drive(f)
    _paired(e, d, "a.txt")
    assert run("pair", e, f) == 0
    assert run("sync-mirror", e) == 1
    assert not (d / "a.txt").exists()
    assert run("sync-mirror", e, "--mirror", f / ".smart_gallery" / "gallery.db") == 0
    assert (f / "a.txt").exists() and not (d / "a.txt").exists()


def test_sync_mirror_applies_and_second_run_is_empty(two_drives, tmp_path):
    e, d = two_drives
    _paired(e, d, "a.txt")
    assert run("sync-mirror", e) == 0
    assert (d / "a.txt").read_text() == "a.txt"
    out = tmp_path / "plan.json"
    assert run("sync-mirror", e, "--dry-run", "--plan-out", out) == 0
    plan = json.loads(out.read_text())
    assert sum(plan["counts"].values()) == 0


def test_sync_mirror_on_the_mirror_side_refuses(two_drives, capsys):
    e, d = two_drives
    _paired(e, d, "a.txt")
    assert run("sync-mirror", d) == 1
    assert "Run sync-mirror on the working drive." in capsys.readouterr().err


def test_large_delete_exits_1_without_flag(two_drives):
    e, d = two_drives
    names = [f"f{i}.txt" for i in range(10)]
    _paired(e, d, *names)
    assert run("sync-mirror", e) == 0
    (e / "f0.txt").unlink()
    (e / "f1.txt").unlink()
    assert run("sync-mirror", e) == 1
    assert (d / "f0.txt").exists()
    assert run("sync-mirror", e, "--confirm-large-delete") == 0
    assert not (d / "f0.txt").exists()


def test_import_uses_mirror_by_default_and_no_mirror_opts_out(two_drives, tmp_path):
    e, d = two_drives
    _paired(e, d, "seed.txt")
    sd = tmp_path / "sd"
    _put(sd, "one.txt", "1")
    assert run("import", sd, "--to", e, "--no-by-media-type") == 0
    assert _has(e, "one.txt") and _has(d, "one.txt")
    sd2 = tmp_path / "sd2"
    _put(sd2, "two.txt", "2")
    assert run("import", sd2, "--to", e, "--no-mirror", "--no-by-media-type") == 0
    assert _has(e, "two.txt") and not _has(d, "two.txt")


def test_free_up_dry_run_lists_without_deleting(two_drives):
    e, d = two_drives
    _paired(e, d, "a.txt")
    assert run("sync-mirror", e) == 0
    assert run("free-up", e, "--extensions", ".txt", "--dry-run") == 0
    assert (e / "a.txt").exists()
