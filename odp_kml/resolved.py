"""What the resolver hands to geometry: a procedure with every reference bound.

Geometry never touches NASR records or the raw AST; it consumes these
already-resolved positions, elevations and declinations.
"""

from __future__ import annotations

import dataclasses

from .geo import LatLon
from .procedure import HoldSpec, Procedure


@dataclasses.dataclass(frozen=True)
class RunwayStart:
    """The departure end of one runway, as used by every construction."""

    runway: str
    der: LatLon
    der_elevation_ft: float
    course_true: float
    min_climb_gradient_ft_nm: float | None


@dataclasses.dataclass(frozen=True)
class ResolvedPoint:
    """A navaid or fix with the declination used to convert its courses."""

    ident: str
    position: LatLon
    declination_east: float


@dataclasses.dataclass(frozen=True)
class ResolvedProcedure:
    procedure: Procedure
    airport_lid: str
    airport_name: str
    airport_position: LatLon
    airport_elevation_ft: float
    airport_variation_east: float
    runways: dict[str, RunwayStart]
    points: dict[str, ResolvedPoint]
    published_holds: dict[str, HoldSpec]
