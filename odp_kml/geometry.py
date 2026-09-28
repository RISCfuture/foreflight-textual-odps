"""Turn a resolved departure procedure into planview shapes.

Every construction works on a `LocalPlane` centred on the airport (x east,
y north, in NM) and converts vertices back to lat/lon only when emitting a
shape. Anything the constructions cannot draw with certainty raises
`Degenerate`; `draw` never returns a partial drawing.
"""

from __future__ import annotations

import dataclasses
import math

from .geo import LocalPlane, heading_to_unit, magnetic_to_true
from .labels import (
    dme_label,
    format_altitude,
    heading_label,
    heading_phrase,
    heading_range_label,
    hold_label,
    navaid_radial_label,
    radial_phrase,
    speed_label,
    turn_phrase,
    vcoa_label,
)
from .plane import (
    Vec,
    along_across,
    arc,
    bearing,
    cross,
    distance,
    midpoint,
    offset,
    sign,
    sub,
    wrap180,
)
from .procedure import (
    Altitude,
    AtFix,
    ClimbHeading,
    ClimbingTurn,
    ClimbInHold,
    Direct,
    Dme,
    EnrouteAltitude,
    HeadingAndRadial,
    HeadingRange,
    HeadingSector,
    HoldSpec,
    Leg,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
    RunwayHeading,
    SpeedRestriction,
    Thence,
    Turn,
    VcoaGroup,
)
from .resolved import ResolvedProcedure, RunwayStart
from .shapes import AirportDrawing, Label, Polyline, Style

__all__ = ["Degenerate", "DisplayParams", "draw", "format_altitude", "turn_radius_nm"]

KT_PER_BANK_FACTOR = 68626.0
STANDARD_RATE_DIVISOR = 188.5
DER_CROSSING_HEIGHT_FT = 35.0
TURN_START_HEIGHT_FT = 400.0
MIN_TURN_START_NM = 1.0
MIN_ALTITUDE_LEG_NM = 1.0
MAX_ALTITUDE_LEG_NM = 30.0
MIN_LEG_NM = 0.5
ANGLE_EPSILON_DEG = 1e-6
MIN_INTERCEPT_DEG = 15.0
ON_RADIAL_TOLERANCE_NM = 0.5
ARRIVAL_TOLERANCE_NM = 0.01
HOLD_HIGH_ALTITUDE_FT = 14000
HOLD_LEG_MINUTES_LOW = 1.0
HOLD_LEG_MINUTES_HIGH = 1.5
SHARED_TAIL_TOLERANCE_NM = 0.5
ON_COURSE_STUB_NM = 1.0
ON_COURSE_DASHES = 4
ON_COURSE_DASH_NM = 0.15
ON_COURSE_BEND_DEG = 45.0
VCOA_DASHES = 36
ARROW_ARM_NM = 0.25
ARROW_SETBACK_NM = 0.5
ARROW_SPLAY_DEG = 30.0
RUNWAY_HEADING_LABEL = "rwy hdg"
UNSTATED_SENSE = 'radial without "inbound" or "outbound"'


@dataclasses.dataclass(frozen=True)
class DisplayParams:
    """Aircraft and drawing assumptions shared by every construction."""

    tas_kt: float = 150.0
    bank_deg: float = 25.0
    default_gradient_ft_nm: float = 200.0
    vcoa_radius_nm: float = 2.0
    heading_range_radius_nm: float = 3.0
    arc_step_deg: float = 5.0
    label_offset_nm: float = 0.35
    label_style: str = "plain"


DEFAULT_PARAMS = DisplayParams()


class Degenerate(Exception):
    """A construction cannot be drawn with certainty.

    `signature` is a short stable category (e.g. ``"radial colinear"``) for
    grouping findings; `detail` carries the specifics.
    """

    def __init__(self, signature: str, detail: str = "") -> None:
        super().__init__(f"{signature}: {detail}" if detail else signature)
        self.signature = signature
        self.detail = detail


def turn_radius_nm(tas_kt: float, bank_deg: float) -> float:
    """The turn radius in NM: 25° bank or standard rate, whichever banks less."""
    bank_radius = tas_kt**2 / (KT_PER_BANK_FACTOR * math.tan(math.radians(bank_deg)))
    standard_rate_radius = tas_kt / STANDARD_RATE_DIVISOR
    return max(bank_radius, standard_rate_radius)


