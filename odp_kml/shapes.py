"""Render primitives: what geometry produces and the KML writer consumes."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from enum import StrEnum

from .geo import LatLon


class Style(StrEnum):
    ROUTE = "route"
    RADIAL = "radial"
    HOLD = "hold"
    VCOA = "vcoa"


@dataclasses.dataclass(frozen=True)
class Polyline:
    """A line; `group` is the runway group whose printed route it draws
    (e.g. ``("34L", "34R")``), empty for lines every route shares."""

    name: str
    style: Style
    points: tuple[LatLon, ...]
    group: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Label:
    """Text at a point. `fallbacks` are where it may stand instead, in order,
    when different text already stands at `at` (see `placed`)."""

    text: str
    at: LatLon
    fallbacks: tuple[LatLon, ...] = ()

    def placed(self, shapes: Iterable[Polyline | Label]) -> Label | None:
        """This label at the first of its points where no label stands, with
        the points after it kept as fallbacks; ``None`` when a label with
        the same text is met first, since that one already says it.

        A point where different text stands is passed over so two labels
        never print on top of each other; when every point is taken, the
        label stays at `at`.
        """
        standing = [(s.at, s.text) for s in shapes if isinstance(s, Label)]
        points = (self.at, *self.fallbacks)
        for index, point in enumerate(points):
            texts = {text for at, text in standing if at == point}
            if self.text in texts:
                return None
            if not texts:
                return Label(self.text, point, points[index + 1 :])
        return self


@dataclasses.dataclass(frozen=True)
class Wedge:
    """A heading range as one or more runways fly it: `sectors` of true
    ``(start, signed sweep)`` degrees (positive clockwise) fanning out from
    the turn-start point `apex`.

    `printed` words the range as the text does, with any published turn
    onto it and altitude to climb to in it, and `phrases` are the pieces of
    `printed` a label may break between. `course` is the true course flown
    into the apex and `turn` any published turn onto the range ("L", "R").
    """

    runways: tuple[str, ...]
    apex: LatLon
    sectors: tuple[tuple[float, float], ...]
    printed: str
    phrases: tuple[str, ...]
    group: tuple[str, ...] = ()
    course: float = 0.0
    turn: str | None = None


@dataclasses.dataclass(frozen=True)
class AirportDrawing:
    """Everything drawn for one airport; becomes one KML Folder.

    `wedges` are heading ranges still to be laid out as shapes, which waits
    until every part of the airport is drawn (see `geometry.lay_out_wedges`).
    """

    lid: str
    name: str
    shapes: tuple[Polyline | Label, ...]
    position: LatLon | None = None
    palette: int = 0
    wedges: tuple[Wedge, ...] = ()
