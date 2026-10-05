"""Identify catalog faces by comparing them with user supplied examples."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger

from smart_gallery.analysis.faces import FaceScanner
from smart_gallery.config import IMAGE_EXTENSIONS
from smart_gallery.db import GalleryRepository

DEFAULT_IDENTIFY_THRESHOLD = 0.5
DEFAULT_IDENTIFY_MARGIN = 0.05


@dataclass
class IdentifyFacesReport:
    persons: int = 0
    sample_images: int = 0
    sample_skipped: int = 0
    faces_assigned: int = 0
    faces_unmatched: int = 0
    faces_ambiguous: int = 0
    faces_already_assigned: int = 0


def _sample_references(samples_root: Path, scanner: FaceScanner):
    if not samples_root.is_dir():
        raise FileNotFoundError(f"Sample directory does not exist: {samples_root}")

    person_dirs = sorted(path for path in samples_root.iterdir() if path.is_dir())
    if not person_dirs:
        raise ValueError(
            f"No person directories found under {samples_root}; expected one "
            "named directory per person."
        )

    refs = {}
    seen_names = set()
    usable_images = skipped = 0
    for person_dir in person_dirs:
        name = person_dir.name.strip()
        if not name:
            raise ValueError(f"Person directory has an empty name: {person_dir}")
        name_key = name.casefold()
        if name_key in seen_names:
            raise ValueError(f"Duplicate person directory name after trimming: {name!r}")
        seen_names.add(name_key)

        embeddings = []
        files = sorted(
            path
            for path in person_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
        for path in files:
            try:
                detections = scanner.detect(scanner.decode(str(path)))
            except Exception as exc:
                logger.warning(f"Could not process sample {path}: {exc}")
                skipped += 1
                continue
            if len(detections) != 1:
                why = "no face" if not detections else f"{len(detections)} faces"
                logger.warning(f"Skipping sample {path}: expected one face, found {why}.")
                skipped += 1
                continue
            embeddings.append(detections[0].embedding)
            usable_images += 1

        if not embeddings:
            logger.warning(
                f"No usable single-face samples for {name!r}; skipping this person."
            )
            continue
        mean = np.asarray(embeddings, dtype=np.float32).mean(axis=0)
        norm = float(np.linalg.norm(mean))
        if norm == 0:
            logger.warning(f"Sample embeddings cancel out for {name!r}; skipping this person.")
            continue
        refs[name] = (mean / norm).astype(np.float32)

    if not refs:
        raise ValueError(f"No usable sample photos found under {samples_root}.")
    return refs, usable_images, skipped


def identify_faces(
    repo: GalleryRepository,
    samples_root: Path,
    *,
    threshold: float = DEFAULT_IDENTIFY_THRESHOLD,
    margin: float = DEFAULT_IDENTIFY_MARGIN,
    reassign: bool = False,
) -> IdentifyFacesReport:
    """Build sample references and assign catalog faces on confident matches."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("--threshold must be between 0 and 1.")
    if not 0.0 <= margin <= 2.0:
        raise ValueError("--margin must be between 0 and 2.")
    if repo.faces_count() == 0:
        raise ValueError("No catalog faces found. Run `scan-faces` first.")

    samples_root = Path(samples_root)
    if not samples_root.is_dir():
        raise FileNotFoundError(f"Sample directory does not exist: {samples_root}")
    scanner = FaceScanner()
    refs, usable_images, skipped = _sample_references(samples_root, scanner)
    result = repo.identify_faces_from_samples(
        refs, threshold=threshold, margin=margin, reassign=reassign
    )
    return IdentifyFacesReport(
        persons=result["persons"],
        sample_images=usable_images,
        sample_skipped=skipped,
        faces_assigned=result["assigned"],
        faces_unmatched=result["unmatched"],
        faces_ambiguous=result["ambiguous"],
        faces_already_assigned=result["already_assigned"],
    )
