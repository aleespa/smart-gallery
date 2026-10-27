"""scan-faces — detect + embed faces for every catalogued image.

A resumable GPU pass: images already scanned at the current ``FACE_DET_VERSION``
are skipped (tracked in ``face_scan_state``), so the job can be killed and
re-run. Each face's thumbnail is saved under ``.smart_gallery/faces`` and the
user's decisions on faces of a re-scanned image carry over to its new faces.
CPU image-decode runs on a worker pool and overlaps with single-owner
GPU inference — decode/IO is the real bottleneck, not the GPU.
"""

import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from smart_gallery.analysis.faces import FaceScanner
from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.db import GalleryRepository
from smart_gallery.face_crops import crop_box, encode_crop, write_crop
from smart_gallery.models import Face
from smart_gallery.mirror.faces import ensure_not_mirror
from smart_gallery.progress import SCANNING, ProgressCallback, ProgressEvent
from smart_gallery.services.file_faces import FileFacesReport, file_faces

_COMMIT_CHUNK = 500


@dataclass
class ScanFacesReport:
    images_scanned: int = 0
    faces_found: int = 0
    provider: str = "unknown"
    decisions_carried: int = 0
    decisions_dropped: int = 0


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
    progress_callback: Optional[ProgressCallback] = None,
) -> ScanFacesReport:
    ensure_not_mirror(repo)
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
    buffer = []  # (media_id, faces, img_size) for add_scan_results
    crops = []  # (Face, jpeg bytes), written once the faces have ids

    def _flush() -> None:
        # Take the batch first: if writing it fails, the ``finally`` below
        # must not try the same batch again and hide the original error.
        batch, batch_crops = list(buffer), list(crops)
        buffer.clear()
        crops.clear()
        repo.add_scan_results(batch, FACE_DET_VERSION)
        report.decisions_carried += repo.last_carryover[0]
        report.decisions_dropped += repo.last_carryover[1]
        for face, data in batch_crops:
            try:
                write_crop(repo.drive_root, face.id, data)
            except Exception as exc:
                logger.debug(f"could not save crop of face {face.id}: {exc}")

    def _submit_next(ex) -> None:
        item = next(work_iter, None)
        if item is not None:
            media_id, abspath = item
            inflight[ex.submit(scanner.decode, abspath)] = media_id

    try:
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
                    img_size = None
                    if img is not None:
                        img_size = (int(img.shape[1]), int(img.shape[0]))
                        for face in face_rows:
                            box = (face.bbox_x, face.bbox_y, face.bbox_w, face.bbox_h)
                            try:
                                data = encode_crop(img, crop_box(box, *img_size))
                            except Exception as exc:
                                logger.debug(f"crop failed for media {media_id}: {exc}")
                                continue
                            crops.append((face, data))
                    buffer.append((media_id, face_rows, img_size))
                    report.images_scanned += 1
                    report.faces_found += len(face_rows)

                    _submit_next(ex)  # keep the pipeline full

                    if len(buffer) >= _COMMIT_CHUNK:
                        _flush()
                    if progress_callback:
                        progress_callback(
                            ProgressEvent(
                                SCANNING, report.images_scanned, total, unit="images"
                            )
                        )
    finally:
        # Keep what was scanned even when the run is cancelled part way.
        if buffer:
            _flush()

    logger.success(
        f"Face scan complete — {report.images_scanned:,} images, "
        f"{report.faces_found:,} faces (provider={report.provider}); "
        f"{report.decisions_carried:,} decisions carried over, "
        f"{report.decisions_dropped:,} dropped. "
        "Next: `smart-gallery file-faces` sorts them into people "
        "(`find-faces` and the People page do this for you)."
    )
    return report


def find_faces(
    repo: GalleryRepository,
    *,
    progress_callback: Optional[ProgressCallback] = None,
) -> tuple[ScanFacesReport, FileFacesReport]:
    """Scan new images for faces, then sort every face into people."""
    ensure_not_mirror(repo)
    scanned = scan_faces(repo, progress_callback=progress_callback)
    filed = file_faces(repo, group=True, progress_callback=progress_callback)
    return scanned, filed
