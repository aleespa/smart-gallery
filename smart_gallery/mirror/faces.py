"""Copy face data from a working catalog to its mirror, and guard the mirror.

Faces are scanned only on the working drive. A mirror's face tables are an
exact derived copy: same face and person ids, with ``media_id`` remapped
through ``relpath`` because media ids differ between catalogs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from smart_gallery.db import GalleryRepository
from smart_gallery.progress import FILING, ProgressCallback, ProgressEvent

_FACE_META = (
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "det_score",
    "person_id",
    "cluster_id",
    "state",
    "suggested_person_id",
    "match_score",
)
_PERSON_COLS = (
    "name",
    "cluster_id",
    "centroid",
    "reference_centroid",
    "face_count",
    "cover_face_id",
    "hidden",
    "created_at",
    "updated_at",
)
_STATE_COLS = ("scanned_at", "n_faces", "det_version", "img_w", "img_h")
_FACE_TABLES = (
    "face_rejections",
    "face_carryover",
    "face_scan_state",
    "faces",
    "persons",
)
_STEPS = 5


@dataclass
class FaceSyncReport:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    persons: int = 0
    reset: bool = False


def ensure_not_mirror(repo: GalleryRepository) -> None:
    """Refuse face commands on a mirror: its face data is copied, not made."""
    pairing = repo.get_pairing()
    if pairing is not None and pairing.role == "mirror":
        raise ValueError(
            "This gallery is a mirror; run face commands on the working drive."
        )


def _read_embeddings(repo: GalleryRepository, ids: List[int]) -> Dict[int, bytes]:
    out: Dict[int, bytes] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        ph = ", ".join("?" for _ in chunk)
        for r in repo.conn.execute(
            f"SELECT id, embedding FROM faces WHERE id IN ({ph})", chunk
        ):
            out[r["id"]] = r["embedding"]
    return out


def _relpaths(repo: GalleryRepository) -> Dict[int, str]:
    return {
        r["id"]: r["relpath"].casefold()
        for r in repo.conn.execute("SELECT id, relpath FROM media_items")
    }


def sync_face_data(
    working: GalleryRepository,
    mirror: GalleryRepository,
    progress_callback: Optional[ProgressCallback] = None,
) -> FaceSyncReport:
    """Make the mirror's face tables an exact copy of the working catalog's."""
    report = FaceSyncReport()

    def tick(done: int) -> None:
        if progress_callback:
            progress_callback(ProgressEvent(FILING, done, _STEPS, unit="steps"))

    tick(0)
    conn = mirror.conn
    conn.commit()  # everything below runs as one transaction
    try:
        source = working.get_meta("db_uuid")
        if mirror.face_source_uuid() != source:
            for table in _FACE_TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('mirror_face_source', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (source,),
            )
            report.reset = True

        w_rel = _relpaths(working)
        m_rel = _relpaths(mirror)
        m_ids = {rel: mid for mid, rel in m_rel.items()}
        w_to_m = {wid: m_ids[rel] for wid, rel in w_rel.items() if rel in m_ids}

        # persons first so faces can point at them
        cols = ", ".join(_PERSON_COLS)
        marks = ", ".join("?" for _ in range(len(_PERSON_COLS) + 1))
        upd = ", ".join(f"{c}=excluded.{c}" for c in _PERSON_COLS)
        w_persons = working.conn.execute(f"SELECT id, {cols} FROM persons").fetchall()
        for p in w_persons:
            conn.execute(
                f"INSERT INTO persons (id, {cols}) VALUES ({marks}) "
                f"ON CONFLICT(id) DO UPDATE SET {upd}",
                tuple(p),
            )
        report.persons = len(w_persons)
        tick(1)

        # faces: diff on the non-embedding columns
        meta_sql = ", ".join(_FACE_META)
        w_faces = {
            r["id"]: r
            for r in working.conn.execute(f"SELECT id, media_id, {meta_sql} FROM faces")
            if r["media_id"] in w_to_m
        }
        m_faces = {
            r["id"]: r
            for r in conn.execute(f"SELECT id, media_id, {meta_sql} FROM faces")
        }

        def sig(row, rel_of) -> tuple:
            return (rel_of[row["media_id"]],) + tuple(row[c] for c in _FACE_META)

        to_insert = [i for i in w_faces if i not in m_faces]
        to_update = [
            i
            for i, r in w_faces.items()
            if i in m_faces and sig(r, w_rel) != sig(m_faces[i], m_rel)
        ]
        to_delete = [i for i in m_faces if i not in w_faces]

        for i in to_delete:
            conn.execute("DELETE FROM faces WHERE id=?", (i,))
        set_sql = ", ".join(f"{c}=?" for c in ("media_id",) + _FACE_META)
        for i in to_update:
            r = w_faces[i]
            conn.execute(
                f"UPDATE faces SET {set_sql} WHERE id=?",
                (w_to_m[r["media_id"]],) + tuple(r[c] for c in _FACE_META) + (i,),
            )
        embeddings = _read_embeddings(working, to_insert) if to_insert else {}
        ins_marks = ", ".join("?" for _ in range(3 + len(_FACE_META)))
        for i in to_insert:
            r = w_faces[i]
            conn.execute(
                f"INSERT INTO faces (id, media_id, embedding, {meta_sql}) "
                f"VALUES ({ins_marks})",
                (i, w_to_m[r["media_id"]], embeddings[i])
                + tuple(r[c] for c in _FACE_META),
            )
        report.inserted = len(to_insert)
        report.updated = len(to_update)
        report.deleted = len(to_delete)
        tick(2)

        # scan state: replace the rows that differ
        state_sql = ", ".join(_STATE_COLS)

        def states(repo, rel_of) -> dict:
            return {
                rel_of[r["media_id"]]: tuple(r[c] for c in _STATE_COLS)
                for r in repo.conn.execute(
                    f"SELECT media_id, {state_sql} FROM face_scan_state"
                )
                if r["media_id"] in rel_of
            }

        w_states = states(working, w_rel)
        m_states = states(mirror, m_rel)
        for rel, vals in w_states.items():
            if rel in m_ids and m_states.get(rel) != vals:
                conn.execute(
                    f"INSERT OR REPLACE INTO face_scan_state "
                    f"(media_id, {state_sql}) VALUES (?, ?, ?, ?, ?, ?)",
                    (m_ids[rel],) + vals,
                )
        for rel in m_states:
            if rel not in w_states:
                conn.execute(
                    "DELETE FROM face_scan_state WHERE media_id=?", (m_ids[rel],)
                )
        tick(3)

        # decisions: replace wholesale
        conn.execute("DELETE FROM face_rejections")
        for r in working.conn.execute("SELECT face_id, person_id FROM face_rejections"):
            if r["face_id"] in w_faces:
                conn.execute(
                    "INSERT OR IGNORE INTO face_rejections (face_id, person_id) "
                    "VALUES (?, ?)",
                    (r["face_id"], r["person_id"]),
                )
        conn.execute("DELETE FROM face_carryover")
        for r in working.conn.execute("SELECT * FROM face_carryover"):
            if r["media_id"] in w_to_m:
                conn.execute(
                    "INSERT INTO face_carryover (id, media_id, bbox_x, bbox_y, "
                    "bbox_w, bbox_h, img_w, img_h, state, person_id, rejected) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        r["id"],
                        w_to_m[r["media_id"]],
                        r["bbox_x"],
                        r["bbox_y"],
                        r["bbox_w"],
                        r["bbox_h"],
                        r["img_w"],
                        r["img_h"],
                        r["state"],
                        r["person_id"],
                        r["rejected"],
                    ),
                )
        tick(4)

        # stale persons last so no kept face loses its person
        keep = {p["id"] for p in w_persons}
        for r in conn.execute("SELECT id FROM persons").fetchall():
            if r["id"] not in keep:
                conn.execute("DELETE FROM persons WHERE id=?", (r["id"],))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    tick(_STEPS)
    return report