def draw(
    resolved: ResolvedProcedure, params: DisplayParams = DEFAULT_PARAMS
) -> AirportDrawing:
    """Draw every runway group, the shared tail and VCOA groups of a procedure.

    Raises `Degenerate` if any construction is uncertain.
    """
    ctx = _Context(resolved, params)
    procedure = resolved.procedure
    pens = [
        pen
        for runway_group in procedure.runway_groups
        if not runway_group.graphic
        for pen in _draw_runway_group(ctx, runway_group)
        if _continues_to_tail(runway_group)
    ]
    if procedure.shared_tail:
        _draw_shared_tail(ctx, pens, procedure.shared_tail)
    for vcoa in procedure.vcoa:
        _draw_vcoa(ctx, vcoa)
    return AirportDrawing(
        resolved.airport_lid,
        resolved.airport_name,
        tuple(ctx.shapes),
        position=resolved.airport_position,
    )


class _Context:
    """The procedure being drawn, its plane, and the shapes emitted so far."""

    def __init__(self, resolved: ResolvedProcedure, params: DisplayParams) -> None:
        self.resolved = resolved
        self.params = params
        self.plane = LocalPlane(resolved.airport_position)
        self.radius = turn_radius_nm(params.tas_kt, params.bank_deg)
        self.shapes: list[Polyline | Label] = []

    def xy(self, ident: str) -> Vec:
        return self.plane.to_xy(self.resolved.points[ident].position)

    def declination(self, ident: str) -> float:
        return self.resolved.points[ident].declination_east

    def heading_true(self, magnetic: int) -> float:
        return magnetic_to_true(magnetic, self.resolved.airport_variation_east)

    def polyline(self, name: str, style: Style, points: list[Vec]) -> None:
        latlons = tuple(self.plane.to_latlon(*p) for p in points)
        self._emit(Polyline(name, style, latlons))

    def arc(self, centre: Vec, radius: float, start: float, sweep: float) -> list[Vec]:
        return arc(centre, radius, start, sweep, self.params.arc_step_deg)

    def label(self, text: str, p: Vec) -> None:
        self._emit(Label(text, self.plane.to_latlon(*p)))

    def _emit(self, shape: Polyline | Label) -> None:
        """Keep one of identical shapes: routes that reach the same hold draw
        it identically, VCOA groups for several runways label the same circle
        at the same point, and groups that are alternatives for one runway
        ("All other courses: …") share its initial climb."""
        if shape not in self.shapes:
            self.shapes.append(shape)


@dataclasses.dataclass
class _Pen:
    """Where the aircraft is: position, true course, and climb bookkeeping.

    `along_nm` is the along-track distance flown since `base_alt_ft`, the
    altitude at which the climb at `gradient` began.
    """

    ctx: _Context
    name: str
    at: Vec
    course: float
    along_nm: float
    base_alt_ft: float
    gradient: float
    runway_course: float | None = None

    def altitude_leg_nm(self, feet: int) -> float:
        """Along-track distance at which the climb reaches `feet`."""
        length = max(MIN_ALTITUDE_LEG_NM, (feet - self.base_alt_ft) / self.gradient)
        if length > MAX_ALTITUDE_LEG_NM:
            raise Degenerate("altitude leg capped", f"{feet} ft needs {length:.1f} NM")
        return length

    def turn_onto(self, course: float, direction: Turn | None) -> tuple[list[Vec], int]:
        """Fly a fly-by arc onto `course`; return its vertices (from the
        current position) and the turn side (+1 right, -1 left, 0 none)."""
        delta = _signed_turn(self.course, course, direction)
        side = sign(delta)
        points = [self.at]
        if side:
            centre = offset(self.at, self.course + 90 * side, self.ctx.radius)
            points = self.ctx.arc(
                centre, self.ctx.radius, self.course - 90 * side, delta
            )
            self.along_nm += self.ctx.radius * math.radians(abs(delta))
        self.at, self.course = points[-1], course
        return points, side

    def straight_to(self, end: Vec) -> None:
        length = distance(self.at, end)
        if length > 0:
            self.along_nm += length
            self.course = bearing(self.at, end)
        self.at = end

    def label_side(self, turn_side: int) -> float:
        """Bearing for offsetting a label: outside the turn, else right."""
        return self.course + 90 * (-turn_side or 1)


def _draw_runway_group(ctx: _Context, runway_group: RunwayGroup) -> list[_Pen]:
    """Draw the group's legs from each of its runways; return where each ended."""
    pens = [
        _start_runway(ctx, ctx.resolved.runways[name]) for name in runway_group.runways
    ]
    for pen in pens:
        _draw_legs(pen, runway_group.legs)
    return pens


