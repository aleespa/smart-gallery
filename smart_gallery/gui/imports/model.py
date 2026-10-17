"""What the Import area needs that isn't widgets. No Qt here."""

from __future__ import annotations

import string
import sys
from collections.abc import Sequence

from smart_gallery.gui.galleries.store import Gallery, is_connected
from smart_gallery.gui.jobspec import JobSpec
from smart_gallery.import_profiles import ImportProfile, expand

_DRIVE_REMOVABLE = 2


def list_removable_drives() -> list[str]:
    """Roots like ``E:\\`` of the removable drives (SD cards, USB sticks)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes

        get_type = ctypes.windll.kernel32.GetDriveTypeW
        roots = (f"{c}:\\" for c in string.ascii_uppercase)
        return [d for d in roots if get_type(d) == _DRIVE_REMOVABLE]
    except Exception:  # ctypes missing or the call failing must not break the page
        return []


def build_import_job(
    profile: ImportProfile,
    source: str,
    galleries: Sequence[Gallery],
    dry_run: bool = False,
) -> JobSpec:
    """The job that imports ``source`` into every gallery, rule by rule.

    A dry run only plans (copies nothing), so it never writes the catalog.
    """
    if not source.strip():
        raise ValueError("Choose a source to import from.")
    if not galleries:
        raise ValueError("Tick at least one target gallery.")
    for gallery in galleries:
        if not is_connected(gallery):
            raise ValueError(f"{gallery.label} is not connected.")
    argvs = expand(profile, source, [str(g.root) for g in galleries])
    if dry_run:
        argvs = [[*argv, "--dry-run"] for argv in argvs]
    title = f"Import · {profile.name}" + (" (dry run)" if dry_run else "")
    return JobSpec(title, tuple(map(tuple, argvs)), writes_catalog=not dry_run)
