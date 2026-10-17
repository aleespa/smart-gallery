"""Streamlit dashboard — reads the per-drive SQLite catalog directly.

Unlike the old upload-an-Excel flow, this opens the catalog read-only and pushes
filters down to SQL. The DB path comes from ``SG_DB_PATH`` (set by
``smart-gallery dashboard``) or a sidebar input. Data is cached on the DB file's
mtime, so it refreshes automatically after an import or sync.

The figures themselves live in ``smart_gallery.dashboard.figures`` (shared with
the desktop app); this module only lays them out.
"""

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from smart_gallery.dashboard import figures

st.set_page_config(page_title="Smart Gallery", layout="wide")
st.title("Smart Gallery Dashboard")


@st.cache_data(show_spinner=False)
def load_data(db_path: str, _mtime: float):
    """Load the whole catalog as a DataFrame. Cached on (path, mtime)."""
    return figures.load_catalog_df(db_path)


# ── Resolve the catalog ──────────────────────────────────────────────────────
st.sidebar.header("Catalog")
default_db = os.environ.get("SG_DB_PATH", "")
db_input = st.sidebar.text_input("Drive or catalog path", value=default_db)

if not db_input:
    st.info("Provide a drive (e.g. E:/) or a gallery.db path to begin.")
    st.stop()

from smart_gallery.config import resolve_db_path

db_path = resolve_db_path(db_input)
if not Path(db_path).exists():
    st.error(f"No catalog found at {db_path}. Run `smart-gallery init` first.")
    st.stop()

df_all = load_data(str(db_path), Path(db_path).stat().st_mtime)
if df_all.empty:
    st.warning("The catalog is empty.")
    st.stop()

df_images, df_videos, df_others = figures.split_media(df_all)


@st.fragment
def main_app_block(df_images, df_videos, df_others):
    st.header("Filters")
    date_mask = pd.Series(True, index=df_images.index)
    if "date_taken" in df_images.columns and not df_images["date_taken"].dropna().empty:
        df_dates = df_images[df_images["date_taken"].notna()]
        periods = pd.period_range(
            start=df_dates["date_taken"].min(),
            end=df_dates["date_taken"].max(),
            freq="M",
        )
        period_strs = [p.strftime("%b %Y") for p in periods]
        if len(period_strs) > 1:
            selected = st.select_slider(
                "Image date range",
                options=period_strs,
                value=(period_strs[0], period_strs[-1]),
            )
            start_p = periods[period_strs.index(selected[0])]
            end_p = periods[period_strs.index(selected[1])]
            date_mask &= (df_images["date_taken"].dt.to_period("M") >= start_p) & (
                df_images["date_taken"].dt.to_period("M") <= end_p
            )

    col1, col2 = st.columns(2)
    cameras = (
        df_images["camera"].value_counts().index.tolist()
        if "camera" in df_images.columns
        else []
    )
    selected_cameras = col1.multiselect("Camera", cameras, default=[])
    lenses = (
        df_images["lens"].value_counts().index.tolist()
        if "lens" in df_images.columns
        else []
    )
    selected_lenses = col2.multiselect("Lens", lenses, default=[])

    mask = date_mask
    if selected_cameras:
        mask &= df_images["camera"].isin(selected_cameras)
    if selected_lenses:
        mask &= df_images["lens"].isin(selected_lenses)
    fi = df_images[mask]
    fv = df_videos

    st.header("General Statistics")
    for col, (label, value) in zip(st.columns(5), figures.metrics(fi, fv, df_others)):
        col.metric(label, value)

    st.header("Visualizations")
    _donut_charts(fi, fv, df_others)
    _settings_distribution(fi)
    _stacked_timeline(fi)
    _video_summary(fv)
    _photo_map(fi)
    _tables(fi, fv, df_others)


def _donut_charts(fi, fv, df_others):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("File Types")
        fig = figures.file_type_donut(fi, fv, df_others)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, key="pie_file")
    with c2:
        st.subheader("Cameras")
        fig = figures.top_donut(fi, "camera")
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, key="pie_cam")
    with c3:
        st.subheader("Lenses")
        fig = figures.top_donut(fi, "lens")
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, key="pie_lens")


def _settings_distribution(fi):
    st.subheader("Camera Settings Distribution")
    fig = figures.settings_histograms(fi)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True, key="settings_hist")


def _stacked_timeline(df):
    fig = figures.timeline_by_camera(df)
    if fig is not None:
        st.header("Photography Timeline")
        st.plotly_chart(fig, use_container_width=True, key="timeline")


def _video_summary(fv):
    fig = figures.video_minutes(fv)
    if fig is not None:
        st.header("Video Recording Summary")
        st.plotly_chart(fig, use_container_width=True, key="video_minutes")


def _photo_map(fi):
    fig = figures.photo_map(fi)
    if fig is not None:
        st.header("Photo Map")
        st.caption(f"Displaying {len(figures.geotagged(fi)):,} geotagged photos")
        st.plotly_chart(fig, use_container_width=True, key="map")


def _tables(fi, fv, df_others):
    st.header("Data")
    t1, t2, t3 = st.tabs(
        [
            f"📷 Images ({len(fi):,})",
            f"🎬 Videos ({len(fv):,})",
            f"📄 Others ({len(df_others):,})",
        ]
    )
    with t1:
        st.dataframe(fi, use_container_width=True, height=300)
    with t2:
        st.dataframe(fv, use_container_width=True, height=300)
    with t3:
        st.dataframe(df_others, use_container_width=True, height=300)


main_app_block(df_images, df_videos, df_others)