def _continues_to_tail(runway_group: RunwayGroup) -> bool:
    return bool(runway_group.legs) and isinstance(runway_group.legs[-1], Thence)


def _draw_shared_tail(ctx: _Context, pens: list[_Pen], legs: tuple[Leg, ...]) -> None:
    """Draw the legs every runway group continues with, once, from where
    the groups converge."""
    if not pens:
        _unsupported(legs)
    first = pens[0]
    for pen in pens[1:]:
        gap = distance(first.at, pen.at)
        if gap > SHARED_TAIL_TOLERANCE_NM:
            raise Degenerate(
                "shared tail start mismatch", f"{pen.name} {gap:.2f} NM away"
            )
    runways = (
        runway
        for group in ctx.resolved.procedure.runway_groups
        if not group.graphic
        for runway in group.runways
    )
    first.name = f"RWY {'/'.join(runways)}"
    _draw_legs(first, legs)


def _draw_vcoa(ctx: _Context, vcoa: VcoaGroup) -> None:
    """A dashed circle to climb in over the airport (or a fix), then its legs."""
    centre = ctx.xy(vcoa.cross.ident) if vcoa.cross else (0.0, 0.0)
    radius = ctx.params.vcoa_radius_nm
    name = f"{_vcoa_runways_name(vcoa.runways)}: VCOA"
    for start in _dash_starts(VCOA_DASHES):
        dash = ctx.arc(centre, radius, start, 180 / VCOA_DASHES)
        ctx.polyline(name, Style.VCOA, dash)
    ctx.label(
        vcoa_label(vcoa.at_or_above, ctx.params.label_style, vcoa.bound),
        offset(centre, 0.0, radius),
    )
    if _departs_toward_something(vcoa.then):
        outward = _departure_bearing(ctx, centre, vcoa.then[0])
        pen = _Pen(
            ctx,
            name,
            offset(centre, outward, radius),
            outward,
            0.0,
            vcoa.at_or_above,
            ctx.params.default_gradient_ft_nm,
        )
        _draw_legs(pen, vcoa.then)


def _vcoa_runways_name(runways: tuple[str, ...]) -> str:
    """``RWY 15/33``, or ``ALL RWYS`` for a VCOA that names no runways."""
    return f"RWY {'/'.join(runways)}" if runways else "ALL RWYS"


def _departs_toward_something(legs: tuple[Leg, ...]) -> bool:
    """Whether the legs after a VCOA fly anywhere beyond "proceed on course"."""
    return any(not isinstance(leg, ProceedOnCourse) for leg in legs)


def _dash_starts(count: int) -> list[float]:
    return [360 * i / count for i in range(count)]


def _departure_bearing(ctx: _Context, centre: Vec, leg: Leg) -> float:
    """Bearing from the VCOA circle's centre toward the first leg's target."""
    match leg:
        case ClimbingTurn(then=then):
            return _departure_bearing(ctx, centre, then)
        case ClimbHeading(heading=heading):
            return ctx.heading_true(heading)
        case Direct(target=target) | ClimbInHold(fix=target):
            ident = target.ident
        case Radial(navaid=navaid) | HeadingAndRadial(navaid=navaid):
            ident = navaid.ident
        case _:
            _unsupported(leg)
    target = ctx.xy(ident)
    if distance(centre, target) < ARRIVAL_TOLERANCE_NM:
        _unsupported(leg)
    return bearing(centre, target)


def _start_runway(ctx: _Context, start: RunwayStart) -> _Pen:
    """Draw the straight climb from the DER to the turn-start point."""
    gradient = start.min_climb_gradient_ft_nm or ctx.params.default_gradient_ft_nm
    der = ctx.plane.to_xy(start.der)
    pen = _Pen(
        ctx,
        f"RWY {start.runway}",
        der,
        start.course_true,
        0.0,
        start.der_elevation_ft + DER_CROSSING_HEIGHT_FT,
        gradient,
        runway_course=start.course_true,
    )
    turn_start = offset(der, start.course_true, _turn_start_nm(gradient))
    ctx.polyline(f"{pen.name}: initial climb", Style.ROUTE, [der, turn_start])
    pen.straight_to(turn_start)
    return pen


def _turn_start_nm(gradient: float) -> float:
    return max(MIN_TURN_START_NM, TURN_START_HEIGHT_FT / gradient)


def _draw_legs(pen: _Pen, legs: tuple[Leg, ...]) -> None:
    for index, leg in enumerate(legs):
        start, course = pen.at, pen.course
        _draw_leg(pen, leg, None)
        if speed := _speed_restriction(leg):
            _label_speed(pen.ctx, speed, start, course)
        if _ends_in_heading_range(leg):
            _require_only_on_course(legs[index + 1 :])
            return


