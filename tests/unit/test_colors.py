"""The central colour module: the generated colourmap and the one-place rule."""

import re
from pathlib import Path

from smart_gallery import colors

HEX = re.compile(r"^#[0-9A-F]{6}$")


def test_colourmap_starts_at_the_accent_and_is_distinct():
    palette = colors.categorical()
    assert len(palette) == colors.CATEGORY_COUNT
    assert palette[0] == colors.ACCENT
    assert all(HEX.match(c) for c in palette)
    assert len(set(palette)) == len(palette)


def test_colourmap_keeps_the_accents_lightness():
    lightness, _, _ = colors._oklch(colors.ACCENT)
    first_round = colors.categorical()[: colors.HUES_PER_ROUND]
    for color in first_round:
        assert abs(colors._oklch(color)[0] - lightness) < 0.02, color


def test_colourmap_follows_another_base():
    palette = colors.categorical(5, base="#3366cc")
    assert palette[0] == "#3366CC"
    assert palette != colors.categorical(5)


def test_oklch_round_trips():
    for color in ("#FF5F6D", "#FFA552", "#3072EA", "#808080"):
        assert colors._from_oklch(*colors._oklch(color)) == color


def test_fixed_series_come_from_the_colourmap():
    palette = colors.categorical()
    assert list(colors.FILE_TYPE_COLORS.values()) == list(palette[:3])
    assert colors.VIDEO == colors.FILE_TYPE_COLORS["Videos"]
    assert list(colors.SETTINGS_COLORS.values()) == list(palette[:4])


def test_sequential_runs_from_clear_to_the_accent():
    stops = colors.sequential()
    assert stops[0][0] == 0.0 and stops[0][1].endswith(",0)")
    assert stops[-1] == (1.0, colors.ACCENT)


def test_no_colour_literals_outside_the_colour_module():
    package = Path(colors.__file__).parent
    literal = re.compile(r"""["']#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?["']""")
    offenders = [
        f"{path.relative_to(package)}:{n}"
        for path in package.rglob("*.py")
        if path.name != "colors.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if literal.search(line)
    ]
    assert offenders == [], "put these colours in smart_gallery/colors.py"
