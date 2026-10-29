"""Qt-free query layer for the People area.

Read-only: the GUI reads through ``ctx.open_repo()`` and every write goes
through a CLI job. A catalog with no ``faces.state`` column (older than schema
v5, or with no face tables at all) raises ``NeedsUpgrade`` from
``people_list``; read-only opens never migrate.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, replace
from typing import Literal, Optional, Sequence

import numpy as np

from smart_gallery.config import FACE_DET_VERSION
from smart_gallery.db.repository import GalleryRepository
from smart_gallery.organize.filters import FilterOptions


class NeedsUpgrade(Exception):
    """The catalog is older than schema v5 and was opened read-only."""


@dataclass(frozen=True)
class FaceTile:
    face_id: int
    media_id: int
    relpath: str
    ext: str
    state: str
    score: Optional[float]
    bbox: tuple[float, float, float, float]
    img_size: Optional[tuple[int, int]]


@dataclass(frozen=True)
class PersonRow:
    id: int
    name: Optional[str]
    photos: int
    faces: int
    suggestions: int
    cover: Optional[FaceTile]  # persons.cover_face_id as a tile
    hidden: bool


@dataclass(frozen=True)
class PeopleList:
    named: list[PersonRow]  # non-hidden, photos desc, then name
    groups: list[PersonRow]  # unnamed non-hidden, photos desc
    hidden_groups: list[PersonRow]
    has_faces: bool
    pending_images: int
    last_undo: Optional[str] = None  # summary of the batch Undo would revert


@dataclass(frozen=True)
class PersonSummary:
    row: PersonRow
    first_year: Optional[int]
    last_year: Optional[int]


_TILE_SELECT = (
    "SELECT f.id AS face_id, f.media_id, m.relpath, m.ext, f.state,"
    " f.match_score AS score, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h,"
    " s.img_w, s.img_h"
    " FROM faces f JOIN media_items m ON m.id = f.media_id"
    " LEFT JOIN face_scan_state s ON s.media_id = f.media_id"
)


def _tile(r) -> FaceTile:
    size = (r["img_w"], r["img_h"]) if r["img_w"] and r["img_h"] else None
    return FaceTile(
        r["face_id"],
        r["media_id"],
        r["relpath"],
        r["ext"],
        r["state"],
        r["score"],
        (r["bbox_x"], r["bbox_y"], r["bbox_w"], r["bbox_h"]),
        size,
    )


def _tiles(repo, where: str, params, order: str) -> list[FaceTile]:
    sql = f"{_TILE_SELECT} WHERE {where} ORDER BY {order}"
    return [_tile(r) for r in repo.conn.execute(sql, params)]


_ROWS_SQL = """
SELECT p.id, p.name, p.hidden, p.cover_face_id,
  COALESCE(c.photos, 0) AS photos, COALESCE(c.faces, 0) AS faces,
  COALESCE(s.n, 0) AS suggestions
FROM persons p
LEFT JOIN (
  SELECT person_id, COUNT(DISTINCT media_id) AS photos, COUNT(*) AS faces
  FROM faces WHERE person_id IS NOT NULL GROUP BY person_id
) c ON c.person_id = p.id
LEFT JOIN (
  SELECT suggested_person_id, COUNT(*) AS n FROM faces
  WHERE state = 'suggested' AND suggested_person_id IS NOT NULL
  GROUP BY suggested_person_id
) s ON s.suggested_person_id = p.id
"""
_ONE_ROW_SQL = """
SELECT p.id, p.name, p.hidden, p.cover_face_id,
  (SELECT COUNT(DISTINCT f.media_id) FROM faces f WHERE f.person_id = p.id) AS photos,
  (SELECT COUNT(*) FROM faces f WHERE f.person_id = p.id) AS faces,
  (SELECT COUNT(*) FROM faces f
     WHERE f.suggested_person_id = p.id AND f.state = 'suggested') AS suggestions
