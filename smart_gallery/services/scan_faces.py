"""scan-faces — detect + embed faces for every catalogued image.

A resumable GPU pass: images already scanned at the current ``FACE_DET_VERSION``
are skipped (tracked in ``face_scan_state``), so the job can be killed and
re-run. CPU image-decode runs on a worker pool and overlaps with single-owner
GPU inference — decode/IO is the real bottleneck, not the GPU.
"""

import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger

from smart_gallery.analysis.faces import FaceScanner
from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.db import GalleryRepository
from smart_gallery.models import Face

_COMMIT_CHUNK = 500


@dataclass
class ScanFacesReport:
    images_scanned: int = 0
    faces_found: int = 0
    provider: str = "unknown"


def _decode_workers() -> int:
    raw = os.getenv("SG_FACES_DECODE_WORKERS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, min(8, (os.cpu_count() or 4) - 1))


def scan_faces(
    repo: GalleryRepository,
    *,
    rescan: bool = False,
    limit: Optional[int] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> ScanFacesReport:
    if rescan:
        logger.info("--rescan: clearing all existing face data first")
        repo.reset_all_faces()

    work = repo.unscanned_images(FACE_DET_VERSION)
    if limit is not None:
        work = work[:limit]
    total = len(work)
    if total == 0:
        logger.success("No images pending face scan — nothing to do.")
        return ScanFacesReport()

    logger.info(f"Scanning {total:,} image(s) for faces…")
    scanner = FaceScanner()  # loads the model; logs/asserts the GPU provider
    report = ScanFacesReport(provider=scanner.provider)

    workers = _decode_workers()
    work_iter = iter(work)
    inflight = {}
    buffer = []

    def _submit_next(ex) -> None:
        item = next(work_iter, None)
        if item is not None:
            media_id, abspath = item
            inflight[ex.submit(scanner.decode, abspath)] = media_id

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in range(workers * 2):  # prime the decode pipeline
            _submit_next(ex)

        while inflight:
            done_set, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for fut in done_set:
                media_id = inflight.pop(fut)
                try:
                    img = fut.result()
                except Exception as exc:  # pragma: no cover - decode safety net
                    logger.debug(f"decode worker error for media {media_id}: {exc}")
                    img = None
                faces = scanner.detect(img)
                face_rows = [
                    Face.from_detection(media_id, d.bbox, d.det_score, d.embedding)
                    for d in faces
                ]
                buffer.append((media_id, face_rows))
                report.images_scanned += 1
                report.faces_found += len(face_rows)

                _submit_next(ex)  # keep the pipeline full

                if len(buffer) >= _COMMIT_CHUNK:
                    repo.add_scan_results(buffer, FACE_DET_VERSION)
                    buffer.clear()
                if progress_callback:
                    progress_callback(report.images_scanned / total)

    if buffer:
        repo.add_scan_results(buffer, FACE_DET_VERSION)

    logger.success(
        f"Face scan complete — {report.images_scanned:,} images, "
        f"{report.faces_found:,} faces (provider={report.provider}). "
        f"Next: `smart-gallery cluster-faces` to group them into people."
    )
    return report
