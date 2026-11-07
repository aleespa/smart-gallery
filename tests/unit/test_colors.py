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


def _luminance(color: str) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    low, high = sorted((_luminance(a), _luminance(b)))
    return (high + 0.05) / (low + 0.05)


def test_text_on_the_accent_fill_is_readable():
    """WCAG AA for normal text: buttons and selections on the accent fill."""
    assert _contrast(colors.ACCENT_FILL, colors.ON_ACCENT) >= 4.5


def test_accent_text_reads_on_the_dark_window():
    """Links and outlines use the bright accent straight on the background."""
    assert _contrast(colors.ACCENT, colors.DARK_PALETTE["Window"]) >= 4.5
