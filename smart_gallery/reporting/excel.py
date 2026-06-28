"""Optional Excel report renderer.

This is presentation only — the catalog is the SQLite DB, not this workbook.
Reads from a repository and writes a formatted 3-sheet ``.xlsx`` (images/videos/
others) with clickable file links, autofilter and frozen headers.
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from smart_gallery.config import to_abspath
from smart_gallery.db import GalleryRepository
from smart_gallery.organize import FilterOptions

_IMAGE_COLS = [
    "name", "ext", "directory_rel", "Full path", "date_taken", "time_taken",
    "camera", "lens", "focal_length", "aperture", "iso", "shutter_speed",
    "latitude", "longitude", "altitude", "size_bytes", "size (MB)", "width", "height",
]
_VIDEO_COLS = [
    "name", "ext", "directory_rel", "Full path", "date_taken", "time_taken",
    "size_bytes", "size (MB)", "width", "height", "duration_ms", "codec", "frame_rate",
]
_OTHER_COLS = ["name", "ext", "directory_rel", "Full path", "size_bytes", "size (MB)"]


def export_report(
    repo: GalleryRepository,
    output_path,
    filters: Optional[FilterOptions] = None,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df = repo.query_df(filters)
    if not df.empty:
        df["Full path"] = df["relpath"].map(
            lambda r: str(to_abspath(r, repo.drive_root))
        )

    def _subset(media_type: str, columns):
        if df.empty:
            return pd.DataFrame(columns=columns)
        part = df[df["media_type"] == media_type]
        present = [c for c in columns if c in part.columns]
        return part[present].reset_index(drop=True)

    images = _subset("image", _IMAGE_COLS)
    videos = _subset("video", _VIDEO_COLS)
    others = _subset("other", _OTHER_COLS)

    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        _to_sheet(images, writer, "images")
        _to_sheet(videos, writer, "videos")
        _to_sheet(others, writer, "others")
    return output_path


def _to_sheet(df: pd.DataFrame, writer: pd.ExcelWriter, sheet_name: str) -> None:
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    workbook = writer.book
    worksheet = writer.sheets[sheet_name]

    header_format = workbook.add_format(
        {
            "bold": True,
            "valign": "middle",
            "align": "left",
            "fg_color": "#000000",
            "font_color": "#FFFFFF",
            "border": 1,
        }
    )
    for col_num, column in enumerate(df.columns):
        worksheet.write(0, col_num, column, header_format)

    if "Full path" in df.columns:
        idx = df.columns.get_loc("Full path")
        for row_num, path in enumerate(df["Full path"], 1):
            if pd.notna(path):
                worksheet.write_url(row_num, idx, f"file:///{path}", string=str(path))

    for col_num, column in enumerate(df.columns):
        column_data = df[column].astype(str)
        width = max(column_data.map(len).max() if not df.empty else 0, len(str(column)))
        worksheet.set_column(col_num, col_num, width + 2)

    worksheet.autofilter(0, 0, max(len(df), 1), max(len(df.columns) - 1, 0))
    worksheet.freeze_panes(1, 0)
