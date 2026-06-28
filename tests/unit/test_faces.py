"""Face-recognition schema, models, repository CRUD and clustering.

These exercise everything that does NOT need a GPU or the heavy InsightFace
model. The clustering test importorskips scikit-learn so it runs only when the
'faces' extra is installed; everything else relies only on numpy (a core dep).
"""

import sqlite3

import numpy as np
import pytest

from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.db import GalleryRepository
from smart_gallery.db.schema import (
    CREATE_FACES,
    CREATE_MEDIA_ITEMS,
    CREATE_META,
    CREATE_PERSONS,
)
from smart_gallery.models import (
    FACE_COLUMNS,
    PERSON_COLUMNS,
    Face,
    Person,
    blob_to_embedding,
    embedding_to_blob,
)


def _unit(*nonzero_at) -> np.ndarray:
    """A length-512 float32 unit vector with 1.0 at the given indices."""
    v = np.zeros(512, dtype=np.float32)
    for i in nonzero_at:
        v[i] = 1.0
    return v / np.linalg.norm(v)


def _add_media(repo, make_item, relpath) -> int:
    repo.upsert_many([make_item(relpath)])
    return repo.conn.execute(
        "SELECT id FROM media_items WHERE relpath=?", (relpath,)
    ).fetchone()[0]


# ── schema lock-step ─────────────────────────────────────────────────────────
def test_face_person_columns_match_schema():
    conn = sqlite3.connect(":memory:")
    conn.execute(CREATE_PERSONS)
    conn.execute(CREATE_FACES)

    fcols = [r[1] for r in conn.execute("PRAGMA table_info(faces)")]
    assert [c for c in fcols if c != "id"] == list(FACE_COLUMNS)

    pcols = [r[1] for r in conn.execute("PRAGMA table_info(persons)")]
    managed = {"id", "created_at", "updated_at"}
    assert [c for c in pcols if c not in managed] == list(PERSON_COLUMNS)


def test_embedding_blob_roundtrip():
    v = np.random.rand(512).astype(np.float32)
    blob = embedding_to_blob(v)
    assert len(blob) == 512 * 4
    assert np.allclose(blob_to_embedding(blob), v)
    assert blob_to_embedding(None) is None


def test_face_as_params_order():
    emb = _unit(0)
    face = Face.from_detection(7, (10.0, 20.0, 50.0, 80.0), 0.91, emb)
    params = face.as_params()
    assert len(params) == len(FACE_COLUMNS)
    assert params[FACE_COLUMNS.index("media_id")] == 7
    assert params[FACE_COLUMNS.index("bbox_w")] == 40.0  # 50 - 10
    assert params[FACE_COLUMNS.index("bbox_h")] == 60.0  # 80 - 20
    assert isinstance(params[FACE_COLUMNS.index("embedding")], bytes)


def test_person_as_params():
    p = Person(name="Alice", cluster_id=3, centroid=_unit(1), face_count=5)
    params = p.as_params()
    assert params[PERSON_COLUMNS.index("name")] == "Alice"
    assert isinstance(params[PERSON_COLUMNS.index("centroid")], bytes)
    assert Person().as_params()[PERSON_COLUMNS.index("centroid")] is None


# ── migration v1 -> v2 ───────────────────────────────────────────────────────
def test_migrate_v1_catalog_gains_face_tables(tmp_path):
    db = tmp_path / "gallery.db"
    conn = sqlite3.connect(db)
    conn.execute(CREATE_META)
    conn.execute(CREATE_MEDIA_ITEMS)
    conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1')")
    conn.commit()
    conn.close()

    with GalleryRepository.open(db) as repo:  # read-write -> migrate runs
        assert repo.schema_version == 2
        tables = {
            r[0]
            for r in repo.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"faces", "persons", "face_scan_state"} <= tables


# ── repository CRUD ──────────────────────────────────────────────────────────
def test_scan_results_resumability(repo, make_item):
    mid_a = _add_media(repo, make_item, "a.jpg")
    mid_b = _add_media(repo, make_item, "b.jpg")  # will have zero faces

    assert repo.count_unscanned_images(FACE_DET_VERSION) == 2

    face = Face.from_detection(mid_a, (10, 10, 50, 60), 0.9, _unit(0))
    inserted = repo.add_scan_results(
        [(mid_a, [face]), (mid_b, [])], FACE_DET_VERSION
    )
    assert inserted == 1

    # Both images now recorded as scanned (b with n_faces=0) -> nothing pending.
    assert repo.count_unscanned_images(FACE_DET_VERSION) == 0
    assert repo.unscanned_images(FACE_DET_VERSION) == []

    ids, embs, pids = repo.load_embeddings()
    assert ids.shape == (1,)
    assert embs.shape == (1, 512)
    assert pids[0] is None


