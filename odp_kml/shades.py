"""Shades of one palette color, to tell an airport's runway groups apart.

Each shade keeps the base color's perceived lightness and varies only its
hue (within ``HUE_SPREAD_DEG`` either side, and never more than
``NEIGHBOUR_SHARE`` of the way to a comparably vivid color it must stay
apart from) and its chroma (down to ``MIN_CHROMA_FRACTION`` of the base), so
every shade stays recognizably the base color: never drifting toward black,
white or gray, nor far enough round the wheel to pass for another palette
color. Colors are worked in OKLCH, where equal steps look equal.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

HUE_SPREAD_DEG = 15.0
MIN_CHROMA_FRACTION = 0.6
NEIGHBOUR_SHARE = 1 / 3
COMPARABLE_CHROMA = 0.6  # colors whose chroma ratio is at least this can be confused
_ROWS_FROM = 4  # counts of this many or more also vary chroma, in two rows

type Rgb = tuple[float, float, float]


def shades(kml_color: str, count: int, others: Iterable[str] = ()) -> list[str]:
    """`count` KML colors ("aabbggrr") spread evenly over the base color's
    envelope, which stops short of the `others` it must not be mistaken for,
    in an order that puts consecutive shades far apart."""
    if count <= 1:
        return [kml_color] * count
    lightness, chroma, hue = _to_oklch(_rgb(kml_color))
    below, above = _hue_limits(chroma, hue, [_to_oklch(_rgb(o)) for o in others])
    rows = 2 if count >= _ROWS_FROM else 1
    columns = math.ceil(count / rows)
    points = [divmod(index, columns) for index in _spread_order(count)]
    return [
        _kml(
            _from_oklch_in_gamut(
                lightness,
                chroma * _chroma_fraction(row, rows),
                hue + _hue_offset(column, columns, below, above),
            ),
            alpha=kml_color[:2],
        )
        for row, column in points
    ]


def _spread_order(count: int) -> list[int]:
    """Grid positions visited in a stride that keeps neighbours apart."""
    stride = (
        next(
            step for step in range(count // 2 + 1, count) if math.gcd(step, count) == 1
        )
        if count > 2
        else 1
    )
    return [index * stride % count for index in range(count)]


def _hue_limits(
    chroma: float, hue: float, others: list[tuple[float, float, float]]
) -> tuple[float, float]:
    """How far the hue may move down and up: `HUE_SPREAD_DEG`, or less where
    a comparably vivid color lies close on that side."""
    below = above = HUE_SPREAD_DEG
    for _, other_chroma, other_hue in others:
        if min(chroma, other_chroma) < COMPARABLE_CHROMA * max(chroma, other_chroma):
            continue
        gap = (other_hue - hue + 180) % 360 - 180
        if gap > 0:
            above = min(above, gap * NEIGHBOUR_SHARE)
        elif gap < 0:
            below = min(below, -gap * NEIGHBOUR_SHARE)
    return below, above


def _hue_offset(column: int, columns: int, below: float, above: float) -> float:
    if columns == 1:
        return 0.0
    return -below + (below + above) * column / (columns - 1)


def _chroma_fraction(row: int, rows: int) -> float:
    if rows == 1:
        return 1.0
    return 1 - (1 - MIN_CHROMA_FRACTION) * row / (rows - 1)


def _rgb(kml_color: str) -> Rgb:
    """Linear-light RGB from a KML "aabbggrr" color."""
    blue, green, red = (int(kml_color[i : i + 2], 16) for i in (2, 4, 6))
    return tuple(_linear(channel / 255) for channel in (red, green, blue))


def _kml(rgb: Rgb, *, alpha: str) -> str:
    red, green, blue = (round(255 * _gamma(channel)) for channel in rgb)
    return f"{alpha}{blue:02x}{green:02x}{red:02x}"


def _linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _gamma(channel: float) -> float:
    channel = min(1.0, max(0.0, channel))
    if channel <= 0.0031308:
        return 12.92 * channel
    return 1.055 * channel ** (1 / 2.4) - 0.055


def _to_oklch(rgb: Rgb) -> tuple[float, float, float]:
    red, green, blue = rgb
    l_ = (0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue) ** (1 / 3)
    m_ = (0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue) ** (1 / 3)
    s_ = (0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue) ** (1 / 3)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return lightness, math.hypot(a, b), math.degrees(math.atan2(b, a))


def _from_oklch(lightness: float, chroma: float, hue: float) -> Rgb:
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def _from_oklch_in_gamut(lightness: float, chroma: float, hue: float) -> Rgb:
    """The color, with its chroma reduced just enough to be displayable."""
    rgb = _from_oklch(lightness, chroma, hue)
    while chroma > 0 and not all(-1e-4 <= channel <= 1 + 1e-4 for channel in rgb):
        chroma -= 0.002
        rgb = _from_oklch(lightness, chroma, hue)
    return rgb