def _ends_in_heading_range(leg: Leg) -> bool:
    return isinstance(leg.then if isinstance(leg, ClimbingTurn) else leg, HeadingRange)


def _require_only_on_course(legs: tuple[Leg, ...]) -> None:
    """After a heading range the aircraft may be on any heading in it, so only
    "before proceeding on course" can follow; it needs no stub of its own."""
    for leg in legs:
        if not isinstance(leg, ProceedOnCourse):
            raise Degenerate("leg after a heading range", repr(leg))


def _speed_restriction(leg: Leg) -> SpeedRestriction | None:
    if isinstance(leg, ClimbingTurn):
        return getattr(leg.then, "speed", None)
    return getattr(leg, "speed", None)


def _label_speed(
    ctx: _Context, speed: SpeedRestriction, start: Vec, course: float
) -> None:
    """Label a speed limit where its leg begins, left of the course so it
    clears the heading label on the right."""
    ctx.label(
        speed_label(speed), offset(start, course - 90, ctx.params.label_offset_nm)
    )


def _draw_leg(pen: _Pen, leg: Leg, direction: Turn | None) -> None:
    match leg:
        case ClimbingTurn():
            _draw_leg(pen, leg.then, leg.direction)
        case ClimbHeading():
            _climb_heading(pen, leg, direction)
        case RunwayHeading():
            _runway_heading(pen, leg, direction)
        case HeadingRange():
            _heading_range(pen, leg, direction)
        case Direct():
            _direct(pen, leg, direction)
        case Radial():
            _radial(pen, leg, direction)
        case HeadingAndRadial():
            _heading_and_radial(pen, leg, direction)
        case ClimbInHold():
            _climb_in_hold(pen, leg)
        case ProceedOnCourse():
            _proceed_on_course(pen, leg)
        case Thence():
            pass
        case _:
            _unsupported(leg)


def _unsupported(leg) -> None:
    raise Degenerate(f"unsupported construction: {type(leg).__name__}", repr(leg))


def _climb_heading(pen: _Pen, leg: ClimbHeading, direction: Turn | None) -> None:
    course = pen.ctx.heading_true(leg.heading)
    name = _leg_name(pen, direction, heading_phrase(leg))
    _climb_course(pen, leg, direction, course, name, heading_label(leg.heading))


def _runway_heading(pen: _Pen, leg: RunwayHeading, direction: Turn | None) -> None:
    """Climb along the runway's own course, from the runway's NASR ends; a
    route that starts over the airport (a VCOA's) has no runway to follow."""
    if pen.runway_course is None:
        _unsupported(leg)
    name = _leg_name(pen, direction, "runway heading")
    _climb_course(pen, leg, direction, pen.runway_course, name, RUNWAY_HEADING_LABEL)


def _climb_course(
    pen: _Pen,
    leg: ClimbHeading | RunwayHeading,
    direction: Turn | None,
    course: float,
    name: str,
    heading_text: str,
) -> None:
    """Turn onto the true `course`, then (with an altitude) climb straight on it.

    An altitude the climb already reached before the turn ends the leg
    where the turn ends.
    """
    ctx = pen.ctx
    points, side = pen.turn_onto(course, direction)
    match leg.until:
        case None:
            if len(points) > 1:
                ctx.polyline(name, Style.ROUTE, points)
                _arrowhead(ctx, name, Style.ROUTE, pen.at, pen.course)
            _offset_label(pen, heading_text, pen.at, side)
            return
        case Altitude():
            _require_climb(pen, leg.until)
            straight = max(0.0, pen.altitude_leg_nm(leg.until.feet) - pen.along_nm)
        case _:
            _unsupported(leg)
    turn_end = pen.at
    pen.straight_to(offset(turn_end, pen.course, straight))
    route = [*points, pen.at] if straight else points
    if len(route) > 1:
        ctx.polyline(name, Style.ROUTE, route)
    _arrowhead(ctx, name, Style.ROUTE, pen.at, pen.course)
    _offset_label(pen, heading_text, midpoint(turn_end, pen.at), side)
    ctx.label(format_altitude(leg.until, ctx.params.label_style), pen.at)


