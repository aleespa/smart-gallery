#!/usr/bin/env python
"""Build a timelapse video from a contiguous range of JPGs in a folder.

Frames are selected by filename between --start and --end (inclusive, natural
sort) and handed straight to ffmpeg through a concat list, so the JPEGs are
decoded and encoded in ffmpeg's own threads — no per-frame Python work. When
the machine has an NVIDIA card the h264_nvenc encoder is used, otherwise it
falls back to libx264.

Run standalone, e.g.:
    uv run custom/timelapse.py --start IMG_4252 --end IMG_4476
    uv run custom/timelapse.py --dir E:/Photos/Canon/2026/08 --fps 30 --scale 1920
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from loguru import logger
from PIL import Image

# NVENC refuses anything wider or taller than this, whatever the codec.
NVENC_MAX_DIM = 4096

DEFAULT_DIR = Path(r"E:\Photos\Canon\2026\08")
DEFAULT_OUTPUT_DIR = Path(r"C:\Users\Alejandro\Pictures")
DEFAULT_START = "IMG_4252"
DEFAULT_END = "IMG_4476"


def natural_key(name: str):
    """Sort key that orders IMG_9.JPG before IMG_10.JPG."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def select_frames(folder: Path, start: str, end: str) -> List[Path]:
    """Return the .jpg files in `folder` between `start` and `end`, inclusive.

    `start`/`end` are matched against the file stem, so IMG_4252 and
    IMG_4252.JPG both work.
    """
    files = sorted(
        (p for p in folder.iterdir() if p.suffix.lower() == ".jpg"),
        key=lambda p: natural_key(p.name),
    )
    if not files:
        raise SystemExit(f"No .jpg files in {folder}")

    stems = [p.stem.lower() for p in files]
    lo = Path(start).stem.lower()
    hi = Path(end).stem.lower()
    try:
        i, j = stems.index(lo), stems.index(hi)
    except ValueError as exc:
        raise SystemExit(f"Frame not found in {folder}: {exc}") from exc
    if i > j:
        i, j = j, i
    return files[i : j + 1]


def target_size(src_w: int, src_h: int, scale: int, max_height: int = 0) -> tuple:
    """Fit (src_w, src_h) inside a `scale`-px long edge and `max_height` rows.

    Never upscales, and rounds to even numbers because the chroma-subsampled
    pixel formats need them. Either limit <= 0 means "no limit"; `scale` <= 0
    with no height cap keeps the source resolution.
    """
    factor = 1.0
    if scale > 0:
        factor = min(factor, scale / max(src_w, src_h))
    if max_height > 0:
        factor = min(factor, max_height / src_h)
    even = lambda n: max(2, int(round(n * factor / 2)) * 2)
    return even(src_w), even(src_h)


def pick_encoder(
    ffmpeg: str, width: int, height: int, lossless: bool, quality: int
) -> List[str]:
    """Choose the encoder args for this output size and quality target.

    Lossless always means libx264 -qp 0: it is the most widely playable truly
    lossless option, and on near-black frames it is both smaller and faster
    than NVENC's lossless mode. Note that it still lands in the High 4:4:4
    Predictive profile, which Windows' and Android's hardware decoders reject
    — the lossy path below stays in plain High profile, which they all take.
    Otherwise prefer the GPU, but fall back to the CPU beyond NVENC's limit.
    """
    if lossless:
        logger.info("encoding losslessly with libx264 -qp 0 (CPU)")
        return ["-c:v", "libx264", "-preset", "medium", "-qp", "0"]

    # High profile at level 5.1 is what consumer hardware decoders implement.
    compat = ["-profile:v", "high", "-level", "5.1"]
    encoders = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    if "h264_nvenc" not in encoders:
        logger.info("h264_nvenc unavailable — encoding with libx264 (CPU)")
    elif max(width, height) > NVENC_MAX_DIM:
        logger.info(
            "{}x{} exceeds NVENC's {}px limit — encoding with libx264 (CPU)",
            width, height, NVENC_MAX_DIM,
        )
    else:
        logger.info("encoding with h264_nvenc (GPU), cq {}", quality)
        return ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr",
                "-cq", str(quality), "-b:v", "0", *compat]
    logger.info("encoding with libx264, crf {}", quality)
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(quality), *compat]


