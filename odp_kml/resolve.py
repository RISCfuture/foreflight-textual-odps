"""Bind a parsed procedure's runway, navaid, fix, and hold references to real
positions from NASR.

Strictness is the point: anything the pipeline cannot pin down with a single,
unambiguous NASR record raises `ResolveError` rather than guessing, so the
caller can turn it into a `Finding` instead of drawing a wrong procedure.
"""

from __future__ import annotations

import dataclasses
import datetime
import functools

from pygeomag import GeoMag, calculate_decimal_year

from . import nasr
from .findings import Kind
from .geo import LatLon, destination, distance_nm, initial_bearing
from .procedure import (
    ClimbInHold,
    Compass8,
    FixRef,
    HoldSpec,
    NavaidRef,
    Procedure,
    Turn,
)
from .resolved import ResolvedPoint, ResolvedProcedure, RunwayStart

_MAX_RANGE_NM = 150.0
_TEST_FACILITY_TYPE = "VOT"
_FEET_PER_NM = 6076.11549


class ResolveError(Exception):
    """A procedure reference could not be bound to NASR data with certainty."""

    def __init__(self, kind: Kind, signature: str, detail: str) -> None:
        super().__init__(f"{kind}: {signature} ({detail})")
        self.kind = kind
        self.signature = signature
        self.detail = detail


def resolve(
    procedure: Procedure,
    airport: nasr.Airport,
    data: nasr.NasrData,
    min_climb: dict[str, float] | None = None,
) -> ResolvedProcedure:
    """Bind every runway, navaid, fix, and hold `procedure` refers to.

    Raises `ResolveError` when a runway end lacks a position, a navaid or fix
    cannot be found (or found more than once) within 150 NM of `airport`, or
    a holding pattern is missing from NASR or contradicts the procedure text.
    """
    nodes = list(_walk(procedure))
    airport_variation = _airport_variation(airport)
    return ResolvedProcedure(
        procedure=procedure,
        airport_lid=airport.lid,
        airport_name=airport.name,
        airport_position=airport.position,
        airport_elevation_ft=airport.elevation_ft,
        airport_variation_east=airport_variation,
        runways={
            rwy: _resolve_runway(airport, rwy, min_climb)
            for rwy in _runway_ids(procedure)
        },
        points=_resolve_points(nodes, airport, data, airport_variation),
        published_holds=_resolve_holds(nodes, data),
    )


# --- AST traversal -----------------------------------------------------


def _runway_ids(procedure: Procedure) -> set[str]:
    """Every runway identifier named by a flown runway group or a VCOA group.

    A runway whose departure is NA or flies a charted DP is never drawn, so
    it is not resolved.
    """
    flown = (
        group for group in procedure.runway_groups if group.legs and not group.graphic
    )
    return {rwy for group in flown for rwy in group.runways} | {
        rwy for group in procedure.vcoa for rwy in group.runways
    }


def _walk(node):
    """Yield every dataclass node in `node`'s tree, depth-first.

    Walking every field generically (rather than switching on node type)
    means a new leg or `Until` variant is covered without touching this
    module, as long as its refs live in dataclass fields or tuples of them.
    """
    if node is None:
        return
    if isinstance(node, tuple):
        for item in node:
            yield from _walk(item)
        return
    if dataclasses.is_dataclass(node):
        yield node
        for field in dataclasses.fields(node):
            yield from _walk(getattr(node, field.name))


# --- Runways -------------------------------------------------------------


def _resolve_runway(
    airport: nasr.Airport, rwy: str, min_climb: dict[str, float] | None
) -> RunwayStart:
    """The `RunwayStart` for departing runway `rwy` at `airport`."""
    end = airport.runway_end(rwy)
    reciprocal = airport.reciprocal_end(rwy)
    if (
        end is None
        or reciprocal is None
        or end.position is None
        or reciprocal.position is None
    ):
        raise ResolveError(
            Kind.RUNWAY_MISSING,
            "runway end position missing",
            f'runway "{rwy}" at {airport.lid}',
        )
    course_true = initial_bearing(end.position, reciprocal.position)
    der = _der_position(airport, rwy, end, reciprocal)
    der_elevation_ft = (
        reciprocal.elevation_ft
        if reciprocal.elevation_ft is not None
        else airport.elevation_ft
    )
    return RunwayStart(
        runway=rwy,
        der=der,
        der_elevation_ft=der_elevation_ft,
        course_true=course_true,
        min_climb_gradient_ft_nm=(min_climb or {}).get(rwy),
    )


