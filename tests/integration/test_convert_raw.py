"""convert-raw against the real R8 CR3 fixture. Needs ExifTool on PATH."""

import json
import subprocess

import pytest

from smart_gallery.services import convert_cr3

pytestmark = pytest.mark.exiftool


def _tags(path):
    out = subprocess.run(
        ["exiftool", "-j", "-n", str(path)], capture_output=True, check=True
    )
    return json.loads(out.stdout)[0]


def test_extracted_jpeg_carries_raw_metadata(tmp_path, copy_image):
    raw = copy_image(tmp_path, "test.CR3", "Shots/IMG_0001.CR3")
    source = _tags(raw)

    report = convert_cr3([tmp_path])

    assert report.converted == 1
    assert report.failures == []
    jpeg = _tags(raw.with_suffix(".jpg"))
    # The fixture is a vertical shot: without Orientation it displays sideways.
    assert jpeg["Orientation"] == source["Orientation"] == 6
    for tag in ("Model", "LensModel", "DateTimeOriginal", "ExposureTime", "ISO"):
        assert jpeg[tag] == source[tag], tag
    assert jpeg["ImageWidth"] == 6000 and jpeg["ImageHeight"] == 4000


def test_existing_jpeg_is_left_alone(tmp_path, copy_image):
    raw = copy_image(tmp_path, "test.CR3", "IMG_0002.CR3")
    existing = raw.with_suffix(".JPG")
    existing.write_bytes(b"not touched")

    report = convert_cr3([raw])

    assert report.skipped == 1 and report.converted == 0
    assert existing.read_bytes() == b"not touched"


def test_unreadable_raw_fails_alone(tmp_path, copy_image):
    good = copy_image(tmp_path, "test.CR3", "IMG_0003.CR3")
    bad = tmp_path / "IMG_0004.CR3"
    bad.write_bytes(b"not a raw file")

    report = convert_cr3([tmp_path])

    assert report.converted == 1
    assert report.outputs == [good.with_suffix(".jpg")]
    assert len(report.failures) == 1 and "IMG_0004.CR3" in report.failures[0]
    assert not bad.with_suffix(".jpg").exists()
