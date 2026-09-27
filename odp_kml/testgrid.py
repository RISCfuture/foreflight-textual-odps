"""A synthetic construction grid: one cell per drawing rule, for verifying
`geometry.draw` visually on a device.

Each cell is a fictional single-runway airport with a VOR and a fix placed
so its one construction traces out cleanly. Every `ResolvedProcedure` is
built directly from `procedure` AST nodes; no grammar or NASR data is
involved.
"""

from __future__ import annotations

import dataclasses

from .geo import LatLon, LocalPlane
from .geometry import DisplayParams, draw
from .palette import PALETTE_SIZE
from .plane import Vec
from .plane import offset as plane_offset
from .procedure import (
    Altitude,
    AltitudeKind,
    AtFix,
    ClimbHeading,
    ClimbingTurn,
    ClimbInHold,
    Compass8,
    Direct,
    Dme,
    FixRef,
    HeadingAndRadial,
    HoldSpec,
    Leg,
    NavaidRef,
    Procedure,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
    Thence,
    Turn,
    VcoaGroup,
)
from .resolved import ResolvedPoint, ResolvedProcedure, RunwayStart
from .shapes import AirportDrawing, Label

__all__ = ["Cell", "cells", "draw_grid"]

DEFAULT_ORIGIN = LatLon(38.5, -117.5)
DEFAULT_SPACING_DEG = 0.5
DEFAULT_COLUMNS = 6

RUNWAY_LENGTH_NM = 6000.0 / 6076.11549
AIRPORT_ELEVATION_FT = 5000.0
VOR_IDENT = "VOR"
FIX_IDENT = "FIXXX"
DEFAULT_VOR_XY = plane_offset((0.0, 0.0), 315.0, 8.0)
DEFAULT_FIX_XY = plane_offset((0.0, 0.0), 135.0, 12.0)


@dataclasses.dataclass(frozen=True)
class Cell:
    """One grid cell: a synthetic airport built to show one construction."""

    code: str
    title: str
    resolved: ResolvedProcedure
    params: DisplayParams


@dataclasses.dataclass(frozen=True)
class _Construction:
    """A cell's procedure and navaid placement, before it sits on the grid."""

    title: str
    groups: tuple[RunwayGroup, ...] = ()
    shared_tail: tuple[Leg, ...] | None = None
    vcoa: tuple[VcoaGroup, ...] = ()
    vor_xy: Vec = DEFAULT_VOR_XY
    fix_xy: Vec = DEFAULT_FIX_XY
    gradient_09: float | None = None
    params: DisplayParams = dataclasses.field(default_factory=DisplayParams)


def _group(*legs: Leg, runway: str = "09") -> RunwayGroup:
    return RunwayGroup((runway,), legs)


def _label_kinds_group() -> RunwayGroup:
    """Three climb-heading legs ending at-or-above, at, and climb-to altitudes."""
    return _group(
        ClimbHeading(90, Altitude(6000, AltitudeKind.AT_OR_ABOVE, "at or above 6000")),
        ClimbHeading(45, Altitude(6500, AltitudeKind.AT, "at 6500")),
        ClimbHeading(90, Altitude(7000, AltitudeKind.TO, "to 7000")),
    )


VOR = NavaidRef(VOR_IDENT)
FIX = FixRef(FIX_IDENT)

