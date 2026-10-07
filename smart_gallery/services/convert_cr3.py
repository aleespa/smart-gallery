"""Extract embedded JPEG images from Canon CR2/CR3 files with ExifTool."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from loguru import logger

from smart_gallery.analysis.extract import _get_exiftool_path


@dataclass
class ConvertCr3Report:
    converted: int = 0
    skipped: int = 0
    failures: List[str] = field(default_factory=list)
    outputs: List[Path] = field(default_factory=list)


def _input_files(sources) -> list[tuple[Path, Path | None]]:
    """Return CR2/CR3 inputs and their relative paths beneath source dirs."""
    found = []
    for source in map(Path, sources):
        if not source.exists():
            raise FileNotFoundError(f"Source does not exist: {source}")
        if source.is_dir():
            for path in sorted(source.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".cr2", ".cr3"}:
                    found.append((path, path.relative_to(source)))
        elif source.is_file() and source.suffix.lower() in {".cr2", ".cr3"}:
            found.append((source, None))
        else:
            raise ValueError(f"Source is not a CR2/CR3 file or directory: {source}")
    return found


def convert_cr3(sources) -> ConvertCr3Report:
    """Write the embedded ``JpgFromRaw`` image from each CR2/CR3 as JPEG.

    Directory inputs are searched recursively. Each JPEG is written beside
    its source, unless a same-stem JPEG already exists, in which case the RAW
    file is skipped.
    """
    files = _input_files(sources)
    report = ConvertCr3Report()

    for source, _relative_path in files:
        output = source.with_suffix(".jpg")
        jpeg_stems = {source.stem.casefold() + ext for ext in (".jpg", ".jpeg")}
        jpeg_exists = any(
            path.is_file() and path.name.casefold() in jpeg_stems
            for path in source.parent.iterdir()
        )
        if jpeg_exists:
            report.skipped += 1
            continue

        result = subprocess.run(
            [_get_exiftool_path(), "-b", "-JpgFromRaw", str(source)],
            capture_output=True,
        )
        if result.returncode != 0 or not result.stdout:
            detail = result.stderr.decode(errors="replace").strip()
            reason = detail or "no embedded JpgFromRaw image was returned"
            message = f"Could not extract JPEG from {source}: {reason}"
            logger.warning(message)
            report.failures.append(message)
            continue

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(result.stdout)
        report.outputs.append(output)
        report.converted += 1

    logger.success(
        f"Converted {report.converted} RAW file(s); skipped {report.skipped} "
        f"with an existing JPEG; failed {len(report.failures)}."
    )
    return report
