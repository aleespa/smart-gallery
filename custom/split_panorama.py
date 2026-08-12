#!/usr/bin/env python
"""Split a panorama into a set of 1:1 frames for an Instagram carousel.

Slices a wide image left-to-right into square frames of the panorama's full
height so that swiping through the carousel reads as one continuous picture.
The trailing strip (when the width isn't an exact multiple of the height) is
padded to a square rather than cropped, so nothing is lost. Every frame is
resized to a fixed square edge (1080px by default, Instagram's feed standard).

Run standalone, e.g.:
    uv run custom/split_panorama.py PATH/TO/pano.jpg --output frames/
"""

import argparse
import sys
from pathlib import Path
from typing import List

from loguru import logger
from PIL import Image

# Instagram allows at most 20 images in a single carousel post.
CAROUSEL_LIMIT = 20


def split_panorama(img: Image.Image, size: int, bg: str) -> List[Image.Image]:
    """Cut a panorama into square frames, padding the trailing strip if needed."""
    img = img.convert("RGB")
    width, height = img.size

    if width <= height:
        logger.warning(
            "Image is {}x{} — not a horizontal panorama; emitting a single "
            "padded frame.",
            width,
            height,
        )
        square = Image.new("RGB", (height, height), bg)
        square.paste(img, ((height - width) // 2, 0))
        return [square.resize((size, size), Image.LANCZOS)]

    n_full, rem = divmod(width, height)
    total = n_full + (1 if rem else 0)
    if total > CAROUSEL_LIMIT:
        logger.warning(
            "{} frames exceeds Instagram's carousel limit of {} — you'll need "
            "to trim or post in batches.",
            total,
            CAROUSEL_LIMIT,
        )

    frames: List[Image.Image] = []
    for i in range(n_full):
        left = i * height
        frames.append(img.crop((left, 0, left + height, height)))

    if rem:
        # Left-align the trailing strip and pad the right, so the seam with the
        # previous frame stays continuous.
        strip = img.crop((n_full * height, 0, width, height))
        square = Image.new("RGB", (height, height), bg)
        square.paste(strip, (0, 0))
        frames.append(square)

    return [f.resize((size, size), Image.LANCZOS) for f in frames]


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Split a panorama into 1:1 frames for an Instagram carousel."
    )
    parser.add_argument("image", type=Path, help="Path to the source panorama.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: <image_dir>/<stem>_ig/).",
    )
    parser.add_argument(
        "--size", type=int, default=1080, help="Output square edge in px (default 1080)."
    )
    parser.add_argument(
        "--bg",
        default="black",
        help="Pad color for the last partial frame — name or #hex (default black).",
    )
    parser.add_argument(
        "--quality", type=int, default=95, help="JPEG quality (default 95)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log the frame plan without writing any files.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """Entry point: load the panorama, split it, and write the frames."""
    args = parse_args(argv)

    if not args.image.is_file():
        logger.error("Not a file: {}", args.image)
        return 1

    try:
        img = Image.open(args.image)
    except (OSError, ValueError) as exc:
        logger.error("Could not open {}: {}", args.image, exc)
        return 1

    frames = split_panorama(img, args.size, args.bg)

    out_dir = args.output or args.image.parent / f"{args.image.stem}_ig"
    stem = args.image.stem
    width = max(2, len(str(len(frames))))

    logger.info(
        "{} → {} frame(s) of {}x{} into {}",
        args.image.name,
        len(frames),
        args.size,
        args.size,
        out_dir,
    )

    if args.dry_run:
        for i in range(1, len(frames) + 1):
            logger.info("[dry-run] would write {}_{:0{w}d}.jpg", stem, i, w=width)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames, start=1):
        dest = out_dir / f"{stem}_{i:0{width}d}.jpg"
        frame.save(dest, "JPEG", quality=args.quality)
        logger.info("wrote {}", dest)

    return 0


if __name__ == "__main__":
    sys.exit(main())