_CONSTRUCTIONS: tuple[_Construction, ...] = (
    _Construction(
        "DER stub only, no legs",
        (RunwayGroup(("09",), ()),),
    ),
    _Construction(
        "Climb heading then proceed on course",
        (
            _group(
                ClimbHeading(90, Altitude(7000, AltitudeKind.AT_OR_ABOVE, "7000")),
                ProceedOnCourse(),
            ),
        ),
    ),
    _Construction(
        "Climb heading to altitude before turning left",
        (
            _group(
                ClimbHeading(90, Altitude(7000, AltitudeKind.AT_OR_ABOVE, "7000")),
                ProceedOnCourse(Turn.LEFT),
            ),
        ),
    ),
    _Construction(
        "Climbing left turn direct to a VOR",
        (_group(ClimbingTurn(Turn.LEFT, Direct(VOR))),),
        vor_xy=(-6.0, 9.0),
    ),
    _Construction(
        "Climbing right turn direct to a VOR",
        (_group(ClimbingTurn(Turn.RIGHT, Direct(VOR))),),
        vor_xy=(-6.0, -9.0),
    ),
    _Construction(
        "Direct to a VOR near the runway heading, turn side unspecified",
        (_group(Direct(VOR)),),
        vor_xy=plane_offset((0.0, 0.0), 100.0, 10.0),
    ),
    _Construction(
        "Climbing right turn, heading to intercept a radial inbound",
        (
            _group(
                ClimbingTurn(
                    Turn.RIGHT,
                    HeadingAndRadial(180, VOR, 90, outbound=False, until=AtFix(VOR)),
                )
            ),
        ),
        vor_xy=(-4.0, -10.0),
    ),
    _Construction(
        "Climbing left turn, heading to intercept a radial outbound",
        (
            _group(
                ClimbingTurn(
                    Turn.LEFT,
                    HeadingAndRadial(
                        360,
                        VOR,
                        45,
                        outbound=True,
                        until=Altitude(9000, AltitudeKind.TO, "to 9000"),
                    ),
                )
            ),
        ),
        vor_xy=(-6.0, 6.0),
    ),
    _Construction(
        "Radial inbound to a VOR on the extended centreline",
        (_group(Radial(VOR, 270, outbound=False, until=AtFix(VOR))),),
        vor_xy=(10.0, 0.0),
    ),
    _Construction(
        "Direct to a VOR then climb in a right-turn hold",
        (
            _group(
                Direct(VOR),
                ClimbInHold(
                    VOR,
                    HoldSpec(Compass8.NE, Turn.RIGHT, 225),
                    Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300"),
                ),
            ),
        ),
        vor_xy=(0.0, 12.0),
    ),
    _Construction(
        "Direct to a VOR then climb in a left-turn hold",
        (
            _group(
                Direct(VOR),
                ClimbInHold(
                    VOR,
                    HoldSpec(Compass8.SW, Turn.LEFT, 225),
                    Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300"),
                ),
            ),
        ),
        vor_xy=(0.0, 12.0),
    ),
    _Construction(
        "Climb in a hold sized by a DME leg length",
        (
            _group(
                Direct(VOR),
                ClimbInHold(VOR, HoldSpec(Compass8.NE, Turn.RIGHT, 225), Dme(VOR, 5.0)),
            ),
        ),
        vor_xy=(0.0, 12.0),
    ),
    _Construction(
        "Climb heading at or below an altitude",
        (
            _group(
                ClimbHeading(
                    90, Altitude(7000, AltitudeKind.AT_OR_BELOW, "at or below 7000")
                )
            ),
        ),
    ),
    _Construction(
        "Climb heading at a mandatory altitude",
        (_group(ClimbHeading(90, Altitude(7000, AltitudeKind.AT, "at 7000"))),),
    ),
    _Construction(
        "Visual climb over the airport then direct to a VOR",
        (),
        vcoa=(VcoaGroup(("09",), None, 7800, (Direct(VOR),)),),
        vor_xy=(0.0, 10.0),
    ),
    _Construction(
        "Two runway groups converging on a shared tail hold",
        (
            RunwayGroup(("09",), (Direct(VOR), Thence())),
            RunwayGroup(("27",), (Direct(VOR), Thence())),
        ),
        shared_tail=(
            ClimbInHold(
                VOR,
                HoldSpec(Compass8.NE, Turn.RIGHT, 225),
                Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300"),
            ),
        ),
        vor_xy=(0.5, 12.0),
    ),
    _Construction(
        "Climb heading at a published climb gradient",
        (_group(ClimbHeading(90, Altitude(7000, AltitudeKind.AT_OR_ABOVE, "7000"))),),
        gradient_09=350.0,
    ),
    _Construction(
        "Climb heading at or below, FMS label style",
        (
            _group(
                ClimbHeading(
                    90, Altitude(7000, AltitudeKind.AT_OR_BELOW, "at or below 7000")
                )
            ),
        ),
        params=DisplayParams(label_style="fms"),
    ),
    _Construction(
        "Direct to a VOR then a radial outbound to an altitude",
        (
            _group(
                Direct(VOR),
                Radial(
                    VOR,
                    100,
                    outbound=True,
                    until=Altitude(9000, AltitudeKind.TO, "to 9000"),
                ),
            ),
        ),
        vor_xy=(6.0, 0.0),
    ),
    _Construction(
        "Radial outbound ending at a fix",
        (_group(Direct(VOR), Radial(VOR, 100, outbound=True, until=AtFix(FIX))),),
        vor_xy=(6.0, 0.0),
        fix_xy=plane_offset((6.0, 0.0), 100.0, 8.0),
    ),
    _Construction(
        "Radial outbound ending at a DME distance",
        (_group(Direct(VOR), Radial(VOR, 100, outbound=True, until=Dme(VOR, 8.0))),),
        vor_xy=(6.0, 0.0),
    ),
    _Construction(
        "Climb heading to an altitude then direct to a hold ahead",
        (
            _group(
                ClimbHeading(90, Altitude(7000, AltitudeKind.TO, "to 7000")),
                ClimbInHold(
                    VOR,
                    HoldSpec(Compass8.NE, Turn.RIGHT, 225),
                    Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300"),
                ),
            ),
        ),
        vor_xy=(14.0, 4.0),
    ),
    _Construction(
        "Climbing turn to a heading, no altitude, then on course",
        (_group(ClimbHeading(135), ProceedOnCourse()),),
    ),
    _Construction(
        "Climbing turn to an altitude reached before the turn-start point",
        (
            _group(
                ClimbingTurn(
                    Turn.LEFT,
                    ClimbHeading(360, Altitude(5300, AltitudeKind.TO, "to 5300")),
                )
            ),
        ),
    ),
    _Construction(
        "Visual climb over a fix then direct to a VOR",
        (),
        vcoa=(VcoaGroup(("09",), FIX, 7800, (Direct(VOR),)),),
    ),
    _Construction(
        "Visual climb over the airport from all runways, then on course",
        (),
        vcoa=(VcoaGroup((), None, 7800, (ProceedOnCourse(),)),),
    ),
    _Construction(
        "Altitude label kinds: at or above, at, climb to (plain style)",
        (_label_kinds_group(),),
    ),
    _Construction(
        "Altitude label kinds: at or above, at, climb to (FMS style)",
        (_label_kinds_group(),),
        params=DisplayParams(label_style="fms"),
    ),
)