def _heading_range(pen: _Pen, leg: HeadingRange, direction: Turn | None) -> None:
    """Each sector as a wedge from where the turn may begin: its two limiting
    headings and the arc between them, `heading_range_radius_nm` out.

    The label (and any altitude) sits just beyond the arc of the first sector.
    """
    ctx = pen.ctx
    match leg.until:
        case None:
            pass
        case Altitude():
            _require_climb(pen, leg.until)
        case _:
            _unsupported(leg)
    radius = ctx.params.heading_range_radius_nm
    apex = pen.at
    for index, sector in enumerate(leg.sectors):
        start = ctx.heading_true(sector.start)
        sweep = _sector_sweep(sector)
        name = f"{_leg_name(pen, direction, 'heading')} {_sector_phrase(sector)}"
        ctx.polyline(name, Style.RADIAL, [offset(apex, start, radius), apex])
        ctx.polyline(name, Style.RADIAL, [apex, offset(apex, start + sweep, radius)])
        ctx.polyline(name, Style.RADIAL, ctx.arc(apex, radius, start, sweep))
        if index == 0:
            outside = offset(
                apex, start + sweep / 2, radius + ctx.params.label_offset_nm
            )
            ctx.label(
                heading_range_label(leg, direction, ctx.params.label_style), outside
            )


def _sector_sweep(sector: HeadingSector) -> float:
    """Signed degrees (positive clockwise) from the sector's start to its end;
    a sector whose ends coincide is refused rather than read as a full circle."""
    if sector.start % 360 == sector.end % 360:
        raise Degenerate("empty heading sector", repr(sector))
    if sector.clockwise:
        return (sector.end - sector.start) % 360
    return -((sector.start - sector.end) % 360)


def _sector_phrase(sector: HeadingSector) -> str:
    """e.g. ``350 CW 162``."""
    sense = "CW" if sector.clockwise else "CCW"
    return f"{sector.start:03d} {sense} {sector.end:03d}"


def _require_climb(pen: _Pen, altitude: Altitude) -> None:
    """Refuse an altitude at or below where the climb began (at least the DER
    crossing height): the text or the data behind it is wrong."""
    if altitude.feet <= pen.base_alt_ft:
        raise Degenerate(
            "altitude not above current",
            f"{altitude.feet} ft, climb began at {pen.base_alt_ft:.0f} ft",
        )


def _direct(pen: _Pen, leg: Direct, direction: Turn | None) -> None:
    """Arc from the current course onto the tangent line through the fix."""
    ctx = pen.ctx
    ident = leg.target.ident
    name = _leg_name(pen, direction, f"direct {ident}")
    target = ctx.xy(ident)
    side = _turn_side(pen, target, direction)
    centre = offset(pen.at, pen.course + 90 * side, ctx.radius)
    tangent = _tangent_point(centre, ctx.radius, target, side, leg)
    points, _ = pen.turn_onto(bearing(centre, tangent) + 90 * side, _turn(side))
    _require_length(distance(tangent, target), leg)
    pen.straight_to(target)
    ctx.polyline(name, Style.ROUTE, [*points, target])
    _arrowhead(ctx, name, Style.ROUTE, target, pen.course)


def _turn_side(pen: _Pen, target: Vec, direction: Turn | None) -> int:
    """+1 to turn right, -1 left: as published, else the smaller turn."""
    if direction is not None:
        return _side(direction)
    return 1 if wrap180(bearing(pen.at, target) - pen.course) >= 0 else -1


def _tangent_point(centre: Vec, radius: float, target: Vec, side: int, leg) -> Vec:
    """Where a turn around `centre` rolls out tracking straight to `target`.

    A target inside the turn circle would need an extended straight stub
    before turning, which the design treats as uncertain.
    """
    reach = distance(centre, target)
    if reach < radius:
        raise Degenerate("fix inside turn circle", repr(leg))
    swing = math.degrees(math.acos(radius / reach))
    return offset(centre, bearing(centre, target) - side * swing, radius)


def _radial(pen: _Pen, leg: Radial, direction: Turn | None) -> None:
    """Turn onto the radial's course from a position already on it, then track it."""
    ctx = pen.ctx
    leg = _with_sense(ctx, leg, pen.at)
    radial_course = _radial_true(ctx, leg)
    name = _leg_name(pen, direction, radial_phrase(leg))
    points, side = pen.turn_onto(_tracking_course(leg, radial_course), direction)
    _require_on_radial(pen.at, ctx.xy(leg.navaid.ident), radial_course, leg)
    _track_radial(pen, leg, name, points, side)