def test_invalidate_faces_by_relpath(repo, make_item):
    mid = _add_media(repo, make_item, "a.jpg")
    repo.add_scan_results(
        [(mid, [Face.from_detection(mid, (0, 0, 40, 40), 0.9, _unit(0))])],
        FACE_DET_VERSION,
    )
    assert repo.faces_count() == 1

    repo.invalidate_faces_by_relpath(["a.jpg"])
    assert repo.faces_count() == 0
    assert repo.count_unscanned_images(FACE_DET_VERSION) == 1  # pending again


def test_delete_media_cascades_faces(repo, make_item):
    mid = _add_media(repo, make_item, "a.jpg")
    repo.add_scan_results(
        [(mid, [Face.from_detection(mid, (0, 0, 40, 40), 0.9, _unit(0))])],
        FACE_DET_VERSION,
    )
    repo.delete_by_relpaths(["a.jpg"])  # FK ON DELETE CASCADE
    assert repo.faces_count() == 0


def test_person_assign_and_recompute(repo, make_item):
    mid = _add_media(repo, make_item, "a.jpg")
    e1, e2 = _unit(0), _unit(1)
    f1 = Face.from_detection(mid, (0, 0, 40, 40), 0.9, e1)  # higher score -> cover
    f2 = Face.from_detection(mid, (0, 0, 40, 40), 0.5, e2)
    repo.add_scan_results([(mid, [f1, f2])], FACE_DET_VERSION)

    ids, _, _ = repo.load_embeddings()
    pid = repo.create_person(Person(cluster_id=0))
    repo.assign_faces(pid, 0, ids.tolist())
    repo.recompute_person(pid)

    person = repo.get_person(pid)
    assert person["face_count"] == 2

    _, centroids = repo.load_person_centroids()
    expected = (e1 + e2) / np.linalg.norm(e1 + e2)
    assert np.allclose(centroids[0], expected, atol=1e-5)

    listed = repo.list_persons()
    assert listed[0]["cover_relpath"] == "a.jpg"


def test_person_samples_top_n_distinct_media(repo, make_item):
    mids = [_add_media(repo, make_item, f"p{i}.jpg") for i in range(4)]
    results = [
        # two faces in the same photo -> that photo counts once, by its best score
        (mids[0], [
            Face.from_detection(mids[0], (0, 0, 40, 40), 0.90, _unit(0)),
            Face.from_detection(mids[0], (0, 0, 40, 40), 0.95, _unit(0)),
        ]),
        (mids[1], [Face.from_detection(mids[1], (0, 0, 40, 40), 0.80, _unit(0))]),
        (mids[2], [Face.from_detection(mids[2], (0, 0, 40, 40), 0.70, _unit(0))]),
        (mids[3], [Face.from_detection(mids[3], (0, 0, 40, 40), 0.60, _unit(0))]),
    ]
    repo.add_scan_results(results, FACE_DET_VERSION)
    ids, _, _ = repo.load_embeddings()
    pid = repo.create_person(Person(cluster_id=0))
    repo.assign_faces(pid, 0, ids.tolist())

    samples = repo.person_samples(limit=3)
    assert samples[pid] == ["p0.jpg", "p1.jpg", "p2.jpg"]  # distinct, best-first, capped


def test_filter_by_person_id_and_name(repo, make_item):
    from smart_gallery.organize.filters import FilterOptions

    mid_a = _add_media(repo, make_item, "a.jpg")
    mid_b = _add_media(repo, make_item, "b.jpg")
    repo.add_scan_results(
        [
            (mid_a, [Face.from_detection(mid_a, (0, 0, 40, 40), 0.9, _unit(0))]),
            (mid_b, [Face.from_detection(mid_b, (0, 0, 40, 40), 0.9, _unit(1))]),
        ],
        FACE_DET_VERSION,
    )
    fa = repo.conn.execute(
        "SELECT id FROM faces WHERE media_id=?", (mid_a,)
    ).fetchone()[0]
    pid = repo.create_person(Person(cluster_id=0))
    repo.assign_faces(pid, 0, [fa])

    # Filter by id works while the cluster is still unnamed.
    assert repo.query_relpaths(FilterOptions(person_ids=[pid])) == ["a.jpg"]
    # An id with no faces yields nothing.
    assert repo.query_relpaths(FilterOptions(person_ids=[9999])) == []
    # And by name once named.
    repo.set_person_name(pid, "Alice")
    assert repo.query_relpaths(FilterOptions(people=["Alice"])) == ["a.jpg"]


