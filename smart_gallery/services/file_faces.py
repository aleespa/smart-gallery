"""file-faces: sort scanned faces into people, suggestions and new groups.

Needs no GPU. Loads every face the user has not decided, files each with
``analysis.filing.decide``, optionally groups the leftovers with HDBSCAN, and
writes the result in one transaction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np

from loguru import logger

from smart_gallery.analysis.filing import (
    Candidate,
    Decision,
    FilingConfig,
    best_allowed,
    decide,
)
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

    return _report(decisions, len(groups))


def _report(decisions: list[Decision], groups_created: int) -> FileFacesReport:
    report = FileFacesReport(groups_created=groups_created)
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


# Scores of one face computed in differently shaped blocks can differ in the
# last bits; a face this close to the line is filed again to be safe.
_SLACK = 1e-4


def _same(a: Optional[Candidate], b: Optional[Candidate]) -> bool:
    if a is None or b is None:
        return a is b
    return (
        a.kind == b.kind
        and np.array_equal(a.examples, b.examples)
        and np.array_equal(a.rejected, b.rejected)
    )


def _only_grew(old: Optional[Candidate], new: Optional[Candidate], k: int) -> bool:
    """True when ``new`` is ``old`` with examples added (as a confirm does)
    and ``old`` already had ``k``: then a face's score only rises (the top
    ``k`` of more examples are no lower) and so does its best example, so
    being nearer a rejected face rules out no face that was allowed before.
    Its score after the change is then the higher of the two."""
    if old is None or new is None or old.kind != new.kind:
        return False
    if len(old.examples) < k or not np.array_equal(old.rejected, new.rejected):
        return False
    have = Counter(row.tobytes() for row in new.examples)
    have.subtract(row.tobytes() for row in old.examples)
    return all(n >= 0 for n in have.values())


def refile_after_labels(
    repo: GalleryRepository,
    before: Mapping[int, Optional[Candidate]],
    labeled: Iterable[int],
    *,
    config: FilingConfig = FilingConfig(),
) -> FileFacesReport:
    """File again only the faces a batch of corrections can have moved.

    ``before`` holds the filing candidates (``repo.filing_candidates``) of
    every person the batch touched, read before it was applied; ``labeled``
    are the faces it corrected. A correction changes only those people's
    candidates, and so only the scores against them. Another face keeps its
    decision when it is linked to none of them and its best score against
    them, before and after, stays below both its own best score (its
    ``match_score``) and ``suggest_thresh - margin``: under every filing rule
    those people then neither win, nor come close enough to matter as a
    runner-up or rival. Everything else is filed exactly as ``file_faces``
    (without grouping) would.

    ``hidden`` faces stay hidden whatever the scores, so only those linked to
    a changed person are refreshed; the rest keep their ``match_score``.
    """
    ensure_not_mirror(repo)
    faces, candidates = repo.load_filing_inputs()
    after = {c.person_id: c for c in candidates}
    changed = {pid for pid, old in before.items() if not _same(old, after.get(pid))}
    labeled = set(labeled)
    if not changed and not labeled:
        return FileFacesReport()

    stored = {
        r[0]: (r[1], r[2])
        for r in repo.conn.execute(
            "SELECT id, suggested_person_id, match_score FROM faces"
        )
    }
    affected = []
    to_check = []
    for f in faces:
        suggested = stored.get(f.face_id, (None, None))[0]
        if f.face_id in labeled or f.person_id in changed or suggested in changed:
            affected.append(f)
        elif f.state != "hidden" and changed:
            to_check.append(f)

    if to_check:
        # A person who only gained examples scores no face higher than now.
        old_c = [
            before[p]
            for p in sorted(changed)
            if before[p] is not None
            and not _only_grew(before[p], after.get(p), config.k)
        ]
        new_c = [after[p] for p in sorted(changed) if p in after]
        near = np.maximum(
            best_allowed(to_check, old_c, config.k),
            best_allowed(to_check, new_c, config.k),
        )
        floor = config.suggest_thresh - config.margin - _SLACK
        for f, t in zip(to_check, near):
            if t == -np.inf:
                continue  # none of them can take this face, before or after
            score = stored[f.face_id][1]
            if score is None or t >= min(floor, score - _SLACK):
                affected.append(f)

    logger.info(
        f"Filing {len(affected):,} of {len(faces):,} faces again "
        f"({len(changed):,} people changed)."
    )
    decisions = decide(affected, candidates, config)
    repo.apply_filing(decisions, [])
    return _report(decisions, 0)