def _heading_and_radial(
    pen: _Pen, leg: HeadingAndRadial, direction: Turn | None
) -> None:
    """Fly a heading to intercept a radial, rounding the corner, then track it."""
    ctx = pen.ctx
    heading = ctx.heading_true(leg.heading)
    radial_course = _radial_true(ctx, leg)
    _require_intercept_angle(heading, radial_course, leg)
    points, heading_side = pen.turn_onto(heading, direction)
    corner = _intercept(pen.at, heading, ctx.xy(leg.navaid.ident), radial_course, leg)
    leg = _with_sense(ctx, leg, corner)
    name = _leg_name(pen, direction, f"heading {leg.heading:03d} to intercept ")
    name += radial_phrase(leg)
    track = _tracking_course(leg, radial_course)
    turn = _signed_turn(heading, track, None)
    lead = ctx.radius * math.tan(math.radians(abs(turn)) / 2)
    if distance(pen.at, corner) < lead:
        raise Degenerate(
            "radial intercept behind", f"turn needs {lead:.2f} NM: {leg!r}"
        )
    heading_start = pen.at
    pen.straight_to(offset(corner, heading + 180, lead))
    heading_middle = midpoint(heading_start, pen.at)
    _offset_label(pen, heading_label(leg.heading), heading_middle, heading_side)
    rounding, side = pen.turn_onto(track, _turn(sign(turn)))
    _track_radial(pen, leg, name, [*points, *rounding], side)


def _with_sense[L: (Radial, HeadingAndRadial)](ctx: _Context, leg: L, joined: Vec) -> L:
    """`leg` with its sense settled: as printed, else toward where it ends.

    A radial printed without "inbound" or "outbound" that ends at a fix or a
    DME distance on that radial is flown whichever way along it reaches that
    point from `joined`, where the aircraft joins the radial. Any other, or
    one whose end is within `MIN_LEG_NM` of the join, is refused.
    """
    if leg.outbound is not None:
        return leg
    navaid = ctx.xy(leg.navaid.ident)
    course = _radial_true(ctx, leg)
    match leg.until:
        case AtFix(target=target):
            end = along_across(sub(ctx.xy(target.ident), navaid), course)[0]
        case Dme(navaid=dme, nm=nm) if dme.ident == leg.navaid.ident:
            end = nm
        case _:
            raise Degenerate(UNSTATED_SENSE, repr(leg))
    start = along_across(sub(joined, navaid), course)[0]
    if abs(end - start) < MIN_LEG_NM:
        raise Degenerate(UNSTATED_SENSE, repr(leg))
    return dataclasses.replace(leg, outbound=end > start)


def _require_intercept_angle(heading: float, radial_course: float, leg) -> None:
    angle = min(
        abs(wrap180(heading - radial_course)),
        abs(wrap180(heading - radial_course - 180)),
    )
    if angle < MIN_INTERCEPT_DEG:
        raise Degenerate("radial colinear", f"{angle:.1f} deg: {leg!r}")


def _intercept(
    start: Vec, heading: float, navaid: Vec, radial_course: float, leg
) -> Vec:
    """Where a ray from `start` on `heading` crosses the radial from `navaid`.

    The crossing must lie ahead of `start` and on the radial itself, not on
    its extension behind the navaid.
    """
    u_h, u_r = heading_to_unit(heading), heading_to_unit(radial_course)
    to_navaid = sub(navaid, start)
    denominator = cross(u_h, u_r)
    ahead = cross(to_navaid, u_r) / denominator
    along_radial = cross(to_navaid, u_h) / denominator
    if ahead <= 0 or along_radial <= 0:
        raise Degenerate("radial intercept behind", repr(leg))
    return offset(start, heading, ahead)


def _require_on_radial(at: Vec, navaid: Vec, radial_course: float, leg) -> None:
    """Refuse to track a radial the route is not already established on."""
    along, across = along_across(sub(at, navaid), radial_course)
    if along < 0 or abs(across) > ON_RADIAL_TOLERANCE_NM:
        raise Degenerate("off radial", f"{across:.2f} NM abeam: {leg!r}")


def _track_radial(
    pen: _Pen, leg: Radial | HeadingAndRadial, name: str, points: list[Vec], side: int
) -> None:
    """Track the radial from the current position to the leg's end, drawing
    the route and the radial itself."""
    ctx = pen.ctx
    navaid = ctx.xy(leg.navaid.ident)
    joined = pen.at
    end = _tracking_end(pen, leg, navaid)
    _require_length(along_across(sub(end, joined), pen.course)[0], leg)
    pen.straight_to(end)
    ctx.polyline(name, Style.ROUTE, [*points, end])
    _arrowhead(ctx, name, Style.ROUTE, end, pen.course)
    if terminator := _terminator_label(ctx, leg.until):
        ctx.label(terminator, end)
    far = max(joined, end, key=lambda p: distance(navaid, p))
    _draw_radial(ctx, leg, navaid, far)


