"""ExifTool batch extraction, producing ``MediaItem`` objects.

Metadata comes from the external ExifTool binary (batch mode, JSON, run across a
thread pool) — the same engine the original project used. Parallelism is tunable
via ``SG_EXIFTOOL_MAX_WORKERS`` and ``SG_EXIFTOOL_BATCH_SIZE``.
"""

import concurrent.futures
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from smart_gallery.config import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from smart_gallery.models import MediaItem
from smart_gallery.progress import (
    READING,
    ProgressCallback,
    ProgressEvent,
    check_cancelled,
)

EXIFTOOL_IMAGE_TAG_ARGS = [
    "-Model",
    "-LensModel",
    "-LensID",
    "-FocalLength",
    "-FNumber",
    "-ISO",
    "-ExposureTime",
    "-GPSLatitude",
    "-GPSLongitude",
    "-GPSAltitude",
    "-ImageWidth",
    "-ImageHeight",
]
EXIFTOOL_VIDEO_TAG_ARGS = [
    "-ImageWidth",
    "-ImageHeight",
    "-Duration",
    "-VideoCodecID",
    "-CompressorID",
    "-VideoFrameRate",
]


def _get_exiftool_path() -> str:
    """Return the exiftool executable path (system PATH, or a bundled copy)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = os.path.join(sys._MEIPASS, "exiftool.exe")
        if os.path.isfile(bundled):
            return bundled
    return "exiftool"


def _get_exiftool_base_args(tag_profile: str = "all") -> List[str]:
    fast_flag = "-fast2" if tag_profile == "image" else "-fast"
    return [
        _get_exiftool_path(),
        "-j",
        "-n",
        fast_flag,
        "-m",
        "-q",
        "-q",
        "-c",
        "%.6f",
        "-FileName",
        "-Directory",
        "-FileSize",
        "-DateTimeOriginal",
        "-CreateDate",
    ]


def _choose_exiftool_plan(file_count: int) -> tuple[int, int]:
    max_cpu = os.cpu_count() or 4
    if file_count < 10000:
        min_batch_size = 250
        workers_by_files = max(1, file_count // min_batch_size)
        workers = min(8, max_cpu, workers_by_files) or 1
        batch_size = max(1, math.ceil(file_count / workers))
    elif file_count < 30000:
        batch_size = 2500
        workers = min(6, max_cpu, max(1, math.ceil(file_count / batch_size)))
    else:
        batch_size = 3000
        workers = min(3, max_cpu, max(1, math.ceil(file_count / batch_size)))

    env_workers = os.getenv("SG_EXIFTOOL_MAX_WORKERS")
    if env_workers:
        try:
            workers = max(1, min(workers, int(env_workers)))
        except ValueError:
            pass

    env_batch = os.getenv("SG_EXIFTOOL_BATCH_SIZE")
    if env_batch:
        try:
            batch_size = max(100, int(env_batch))
        except ValueError:
            pass

    return max(1, workers), max(1, batch_size)


def run_exiftool_batch(file_paths: List[Path], tag_profile: str = "all") -> List[Dict]:
    """Run ExifTool over a batch of files; return the parsed JSON records."""
    if not file_paths:
        return []

    with tempfile.NamedTemporaryFile(
        mode="w", delete=False, encoding="utf-8", suffix=".txt"
    ) as tmp:
        for path in file_paths:
            tmp.write(str(path) + "\n")
        tmp_path = tmp.name

    profile_tags = EXIFTOOL_IMAGE_TAG_ARGS + EXIFTOOL_VIDEO_TAG_ARGS
    if tag_profile == "image":
        profile_tags = EXIFTOOL_IMAGE_TAG_ARGS
    elif tag_profile == "video":
        profile_tags = EXIFTOOL_VIDEO_TAG_ARGS

    cmd = _get_exiftool_base_args(tag_profile) + profile_tags + ["-@", tmp_path]
    exiftool_cwd = os.path.dirname(cmd[0])

    env = os.environ.copy()
    if hasattr(sys, "_MEIPASS"):
        perl_lib = os.path.join(sys._MEIPASS, "exiftool_files", "lib")
        if os.path.isdir(perl_lib):
            env["PERL5LIB"] = perl_lib

    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=exiftool_cwd or None,
            env=env,
        )
        if process.stderr:
            logger.debug(f"ExifTool stderr: {process.stderr.strip()}")
        if not process.stdout:
            return []
        return json.loads(process.stdout)
    except FileNotFoundError:
        logger.error("ExifTool not found. Install it and ensure 'exiftool' is on PATH.")
        raise
    except Exception as exc:
        logger.error(f"Error running ExifTool: {exc}")
        return []
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def analyze_paths(
    file_paths: List[Path],
    drive_root: Optional[Path] = None,
    stop_event: Optional[threading.Event] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[MediaItem]:
    """Extract metadata for ``file_paths`` and return a flat list of MediaItems.

    Image/video files go through ExifTool; everything else is recorded stat-only.
    When ``drive_root`` is given, items are anchored to it (relpath set) — use
    this for files that already live on the drive (init/sync). For imports leave
    it ``None`` and re-anchor each item to its destination after copying.
    """
    image_files: List[Path] = []
    video_files: List[Path] = []
    items: List[MediaItem] = []

    for path in file_paths:
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            image_files.append(path)
        elif suffix in VIDEO_EXTENSIONS:
            video_files.append(path)
        else:
            items.append(MediaItem.from_path(path, drive_root))

    total = len(image_files) + len(video_files) + len(items)
    if total == 0:
        return []

    processed = len(items)
    if progress_callback:
        progress_callback(ProgressEvent(READING, processed, total))

    def _run(file_list: List[Path], tag_profile: str) -> None:
        nonlocal processed
        if not file_list:
            return
        num_workers, batch_size = _choose_exiftool_plan(len(file_list))
        batches = [
            file_list[i : i + batch_size] for i in range(0, len(file_list), batch_size)
        ]
        # Not a ``with`` block: leaving one waits for the running batches,
        # which a cancel must not.
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=num_workers)
        futures = {
            ex.submit(run_exiftool_batch, batch, tag_profile): len(batch)
            for batch in batches
        }
        pending = set(futures)
        try:
            while pending:
                # Wake at least once a second to see a stop request, even
                # while every worker is still inside a batch.
                done, pending = concurrent.futures.wait(
                    pending, timeout=1, return_when=concurrent.futures.FIRST_COMPLETED
                )
                check_cancelled()
                if stop_event and stop_event.is_set():
                    break
                for future in done:
                    for record in future.result():
                        item = MediaItem.from_exiftool(record, drive_root)
                        if item is not None:
                            items.append(item)
                    processed += futures[future]
                    if progress_callback:
                        progress_callback(
                            ProgressEvent(READING, min(processed, total), total)
                        )
        except BaseException:
            # Cancelled (or failed): drop the queued batches without waiting.
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        # A stop_event leaves batches pending: cancel them, don't wait.
        ex.shutdown(wait=not pending, cancel_futures=True)

    _run(image_files, "image")
    _run(video_files, "video")
    return items


def run_exiftool_argfile(args: list[str]) -> str:
    """Run one ExifTool process over an argfile and return its stderr.

    An argfile keeps any number of paths off the command line, and
    ``-charset filename=utf8`` lets it carry non-ASCII paths on Windows.
    """
    with tempfile.TemporaryDirectory() as tmp:
        argfile = Path(tmp) / "args.txt"
        argfile.write_text("\n".join(args) + "\n", encoding="utf-8")
        result = subprocess.run(
            [_get_exiftool_path(), "-charset", "filename=utf8", "-@", str(argfile)],
            capture_output=True,
        )
    return result.stderr.decode(errors="replace")
