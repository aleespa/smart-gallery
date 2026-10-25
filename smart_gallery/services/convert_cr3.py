"""Extract embedded JPEG images from Canon CR2/CR3 files with ExifTool."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from loguru import logger

from smart_gallery.analysis.extract import run_exiftool_argfile


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


def _extract_previews(sources: list[Path]) -> str:
    """Write each RAW's ``JpgFromRaw`` beside it as ``<stem>.jpg``.

    One process for the whole batch: starting ExifTool costs far more than
    extracting a preview. ``-w`` never overwrites, and writes nothing for a
    file without the tag, so callers detect failures by the missing output.
    Returns ExifTool's stderr.
    """
    return run_exiftool_argfile(
        ["-b", "-JpgFromRaw", "-w", "%d%f.jpg", *map(str, sources)]
    )


def _copy_metadata(pairs: list[tuple[Path, Path]]) -> list[str]:
    """Copy every tag from each RAW into its extracted JPEG.

    The embedded image carries no EXIF of its own, so without this a vertical
    shot displays sideways and the date, lens and GPS are lost. All pairs go
    through one ExifTool process (``-execute`` per pair). Returns ExifTool's
    error lines.
    """
    args = []
    for source, output in pairs:
        args += [
            "-tagsFromFile",
            str(source),
            "-all:all",
            "-overwrite_original",
            "-m",
            str(output),
            "-execute",
        ]
    stderr = run_exiftool_argfile(args)
    return [line for line in stderr.splitlines() if line.startswith("Error")]


def convert_cr3(sources) -> ConvertCr3Report:
    """Write the embedded ``JpgFromRaw`` image from each CR2/CR3 as JPEG.

    Directory inputs are searched recursively. Each JPEG is written beside
    its source, unless a same-stem JPEG already exists, in which case the RAW
    file is skipped. The RAW's metadata (orientation, capture date, camera,
    lens, exposure, GPS) is then copied into each new JPEG.
    """
    files = _input_files(sources)
    report = ConvertCr3Report()

    # One listing per directory, not per RAW: a card folder holds thousands.
    jpeg_names: dict[Path, set[str]] = {}
    pending = []
    for source, _relative_path in files:
        if source.parent not in jpeg_names:
            jpeg_names[source.parent] = {
                path.name.casefold()
                for path in source.parent.iterdir()
                if path.suffix.lower() in {".jpg", ".jpeg"} and path.is_file()
            }
        stem = source.stem.casefold()
        if {stem + ".jpg", stem + ".jpeg"} & jpeg_names[source.parent]:
            report.skipped += 1
            continue
        pending.append(source)

    if pending:
        stderr = _extract_previews(pending).splitlines()
        pairs = []
        for source in pending:
            output = source.with_suffix(".jpg")
            if output.is_file() and output.stat().st_size:
                pairs.append((source, output))
                continue
            output.unlink(missing_ok=True)
            reason = next(
                (line for line in stderr if source.name in line),
                "no embedded JpgFromRaw image was returned",
            )
            message = f"Could not extract JPEG from {source}: {reason}"
            logger.warning(message)
            report.failures.append(message)

        report.outputs = [output for _source, output in pairs]
        report.converted = len(pairs)
        if pairs:
            for error in _copy_metadata(pairs):
                message = f"Could not copy metadata: {error}"
                logger.warning(message)
                report.failures.append(message)

    logger.success(
        f"Converted {report.converted} RAW file(s); skipped {report.skipped} "
        f"with an existing JPEG; failed {len(report.failures)}."
    )
    return report
