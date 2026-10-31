"""Move a photo's faces to another media row, keeping face ids.

Used when a CR3 is replaced by its embedded JPEG. Boxes are stored in the
pixels the scanner decoded: the unrotated embedded preview for a RAW, the
EXIF-oriented image for a JPEG, so boxes are rotated into the oriented frame.
Nothing here commits; the caller owns the transaction.
"""

from __future__ import annotations

import sqlite3

Box = tuple[float, float, float, float]


def rotate_box(box: Box, orientation: int | None, width: int, height: int) -> Box:
    """Map an (x, y, w, h) box from the stored width x height frame to the
    EXIF-oriented frame. Mirrored orientations are refused."""
    x, y, w, h = box
    if orientation in (None, 1):
        return (x, y, w, h)
    if orientation == 3:
        return (width - x - w, height - y - h, w, h)
    if orientation == 6:
        return (height - y - h, x, h, w)
    if orientation == 8:
        return (y, width - x - w, h, w)
    raise ValueError(f"Unsupported EXIF orientation {orientation}")


def move_faces(
    conn: sqlite3.Connection,
    src_id: int,
    dst_id: int,
    orientation: int | None,
    width: int,
    height: int,
) -> list[int]:
    """Re-point src's faces, carry-over rows and scan state to dst.

    Returns the moved face ids. Does not commit.
    """
    swap = orientation in (6, 8)
    # Validate the orientation before touching any row.
    rotate_box((0, 0, 0, 0), orientation, width, height)

    ids = []
    rows = conn.execute(
        "SELECT id, bbox_x, bbox_y, bbox_w, bbox_h FROM faces "
        "WHERE media_id=? ORDER BY id",
        (src_id,),
    ).fetchall()
    for r in rows:
        x, y, w, h = rotate_box((r[1], r[2], r[3], r[4]), orientation, width, height)
        conn.execute(
            "UPDATE faces SET media_id=?, bbox_x=?, bbox_y=?, bbox_w=?, bbox_h=? "
            "WHERE id=?",
            (dst_id, x, y, w, h, r[0]),
        )
        ids.append(r[0])

    rows = conn.execute(
        "SELECT id, bbox_x, bbox_y, bbox_w, bbox_h, img_w, img_h "
        "FROM face_carryover WHERE media_id=?",
        (src_id,),
    ).fetchall()
    for r in rows:
        x, y, w, h = rotate_box((r[1], r[2], r[3], r[4]), orientation, width, height)
        iw, ih = (r[6], r[5]) if swap else (r[5], r[6])
        conn.execute(
            "UPDATE face_carryover SET media_id=?, bbox_x=?, bbox_y=?, bbox_w=?, "
            "bbox_h=?, img_w=?, img_h=? WHERE id=?",
            (dst_id, x, y, w, h, iw, ih, r[0]),
        )

    has_state = conn.execute(
        "SELECT 1 FROM face_scan_state WHERE media_id=?", (src_id,)
    ).fetchone()
    if has_state:
        conn.execute("DELETE FROM face_scan_state WHERE media_id=?", (dst_id,))
        swap_sql = ", img_w=img_h, img_h=img_w" if swap else ""
        conn.execute(
            f"UPDATE face_scan_state SET media_id=?{swap_sql} WHERE media_id=?",
            (dst_id, src_id),
        )
    return ids
