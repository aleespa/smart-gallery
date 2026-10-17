# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the Windows desktop app.

One folder, ``dist/SmartGallery/``, holding two executables that share the
same runtime:

- ``SmartGallery.exe``: the windowed PySide6 app (no console).
- ``smart-gallery.exe``: the console CLI. The app runs every write as this
  exe in a subprocess (``gui/paths.py:cli_command``).

Run from the repository root:
    uv run pyinstaller packaging/smart_gallery.spec --noconfirm

The face stack (insightface, onnxruntime-gpu, the nvidia-* CUDA/cuDNN wheels)
is bundled only when it is importable at build time, i.e. after
``uv sync --extra faces``. InsightFace models are not bundled; the app uses
``~/.insightface/models``.
"""

import importlib.metadata
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is set by PyInstaller)
HERE = ROOT / "packaging"

datas = []
binaries = []
hiddenimports = [
    # pandas loads its Excel writer by name (reporting/excel.py).
    "xlsxwriter",
]

# QtWebEngine (Dashboard) needs QtWebEngineProcess.exe, its .pak resources,
# ICU data and locales next to the Qt DLLs.
_d, _b, _h = collect_all("PySide6")
datas += _d
binaries += _b
hiddenimports += _h

# The Dashboard inlines plotly.js from plotly's package data at runtime
# (plotly.offline.get_plotlyjs).
datas += collect_data_files("plotly")


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


if _installed("insightface"):
    # onnxruntime-gpu and the CPU onnxruntime wheel both import as
    # `onnxruntime`; with both installed the CPU build shadows the GPU one.
    try:
        importlib.metadata.distribution("onnxruntime")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise SystemExit(
            "The CPU 'onnxruntime' wheel is installed next to onnxruntime-gpu. "
            "Run `uv sync --extra faces --extra gui` (it removes it) and rebuild."
        )

    for package in ("insightface", "onnxruntime"):
        _d, _b, _h = collect_all(package)
        datas += _d
        binaries += _b
        hiddenimports += _h

    # CUDA/cuDNN DLLs from the nvidia-* wheels, kept at their site-packages
    # layout (nvidia/<lib>/bin/*.dll) so onnxruntime.preload_dlls() finds
    # them relative to the bundled onnxruntime package, as it does unfrozen.
    nvidia = importlib.util.find_spec("nvidia")
    nvidia_dirs = nvidia.submodule_search_locations if nvidia else None
    for location in nvidia_dirs or []:
        base = Path(location)
        for dll in base.rglob("*.dll"):
            relative = dll.relative_to(base.parent)
            if {"bin", "lib"} & set(relative.parts):
                binaries.append((str(dll), relative.parent.as_posix()))

# Not used by the desktop app: the Streamlit dashboard (`dashboard`), the
# Textual TUI (`ui`) and the static report figures (`report --figures`).
excludes = ["streamlit", "textual", "matplotlib", "geopandas"]


def _analysis(script):
    return Analysis(  # noqa: F821
        [str(HERE / script)],
        pathex=[str(ROOT)],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=excludes,
        noarchive=False,
    )


gui_a = _analysis("entry_gui.py")
cli_a = _analysis("entry_cli.py")

# A frozen exe ignores PYTHONIOENCODING, so without UTF-8 mode the CLI writes
# the console code page (cp1252) into the app's pipe and mangles or crashes on
# non-ASCII names.
utf8_mode = [("X utf8", None, "OPTION")]

gui_exe = EXE(  # noqa: F821
    PYZ(gui_a.pure),  # noqa: F821
    gui_a.scripts,
    utf8_mode,
    exclude_binaries=True,
    name="SmartGallery",
    console=False,
)
cli_exe = EXE(  # noqa: F821
    PYZ(cli_a.pure),  # noqa: F821
    cli_a.scripts,
    utf8_mode,
    exclude_binaries=True,
    name="smart-gallery",
    console=True,
)

coll = COLLECT(  # noqa: F821
    gui_exe,
    gui_a.binaries,
    gui_a.datas,
    cli_exe,
    cli_a.binaries,
    cli_a.datas,
    name="SmartGallery",
)
