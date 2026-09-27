"""Geodesy on a spherical earth plus a local tangent plane for constructions."""

from __future__ import annotations

import dataclasses

EARTH_RADIUS_NM = 3440.065


@dataclasses.dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float