def cells(
    origin: LatLon = DEFAULT_ORIGIN,
    spacing_deg: float = DEFAULT_SPACING_DEG,
    columns: int = DEFAULT_COLUMNS,
) -> list[Cell]:
    """One `Cell` per entry in the construction list, laid out row-major."""
    return [cell for cell, _ in _layout(origin, spacing_deg, columns)]


def draw_grid(
    origin: LatLon = DEFAULT_ORIGIN,
    spacing_deg: float = DEFAULT_SPACING_DEG,
    columns: int = DEFAULT_COLUMNS,
) -> list[AirportDrawing]:
    """Draw every cell, each labelled with its code and construction title
    and cycling through the palette so every line color is on show."""
    return [
        dataclasses.replace(_draw_cell(cell, top_left), palette=index % PALETTE_SIZE)
        for index, (cell, top_left) in enumerate(_layout(origin, spacing_deg, columns))
    ]


def _draw_cell(cell: Cell, top_left: LatLon) -> AirportDrawing:
    drawing = draw(cell.resolved, cell.params)
    title = Label(f"{cell.code}: {cell.title}", top_left)
    return dataclasses.replace(drawing, shapes=(title, *drawing.shapes))


def _layout(
    origin: LatLon, spacing_deg: float, columns: int
) -> list[tuple[Cell, LatLon]]:
    """Every cell alongside the lat/lon of its top-left corner."""
    return [
        _place(index, construction, origin, spacing_deg, columns)
        for index, construction in enumerate(_CONSTRUCTIONS)
    ]


def _place(
    index: int,
    construction: _Construction,
    origin: LatLon,
    spacing_deg: float,
    columns: int,
) -> tuple[Cell, LatLon]:
    row, col = divmod(index, columns)
    code = f"{chr(ord('A') + row)}{col + 1}"
    centre = LatLon(origin.lat - row * spacing_deg, origin.lon + col * spacing_deg)
    top_left = LatLon(centre.lat + spacing_deg / 2, centre.lon - spacing_deg / 2)
    lid = f"T{index + 1:02d}"
    resolved = _build_resolved(lid, construction, centre)
    return Cell(code, construction.title, resolved, construction.params), top_left


def _build_resolved(
    lid: str, construction: _Construction, airport_position: LatLon
) -> ResolvedProcedure:
    """A one-runway synthetic airport at `airport_position` flying `construction`."""
    plane = LocalPlane(airport_position)
    runways = {
        "09": RunwayStart(
            "09", airport_position, AIRPORT_ELEVATION_FT, 90.0, construction.gradient_09
        ),
        "27": RunwayStart(
            "27",
            plane.to_latlon(*plane_offset((0.0, 0.0), 90.0, RUNWAY_LENGTH_NM)),
            AIRPORT_ELEVATION_FT,
            270.0,
            None,
        ),
    }
    points = {
        VOR_IDENT: ResolvedPoint(VOR_IDENT, plane.to_latlon(*construction.vor_xy), 0.0),
        FIX_IDENT: ResolvedPoint(FIX_IDENT, plane.to_latlon(*construction.fix_xy), 0.0),
    }
    procedure = Procedure(
        lid, None, construction.groups, construction.shared_tail, construction.vcoa
    )
    return ResolvedProcedure(
        procedure=procedure,
        airport_lid=lid,
        airport_name=construction.title,
        airport_position=airport_position,
        airport_elevation_ft=AIRPORT_ELEVATION_FT,
        airport_variation_east=0.0,
        runways=runways,
        points=points,
        published_holds={},
    )
