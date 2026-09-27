"""Give neighbouring airports different line colors.

Greedy graph coloring: airports are visited in LID order and each takes the
lowest palette index not already used by a colored airport within
``radius_nm``. When every index is taken nearby, the least-used one wins.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Iterable

from .geo import distance_nm
from .shapes import AirportDrawing

PALETTE_SIZE = 6
DEFAULT_RADIUS_NM = 40.0


def assign_palettes(
    drawings: Iterable[AirportDrawing], *, radius_nm: float = DEFAULT_RADIUS_NM
) -> list[AirportDrawing]:
    """Return copies of ``drawings`` with ``palette`` set so that no two
    airports within ``radius_nm`` share one, wherever the palette allows."""
    colored: list[AirportDrawing] = []
    for drawing in sorted(drawings, key=lambda d: d.lid):
        neighbours = [other for other in colored if _within(drawing, other, radius_nm)]
        palette = _least_conflicting(Counter(other.palette for other in neighbours))
        colored.append(dataclasses.replace(drawing, palette=palette))
    return colored


def _within(a: AirportDrawing, b: AirportDrawing, radius_nm: float) -> bool:
    if a.position is None or b.position is None:
        return False
    return distance_nm(a.position, b.position) <= radius_nm


def _least_conflicting(used: Counter[int]) -> int:
    return min(range(PALETTE_SIZE), key=lambda index: (used[index], index))
