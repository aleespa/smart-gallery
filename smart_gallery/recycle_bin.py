"""Move files to the Windows Recycle Bin, never deleting them for good.

``supports_recycle_bin`` is the guard callers run first: only fixed local drives
on Windows have a Recycle Bin. ``move_to_recycle_bin`` asks the shell to delete
with undo (``FOF_ALLOWUNDO``); ``FOF_WANTNUKEWARNING`` makes Windows ask rather
than delete permanently if the Recycle Bin can't take a file.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

DRIVE_FIXED = 3

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


def supports_recycle_bin(path: Path) -> bool:
    """True only on Windows, for a path on a fixed local drive."""
    if sys.platform != "win32":
        return False
    anchor = Path(os.path.abspath(path)).anchor
    if not anchor:
        return False
    try:
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(anchor))
    except (AttributeError, OSError):
        return False
    return drive_type == DRIVE_FIXED


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