def _der_position(
    airport: nasr.Airport, rwy: str, end: nasr.RunwayEnd, reciprocal: nasr.RunwayEnd
) -> LatLon:
    """The departure end of the runway: the reciprocal end's position, pulled
    back toward `end` when the published takeoff run available is shorter
    than the full runway length."""
    runway = airport.runway_containing(rwy)
    if runway is None:
        raise ResolveError(
            Kind.RUNWAY_MISSING,
            "runway end position missing",
            f'runway "{rwy}" at {airport.lid}',
        )
    if (
        runway.length_ft is None
        or end.tora_ft is None
        or end.tora_ft >= runway.length_ft
    ):
        return reciprocal.position
    shift_nm = (runway.length_ft - end.tora_ft) / _FEET_PER_NM
    back_bearing = initial_bearing(reciprocal.position, end.position)
    return destination(reciprocal.position, back_bearing, shift_nm)


# --- Magnetic variation ----------------------------------------------------


def _airport_variation(airport: nasr.Airport) -> float:
    """The airport's published magnetic variation, or a WMM estimate."""
    return (
        airport.mag_var_east
        if airport.mag_var_east is not None
        else _wmm_declination(airport.position)
    )


@functools.cache
def _geo_mag() -> GeoMag:
    return GeoMag()


def _wmm_declination(position: LatLon) -> float:
    """The current-year WMM magnetic declination (east positive) at `position`."""
    decimal_year = calculate_decimal_year(datetime.datetime.now(tz=datetime.UTC).date())
    result = _geo_mag().calculate(
        glat=position.lat,
        glon=position.lon,
        alt=0,
        time=decimal_year,
        allow_date_outside_lifespan=True,
    )
    return result.d


# --- Navaids and fixes -------------------------------------------------


def _resolve_points(
    nodes: list, airport: nasr.Airport, data: nasr.NasrData, airport_variation: float
) -> dict[str, ResolvedPoint]:
    """Every navaid or fix the procedure refers to, resolved by identifier.

    Raises `ResolveError` when refs sharing an identifier land on different
    facilities. Where a navaid and a fix share one, the navaid's resolution
    (with its own declination) is kept.
    """
    refs = {node for node in nodes if isinstance(node, (NavaidRef, FixRef))}
    points: dict[str, ResolvedPoint] = {}
    for ref in sorted(refs, key=lambda ref: isinstance(ref, FixRef)):
        point = _resolve_ref(ref, airport, data, airport_variation)
        _require_same_facility(points.setdefault(ref.ident, point), point)
    return points


def _require_same_facility(kept: ResolvedPoint, other: ResolvedPoint) -> None:
    if kept.position != other.position:
        raise ResolveError(
            Kind.AMBIGUOUS_REF,
            "ident resolves to two facilities",
            f'"{kept.ident}" at {kept.position} and {other.position}',
        )


def _resolve_ref(
    ref: NavaidRef | FixRef,
    airport: nasr.Airport,
    data: nasr.NasrData,
    airport_variation: float,
) -> ResolvedPoint:
    if isinstance(ref, NavaidRef):
        return _resolve_navaid(ref, airport, data)
    return _resolve_fix(ref, airport, data, airport_variation)


def _resolve_navaid(
    ref: NavaidRef, airport: nasr.Airport, data: nasr.NasrData
) -> ResolvedPoint:
    """The single navaid within range named `ref`, with its own declination.

    A VOR test facility (VOT) sharing the ident is never it: it has no
    radials and is no fix to fly to.
    """
    matches = [
        navaid
        for navaid in data.navaids.get(ref.ident, ())
        if navaid.type != _TEST_FACILITY_TYPE
        and (ref.type is None or navaid.type == ref.type.value)
        and distance_nm(airport.position, navaid.position) <= _MAX_RANGE_NM
    ]
    navaid = _pick_one(matches, ref.ident, "navaid not found", "navaid ambiguous")
    declination = (
        navaid.mag_var_east
        if navaid.mag_var_east is not None
        else _wmm_declination(navaid.position)
    )
    return ResolvedPoint(ref.ident, navaid.position, declination)


