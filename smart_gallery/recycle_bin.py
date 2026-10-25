"""Move files to the Windows Recycle Bin, never deleting them for good.

``supports_recycle_bin`` is the guard callers run first: only fixed local drives
on Windows have a Recycle Bin, and only while it is turned on for the drive (its
``NukeOnDelete`` setting) and not switched off by policy (``NoRecycleFiles``).
``recycle_bin_capacity`` is the drive's Recycle Bin size limit, when one is set.
``move_to_recycle_bin`` asks the shell to delete with undo (``FOF_ALLOWUNDO``);
``FOF_WANTNUKEWARNING`` makes Windows ask rather than delete permanently if the
Recycle Bin can't take a file.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

DRIVE_FIXED = 3

# Per-volume Recycle Bin settings and the Explorer policy, under HKCU / HKLM.
BITBUCKET_VOLUMES = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
)
EXPLORER_POLICIES = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
MB = 1024 * 1024

FO_DELETE = 3
FOF_SILENT = 0x4
FOF_NOCONFIRMATION = 0x10
FOF_ALLOWUNDO = 0x40
FOF_NOERRORUI = 0x400
FOF_WANTNUKEWARNING = 0x4000

_RECYCLE_FLAGS = (
    FOF_ALLOWUNDO
    | FOF_NOCONFIRMATION
    | FOF_NOERRORUI
    | FOF_SILENT
    | FOF_WANTNUKEWARNING
)


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _anchor(path: Path) -> str:
    return Path(os.path.abspath(path)).anchor


def _drive_type(anchor: str) -> int:
    return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(anchor))


def _volume_guid(anchor: str) -> str:
    """``{GUID}`` of the volume mounted at ``anchor`` (``"C:\\"``)."""
    buf = ctypes.create_unicode_buffer(64)
    kernel32 = ctypes.windll.kernel32
    if not kernel32.GetVolumeNameForVolumeMountPointW(
        ctypes.c_wchar_p(anchor), buf, len(buf)
    ):
        raise OSError(f"No volume name for {anchor}")
    name = buf.value  # \\?\Volume{GUID}\
    start, end = name.find("{"), name.find("}")
    if start < 0 or end < start:
        raise OSError(f"Unexpected volume name {name!r}")
    return name[start : end + 1]


def _volume_key(anchor: str) -> str:
    """The drive's Recycle Bin settings key under HKCU."""
    return BITBUCKET_VOLUMES + "\\" + _volume_guid(anchor)


def _read_dword(hive: str, key: str, name: str) -> int | None:
    """A registry DWORD from ``"HKCU"``/``"HKLM"``; None when key or value is missing."""
    import winreg

    root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]
    try:
        with winreg.OpenKey(root, key) as handle:
            value, kind = winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None
    return int(value) if kind == winreg.REG_DWORD else None


def _recycling_turned_off(anchor: str) -> bool:
    if _read_dword("HKCU", _volume_key(anchor), "NukeOnDelete") == 1:
        return True
    return any(
        _read_dword(hive, EXPLORER_POLICIES, "NoRecycleFiles") == 1
        for hive in ("HKCU", "HKLM")
    )


def supports_recycle_bin(path: Path) -> bool:
    """True only on Windows, for a path on a fixed local drive whose Recycle Bin
    is turned on. If its settings can't be read, a fixed drive counts."""
    if sys.platform != "win32":
        return False
    anchor = _anchor(path)
    if not anchor:
        return False
    try:
        if _drive_type(anchor) != DRIVE_FIXED:
            return False
    except (AttributeError, OSError):
        return False
    try:
        return not _recycling_turned_off(anchor)
    except Exception:  # unreadable settings: keep the fixed-drive answer
        return True


def recycle_bin_capacity(path: Path) -> int | None:
    """The drive's Recycle Bin limit in bytes (``MaxCapacity``), or None when
    it isn't set or can't be read. Best effort."""
    if sys.platform != "win32":
        return None
    try:
        megabytes = _read_dword("HKCU", _volume_key(_anchor(path)), "MaxCapacity")
    except Exception:
        return None
    return None if megabytes is None else megabytes * MB


def move_to_recycle_bin(path: Path) -> None:
    """Send one file to the Recycle Bin. Raises ``OSError`` on any failure,
    and whenever the file is still there afterwards."""
    if sys.platform != "win32":
        raise OSError(f"The Recycle Bin is only available on Windows: {path}")
    target = os.path.abspath(path)
    if not os.path.lexists(target):
        raise OSError(f"File not found: {target}")

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = target + "\0\0"  # a double-NUL-terminated list of one path
    op.pTo = None
    op.fFlags = _RECYCLE_FLAGS
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None

    shell32 = ctypes.windll.shell32
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    result = shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError(f"Could not move {target} to the Recycle Bin (code {result:#x}).")
    if op.fAnyOperationsAborted:
        raise OSError(f"Moving {target} to the Recycle Bin was cancelled.")
    if os.path.lexists(target):
        raise OSError(f"{target} is still there after moving it to the Recycle Bin.")
