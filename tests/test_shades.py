from __future__ import annotations

import pytest

from odp_kml.kml import PALETTE_COLORS
from odp_kml.shades import (
    HUE_SPREAD_DEG,
    MIN_CHROMA_FRACTION,
    _rgb,
    _to_oklch,
    shades,
)


def oklch(kml_color):
    return _to_oklch(_rgb(kml_color))


@pytest.mark.parametrize("base", PALETTE_COLORS)
@pytest.mark.parametrize("count", [2, 4, 8])
def test_shades_are_distinct_variations_of_the_base_color(base, count):
    lightness, chroma, hue = oklch(base)

    colors = shades(base, count, [c for c in PALETTE_COLORS if c != base])

    assert len(set(colors)) == count
    for color in colors:
        shade_lightness, shade_chroma, shade_hue = oklch(color)
        assert shade_lightness == pytest.approx(lightness, abs=0.01)
        assert abs((shade_hue - hue + 180) % 360 - 180) <= HUE_SPREAD_DEG + 1
        assert MIN_CHROMA_FRACTION * chroma - 0.03 <= shade_chroma <= chroma + 0.01


def test_shades_lean_less_toward_a_comparably_vivid_neighbour():
    red, orange = "ff1e1ec8", "ff0078e6"
    _, _, red_hue = oklch(red)
    _, _, orange_hue = oklch(orange)

    hues = [oklch(color)[2] for color in shades(red, 4, [orange])]

    toward_orange = max((hue - red_hue + 180) % 360 - 180 for hue in hues)
    assert toward_orange == pytest.approx((orange_hue - red_hue) / 3, abs=1)
    assert min((hue - red_hue + 180) % 360 - 180 for hue in hues) == pytest.approx(
        -HUE_SPREAD_DEG, abs=1
    )


def test_one_group_keeps_the_base_color():
    assert shades(PALETTE_COLORS[0], 1) == [PALETTE_COLORS[0]]