def _resolve_fix(
    ref: FixRef, airport: nasr.Airport, data: nasr.NasrData, airport_variation: float
) -> ResolvedPoint:
    """The single fix within range named `ref`, using the airport's declination."""
    matches = [
        fix
        for fix in data.fixes.get(ref.ident, ())
        if distance_nm(airport.position, fix.position) <= _MAX_RANGE_NM
    ]
    fix = _pick_one(matches, ref.ident, "fix not found", "fix ambiguous")
    return ResolvedPoint(ref.ident, fix.position, airport_variation)


def _pick_one(
    candidates: list, ident: str, missing_signature: str, ambiguous_signature: str
):
    """The single candidate in range, or raise for zero or more than one."""
    if not candidates:
        raise ResolveError(
            Kind.UNRESOLVED_REF,
            missing_signature,
            f'no match for "{ident}" within {_MAX_RANGE_NM:.0f} NM',
        )
    if len(candidates) > 1:
        raise ResolveError(
            Kind.AMBIGUOUS_REF,
            ambiguous_signature,
            f'{len(candidates)} matches for "{ident}" within {_MAX_RANGE_NM:.0f} NM',
        )
    return candidates[0]


# --- Holding patterns --------------------------------------------------


def _resolve_holds(nodes: list, data: nasr.NasrData) -> dict[str, HoldSpec]:
    """Published `HoldSpec`s for every `ClimbInHold` whose text omits one.

    Also checks a text-provided hold against every published hold at that
    fix that plausibly belongs to this procedure, raising when none of them
    agrees on inbound course and turn.
    """
    navaid_idents = {node.ident for node in nodes if isinstance(node, NavaidRef)}
    published: dict[str, HoldSpec] = {}
    for leg in nodes:
        if not isinstance(leg, ClimbInHold):
            continue
        fix_ident = leg.fix.ident
        matches = _matching_holds(data, fix_ident, navaid_idents)
        if leg.hold is None:
            published[fix_ident] = _hold_from_nasr(matches, fix_ident)
        else:
            _check_hold_agrees_with_any(leg.hold, matches, fix_ident)
    return published


def _matching_holds(
    data: nasr.NasrData, fix_ident: str, navaid_idents: set[str]
) -> tuple[nasr.Hold, ...]:
    """HPF holds at `fix_ident` whose navaid, if any, plausibly belongs to
    this procedure.

    A hold record's own fix identifier can be reused nationally for an
    unrelated navaid elsewhere, so a hold is kept only when it has no
    navaid, its navaid is `fix_ident` itself, or that navaid is one this
    procedure already resolves.
    """
    return tuple(
        hold
        for hold in data.holds.get(fix_ident, ())
        if hold.fix_ident == fix_ident
        and (
            hold.navaid_ident is None
            or hold.navaid_ident == fix_ident
            or hold.navaid_ident in navaid_idents
        )
    )


def _hold_from_nasr(matches: tuple[nasr.Hold, ...], fix_ident: str) -> HoldSpec:
    """The single published hold at `fix_ident`, or raise if it is not unique."""
    if len(matches) != 1:
        raise ResolveError(
            Kind.HOLD_AMBIGUOUS,
            "hold ambiguous",
            f'{len(matches)} published holds at "{fix_ident}"',
        )
    return _hold_spec(matches[0])


def _check_hold_agrees_with_any(
    text_hold: HoldSpec, published: tuple[nasr.Hold, ...], fix_ident: str
) -> None:
    """Raise if the text's hold matches none of `published` on inbound
    course and turn. Nothing to check against when NASR has no holds here:
    the text's hold is trusted as-is."""
    if not published:
        return
    specs = [_hold_spec(hold) for hold in published]
    if not any(
        spec.turns == text_hold.turns
        and spec.inbound_course == text_hold.inbound_course
        for spec in specs
    ):
        raise ResolveError(
            Kind.HOLD_AMBIGUOUS,
            "hold contradicts NASR",
            f'text says {text_hold}, NASR has {specs} at "{fix_ident}"',
        )


def _hold_spec(hold: nasr.Hold) -> HoldSpec:
    """A `HoldSpec` from a NASR `Hold` record.

    Raises `ResolveError` when the record's turn or direction field is not
    one of the known enum values, rather than guessing.
    """
    try:
        direction = Compass8(hold.direction) if hold.direction else None
        turn = Turn(hold.turn)
    except ValueError as error:
        raise ResolveError(
            Kind.HOLD_AMBIGUOUS,
            "hold record unparseable",
            f'hold at "{hold.fix_ident}": {error}',
        ) from error
    return HoldSpec(direction, turn, hold.inbound_course)
