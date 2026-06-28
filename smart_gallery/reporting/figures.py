"""Optional static matplotlib/geopandas figures (requires the ``figures`` extra).

Reads straight from the catalog via a repository. The interactive dashboard is
the primary way to explore data; these PNGs are for reports/archives.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from smart_gallery.db import GalleryRepository

PLOT_COLORS = {
    "lens": "lightgreen",
    "aperture": "coral",
    "shutter_speed": "orchid",
    "iso": "gold",
    "focal_length": "teal",
    "video_duration": "crimson",
    "timeline_count": "blue",
    "timeline_time": "darkorange",
    "stacked_colors": [
        "#e60000", "#3d6ba6", "#65880f", "#4e4e94",
        "#a70000", "#2b4e72", "#5f720f", "#313178",
        "#ff5252", "#5a8cc2", "#8eb027", "#7474b0",
    ],
    "pie_chart": ["#ff9999", "#66b3ff", "#99ff99"],
}


def _save(fig, output_dir: Path, filename: str):
    import matplotlib.pyplot as plt

    fig.tight_layout()
    fig.savefig(output_dir / filename, dpi=200, bbox_inches="tight")
    plt.close(fig)


def _parse_shutter(s):
    if pd.isna(s):
        return None
    try:
        if isinstance(s, (int, float)):
            return float(s)
        s_str = str(s).rstrip("s")
        if "/" in s_str:
            num, den = s_str.split("/")
            return float(num) / float(den)
        return float(s_str)
    except Exception:
        return None


def plot_general_counts(df_images, df_videos, df_others, output_dir):
    import matplotlib.pyplot as plt

    counts = {
        "Images": len(df_images),
        "Videos": len(df_videos),
        "Others": len(df_others),
    }
    counts = {k: v for k, v in counts.items() if v > 0}
    if not counts:
        return
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.pie(
        counts.values(),
        labels=counts.keys(),
        autopct="%1.1f%%",
        startangle=140,
        colors=PLOT_COLORS["pie_chart"],
    )
    ax.set_title("File Type Distribution", fontsize=16)
    _save(fig, output_dir, "general_file_types.png")


def plot_cameras(df, output_dir):
    import matplotlib.pyplot as plt

    if "camera" not in df.columns or df["camera"].dropna().empty:
        return
    counts = df["camera"].value_counts().nlargest(15)
    fig, ax = plt.subplots(figsize=(10, 8))
    colors = [
        PLOT_COLORS["stacked_colors"][i % len(PLOT_COLORS["stacked_colors"])]
        for i in range(len(counts))
    ]
    wedges, _ = ax.pie(counts.values, startangle=140, colors=colors)
    total = sum(counts.values)
    labels = [f"{i} ({v / total * 100:.1f}%)" for i, v in zip(counts.index, counts.values)]
    ax.legend(wedges, labels, title="Camera", loc="center left", bbox_to_anchor=(1, 0.5), frameon=False)
    ax.set_title("Top Cameras Used", fontsize=16)
    _save(fig, output_dir, "images_cameras.png")


def plot_lenses(df, output_dir):
    import matplotlib.pyplot as plt

    if "lens" not in df.columns or df["lens"].dropna().empty:
        return
    counts = df["lens"].value_counts().nlargest(15)
    fig, ax = plt.subplots(figsize=(10, 6))
    counts.plot(kind="bar", color=PLOT_COLORS["lens"], ax=ax)
    ax.set_title("Top Lenses Used", fontsize=16)
    ax.tick_params(axis="x", rotation=45)
    _save(fig, output_dir, "images_lenses.png")


def plot_video_duration(df, output_dir):
    import matplotlib.pyplot as plt

    if "duration_ms" not in df.columns or df["duration_ms"].dropna().empty:
        return
    durations_s = df["duration_ms"].dropna() / 1000.0
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(durations_s, bins=50, color=PLOT_COLORS["video_duration"], edgecolor="black")
    ax.set_title("Video Duration Distribution", fontsize=16)
    ax.set_xlabel("Duration (seconds)")
    ax.set_ylabel("Number of Videos")
    _save(fig, output_dir, "videos_duration.png")


def plot_locations(df, output_dir):
    import matplotlib.pyplot as plt

    if "latitude" not in df.columns or "longitude" not in df.columns:
        return
    df_loc = df.dropna(subset=["latitude", "longitude"]).copy()
    df_loc["latitude"] = pd.to_numeric(df_loc["latitude"], errors="coerce")
    df_loc["longitude"] = pd.to_numeric(df_loc["longitude"], errors="coerce")
    df_loc = df_loc.dropna(subset=["latitude", "longitude"])
    if df_loc.empty:
        return

    world = None
    try:
        import geodatasets
        import geopandas as gpd

        world = gpd.read_file(geodatasets.get_path("naturalearth.land"))
    except Exception:
        world = None

    fig, ax = plt.subplots(figsize=(12, 8), dpi=120)
    if world is not None:
        world.plot(ax=ax, color="white", edgecolor="lightgray")
    ax.scatter(df_loc["longitude"], df_loc["latitude"], alpha=0.5, c="blue", s=20, zorder=5)
    ax.axis("off")
    _save(fig, output_dir, "locations_map.png")


def generate_plots(repo: GalleryRepository, figures_dir) -> Path:
    """Render the figure set for a catalog into ``figures_dir``."""
    figures_dir = Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)

    df = repo.query_df()
    if df.empty:
        return figures_dir
    df_images = df[df["media_type"] == "image"].reset_index(drop=True)
    df_videos = df[df["media_type"] == "video"].reset_index(drop=True)
    df_others = df[df["media_type"] == "other"].reset_index(drop=True)

    plot_general_counts(df_images, df_videos, df_others, figures_dir)
    if not df_images.empty:
        plot_cameras(df_images, figures_dir)
        plot_lenses(df_images, figures_dir)
        plot_locations(df_images, figures_dir)
    if not df_videos.empty:
        plot_video_duration(df_videos, figures_dir)
    return figures_dir