def build_video(
    frames: List[Path],
    output: Path,
    fps: int,
    scale: int,
    ffmpeg: str,
    lossless: bool = False,
    quality: int = 18,
    max_height: int = 0,
    dry_run: bool = False,
) -> int:
    """Concatenate `frames` into an mp4 at `output` via ffmpeg."""
    with Image.open(frames[0]) as probe:
        src_w, src_h = probe.size
    width, height = target_size(src_w, src_h, scale, max_height)

    # Camera JPEGs are full-range (yuvj*). Lossless keeps that range and 4:2:2
    # chroma so the frames survive intact; the lossy path converts to the
    # limited-range yuv420p every H.264 player handles, and says so explicitly
    # rather than letting swscale guess.
    pix_fmt, rng, tag = ("yuv422p", "full", "pc") if lossless else \
                        ("yuv420p", "limited", "tv")
    vf = (f"scale={width}:{height}:flags=lanczos:in_range=full:out_range={rng},"
          f"format={pix_fmt}")

    with tempfile.TemporaryDirectory() as tmp:
        list_file = Path(tmp) / "frames.txt"
        # ffmpeg's concat parser wants forward slashes and escaped quotes.
        list_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in frames), encoding="utf-8"
        )

        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-stats", "-y",
            "-r", str(fps),
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-vf", vf,
            "-fps_mode", "cfr", "-r", str(fps),
            *pick_encoder(ffmpeg, width, height, lossless, quality),
            "-color_range", tag,
            "-movflags", "+faststart",
            str(output),
        ]

        if dry_run:
            logger.info("[dry-run] {}", " ".join(cmd))
            return 0

        output.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "{} frames of {}x{} (from {}x{}) @ {} fps → {:.1f}s → {}",
            len(frames), width, height, src_w, src_h, fps,
            len(frames) / fps, output,
        )
        return subprocess.run(cmd, check=False).returncode


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Build a timelapse mp4 from a range of JPGs in a folder."
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                        help=f"Folder holding the JPGs (default {DEFAULT_DIR}).")
    parser.add_argument("--start", default=DEFAULT_START,
                        help=f"First frame, by name (default {DEFAULT_START}).")
    parser.add_argument("--end", default=DEFAULT_END,
                        help=f"Last frame, by name (default {DEFAULT_END}).")
    parser.add_argument("--fps", type=int, default=24,
                        help="Frames per second (default 24).")
    parser.add_argument("--scale", type=int, default=3840,
                        help="Long-edge size in px; 0 keeps the source size "
                             "(default 3840).")
    parser.add_argument("--output", type=Path, default=None,
                        help=f"Output .mp4 (default "
                             f"{DEFAULT_OUTPUT_DIR}\\timelapse_<start>-<end>.mp4).")
    parser.add_argument("--max-height", type=int, default=0,
                        help="Extra cap on output height in px; 0 is no cap. "
                             "Use 2160 to stay inside the 4K UHD frame that "
                             "phone and TV decoders guarantee.")
    parser.add_argument("--quality", type=int, default=18,
                        help="CQ/CRF for the lossy path, lower is better "
                             "(default 18). Ignored with --lossless.")
    parser.add_argument("--lossless", action="store_true",
                        help="Encode losslessly (libx264 -qp 0, 4:2:2, full "
                             "range) — pixel-identical to the source JPEGs at "
                             "the chosen --scale. Much larger files.")
    parser.add_argument("--ffmpeg", default=None,
                        help="Path to ffmpeg (default: found on PATH).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log the ffmpeg command without running it.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """Entry point: pick the frame range and encode it."""
    args = parse_args(argv)

    ffmpeg: Optional[str] = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("ffmpeg not found on PATH — pass --ffmpeg C:/path/to/ffmpeg.exe")
        return 1

    if not args.dir.is_dir():
        logger.error("Not a directory: {}", args.dir)
        return 1

    frames = select_frames(args.dir, args.start, args.end)
    output = args.output or (
        DEFAULT_OUTPUT_DIR
        / f"timelapse_{Path(args.start).stem}-{Path(args.end).stem}.mp4"
    )

    return build_video(
        frames, output, args.fps, args.scale, ffmpeg, args.lossless,
        args.quality, args.max_height, args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