def _terminator_label(ctx: _Context, until) -> str | None:
    """The altitude or DME distance ending a radial leg; a fix is already
    named on ForeFlight's own chart."""
    match until:
        case Altitude():
            return format_altitude(until, ctx.params.label_style)
        case Dme():
            return dme_label(until)
    return None


def _tracking_end(pen: _Pen, leg: Radial | HeadingAndRadial, navaid: Vec) -> Vec:
    """Where tracking the radial stops: at the navaid, an altitude, a fix or a DME."""
    ctx = pen.ctx
    match leg.until:
        case None if not leg.outbound:
            return navaid
        case AtFix(target=target) if target.ident == leg.navaid.ident:
            return navaid
        case AtFix(target=target):
            fix = ctx.xy(target.ident)
            along, across = along_across(sub(fix, pen.at), pen.course)
            if abs(across) > ON_RADIAL_TOLERANCE_NM:
                raise Degenerate("off radial", f"{target.ident} {across:.2f} NM abeam")
            return offset(pen.at, pen.course, along)
        case Dme(navaid=dme, nm=nm, fix=fix) if dme.ident == leg.navaid.ident:
            end = offset(navaid, _radial_true(ctx, leg), nm)
            if fix is not None:
                _require_at(ctx, fix.ident, end, leg)
            return end
        case Altitude(feet=feet):
            remaining = pen.altitude_leg_nm(feet) - pen.along_nm
            end = offset(pen.at, pen.course, remaining)
            if not leg.outbound and along_across(sub(navaid, end), pen.course)[0] < 0:
                _unsupported(leg)
            return end
    _unsupported(leg)


def _require_at(ctx: _Context, ident: str, end: Vec, leg) -> None:
    """Refuse a DME terminator whose named fix is not at that distance."""
    miss = distance(ctx.xy(ident), end)
    if miss > ON_RADIAL_TOLERANCE_NM:
        raise Degenerate("DME fix mismatch", f"{ident} {miss:.2f} NM away: {leg!r}")


def _draw_radial(ctx: _Context, leg, navaid: Vec, far: Vec) -> None:
    radial = navaid_radial_label(leg.navaid, leg.radial)
    ctx.polyline(radial, Style.RADIAL, [navaid, far])
    anchor = midpoint(navaid, far)
    ctx.label(
        radial, offset(anchor, bearing(navaid, far) + 90, ctx.params.label_offset_nm)
    )


def _radial_true(ctx: _Context, leg: Radial | HeadingAndRadial) -> float:
    return magnetic_to_true(leg.radial, ctx.declination(leg.navaid.ident))


def _tracking_course(leg: Radial | HeadingAndRadial, radial_course: float) -> float:
    return radial_course if leg.outbound else (radial_course + 180) % 360


def _proceed_on_course(pen: _Pen, leg: ProceedOnCourse) -> None:
    """A short dashed stub on course, bent toward the restricted side if any."""
    ctx = pen.ctx
    bend = 0.0
    if leg.turn_restriction is not None:
        bend = ON_COURSE_BEND_DEG * _side(leg.turn_restriction)
    course = pen.course + bend
    name = _leg_name(pen, None, "proceed on course")
    spacing = (ON_COURSE_STUB_NM - ON_COURSE_DASH_NM) / (ON_COURSE_DASHES - 1)
    for i in range(ON_COURSE_DASHES):
        start = offset(pen.at, course, i * spacing)
        ctx.polyline(
            name, Style.ROUTE, [start, offset(start, course, ON_COURSE_DASH_NM)]
        )
    pen.straight_to(offset(pen.at, course, ON_COURSE_STUB_NM))
    _arrowhead(ctx, name, Style.ROUTE, pen.at, pen.course)


