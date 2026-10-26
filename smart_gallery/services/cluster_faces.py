"""cluster-faces and split-person — group face embeddings into people.

``cluster-faces`` now sorts with the People defaults (``file_faces``): faces
go to named people, suggestions or new unnamed groups, and nothing the user
decided is touched.

Modes:
  * default      — file every undecided face and group the leftovers.
  * --rebuild    — first return every undecided face to the pool and drop the
                   unnamed groups (named and hidden people stay).
  * --incremental— file faces into existing people only; no new groups.

``split-person`` re-clusters one person's faces with the clustering helpers
below. Embeddings are L2-normalized, so Euclidean distance is monotonic in
cosine distance. Default clustering is HDBSCAN; DBSCAN with the cosine metric
is available too.
"""

from dataclasses import dataclass

from loguru import logger

from smart_gallery.analysis.filing import FilingConfig
from smart_gallery.db import GalleryRepository
from smart_gallery.services.file_faces import file_faces

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


def _run_clustering(
    embs, algo: str, eps: float, min_samples: int, min_cluster_size: int
):
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


def _face_owners(repo: GalleryRepository) -> dict:
    return {
        r["id"]: r["person_id"]
        for r in repo.conn.execute("SELECT id, person_id FROM faces")
    }


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
    """Sort faces into people with ``file_faces``.

    ``algo``, ``eps``, ``min_samples``, ``min_cluster_size`` and ``pca`` are
    accepted for old command lines but no longer used; ``match_thresh`` is the
    filing step's ``auto_thresh``.
    """
    if (algo, eps, min_samples, min_cluster_size, pca) != (
        "hdbscan",
        DEFAULT_EPS,
        DEFAULT_MIN_SAMPLES,
        DEFAULT_MIN_CLUSTER_SIZE,
        0,
    ):
        logger.info(
            "cluster-faces now sorts with the People defaults; --algo, --eps, "
            "--min-samples, --min-cluster-size and --pca are ignored here "
            "(they still apply to split-person)."
        )
    if rebuild:
        logger.info(
            "--rebuild: returning undecided faces to the pool and dropping "
            "unnamed groups (named people and your choices stay)"
        )
        repo.clear_persons()

    existing = {r["id"] for r in repo.conn.execute("SELECT id FROM persons")}
    before = _face_owners(repo)
    filed = file_faces(
        repo, group=not incremental, config=FilingConfig(auto_thresh=match_thresh)
    )
    gained = {
        pid
        for fid, pid in _face_owners(repo).items()
        if pid is not None and pid in existing and pid != before.get(fid)
    }

    report = ClusterReport(
        persons_created=filed.groups_created,
        persons_matched=len(gained),
        faces_assigned=filed.auto + filed.grouped,
        noise=filed.hidden,
    )
    logger.success(
        f"Sorting done — {report.faces_assigned:,} faces filed, "
        f"{report.persons_created} new group(s), {report.persons_matched} "
        f"existing people gained faces, {filed.suggested:,} suggested, "
        f"{report.noise:,} hidden. Next: `smart-gallery people` to review."
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
    """Re-cluster the faces of ONE (impure) person with tighter settings.

    Only faces the user did not file are re-clustered. The sub-group closest
    to the person's ``user`` faces (or the largest, without any) stays, and
    so does every face that fits no sub-group; each other sub-group becomes a
    new unnamed group whose faces are marked as not this person. When the
    faces form no separate sub-groups nothing changes. The person itself is
    never deleted.

    Defaults are deliberately stricter than a full run (smaller eps / cluster
    size) since the point is to break an over-merged cluster apart.
    """
    if repo.get_person(person_id) is None:
        raise ValueError(f"No person with id {person_id}.")

    import numpy as np

    face_ids, embs, states = repo.load_person_faces(person_id)
    is_user = np.array([s == "user" for s in states], dtype=bool)
    work_ids, work_embs = face_ids[~is_user], embs[~is_user]
    if len(work_ids) == 0:
        logger.warning(f"Person {person_id} has no faces to split.")
        return ClusterReport()

    logger.info(
        f"Splitting person {person_id} ({len(work_ids):,} faces) with {algo} "
        f"(eps={eps}, min_cluster_size={min_cluster_size})…"
    )
    labels = _run_clustering(
        _maybe_pca(work_embs, pca), algo, eps, min_samples, min_cluster_size
    )
    label_set = sorted({int(v) for v in labels if v >= 0})

    keep = None
    if label_set:
        if is_user.any():
            anchor = embs[is_user].mean(axis=0)

            def closeness(label: int) -> float:
                c = work_embs[labels == label].mean(axis=0)
                return float(c @ anchor / (np.linalg.norm(c) or 1.0))

            keep = max(label_set, key=closeness)
        else:
            keep = max(label_set, key=lambda lb: int((labels == lb).sum()))

    groups = [work_ids[labels == lb].tolist() for lb in label_set if lb != keep]
    if not groups:
        logger.warning(
            f"Could not split person {person_id}: its faces do not form "
            "separate groups. Nothing changed."
        )
        return ClusterReport()
    created = repo.apply_split(person_id, groups)

    report = ClusterReport(
        persons_created=len(created),
        faces_assigned=sum(len(g) for g in groups),
    )
    logger.success(
        f"Split person {person_id}: {report.faces_assigned:,} face(s) moved to "
        f"{report.persons_created} new group(s); the rest stay. Review with "
        "`smart-gallery people`."
    )
    return report
