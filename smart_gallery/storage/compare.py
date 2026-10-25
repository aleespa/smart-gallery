"""Do a RAW's embedded preview and its same-named JPEG show the same picture?

No Qt. Pillow is imported inside functions so the core CLI imports without the
``gui`` extra. Previews are pulled out with one ExifTool process per batch.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from smart_gallery.analysis.extract import run_exiftool_argfile
from smart_gallery.storage.check_cache import CheckCache
from smart_gallery.storage.pairs import Pair, PairKey

MATCH, DIFFERENT, UNREADABLE = "match", "different", "unreadable"
MATCH_CORRELATION = 0.90
ASPECT_TOLERANCE = 0.02
_THUMB = 32


def extract_previews(raws: Sequence[Path], out_dir: Path) -> dict[Path, Path]:
    """Write each RAW's embedded preview into ``out_dir``; missing = no entry."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    found: dict[Path, Path] = {}
    for tag in ("PreviewImage", "JpgFromRaw"):
        todo = [(i, r) for i, r in enumerate(raws) if r not in found]
        if not todo:
            break
        args: list[str] = []
        for i, raw in todo:
            target = out_dir / f"{i}.jpg"
            if target.exists() and target.stat().st_size == 0:
                target.unlink()
            args += ["-b", f"-{tag}", "-W", str(target), str(raw), "-execute"]
        run_exiftool_argfile(args)
        for i, raw in todo:
            target = out_dir / f"{i}.jpg"
            if target.exists() and target.stat().st_size > 0:
                found[raw] = target
            elif target.exists():
                target.unlink()
    return found


def _load(path: Path):
    from PIL import Image

    with Image.open(path) as img:
        size = img.size
        img.draft("L", (256, 256))
        gray = img.convert("L")
    return size, gray


def compare_images(preview: Path, jpeg: Path) -> str:
    import numpy as np
    from PIL import Image

    try:
        (w1, h1), a = _load(preview)
        (w2, h2), b = _load(jpeg)
        if not (w1 and h1 and w2 and h2):
            return UNREADABLE
        r1, r2 = w1 / h1, w2 / h2
        if abs(r1 - r2) / max(r1, r2) > ASPECT_TOLERANCE:
            return DIFFERENT
        box = Image.Resampling.BOX
        x = np.asarray(a.resize((_THUMB, _THUMB), box), dtype=np.float64).ravel()
        y = np.asarray(b.resize((_THUMB, _THUMB), box), dtype=np.float64).ravel()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return UNREADABLE
    x -= x.mean()
    y -= y.mean()
    denom = float(np.sqrt((x * x).sum() * (y * y).sum()))
    if denom == 0:
        return MATCH if np.allclose(x, y) else DIFFERENT
    return MATCH if float((x * y).sum()) / denom >= MATCH_CORRELATION else DIFFERENT


def report_cached(
    pairs: Sequence[Pair],
    cache: CheckCache,
    on_result: Callable[[PairKey, str], None],
) -> list[Pair]:
    """Report each cached verdict; return the pairs the cache doesn't know.
    Reads no image file and runs no ExifTool."""
    todo: list[Pair] = []
    for pair in pairs:
        verdict = cache.get(pair)
        if verdict is None:
            todo.append(pair)
        else:
            on_result(pair.key, verdict)
    return todo


def check_pairs(
    pairs: Sequence[Pair],
    root: Path,
    cache: CheckCache,
    on_result: Callable[[PairKey, str], None],
    cancelled: Callable[[], bool] = lambda: False,
    batch: int = 50,
) -> None:
    root = Path(root)
    todo = report_cached(pairs, cache, on_result)

    for start in range(0, len(todo), batch):
        if cancelled():
            cache.save()
            return
        chunk = todo[start : start + batch]
        raws = {p: root / p.raw.relpath for p in chunk}
        existing = [r for r in dict.fromkeys(raws.values()) if r.is_file()]
        with TemporaryDirectory() as tmp:
            previews = extract_previews(existing, Path(tmp)) if existing else {}
            for pair in chunk:
                jpeg = root / pair.jpeg.relpath
                preview = previews.get(raws[pair])
                if preview is None or not jpeg.is_file():
                    verdict = UNREADABLE
                else:
                    verdict = compare_images(preview, jpeg)
                cache.put(pair, verdict)
                on_result(pair.key, verdict)
        cache.save()
