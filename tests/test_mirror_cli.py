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
    assert {"mirror_free_bytes", "mirror_trash_bytes", "trash_bytes"} <= set(plan)
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


def _plan_out(e, tmp_path, *extra):
    out = tmp_path / "preview.json"
    assert run("sync-mirror", e, "--dry-run", "--plan-out", out, *extra) == 0
    return out


def test_expect_plan_applies_the_previewed_plan(two_drives, tmp_path):
    e, d = two_drives
    _paired(e, d, *[f"f{i}.txt" for i in range(3)])
    assert run("sync-mirror", e) == 0
    (e / "f0.txt").unlink()
    preview = _plan_out(e, tmp_path)
    args = ("--confirm-large-delete", "--expect-plan", preview)
    assert run("sync-mirror", e, *args) == 0
    assert not (d / "f0.txt").exists()


def test_expect_plan_refuses_a_trash_that_was_not_previewed(
    two_drives, tmp_path, capsys
):
    e, d = two_drives
    _paired(e, d, *[f"f{i}.txt" for i in range(3)])
    assert run("sync-mirror", e) == 0
    (e / "f0.txt").unlink()
    preview = _plan_out(e, tmp_path)
    (e / "f1.txt").unlink()  # deleted after the preview
    _put(e, "new.txt", "new")
    args = ("--confirm-large-delete", "--expect-plan", preview)
    assert run("sync-mirror", e, *args) == 1
    assert "f1.txt" in capsys.readouterr().err
    assert (d / "f0.txt").exists() and (d / "f1.txt").exists()
    assert not (d / "new.txt").exists()  # nothing applied


def test_expect_plan_refuses_a_choice_on_files_not_previewed(two_drives, tmp_path):
    e, d = two_drives
    _paired(e, d, "a.txt", "b.txt")
    assert run("sync-mirror", e) == 0
    _put(d, "a.txt", "edited on D")
    preview = _plan_out(e, tmp_path)
    _put(d, "b.txt", "edited on D later")
    args = ("--changed-on-mirror", "take-working", "--expect-plan", preview)
    assert run("sync-mirror", e, *args) == 1
    assert (d / "a.txt").read_text() == "edited on D"
    assert (d / "b.txt").read_text() == "edited on D later"


def test_free_up_plan_out_and_summary_last_with_many_skipped(
    two_drives, tmp_path, capsys
):
    e, d = two_drives
    names = [f"only{i:03}.txt" for i in range(250)]
    _paired(e, d, "a.txt", *names)
    assert run("sync-mirror", e) == 0
    for n in names:  # only on E: never verified on the mirror
        (d / n).unlink()
    capsys.readouterr()
    out = tmp_path / "free.json"
    args = ("--extensions", ".txt", "--dry-run", "--plan-out", out)
    assert run("free-up", e, *args) == 0
    plan = json.loads(out.read_text())
    assert plan["files"] == 1 and plan["relpaths"] == ["a.txt"]
    assert plan["bytes"] == 5 and len(plan["skipped"]) == 250
    lines = capsys.readouterr().err.splitlines()
    notes = [ln for ln in lines if "Skipped: " in ln or "Would free" in ln]
    assert len(notes) == 251
    assert "Would free 1 file(s)" in notes[-1]  # the summary comes last
    assert (e / "a.txt").exists()


def test_free_up_expect_plan_deletes_only_previewed_files(two_drives, tmp_path):
    e, d = two_drives
    _paired(e, d, "a.txt")
    assert run("sync-mirror", e) == 0
    out = tmp_path / "free.json"
    assert (
        run("free-up", e, "--extensions", ".txt", "--dry-run", "--plan-out", out) == 0
    )
    _put(e, "b.txt", "b")  # mirrored after the preview
    assert run("sync-mirror", e) == 0
    assert run("free-up", e, "--extensions", ".txt", "--expect-plan", out) == 0
    assert not (e / "a.txt").exists()
    assert (e / "b.txt").exists()