FROM persons p WHERE p.id = ?
"""
_IN_CHUNK = 500


def _covers(repo, face_ids: list[int]) -> dict[int, FaceTile]:
    out: dict[int, FaceTile] = {}
    for i in range(0, len(face_ids), _IN_CHUNK):
        chunk = face_ids[i : i + _IN_CHUNK]
        ph = ", ".join("?" for _ in chunk)
        for t in _tiles(repo, f"f.id IN ({ph})", chunk, "f.id"):
            out[t.face_id] = t
    return out


def _person_rows(repo, sql: str = _ROWS_SQL, params=()) -> list[PersonRow]:
    found = repo.conn.execute(sql, params).fetchall()
    covers = _covers(
        repo, [r["cover_face_id"] for r in found if r["cover_face_id"] is not None]
    )
    return [
        PersonRow(
            r["id"],
            r["name"],
            r["photos"],
            r["faces"],
            r["suggestions"],
            covers.get(r["cover_face_id"]),
            bool(r["hidden"]),
        )
        for r in found
    ]


def _has_v5(repo) -> bool:
    cols = {r["name"] for r in repo.conn.execute("PRAGMA table_info(faces)")}
    return "state" in cols


def _by_photos(rows: list[PersonRow]) -> list[PersonRow]:
    return sorted(rows, key=lambda p: (-p.photos, (p.name or "").lower(), p.id))


def people_list(repo: GalleryRepository) -> PeopleList:
    if not _has_v5(repo):
        raise NeedsUpgrade("This catalog needs upgrading before the People page.")
    rows = _person_rows(repo)
    visible = [p for p in rows if not p.hidden]
    has_faces = repo.conn.execute("SELECT 1 FROM faces LIMIT 1").fetchone() is not None
    return PeopleList(
        named=_by_photos([p for p in visible if p.name]),
        groups=_by_photos([p for p in visible if not p.name]),
        hidden_groups=_by_photos([p for p in rows if p.hidden]),
        has_faces=has_faces,
        pending_images=repo.count_unscanned_images(FACE_DET_VERSION),
        last_undo=(info.summary if (info := repo.last_undo()) else None),
    )


def person_summary(repo: GalleryRepository, person_id: int) -> PersonSummary | None:
    rows = _person_rows(repo, _ONE_ROW_SQL, (person_id,))
    if not rows:
        return None
    r = repo.conn.execute(
        "SELECT MIN(substr(m.date_taken, 1, 4)) AS lo,"
        " MAX(substr(m.date_taken, 1, 4)) AS hi"
        " FROM faces f JOIN media_items m ON m.id = f.media_id"
        " WHERE f.person_id = ? AND m.date_taken IS NOT NULL",
        (person_id,),
    ).fetchone()

    def year(v):
        return int(v) if v and str(v).isdigit() else None

    return PersonSummary(rows[0], year(r["lo"]), year(r["hi"]))


def person_faces(
    repo: GalleryRepository,
    person_id: int,
    sort: Literal["least_sure", "newest"],
) -> list[FaceTile]:
    if sort == "newest":
        order = "(m.date_taken IS NULL), m.date_taken DESC, f.id"
    else:
        order = "(f.state = 'user'), (f.match_score IS NOT NULL), f.match_score, f.id"
    return _tiles(repo, "f.person_id = ?", (person_id,), order)


def suggestions_for(repo: GalleryRepository, person_id: int) -> list[FaceTile]:
    return _tiles(
        repo,
        "f.suggested_person_id = ? AND f.state = 'suggested'",
        (person_id,),
        "f.match_score DESC, f.id",
    )


def hidden_faces(repo: GalleryRepository) -> list[FaceTile]:
    return _tiles(
        repo,
        "f.person_id IS NULL AND f.state IN ('hidden', 'ignored')",
        (),
        "(m.date_taken IS NULL), m.date_taken DESC, f.id",
    )


def photos_filter(
    person_ids: Sequence[int], match: Literal["any", "all"]
) -> FilterOptions:
    return FilterOptions(person_ids=list(person_ids), people_match=match)


def library_filter(
    base: FilterOptions, names: Sequence[str], match: str
) -> FilterOptions:
    return replace(base, people=list(names), people_match=match, person_ids=None)


def name_owner(rows: PeopleList, name: str, except_id: int) -> Optional[PersonRow]:
    """The person already called ``name`` (case-insensitive), other than
    ``except_id``."""
    wanted = name.strip().lower()
    if not wanted:
        return None
    for p in (*rows.named, *rows.hidden_groups):
        if p.id != except_id and p.name and p.name.strip().lower() == wanted:
            return p
    return None


FIND_MORE_LIMIT = 300
FIND_MORE_FLOOR = 0.35  # = FilingConfig.suggest_thresh

_POOL_SQL = (
    "SELECT f.id, f.embedding FROM faces f"
    " WHERE f.state NOT IN ('user', 'ignored')"
    " AND (f.person_id IS NULL OR f.person_id IN"
    "  (SELECT id FROM persons WHERE (name IS NULL OR name = '') AND hidden = 0))"
    " AND f.person_id IS NOT :pid"
    " AND NOT EXISTS (SELECT 1 FROM face_rejections r"
    "  WHERE r.face_id = f.id AND r.person_id = :pid)"
)


def find_more(
    repo,
    person_id: int,
    *,
    limit: int = FIND_MORE_LIMIT,
    floor: float = FIND_MORE_FLOOR,
) -> list[FaceTile]:
    """Unclassified faces most like this person, best first. Reads the pool
    in blocks and keeps only the best ``limit``, so memory stays bounded."""
    from smart_gallery.analysis import filing

    examples = repo.person_examples(person_id)
    if not len(examples) or limit <= 0:
        return []
    cand = [
        filing.Candidate(
            person_id, "named", examples, np.empty((0, examples.shape[1]), np.float32)
        )
    ]
    k = filing.FilingConfig().k
    best: list[tuple[float, int]] = []  # min-heap of (score, -face_id)
    cur = repo.conn.execute(_POOL_SQL, {"pid": person_id})
    while True:
        rows = cur.fetchmany(filing.CHUNK)
        if not rows:
            break
        embs = np.stack([np.frombuffer(r["embedding"], np.float32) for r in rows])
        scores = filing.score_matrix(embs, cand, k)[:, 0]
        for r, sc in zip(rows, scores):
            sc = float(sc)
            if sc < floor:
                continue
            item = (sc, -r["id"])
            if len(best) < limit:
                heapq.heappush(best, item)
            elif item > best[0]:
                heapq.heapreplace(best, item)
    ranked = sorted(best, key=lambda t: (-t[0], -t[1]))
    if not ranked:
        return []
    scores_by_id = {-neg: sc for sc, neg in ranked}
    tiles = _covers(repo, list(scores_by_id))
    return [replace(tiles[i], score=sc) for i, sc in scores_by_id.items() if i in tiles]
