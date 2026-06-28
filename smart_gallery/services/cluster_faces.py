"""cluster-faces — group face embeddings into people.

Embeddings are L2-normalized, so Euclidean distance is monotonic in cosine
distance. Default clustering is HDBSCAN (no eps tuning, density-aware); DBSCAN
with cosine metric is available too. Each resulting cluster becomes a ``persons``
row (unnamed) with a centroid for later incremental matching.

Modes:
  * default      — cluster faces not yet assigned to a person (first run = all).
  * --rebuild    — clear all persons and re-cluster everything from scratch.
  * --incremental— match unassigned faces to existing person centroids (fast;
                   for new photos added by a later sync + scan-faces).
"""

from collections import defaultdict
from dataclasses import dataclass

from loguru import logger

from smart_gallery.db import GalleryRepository
from smart_gallery.models import Person

DEFAULT_EPS = 0.45
DEFAULT_MIN_SAMPLES = 4
DEFAULT_MIN_CLUSTER_SIZE = 5
DEFAULT_MATCH_THRESH = 0.5


@dataclass
class ClusterReport:
    persons_created: int = 0
    persons_matched: int = 0
    faces_assigned: int = 0
    noise: int = 0


def _maybe_pca(embs, pca: int):
    """Optionally reduce embedding dimensionality before clustering. ArcFace
    vectors keep almost all of their discriminative variance in well under 512
    dims, and a smaller space makes HDBSCAN's space-partitioning tree (which
    degrades badly past ~50-d) far faster. Re-normalize so cosine geometry
    holds. Off by default to preserve maximum accuracy."""
    if not pca or pca >= embs.shape[1] or embs.shape[0] <= pca:
        return embs
    import numpy as np
    from sklearn.decomposition import PCA

    reduced = PCA(n_components=pca, random_state=0).fit_transform(embs)
    norms = np.linalg.norm(reduced, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    logger.info(f"PCA-reduced embeddings {embs.shape[1]} -> {pca} dims for clustering")
    return (reduced / norms).astype("float32")


def _run_clustering(embs, algo: str, eps: float, min_samples: int,
                    min_cluster_size: int):
    """Return an integer label per row (>=0 = cluster, -1 = noise)."""
    if algo == "dbscan":
        from sklearn.cluster import DBSCAN

        return DBSCAN(
            eps=eps, min_samples=min_samples, metric="cosine", n_jobs=-1
        ).fit_predict(embs)
    try:
        from hdbscan import HDBSCAN
    except ImportError as exc:  # pragma: no cover - optional within the extra
        raise RuntimeError(
            "hdbscan not installed; use --algo dbscan or `uv sync --extra faces`."
        ) from exc
    # Euclidean on L2-normalized vectors ranks the same as cosine.
    # core_dist_n_jobs=-1 parallelizes the dominant core-distance phase across
    # all cores — the biggest safe speedup at scale (no effect on the result).
    return HDBSCAN(
        min_cluster_size=min_cluster_size, metric="euclidean", core_dist_n_jobs=-1
    ).fit_predict(embs)


def cluster_faces(
    repo: GalleryRepository,
    *,
    algo: str = "hdbscan",
    eps: float = DEFAULT_EPS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    rebuild: bool = False,
    incremental: bool = False,
    match_thresh: float = DEFAULT_MATCH_THRESH,
    pca: int = 0,
) -> ClusterReport:
    if incremental:
        return _incremental(repo, match_thresh)

    if rebuild:
        logger.info("--rebuild: clearing existing persons and re-clustering all faces")
        repo.clear_persons()

    face_ids, embs, _ = repo.load_embeddings(only_unassigned=not rebuild)
    if len(face_ids) == 0:
        logger.success("No unassigned faces to cluster.")
        return ClusterReport()

    embs = _maybe_pca(embs, pca)
    logger.info(f"Clustering {len(face_ids):,} faces with {algo}…")
    labels = _run_clustering(embs, algo, eps, min_samples, min_cluster_size)

    import numpy as np

    report = ClusterReport(noise=int((labels == -1).sum()))
    for label in sorted({int(v) for v in labels if v >= 0}):
        member_idx = np.where(labels == label)[0]
        member_fids = face_ids[member_idx].tolist()
        person_id = repo.create_person(Person(cluster_id=label))
        repo.assign_faces(person_id, label, member_fids)
        repo.recompute_person(person_id)
        report.persons_created += 1
        report.faces_assigned += len(member_fids)

    logger.success(
        f"Clustering done — {report.persons_created} people from "
        f"{report.faces_assigned:,} faces ({report.noise:,} ungrouped). "
        f"Next: `smart-gallery people` to review, then `name-person`."
    )
    return report


def split_person(
    repo: GalleryRepository,
    person_id: int,
    *,
    algo: str = "hdbscan",
    eps: float = 0.30,
    min_samples: int = 3,
    min_cluster_size: int = 3,
    pca: int = 0,
) -> ClusterReport:
    """Re-cluster the faces of ONE (impure) person with tighter settings,
    replacing it with the resulting sub-clusters. Faces that no longer group
    are left unassigned. Other people are untouched.

    Defaults are deliberately stricter than a full run (smaller eps / cluster
    size) since the point is to break an over-merged cluster apart.
    """
    if repo.get_person(person_id) is None:
        raise ValueError(f"No person with id {person_id}.")

    face_ids, embs = repo.load_face_embeddings_for_person(person_id)
    if len(face_ids) == 0:
        logger.warning(f"Person {person_id} has no faces.")
        return ClusterReport()

    logger.info(
        f"Splitting person {person_id} ({len(face_ids):,} faces) with {algo} "
        f"(eps={eps}, min_cluster_size={min_cluster_size})…"
    )
    work = _maybe_pca(embs, pca)
    labels = _run_clustering(work, algo, eps, min_samples, min_cluster_size)

    import numpy as np

    repo.delete_person(person_id)  # unassigns these faces; we re-assign below

    report = ClusterReport(noise=int((labels == -1).sum()))
    for label in sorted({int(v) for v in labels if v >= 0}):
        member_fids = face_ids[np.where(labels == label)[0]].tolist()
        new_id = repo.create_person(Person(cluster_id=label))
        repo.assign_faces(new_id, label, member_fids)
        repo.recompute_person(new_id)
        report.persons_created += 1
        report.faces_assigned += len(member_fids)

    logger.success(
        f"Split person {person_id} -> {report.persons_created} sub-cluster(s) "
        f"from {report.faces_assigned:,} faces ({report.noise:,} now ungrouped). "
        f"Review with `smart-gallery people`."
    )
    return report


def _incremental(repo: GalleryRepository, match_thresh: float) -> ClusterReport:
    import numpy as np

    face_ids, embs, _ = repo.load_embeddings(only_unassigned=True)
    if len(face_ids) == 0:
        logger.success("No unassigned faces — nothing to match.")
        return ClusterReport()

    person_ids, centroids = repo.load_person_centroids()
    if len(person_ids) == 0:
        logger.warning(
            "No existing people to match against. Run `cluster-faces` (full) first."
        )
        return ClusterReport()

    sims = embs @ centroids.T  # both L2-normalized -> cosine similarity
    best = sims.argmax(axis=1)
    best_sim = sims[np.arange(len(face_ids)), best]

    groups = defaultdict(list)
    for i, fid in enumerate(face_ids):
        if best_sim[i] >= match_thresh:
            groups[int(person_ids[best[i]])].append(int(fid))

    report = ClusterReport()
    for pid, fids in groups.items():
        repo.assign_faces(pid, None, fids)
        repo.recompute_person(pid)
        report.persons_matched += 1
        report.faces_assigned += len(fids)

    logger.success(
        f"Incremental match — {report.faces_assigned:,} faces attached to "
        f"{report.persons_matched} existing people "
        f"({len(face_ids) - report.faces_assigned:,} left unmatched)."
    )
    return report
