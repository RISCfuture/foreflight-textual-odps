"""Render primitives: what geometry produces and the KML writer consumes."""

from __future__ import annotations

import dataclasses
from enum import StrEnum

from .geo import LatLon


class Style(StrEnum):
    ROUTE = "route"
    RADIAL = "radial"
    HOLD = "hold"
    VCOA = "vcoa"


@dataclasses.dataclass(frozen=True)
class Polyline:
    name: str
    style: Style
    points: tuple[LatLon, ...]


@dataclasses.dataclass(frozen=True)
class Label:
    text: str
    at: LatLon


@dataclasses.dataclass(frozen=True)
class AirportDrawing:
    """Everything drawn for one airport; becomes one KML Folder."""

    lid: str
    name: str
    shapes: tuple[Polyline | Label, ...]
