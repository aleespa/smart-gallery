"""file-faces: sort scanned faces into people, suggestions and new groups.

Needs no GPU. Loads every face the user has not decided, files each with
``analysis.filing.decide``, optionally groups the leftovers with HDBSCAN, and
writes the result in one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from loguru import logger

from smart_gallery.analysis.filing import Decision, FilingConfig, decide
from smart_gallery.db import GalleryRepository
from smart_gallery.mirror.faces import ensure_not_mirror
from smart_gallery.progress import FILING, ProgressCallback, ProgressEvent


@dataclass
class FileFacesReport:
    auto: int = 0
    suggested: int = 0
    grouped: int = 0
    hidden: int = 0
    left: int = 0
    groups_created: int = 0


def _group_leftovers(
    leftovers: list[Decision], embeddings: dict, cfg: FilingConfig
) -> tuple[list[list[int]], set[int]]:
    """Cluster leftover faces. Returns (groups of face ids, ids left as noise)."""
    import numpy as np

    from smart_gallery.services.cluster_faces import (
        DEFAULT_EPS,
        DEFAULT_MIN_SAMPLES,
        _run_clustering,
    )

    ids = [d.face_id for d in leftovers]
    embs = np.stack([embeddings[i] for i in ids]).astype(np.float32)
    labels = _run_clustering(
        embs, "hdbscan", DEFAULT_EPS, DEFAULT_MIN_SAMPLES, cfg.min_group_size
    )
    groups = []
    grouped: set[int] = set()
    for label in sorted({int(v) for v in labels if v >= 0}):
        members = [ids[i] for i in np.where(labels == label)[0]]
        if len(members) >= cfg.min_group_size:
            groups.append(members)
            grouped.update(members)
    return groups, set(ids) - grouped


def file_faces(
    repo: GalleryRepository,
    *,
    group: bool = True,
    config: FilingConfig = FilingConfig(),
    progress_callback: Optional[ProgressCallback] = None,
) -> FileFacesReport:
    ensure_not_mirror(repo)

    def tick(done: int) -> None:
        if progress_callback:
            progress_callback(ProgressEvent(FILING, done, 3, unit="steps"))

    tick(0)
    faces, candidates = repo.load_filing_inputs()
    embeddings = {f.face_id: f.embedding for f in faces}
    tick(1)
    decisions = decide(faces, candidates, config)
    tick(2)

    groups: list[list[int]] = []
    leftovers = [d for d in decisions if d.state == "new"]
    if group and len(leftovers) >= config.min_group_size:
        logger.info(f"Grouping {len(leftovers):,} leftover faces...")
        groups, noise = _group_leftovers(leftovers, embeddings, config)
        in_group = {i for g in groups for i in g}
        decisions = [
            (
                replace(d, state="hidden", person_id=None, suggested_person_id=None)
                if d.face_id in noise
                else d
            )
            for d in decisions
            if d.face_id not in in_group  # apply_filing writes group members
        ]

    repo.apply_filing(decisions, groups)
    if groups:
        # The faces were filed before the new groups existed. File them once
        # more against the new groups too, so a second run finds nothing to do.
        faces, candidates = repo.load_filing_inputs()
        decisions = decide(faces, candidates, config)
        repo.apply_filing(decisions, [])
    tick(3)

    report = FileFacesReport(groups_created=len(groups))
    for d in decisions:
        if d.state == "auto":
            report.auto += 1
        elif d.state == "suggested":
            report.suggested += 1
        elif d.state == "cluster":
            report.grouped += 1
        elif d.state == "hidden":
            report.hidden += 1
        else:
            report.left += 1
    return report
