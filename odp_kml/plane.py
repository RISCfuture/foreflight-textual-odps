"""Vector arithmetic on a local tangent plane (x east, y north, in NM).

Bearings are true degrees clockwise from north, as in `geo`.
"""

from __future__ import annotations

import math

from .geo import heading_to_unit, unit_to_heading

Vec = tuple[float, float]


def offset(p: Vec, bearing: float, distance: float) -> Vec:
    """The point `distance` NM from `p` along `bearing`."""
    ux, uy = heading_to_unit(bearing)
    return p[0] + distance * ux, p[1] + distance * uy


def sub(a: Vec, b: Vec) -> Vec:
    """The vector from `b` to `a`."""
    return a[0] - b[0], a[1] - b[1]


def cross(a: Vec, b: Vec) -> float:
    """The 2-D cross product `a.x·b.y − a.y·b.x`."""
    return a[0] * b[1] - a[1] * b[0]


def along_across(v: Vec, bearing: float) -> tuple[float, float]:
    """Components of `v` along `bearing` and to its right."""
    ux, uy = heading_to_unit(bearing)
    return v[0] * ux + v[1] * uy, v[0] * uy - v[1] * ux


def distance(a: Vec, b: Vec) -> float:
    """Straight-line distance between two plane points."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def bearing(a: Vec, b: Vec) -> float:
    """True bearing from `a` to `b`."""
    return unit_to_heading(b[0] - a[0], b[1] - a[1])


def midpoint(a: Vec, b: Vec) -> Vec:
    """The point halfway between `a` and `b`."""
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def sign(x: float) -> int:
    """+1, -1, or 0 according to the sign of `x`."""
    return (x > 0) - (x < 0)


def wrap180(deg: float) -> float:
    """Wrap an angle into [-180, 180)."""
    return (deg + 180) % 360 - 180


def arc(
    centre: Vec, radius: float, start: float, sweep: float, step: float
) -> list[Vec]:
    """Vertices on a circle from bearing `start` (seen from `centre`) through
    a signed `sweep` (positive clockwise), at most `step` degrees apart."""
    steps = max(1, math.ceil(abs(sweep) / step))
    return [offset(centre, start + sweep * i / steps, radius) for i in range(steps + 1)]
