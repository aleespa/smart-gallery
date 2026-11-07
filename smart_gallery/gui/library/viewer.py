"""Shared helpers for showing files, and the photo viewer's old home.

The viewer itself lives in ``gui/viewer/`` (``MediaViewer`` is re-exported
here so the pages that open it keep their import).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from smart_gallery.gui.viewer.info import format_duration, format_size  # noqa: F401
from smart_gallery.gui.viewer.previews import to_qimage as _to_qimage  # noqa: F401
from smart_gallery.gui.viewer.window import MediaViewer, show_in_explorer  # noqa: F401


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
