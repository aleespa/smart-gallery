"""Streamlit dashboard — reads the per-drive SQLite catalog directly.

Unlike the old upload-an-Excel flow, this opens the catalog read-only and pushes
filters down to SQL. The DB path comes from ``SG_DB_PATH`` (set by
``smart-gallery dashboard``) or a sidebar input. Data is cached on the DB file's
mtime, so it refreshes automatically after an import or sync.
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from smart_gallery.db import GalleryRepository

st.set_page_config(page_title="Smart Gallery", layout="wide")
st.title("Smart Gallery Dashboard")

STACKED_COLORS = [
    "#e60000", "#3d6ba6", "#65880f", "#4e4e94", "#a70000", "#2b4e72",
    "#5f720f", "#313178", "#ff5252", "#5a8cc2", "#8eb027", "#7474b0",
]


@st.cache_data(show_spinner=False)
def load_data(db_path: str, _mtime: float):
    """Load the whole catalog as a DataFrame. Cached on (path, mtime)."""
    with GalleryRepository.open(db_path, read_only=True) as repo:
        df = repo.query_df()
    if "date_taken" in df.columns:
        df["date_taken"] = pd.to_datetime(df["date_taken"], errors="coerce")
    return df


def _parse_shutter(s):
    if pd.isna(s):
        return None
    try:
        s_str = str(s).rstrip("s")
        if "/" in s_str:
            num, den = s_str.split("/")
            return float(num) / float(den)
        return float(s_str)
    except Exception:
        return None


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

df_images = df_all[df_all["media_type"] == "image"].reset_index(drop=True)
df_videos = df_all[df_all["media_type"] == "video"].reset_index(drop=True)
df_others = df_all[df_all["media_type"] == "other"].reset_index(drop=True)


@st.fragment
def main_app_block(df_images, df_videos, df_others):
    st.header("Filters")
    date_mask = pd.Series(True, index=df_images.index)
    if "date_taken" in df_images.columns and not df_images["date_taken"].dropna().empty:
        df_dates = df_images[df_images["date_taken"].notna()]
        periods = pd.period_range(
            start=df_dates["date_taken"].min(), end=df_dates["date_taken"].max(), freq="M"
        )
        period_strs = [p.strftime("%b %Y") for p in periods]
        if len(period_strs) > 1:
            selected = st.select_slider(
                "Image date range", options=period_strs,
                value=(period_strs[0], period_strs[-1]),
            )
            start_p = periods[period_strs.index(selected[0])]
            end_p = periods[period_strs.index(selected[1])]
            date_mask &= (df_images["date_taken"].dt.to_period("M") >= start_p) & (
                df_images["date_taken"].dt.to_period("M") <= end_p
            )

    col1, col2 = st.columns(2)
    cameras = df_images["camera"].value_counts().index.tolist() if "camera" in df_images.columns else []
    selected_cameras = col1.multiselect("Camera", cameras, default=[])
    lenses = df_images["lens"].value_counts().index.tolist() if "lens" in df_images.columns else []
    selected_lenses = col2.multiselect("Lens", lenses, default=[])

    mask = date_mask
    if selected_cameras:
        mask &= df_images["camera"].isin(selected_cameras)
    if selected_lenses:
        mask &= df_images["lens"].isin(selected_lenses)
    fi = df_images[mask]
    fv = df_videos

    st.header("General Statistics")
    total_size = sum(
        d["size (MB)"].sum() for d in (fi, fv, df_others) if "size (MB)" in d.columns
    )
    total_duration_sec = (
        fv["duration_ms"].sum(skipna=True) / 1000.0 if "duration_ms" in fv.columns else 0.0
    )
    h, m, s = int(total_duration_sec // 3600), int((total_duration_sec % 3600) // 60), int(total_duration_sec % 60)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Files", f"{len(fi) + len(fv) + len(df_others):,}")
    c2.metric("Images (filtered)", f"{len(fi):,}")
    c3.metric("Videos", f"{len(fv):,}")
    c4.metric("Total Size", f"{total_size:,.0f} MB")
    c5.metric("Video Length", f"{h}h {m}m {s}s")

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
        counts = {k: v for k, v in {"Images": len(fi), "Videos": len(fv), "Others": len(df_others)}.items() if v > 0}
        if counts:
            fig = px.pie(names=list(counts), values=list(counts.values()),
                         color_discrete_sequence=["#66b3ff", "#99ff99", "#ff9999"], hole=0.4)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(showlegend=False, height=300, margin=dict(l=20, r=20, t=20, b=20))
            st.plotly_chart(fig, use_container_width=True, key="pie_file")
    with c2:
        st.subheader("Cameras")
        if "camera" in fi.columns and fi["camera"].dropna().any():
            cc = fi["camera"].value_counts().nlargest(10).reset_index()
            cc.columns = ["camera", "count"]
            fig = px.pie(cc, names="camera", values="count", color_discrete_sequence=STACKED_COLORS, hole=0.4)
            fig.update_traces(textposition="inside", textinfo="percent")
            fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.5), height=300, margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig, use_container_width=True, key="pie_cam")
    with c3:
        st.subheader("Lenses")
        if "lens" in fi.columns and fi["lens"].dropna().any():
            lc = fi["lens"].value_counts().nlargest(10).reset_index()
            lc.columns = ["lens", "count"]
            fig = px.pie(lc, names="lens", values="count", color_discrete_sequence=STACKED_COLORS, hole=0.4)
            fig.update_traces(textposition="inside", textinfo="percent")
            fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.5), height=300, margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig, use_container_width=True, key="pie_lens")


def _settings_distribution(fi):
    st.subheader("Camera Settings Distribution")
    if fi.empty:
        return
    fig = make_subplots(rows=2, cols=2, subplot_titles=["Aperture", "Shutter Speed", "ISO", "Focal Length"])
    STD_APERTURES = [1.2, 1.8, 2.2, 2.8, 3.5, 4.0, 5.6, 8, 11, 16, 22]
    STD_SHUTTERS = [1/4000, 1/2000, 1/1000, 1/500, 1/250, 1/125, 1/60, 1/30, 1/15, 1/8, 1/4, 1/2, 1, 2, 4, 8, 15, 30]
    STD_ISOS = [100, 200, 400, 800, 1600, 3200, 6400, 12800]
    STD_FOCALS = [14, 18, 24, 28, 35, 50, 70, 85, 105, 135, 200, 300, 400]

    def _log_hist(series, row, col, color, tickvals, ticktext):
        data = pd.to_numeric(series, errors="coerce").dropna()
        data = data[(data > data.quantile(0.01)) & (data <= data.quantile(0.99)) & (data > 0)]
        if data.empty:
            return
        log_data = np.log10(data)
        fig.add_trace(go.Histogram(x=log_data, nbinsx=30, marker_color=color, opacity=0.7, showlegend=False), row=row, col=col)
        valid = [(v, t) for v, t in zip(tickvals, ticktext) if log_data.min() * 0.9 <= np.log10(v) <= log_data.max() * 1.1]
        if valid:
            vt, tt = zip(*valid)
            fig.update_xaxes(tickvals=[np.log10(v) for v in vt], ticktext=list(tt), row=row, col=col, tickangle=45)

    _log_hist(fi.get("aperture", pd.Series()), 1, 1, "coral", STD_APERTURES, [f"f{v:g}" for v in STD_APERTURES])
    ss = fi["shutter_speed"].apply(_parse_shutter) if "shutter_speed" in fi.columns else pd.Series()
    _log_hist(ss, 1, 2, "orchid", STD_SHUTTERS, [f"1/{int(round(1/v))}" if v < 1 else f"{v:g}s" for v in STD_SHUTTERS])
    _log_hist(fi.get("iso", pd.Series()), 2, 1, "gold", STD_ISOS, [str(v) for v in STD_ISOS])
    _log_hist(fi.get("focal_length", pd.Series()), 2, 2, "teal", STD_FOCALS, [f"{v}mm" for v in STD_FOCALS])
    fig.update_layout(height=600, margin=dict(t=50, b=50))
    st.plotly_chart(fig, use_container_width=True, key="settings_hist")


def _stacked_timeline(df):
    if "date_taken" not in df.columns or "camera" not in df.columns or df.empty:
        return
    st.header("Photography Timeline")
    tmp = df.dropna(subset=["date_taken", "camera"]).copy()
    if tmp.empty:
        return
    tmp["period"] = tmp["date_taken"].dt.to_period("M").astype(str)
    top = tmp["camera"].value_counts().nlargest(10).index
    tmp["camera"] = tmp["camera"].where(tmp["camera"].isin(top), other="Other")
    pivot = tmp.groupby(["period", "camera"]).size().reset_index(name="count")
    fig = px.bar(pivot, x="period", y="count", color="camera", barmode="stack",
                 color_discrete_sequence=STACKED_COLORS, title="Monthly Photo Count by Camera")
    fig.update_layout(xaxis_tickangle=45)
    st.plotly_chart(fig, use_container_width=True, key="timeline")


def _video_summary(fv):
    if fv.empty or "date_taken" not in fv.columns or "duration_ms" not in fv.columns:
        return
    st.header("Video Recording Summary")
    tmp = fv.dropna(subset=["date_taken"]).copy()
    if tmp.empty:
        return
    tmp["month"] = tmp["date_taken"].dt.to_period("M").astype(str)
    aggs = tmp.groupby("month")["duration_ms"].sum().reset_index()
    aggs["minutes"] = aggs["duration_ms"] / 60000.0
    fig = px.bar(aggs, x="month", y="minutes", title="Minutes Recorded per Month", color_discrete_sequence=["crimson"])
    fig.update_layout(xaxis_tickangle=45, yaxis_title="Duration (Minutes)")
    st.plotly_chart(fig, use_container_width=True, key="video_minutes")


def _photo_map(fi):
    if fi.empty or "latitude" not in fi.columns or "longitude" not in fi.columns:
        return
    df_loc = fi[["latitude", "longitude", "camera", "date_taken", "name"]].copy()
    df_loc["latitude"] = pd.to_numeric(df_loc["latitude"], errors="coerce")
    df_loc["longitude"] = pd.to_numeric(df_loc["longitude"], errors="coerce")
    df_loc = df_loc.dropna(subset=["latitude", "longitude"])
    if df_loc.empty:
        return
    st.header("Photo Map")
    st.caption(f"Displaying {len(df_loc):,} geotagged photos")
    fig = px.density_map(df_loc, lat="latitude", lon="longitude", radius=10, zoom=2,
                         map_style="open-street-map", color_continuous_scale="Viridis", opacity=0.75)
    fig.update_layout(height=600, margin=dict(l=0, r=0, t=30, b=0), coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True, key="map")


def _tables(fi, fv, df_others):
    st.header("Data")
    t1, t2, t3 = st.tabs([f"📷 Images ({len(fi):,})", f"🎬 Videos ({len(fv):,})", f"📄 Others ({len(df_others):,})"])
    with t1:
        st.dataframe(fi, use_container_width=True, height=300)
    with t2:
        st.dataframe(fv, use_container_width=True, height=300)
    with t3:
        st.dataframe(df_others, use_container_width=True, height=300)


main_app_block(df_images, df_videos, df_others)