def _climb_in_hold(pen: _Pen, leg: ClimbInHold) -> None:
    """Proceed direct to the fix if not already there, then draw the racetrack.

    A fix behind the aircraft would need a turn the procedure does not
    describe, so it is degenerate.
    """
    ctx = pen.ctx
    ident = leg.fix.ident
    spec = _hold_spec(ctx, leg)
    fix = ctx.xy(ident)
    if distance(pen.at, fix) > ARRIVAL_TOLERANCE_NM:
        _require_ahead(pen, fix, leg)
        _direct(pen, Direct(leg.fix), None)
    inbound = magnetic_to_true(spec.inbound_course, ctx.declination(ident))
    side = _side(spec.turns)
    length = _hold_leg_nm(ctx, leg)
    racetrack = _racetrack(ctx, fix, inbound, side, length)
    name = f"HOLD {ident}"
    ctx.polyline(name, Style.HOLD, racetrack)
    _arrowhead(ctx, name, Style.HOLD, fix, inbound)
    inbound_middle = offset(fix, inbound + 180, length / 2)
    label_at = offset(inbound_middle, inbound - 90 * side, ctx.params.label_offset_nm)
    ctx.label(hold_label(spec, leg.until, ctx.params.label_style), label_at)
    pen.at, pen.course = fix, inbound
    if isinstance(leg.until, Altitude):
        pen.base_alt_ft, pen.along_nm = leg.until.feet, 0.0


def _require_ahead(pen: _Pen, fix: Vec, leg: ClimbInHold) -> None:
    if abs(wrap180(bearing(pen.at, fix) - pen.course)) > 90:
        raise Degenerate("hold fix behind", repr(leg))


def _hold_spec(ctx: _Context, leg: ClimbInHold) -> HoldSpec:
    spec = leg.hold or ctx.resolved.published_holds.get(leg.fix.ident)
    if spec is None:
        raise Degenerate("hold unspecified", repr(leg))
    return spec


def _hold_leg_nm(ctx: _Context, leg: ClimbInHold) -> float:
    """One minute legs up to 14,000 ft, 1.5 minutes above, or a published DME
    length. A hold to an en-route minimum (MEA/MCA), whose figure the text
    does not give, takes one-minute legs."""
    tas = ctx.params.tas_kt
    match leg.until:
        case Dme(navaid=navaid, nm=nm) if navaid.ident == leg.fix.ident:
            return nm
        case Altitude(feet=feet) if feet > HOLD_HIGH_ALTITUDE_FT:
            return tas * HOLD_LEG_MINUTES_HIGH / 60
        case None | Altitude() | EnrouteAltitude():
            return tas * HOLD_LEG_MINUTES_LOW / 60
    _unsupported(leg)


def _racetrack(
    ctx: _Context, fix: Vec, inbound: float, side: int, length: float
) -> list[Vec]:
    """The closed holding pattern, starting and ending at the fix.

    Turns of radius R at each end put the outbound leg 2R to the turning
    side of the inbound leg, which is `length` long.
    """
    across = inbound + 90 * side
    radius = ctx.radius
    far_end = offset(fix, inbound + 180, length)
    fix_turn = ctx.arc(
        offset(fix, across, radius), radius, inbound - 90 * side, 180 * side
    )
    far_turn = ctx.arc(offset(far_end, across, radius), radius, across, 180 * side)
    return [*fix_turn, *far_turn, fix]


def _leg_name(pen: _Pen, direction: Turn | None, phrase: str) -> str:
    return f"{pen.name}: {turn_phrase(direction)}{phrase}"


def _side(turn: Turn) -> int:
    return 1 if turn is Turn.RIGHT else -1


def _turn(side: int) -> Turn:
    return Turn.RIGHT if side > 0 else Turn.LEFT


def _require_length(length: float, leg) -> None:
    if length < MIN_LEG_NM:
        raise Degenerate("leg too short", f"{length:.2f} NM: {leg!r}")


def _offset_label(pen: _Pen, text: str, anchor: Vec, turn_side: int) -> None:
    pen.ctx.label(
        text, offset(anchor, pen.label_side(turn_side), pen.ctx.params.label_offset_nm)
    )


def _arrowhead(ctx: _Context, name: str, style: Style, end: Vec, course: float) -> None:
    """A two-armed "V" pointing along `course`, its tip `ARROW_SETBACK_NM`
    short of `end` so arrows converging on one fix stay distinguishable."""
    back = course + 180
    tip = offset(end, back, ARROW_SETBACK_NM)
    arms = [
        offset(tip, back - ARROW_SPLAY_DEG, ARROW_ARM_NM),
        tip,
        offset(tip, back + ARROW_SPLAY_DEG, ARROW_ARM_NM),
    ]
    ctx.polyline(f"{name} arrow", style, arms)


def _signed_turn(course: float, target: float, direction: Turn | None) -> float:
    """Degrees to turn from `course` onto `target` (positive right), the
    shorter way unless `direction` forces a side."""
    delta = wrap180(target - course)
    if abs(delta) < ANGLE_EPSILON_DEG:
        return 0.0
    if direction is Turn.RIGHT and delta < 0:
        return delta + 360
    if direction is Turn.LEFT and delta > 0:
        return delta - 360
    return delta