def test_delete_person_unassigns_faces(repo, make_item):
    mid = _add_media(repo, make_item, "a.jpg")
    repo.add_scan_results(
        [(mid, [Face.from_detection(mid, (0, 0, 40, 40), 0.9, _unit(0))])],
        FACE_DET_VERSION,
    )
    ids, _, _ = repo.load_embeddings()
    pid = repo.create_person(Person(cluster_id=0))
    repo.assign_faces(pid, 0, ids.tolist())

    assert repo.delete_person(pid) is True
    assert repo.get_person(pid) is None
    assert repo.faces_count() == 1  # face kept, just unassigned
    _, _, pids = repo.load_embeddings()
    assert pids[0] is None
    assert repo.delete_person(9999) is False


@pytest.mark.faces
def test_split_person_breaks_impure_cluster(repo, make_item):
    pytest.importorskip("sklearn")
    from smart_gallery.services.cluster_faces import split_person

    rng = np.random.default_rng(1)
    mid = _add_media(repo, make_item, "a.jpg")
    faces = []
    for center in (_unit(0), _unit(200)):  # two genuinely different people
        for _ in range(6):
            v = center + 0.01 * rng.standard_normal(512).astype(np.float32)
            faces.append(
                Face.from_detection(
                    mid, (0, 0, 40, 40), 0.9, (v / np.linalg.norm(v)).astype("float32")
                )
            )
    repo.add_scan_results([(mid, faces)], FACE_DET_VERSION)

    ids, _, _ = repo.load_embeddings()
    impure = repo.create_person(Person(cluster_id=0))  # both people lumped together
    repo.assign_faces(impure, 0, ids.tolist())
    repo.recompute_person(impure)
    assert len(repo.list_persons()) == 1

    report = split_person(repo, impure, algo="dbscan", eps=0.3, min_samples=2)
    assert report.persons_created == 2
    people = repo.list_persons()
    assert len(people) == 2  # the one impure cluster became two clean ones
    assert sorted(p["face_count"] for p in people) == [6, 6]


def test_merge_persons(repo, make_item):
    mid = _add_media(repo, make_item, "a.jpg")
    f1 = Face.from_detection(mid, (0, 0, 40, 40), 0.9, _unit(0))
    f2 = Face.from_detection(mid, (0, 0, 40, 40), 0.8, _unit(1))
    repo.add_scan_results([(mid, [f1, f2])], FACE_DET_VERSION)
    ids, _, _ = repo.load_embeddings()

    p1 = repo.create_person(Person(cluster_id=0))
    p2 = repo.create_person(Person(cluster_id=1))
    repo.assign_faces(p1, 0, [int(ids[0])])
    repo.assign_faces(p2, 1, [int(ids[1])])
    repo.recompute_person(p1)
    repo.recompute_person(p2)

    repo.merge_persons(p1, [p2])
    assert repo.get_person(p2) is None
    assert repo.get_person(p1)["face_count"] == 2


# ── clustering (needs the 'faces' extra for scikit-learn) ────────────────────
@pytest.mark.faces
def test_cluster_synthetic_three_people(repo, make_item):
    pytest.importorskip("sklearn")
    from smart_gallery.services.cluster_faces import cluster_faces

    rng = np.random.default_rng(0)
    mid = _add_media(repo, make_item, "a.jpg")
    centers = [_unit(0), _unit(100), _unit(300)]
    faces = []
    for c in centers:
        for _ in range(10):
            v = c + 0.01 * rng.standard_normal(512).astype(np.float32)
            v = v / np.linalg.norm(v)
            faces.append(
                Face.from_detection(mid, (0, 0, 40, 40), 0.9, v.astype(np.float32))
            )
    repo.add_scan_results([(mid, faces)], FACE_DET_VERSION)

    report = cluster_faces(repo, algo="dbscan", eps=0.3, min_samples=3)
    assert report.persons_created == 3
    assert report.faces_assigned == 30
    assert len(repo.list_persons()) == 3
