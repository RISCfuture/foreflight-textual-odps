"""Turn a resolved departure procedure into planview shapes.

Every construction works on a `LocalPlane` centred on the airport (x east,
y north, in NM) and converts vertices back to lat/lon only when emitting a
shape. Anything the constructions cannot draw with certainty raises
`Degenerate`; `draw` never returns a partial drawing.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from collections.abc import Sequence

from .geo import LocalPlane, distance_nm, heading_to_unit, magnetic_to_true
from .labels import (
    dme_label,
    format_altitude,
    heading_label,
    heading_phrase,
    heading_range_label,
    heading_range_lines,
    heading_range_phrases,
    hold_label,
    navaid_radial_label,
    radial_phrase,
    runway_tag,
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
    Compass8,
    CrossAt,
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
    StraightAhead,
    Thence,
    Turn,
    VcoaGroup,
)
from .resolved import ResolvedProcedure, RunwayStart
from .shapes import AirportDrawing, Label, Polyline, Style, Wedge

__all__ = [
    "Degenerate",
    "DisplayParams",
    "draw",
    "format_altitude",
    "lay_out_note",
    "lay_out_wedges",
    "trace",
    "turn_radius_nm",
]

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
INTERCEPT_ANGLE_DEG = 45.0
MAX_INTERCEPT_TURN_DEG = 270.0
CONVERGE_ANGLE_DEG = 20.0
ON_RADIAL_TOLERANCE_NM = 0.5
JOINED_RADIAL_MAX_DEG = 3.0
ON_TRACK_DEG = 1.0
ARRIVAL_TOLERANCE_NM = 0.01
HOLD_HIGH_ALTITUDE_FT = 14000
HOLD_LEG_MINUTES_LOW = 1.0
HOLD_LEG_MINUTES_HIGH = 1.5
SHARED_TAIL_TOLERANCE_NM = 0.5
ON_COURSE_STUB_NM = 1.0
ON_COURSE_DASHES = 4
ON_COURSE_DASH_NM = 0.15
ON_COURSE_DASH_SPACING_NM = (ON_COURSE_STUB_NM - ON_COURSE_DASH_NM) / (
    ON_COURSE_DASHES - 1
)
ON_COURSE_BEND_DEG = 45.0
VCOA_DASHES = 36
VCOA_LABEL_BEARINGS = (0.0, 180.0, 90.0, 270.0)
WEDGE_DASH_NM = 0.12
WEDGE_STRAIGHT_NM = 0.3
WEDGE_GAP_NM = 0.1
ARROW_ARM_NM = 0.25
ARROW_SETBACK_NM = 0.5
ARROW_SPLAY_DEG = 30.0
COMPASS_BEARINGS = {point: 45.0 * index for index, point in enumerate(Compass8)}
RUNWAY_HEADING_LABEL = "rwy hdg"
SHARED_WEDGE_NM = 1.5
LABEL_CHAR_NM = 0.16
LABEL_LINE_NM = 0.32
LABEL_GAP_NM = 0.1
LABEL_STEP_NM = 0.25
ZOOMED_OUT = 1.6
# What a wedge's labels pay, in NM of distance from its apex, for: each
# square NM of other labels they overlap (and would overlap zoomed out),
# each NM of route or radial line under them, centring inside another
# wedge, and each degree off the middle of a sector.
LABEL_OVERLAP_COST = 100.0
LABEL_MARGIN_COST = 20.0
LINE_CROSSING_COST = 4.0
RADIAL_CROSSING_COST = 1.0
OTHER_WEDGE_COST = 3.0
OFF_MIDDLE_COST = 0.005
# ForeFlight's airport symbol and name, which a note keeps clear of.
AIRPORT_MARK_HALF_NM = (0.6, 0.3)
NOTE_MAX_REACH_NM = 2.0
NOTE_BEARING_STEP_DEG = 15
UNSTATED_SENSE = 'radial without "inbound" or "outbound"'
UNSTATED_SENSE_TURN_DEG = 60.0
RADIAL_COLINEAR = "radial colinear"
INTERCEPT_BEHIND = "radial intercept behind"
NO_INTERCEPT_AHEAD = (RADIAL_COLINEAR, INTERCEPT_BEHIND)


@dataclasses.dataclass(frozen=True)
class DisplayParams:
    """Aircraft and drawing assumptions shared by every construction."""

    tas_kt: float = 150.0
    bank_deg: float = 25.0
    default_gradient_ft_nm: float = 200.0
    vcoa_radius_nm: float = 2.0
    heading_range_radius_nm: float = 1.0
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
    """Draw every runway group, the shared tail and VCOA groups of a procedure,
    heading-range wedges included.

    Raises `Degenerate` if any construction is uncertain.
    """
    return lay_out_wedges(trace(resolved, params), params)


def trace(
    resolved: ResolvedProcedure, params: DisplayParams = DEFAULT_PARAMS
) -> AirportDrawing:
    """`draw`, with heading-range wedges left pending on the drawing, so that
    parts of an airport drawn one at a time can be merged before
    `lay_out_wedges` draws them.

    A runway whose departure is NA or flies a charted DP is not drawn.
    Raises `Degenerate` if any construction is uncertain.
    """
    ctx = _Context(resolved, params)
    procedure = resolved.procedure
    pens = []
    for runway_group in procedure.runway_groups:
        if not runway_group.legs or runway_group.graphic:
            continue
        group_pens = _draw_runway_group(ctx, runway_group)
        if _continues_to_tail(runway_group):
            pens += group_pens
        else:
            _end_routes(group_pens)
    if procedure.shared_tail:
        _draw_shared_tail(ctx, pens, procedure.shared_tail)
    for vcoa in procedure.vcoa:
        _draw_vcoa(ctx, vcoa)
    return AirportDrawing(
        resolved.airport_lid,
        resolved.airport_name,
        tuple(ctx.shapes),
        position=resolved.airport_position,
        wedges=tuple(ctx.wedges),
    )


def lay_out_wedges(
    drawing: AirportDrawing, params: DisplayParams = DEFAULT_PARAMS
) -> AirportDrawing:
    """Draw the drawing's pending wedges: each sector's two limiting headings
    and the arc between them, `heading_range_radius_nm` out, labelled with
    the wedge's runways, each sector's headings and any altitude.

    Wedges that read the same from turn-start points within
    `SHARED_WEDGE_NM` of one another (parallel runways departing alike) are
    drawn once, from the midpoint of those points. A wedge's labels stand
    stacked together inside it, as near its apex as they fit clear of the
    airport's other labels and lines, else just beyond its arc.
    """
    if not drawing.wedges:
        return drawing
    plane = LocalPlane(drawing.position or drawing.wedges[0].apex)
    step = params.arc_step_deg
    fans = [_Fan.of(plane, params, wedges) for wedges in _shared_wedges(drawing.wedges)]
    lines = dict.fromkeys(line for fan in fans for line in fan.polylines(plane, step))
    shapes = [*drawing.shapes, *lines]
    for fan in fans:
        others = [other for other in fans if other is not fan]
        shapes += fan.labels(plane, _Clutter.of(plane, shapes, others), step)
    return dataclasses.replace(drawing, shapes=tuple(shapes), wedges=())


def lay_out_note(drawing: AirportDrawing, texts: list[str]) -> AirportDrawing:
    """`drawing` with `texts` stacked as labels near the airport, within
    `NOTE_MAX_REACH_NM` of it, where they are least cluttered by the
    airport's own lines and labels (wedge labels among them, so this runs
    after `lay_out_wedges`) and by ForeFlight's airport symbol and name,
    and otherwise as near the airport as they can stand, south first."""
    plane = LocalPlane(drawing.position)
    airport = (0.0, 0.0)
    clutter = _Clutter.of(
        plane, list(drawing.shapes), [], [_box(airport, AIRPORT_MARK_HALF_NM)]
    )
    half = _block_half_size(texts)
    spots = [
        offset(airport, bearing_deg, reach)
        for reach in _note_reaches()
        for bearing_deg in range(180, 540, NOTE_BEARING_STEP_DEG)
    ]
    centre = min(
        spots, key=lambda spot: clutter.cost(_box(spot, half)) + distance(airport, spot)
    )
    labels = (
        Label(text, plane.to_latlon(*at))
        for text, at in zip(texts, _block_lines(centre, len(texts)), strict=True)
    )
    return dataclasses.replace(drawing, shapes=(*drawing.shapes, *labels))


def _note_reaches() -> list[float]:
    """Distances out from the airport at which to try centring a note."""
    steps = math.floor(NOTE_MAX_REACH_NM / LABEL_STEP_NM)
    return [i * LABEL_STEP_NM for i in range(2, steps + 1)]


class _Context:
    """The procedure being drawn, its plane, and the shapes and wedges
    emitted so far."""

    def __init__(self, resolved: ResolvedProcedure, params: DisplayParams) -> None:
        self.resolved = resolved
        self.params = params
        self.plane = LocalPlane(resolved.airport_position)
        self.radius = turn_radius_nm(params.tas_kt, params.bank_deg)
        self.shapes: list[Polyline | Label] = []
        self.wedges: list[Wedge] = []
        self.group: tuple[str, ...] = ()

    def xy(self, ident: str) -> Vec:
        return self.plane.to_xy(self.resolved.points[ident].position)

    def declination(self, ident: str) -> float:
        return self.resolved.points[ident].declination_east

    def heading_true(self, magnetic: int) -> float:
        return magnetic_to_true(magnetic, self.resolved.airport_variation_east)

    def polyline(self, name: str, style: Style, points: list[Vec]) -> Polyline:
        latlons = tuple(self.plane.to_latlon(*p) for p in points)
        line = Polyline(name, style, latlons, self.group)
        self._emit(line)
        return line

    def discard(self, line: Polyline) -> None:
        """Remove `line`, as drawn for any runway group."""
        if (index := self._twin(line)) is not None:
            del self.shapes[index]

    def arc(self, centre: Vec, radius: float, start: float, sweep: float) -> list[Vec]:
        return arc(centre, radius, start, sweep, self.params.arc_step_deg)

    def label(self, text: str, p: Vec, *fallbacks: Vec) -> None:
        latlons = tuple(self.plane.to_latlon(*q) for q in fallbacks)
        self._emit(Label(text, self.plane.to_latlon(*p), latlons))

    def _emit(self, shape: Polyline | Label) -> None:
        """Keep one of identical shapes: routes that reach the same hold draw
        it identically, VCOA groups for several runways label the same circle
        at the same point, and groups that are alternatives for one runway
        ("All other courses: …") share its initial climb. A line drawn for
        two runway groups belongs to neither. A label moves to a fallback
        point rather than print over different text."""
        if isinstance(shape, Label):
            if (label := shape.placed(self.shapes)) is not None:
                self.shapes.append(label)
            return
        if (twin := self._twin(shape)) is not None:
            if self.shapes[twin] != shape:
                self.shapes[twin] = dataclasses.replace(shape, group=())
            return
        self.shapes.append(shape)

    def _twin(self, line: Polyline) -> int | None:
        """The index of the same line already drawn, for this runway group or
        another."""
        return next(
            (
                index
                for index, shape in enumerate(self.shapes)
                if isinstance(shape, Polyline)
                and dataclasses.replace(shape, group=line.group) == line
            ),
            None,
        )


@dataclasses.dataclass(frozen=True)
class _Climb:
    """Climb gradients in ft/NM: `gradient` up to `ceiling_ft`, `above` it
    over; `gradient` throughout when there is no ceiling."""

    gradient: float
    ceiling_ft: float | None = None
    above: float | None = None

    def nm_between(self, low: float, high: float) -> float:
        """Along-track distance the climb takes from `low` to `high` feet,
        negative when `high` is the lower."""
        if self.ceiling_ft is None or self.above is None:
            return (high - low) / self.gradient
        ceiling = self.ceiling_ft
        below_nm = (min(high, ceiling) - min(low, ceiling)) / self.gradient
        return below_nm + (max(high, ceiling) - max(low, ceiling)) / self.above


def _runway_climb(start: RunwayStart, default_gradient: float) -> _Climb:
    """The runway's published minimum gradient to the altitude it is published
    to and `default_gradient` above that, or `default_gradient` throughout
    when none is published."""
    published = start.min_climb
    if published is None:
        return _Climb(default_gradient)
    return _Climb(published.ft_per_nm, published.to_ft, default_gradient)


@dataclasses.dataclass(frozen=True)
class _Arrowhead:
    """An arrowhead drawn short of `end`, kept so it can be redrawn at `end`."""

    shape: Polyline
    name: str
    style: Style
    end: Vec
    course: float


@dataclasses.dataclass
class _Pen:
    """Where the aircraft is: position, true course, and climb bookkeeping.

    `along_nm` is the along-track distance flown on `climb` since it passed
    `base_alt_ft`, negative while it has yet to reach it.
    `straight_out` holds while every leg flown has kept to the runway's own
    course, from its DER. `open_heading` marks a route left on a heading
    printed with no terminator, which it holds until it intercepts the next
    leg's radial. `runways` are those the route departs from.
    """

    ctx: _Context
    name: str
    at: Vec
    course: float
    along_nm: float
    base_alt_ft: float
    climb: _Climb
    runway_course: float | None = None
    straight_out: bool = False
    turn_pending: bool = False
    pending_direction: Turn | None = None
    open_heading: bool = False
    last_arrow: _Arrowhead | None = None
    runways: tuple[str, ...] = ()
    group: tuple[str, ...] = ()

    def altitude_leg_nm(self, feet: int) -> float:
        """Along-track distance at which the climb reaches `feet`."""
        length = max(MIN_ALTITUDE_LEG_NM, self.climb.nm_between(self.base_alt_ft, feet))
        if length > MAX_ALTITUDE_LEG_NM:
            raise Degenerate("altitude leg capped", f"{feet} ft needs {length:.1f} NM")
        return length

    def level_at(self, feet: int) -> None:
        """Climb no higher than `feet` until a later leg climbs on: level off
        there if the climb has reached it, else keep climbing toward it."""
        still_to_climb_nm = (
            self.climb.nm_between(self.base_alt_ft, feet) - self.along_nm
        )
        self.base_alt_ft, self.along_nm = feet, min(0.0, -still_to_climb_nm)

    def turn_onto(self, course: float, direction: Turn | None) -> tuple[list[Vec], int]:
        """Fly a fly-by arc onto `course`; return its vertices (from the
        current position) and the turn side (+1 right, -1 left, 0 none)."""
        delta = _signed_turn(self.course, course, direction)
        points = _turn_arc(
            self.at, self.course, delta, self.ctx.radius, self.ctx.params.arc_step_deg
        )
        self.along_nm += self.ctx.radius * math.radians(abs(delta))
        self.at, self.course = points[-1], course
        return points, sign(delta)

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
    """Draw the group's legs from each of its runways; return where each ended.

    A minimum climb on the headings a heading range leaves out names no
    route, so its group draws nothing.
    """
    if runway_group.climb_gradient_only:
        return []
    ctx.group = runway_group.runways
    pens = [
        _start_runway(ctx, ctx.resolved.runways[name]) for name in runway_group.runways
    ]
    for pen in pens:
        pen.group = runway_group.runways
        _draw_legs(pen, runway_group.legs)
    return pens


def _continues_to_tail(runway_group: RunwayGroup) -> bool:
    return bool(runway_group.legs) and isinstance(runway_group.legs[-1], Thence)


def _draw_shared_tail(ctx: _Context, pens: list[_Pen], legs: tuple[Leg, ...]) -> None:
    """Draw the legs every runway group continues with, once, from where
    the groups converge.

    Groups that end in a turn with no route of their own ("climbing right
    turn, thence ...") each fly the tail's first leg in that turn, and
    converge where it ends; so do groups that end on a heading with no
    terminator before a tail that begins on a radial ("heading 170°, thence
    ... on RZS R-185"). Either every group does so or none.
    """
    if not pens:
        raise Degenerate("shared tail without a runway route", repr(legs))
    pending = [_flies_first_tail_leg(pen, legs[0]) for pen in pens]
    if any(pending):
        if not all(pending):
            raise Degenerate("shared tail start mismatch", "turns into the tail")
        for pen in pens:
            _draw_legs(pen, legs[:1], pen.pending_direction)
        legs = legs[1:]
        if not legs:
            _end_routes(pens)
            return
    first = _shared_radial_start(ctx, pens, legs[0]) or _converged(pens)
    _require_one_sense(ctx, pens, legs[0])
    first.straight_out = False
    first.runways = tuple(
        runway
        for group in ctx.resolved.procedure.runway_groups
        if group.legs and not group.graphic
        for runway in group.runways
    )
    first.name = f"RWY {'/'.join(first.runways)}"
    first.group = ()
    _draw_legs(first, legs)
    _end_routes([first])


def _flies_first_tail_leg(pen: _Pen, first: Leg) -> bool:
    return pen.turn_pending or (pen.open_heading and isinstance(first, Radial))


def _converged(pens: list[_Pen]) -> _Pen:
    """The first route, provided every other ends within tolerance of it."""
    first = pens[0]
    for pen in pens[1:]:
        gap = distance(first.at, pen.at)
        if gap > SHARED_TAIL_TOLERANCE_NM:
            raise Degenerate(
                "shared tail start mismatch", f"{pen.name} {gap:.2f} NM away"
            )
    return first


def _require_one_sense(ctx: _Context, pens: list[_Pen], leg: Leg) -> None:
    """Refuse a tail that sets out along a radial every route would fly a
    different way, each settling its unprinted sense from where and on what
    course it arrives."""
    direction = None
    if isinstance(leg, ClimbingTurn):
        leg, direction = leg.then, leg.direction
    if not isinstance(leg, Radial):
        return
    senses = {
        _with_sense(ctx, leg, pen.at, pen.course, direction).outbound for pen in pens
    }
    if len(senses) > 1:
        raise Degenerate(UNSTATED_SENSE, f"routes disagree: {leg!r}")


def _shared_radial_start(ctx: _Context, pens: list[_Pen], leg: Leg) -> _Pen | None:
    """When the tail goes on along a radial every route is already tracking,
    the route that joined it farthest back, since the others fly part of it.

    ``None`` unless every route is on that radial, flying it the same way.
    """
    if not isinstance(leg, Radial) or leg.outbound is None:
        return None
    navaid = ctx.xy(leg.navaid.ident)
    radial_course = _radial_true(ctx, leg)
    track = _tracking_course(leg, radial_course)
    placed = []
    for pen in pens:
        along, across = along_across(sub(pen.at, navaid), radial_course)
        if (
            along < 0
            or abs(across) > ON_RADIAL_TOLERANCE_NM
            or abs(wrap180(pen.course - track)) > ON_TRACK_DEG
        ):
            return None
        placed.append((along, pen))
    pick = min if leg.outbound else max
    return pick(placed, key=lambda item: item[0])[1]


def _draw_vcoa(ctx: _Context, vcoa: VcoaGroup) -> None:
    """A dashed circle to climb in over the airport (or a fix), then its legs.

    The label stands at the circle's north point or, where a different
    label (another altitude's) already stands, at its south, east or west
    point.
    """
    centre = ctx.xy(vcoa.cross.ident) if vcoa.cross else (0.0, 0.0)
    radius = ctx.params.vcoa_radius_nm
    name = f"{_runways_name(vcoa.runways)}: VCOA"
    ctx.group = ()
    for start in _dash_starts(VCOA_DASHES):
        dash = ctx.arc(centre, radius, start, 180 / VCOA_DASHES)
        ctx.polyline(name, Style.VCOA, dash)
    points = (offset(centre, angle, radius) for angle in VCOA_LABEL_BEARINGS)
    ctx.label(vcoa_label(vcoa.at_or_above, ctx.params.label_style, vcoa.bound), *points)
    if vcoa.speed is not None:
        ctx.label(speed_label(vcoa.speed), offset(centre, 180.0, radius))
    if _departs_toward_something(vcoa.then):
        outward = _departure_bearing(ctx, centre, vcoa.then[0])
        pen = _Pen(
            ctx,
            name,
            offset(centre, outward, radius),
            outward,
            0.0,
            vcoa.at_or_above,
            _Climb(ctx.params.default_gradient_ft_nm),
            runways=vcoa.runways,
        )
        _draw_legs(pen, vcoa.then)
        _end_routes([pen])


def _runways_name(runways: tuple[str, ...]) -> str:
    """``RWY 15/33``, or ``ALL RWYS`` for a VCOA that names no runways."""
    return f"RWY {'/'.join(runways)}" if runways else "ALL RWYS"


def _departs_toward_something(legs: tuple[Leg, ...]) -> bool:
    """Whether the legs after a VCOA fly anywhere beyond "proceed on course"
    in no one stated direction."""
    return any(
        not isinstance(leg, ProceedOnCourse) or len(leg.toward) == 1 for leg in legs
    )


def _dash_starts(count: int) -> list[float]:
    return [360 * i / count for i in range(count)]


def _departure_bearing(ctx: _Context, centre: Vec, leg: Leg) -> float:
    """Bearing from the VCOA circle's centre toward the first leg's target,
    or the compass direction it proceeds in."""
    match leg:
        case ClimbingTurn(then=then):
            return _departure_bearing(ctx, centre, then)
        case ClimbHeading(heading=heading):
            return ctx.heading_true(heading)
        case ProceedOnCourse(toward=(point,)):
            return COMPASS_BEARINGS[point]
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
    climb = _runway_climb(start, ctx.params.default_gradient_ft_nm)
    der = ctx.plane.to_xy(start.der)
    pen = _Pen(
        ctx,
        f"RWY {start.runway}",
        der,
        start.course_true,
        0.0,
        start.der_elevation_ft + DER_CROSSING_HEIGHT_FT,
        climb,
        runway_course=start.course_true,
        straight_out=True,
        runways=(start.runway,),
    )
    turn_start = offset(der, start.course_true, _turn_start_nm(pen))
    ctx.polyline(f"{pen.name}: initial climb", Style.ROUTE, [der, turn_start])
    pen.straight_to(turn_start)
    return pen


def _turn_start_nm(pen: _Pen) -> float:
    turn_start_ft = pen.base_alt_ft + TURN_START_HEIGHT_FT
    return max(MIN_TURN_START_NM, pen.climb.nm_between(pen.base_alt_ft, turn_start_ft))


def _draw_legs(pen: _Pen, legs: tuple[Leg, ...], direction: Turn | None = None) -> None:
    """Draw `legs` in order; `direction` is a turn owed to the first of them."""
    pen.ctx.group = pen.group
    for index, leg in enumerate(legs):
        start, course = pen.at, pen.course
        _draw_leg(pen, leg, direction if index == 0 else None)
        pen.straight_out &= _keeps_to_runway_course(leg)
        if not isinstance(leg, Thence):
            pen.open_heading = _leaves_open_heading(leg)
        if speed := _speed_restriction(leg):
            _label_speed(pen.ctx, speed, start, course)
        if _ends_in_heading_range(leg):
            _require_only_on_course(legs[index + 1 :])
            return


def _keeps_to_runway_course(leg: Leg) -> bool:
    return isinstance(leg, RunwayHeading | StraightAhead)


def _leaves_open_heading(leg: Leg) -> bool:
    turned = leg.then if isinstance(leg, ClimbingTurn) else leg
    return isinstance(turned, ClimbHeading | RunwayHeading) and turned.until is None


def _ends_in_heading_range(leg: Leg) -> bool:
    turned = leg.then if isinstance(leg, ClimbingTurn) else leg
    return isinstance(turned, HeadingRange)


def _require_only_on_course(legs: tuple[Leg, ...]) -> None:
    """After a heading range the aircraft may be on any heading in it, so only
    "before proceeding on course" can follow; it needs no stub of its own."""
    for leg in legs:
        if not isinstance(leg, ProceedOnCourse):
            raise Degenerate("leg after a heading range", repr(leg))


def _speed_restriction(leg: Leg) -> SpeedRestriction | None:
    if isinstance(leg, ClimbingTurn):
        return getattr(leg.then, "speed", None) if leg.then is not None else None
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
        case ClimbingTurn(then=None):
            pen.turn_pending, pen.pending_direction = True, leg.direction
        case ClimbingTurn():
            # A printed turn leaves the heading the route was holding.
            pen.open_heading = False
            _draw_leg(pen, leg.then, leg.direction)
        case ClimbHeading():
            _climb_heading(pen, leg, direction)
        case RunwayHeading():
            _runway_heading(pen, leg, direction)
        case StraightAhead():
            _straight_ahead(pen, leg, direction)
        case HeadingRange():
            _heading_range(pen, leg, direction)
        case Direct():
            _direct(pen, leg, direction)
        case Radial() if pen.open_heading and direction is None:
            _hold_heading_to_radial(pen, leg)
        case Radial(intercept=True):
            _intercept_radial(pen, leg, direction)
        case Radial():
            _radial(pen, leg, direction)
        case HeadingAndRadial():
            _heading_and_radial(pen, leg, direction)
        case ClimbInHold():
            _climb_in_hold(pen, leg)
        case ProceedOnCourse():
            _proceed_on_course(pen, leg)
        case CrossAt():
            _cross_at(pen, leg)
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


def _straight_ahead(pen: _Pen, leg: StraightAhead, direction: Turn | None) -> None:
    """Climb on along the runway's course, the course a climb naming none
    ("climb to 1200 before turning left") holds straight out from the runway;
    after any other leg, in a shared tail, or owing a turn, straight ahead has
    no certain course."""
    if direction is not None or not pen.straight_out:
        _unsupported(leg)
    name = _leg_name(pen, None, "straight ahead")
    _climb_course(pen, leg, None, pen.runway_course, name, None)


def _climb_course(
    pen: _Pen,
    leg: ClimbHeading | RunwayHeading | StraightAhead,
    direction: Turn | None,
    course: float,
    name: str,
    heading_text: str | None,
) -> None:
    """Turn onto the true `course`, then (with an altitude) climb straight on it,
    labelled with `heading_text` when the text names a heading.

    An altitude the climb already reached before the turn ends the leg
    where the turn ends.
    """
    ctx = pen.ctx
    points, side = pen.turn_onto(course, direction)
    match leg.until:
        case None:
            if len(points) > 1:
                ctx.polyline(name, Style.ROUTE, points)
                _arrowhead(pen, name, Style.ROUTE, pen.at, pen.course)
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
    _arrowhead(pen, name, Style.ROUTE, pen.at, pen.course)
    if heading_text is not None:
        _offset_label(pen, heading_text, midpoint(turn_end, pen.at), side)
    ctx.label(format_altitude(leg.until, ctx.params.label_style), pen.at)


def _heading_range(pen: _Pen, leg: HeadingRange, direction: Turn | None) -> None:
    """The sectors as a wedge fanning out from where the turn may begin, for
    `lay_out_wedges` to draw once the airport's other shapes are known. The
    route goes on into the wedge, so the arrowhead before it keeps its
    setback."""
    ctx = pen.ctx
    pen.last_arrow = None
    match leg.until:
        case None:
            pass
        case Altitude():
            _require_climb(pen, leg.until)
        case _:
            _unsupported(leg)
    sectors = tuple(
        (ctx.heading_true(sector.start), _sector_sweep(sector))
        for sector in leg.sectors
    )
    style = ctx.params.label_style
    wedge = Wedge(
        pen.runways,
        ctx.plane.to_latlon(*pen.at),
        sectors,
        heading_range_label(leg, direction, style),
        heading_range_phrases(leg, direction, style),
        pen.group,
        pen.course,
        direction,
    )
    ctx.wedges.append(wedge)


def _sector_sweep(sector: HeadingSector) -> float:
    """Signed degrees (positive clockwise) from the sector's start to its end;
    a sector whose ends coincide is refused rather than read as a full circle."""
    if sector.start % 360 == sector.end % 360:
        raise Degenerate("empty heading sector", repr(sector))
    if sector.clockwise:
        return (sector.end - sector.start) % 360
    return -((sector.start - sector.end) % 360)


type _Box = tuple[float, float, float, float]
type _Segment = tuple[Vec, Vec]


def _shared_wedges(wedges: tuple[Wedge, ...]) -> list[list[Wedge]]:
    """The wedges in drawing order, each grouped with the earlier ones it
    can be drawn as."""
    groups: list[list[Wedge]] = []
    for wedge in wedges:
        group = next((group for group in groups if _joins(wedge, group)), None)
        if group is None:
            groups.append([wedge])
        else:
            group.append(wedge)
    return groups


def _joins(wedge: Wedge, group: list[Wedge]) -> bool:
    """Whether `wedge` reads the same as every wedge in `group`, from a
    turn-start point within `SHARED_WEDGE_NM` of each of theirs, with all
    their runways still fitting one label."""
    runways = _runways_of([*group, wedge])
    return runway_tag(runways) is not None and all(
        (other.sectors, other.printed) == (wedge.sectors, wedge.printed)
        and distance_nm(other.apex, wedge.apex) <= SHARED_WEDGE_NM
        for other in group
    )


def _runways_of(wedges: Sequence[Wedge]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(runway for wedge in wedges for runway in wedge.runways))


def _centroid(points: list[Vec]) -> Vec:
    return (
        sum(x for x, _ in points) / len(points),
        sum(y for _, y in points) / len(points),
    )


@dataclasses.dataclass(frozen=True)
class _Fan:
    """Wedges that read alike, drawn as one on the plane: their `sectors`
    fanning out `radius` NM from `apex`, the midpoint of their own apexes."""

    wedges: tuple[Wedge, ...]
    apex: Vec
    radius: float
    turn_radius: float

    @classmethod
    def of(cls, plane: LocalPlane, params: DisplayParams, wedges: list[Wedge]) -> _Fan:
        """`wedges` drawn as one from the midpoint of their apexes, reaching
        `heading_range_radius_nm`, or farther when turning onto a limiting
        heading takes the aircraft wider than that."""
        apex = _centroid([plane.to_xy(wedge.apex) for wedge in wedges])
        turn_radius = turn_radius_nm(params.tas_kt, params.bank_deg)
        fan = cls(tuple(wedges), apex, params.heading_range_radius_nm, turn_radius)
        widest = max(
            distance(apex, fan.turn_onto(heading, params.arc_step_deg)[-1])
            for heading in fan.limiting_headings()
        )
        return dataclasses.replace(
            fan, radius=max(fan.radius, widest + WEDGE_STRAIGHT_NM)
        )

    @property
    def sectors(self) -> tuple[tuple[float, float], ...]:
        return self.wedges[0].sectors

    @property
    def runways(self) -> tuple[str, ...]:
        return _runways_of(self.wedges)

    def texts(self) -> list[str]:
        """The labels, top to bottom: the runways, then the range as printed."""
        return heading_range_lines(self.runways, self.wedges[0].phrases)

    def polylines(self, plane: LocalPlane, step: float) -> list[Polyline]:
        """The route splitting at the apex into a route-weight line along each
        limiting heading, each ending in an arrowhead, with a light dashed arc
        between them; named for the runways and the range as printed."""
        name = f"{_runways_name(self.runways)}: {self.wedges[0].printed}"

        def line(style: Style, points: Sequence[Vec], suffix: str = "") -> Polyline:
            latlons = tuple(plane.to_latlon(*p) for p in points)
            return Polyline(f"{name}{suffix}", style, latlons, self.wedges[0].group)

        tips = {}
        limits = []
        for heading in self.limiting_headings():
            points = self.edge(heading, step)
            tips[heading] = points[-1]
            limits += [
                line(Style.ROUTE, points),
                line(Style.ROUTE, _arrow_arms(points[-1], heading), " arrow"),
            ]
        dashes = [
            line(Style.RADIAL, dash)
            for start, sweep in self.sectors
            for dash in _dashed_arc(
                self.apex, self.radius, *self.between(tips, start, sweep), step
            )
        ]
        return [*limits, *dashes]

    def turn_onto(self, heading: float, step: float) -> list[Vec]:
        """The aircraft's turn from the apex onto `heading` (the published
        way, else the shorter), flown like every other turn in the drawing."""
        delta = _signed_turn(self.course, heading, self.turn)
        return _turn_arc(self.apex, self.course, delta, self.turn_radius, step)

    def edge(self, heading: float, step: float) -> list[Vec]:
        """The turn onto `heading`, then straight on until `radius` out."""
        points = self.turn_onto(heading, step)
        return [*points, _out_to(self.apex, points[-1], heading, self.radius)]

    def between(
        self, tips: dict[float, Vec], start: float, sweep: float
    ) -> tuple[float, float]:
        """The sector's arc as a start bearing and sweep between its tips."""
        first = bearing(self.apex, tips[round(start % 360, 6)])
        last = bearing(self.apex, tips[round((start + sweep) % 360, 6)])
        return first, sweep + wrap180(last - (start + sweep)) - wrap180(first - start)

    @property
    def course(self) -> float:
        """The course flown into the apex (averaged over merged runways)."""
        x = sum(math.sin(math.radians(wedge.course)) for wedge in self.wedges)
        y = sum(math.cos(math.radians(wedge.course)) for wedge in self.wedges)
        return math.degrees(math.atan2(x, y)) % 360

    @property
    def turn(self) -> Turn | None:
        return Turn(self.wedges[0].turn) if self.wedges[0].turn else None

    def limiting_headings(self) -> list[float]:
        """Each distinct limiting heading once: "or" sectors share an end."""
        headings = (
            heading % 360
            for start, sweep in self.sectors
            for heading in (start, start + sweep)
        )
        return list(dict.fromkeys(round(heading, 6) for heading in headings))

    def labels(self, plane: LocalPlane, clutter: _Clutter, step: float) -> list[Label]:
        """`texts` stacked at `_label_spot`."""
        texts = self.texts()
        centre = _label_spot(self, _block_half_size(texts), clutter, step)
        return [
            Label(text, plane.to_latlon(*at))
            for text, at in zip(texts, _block_lines(centre, len(texts)), strict=True)
        ]

    def bearings(self, step: float) -> list[tuple[float, float]]:
        """Bearings across each sector at most `step` apart, with their signed
        angles from the sector's middle."""
        return [
            (start + sweep * i / steps, sweep * (i / steps - 0.5))
            for start, sweep in self.sectors
            for steps in [max(1, math.ceil(abs(sweep) / step))]
            for i in range(steps + 1)
        ]

    def covers(self, p: Vec) -> bool:
        """Whether the point `p` lies inside the wedge."""
        heading = bearing(self.apex, p)
        return distance(self.apex, p) <= self.radius and any(
            _within_sweep(heading, start, sweep) for start, sweep in self.sectors
        )


def _out_to(apex: Vec, start: Vec, heading: float, reach: float) -> Vec:
    """The point on `heading` from `start` that lies `reach` from `apex`."""
    ux, uy = heading_to_unit(heading)
    dx, dy = start[0] - apex[0], start[1] - apex[1]
    along = dx * ux + dy * uy
    return offset(
        start,
        heading,
        -along + math.sqrt(max(0.0, along**2 - dx**2 - dy**2 + reach**2)),
    )


def _arrow_arms(tip: Vec, course: float) -> list[Vec]:
    """A two-armed "V" with its point at `tip`, pointing along `course`."""
    back = course + 180
    return [
        offset(tip, back - ARROW_SPLAY_DEG, ARROW_ARM_NM),
        tip,
        offset(tip, back + ARROW_SPLAY_DEG, ARROW_ARM_NM),
    ]


def _dashed_arc(
    centre: Vec, radius: float, start: float, sweep: float, step: float
) -> list[list[Vec]]:
    """The arc from `start` through `sweep` degrees as dashes about
    `WEDGE_DASH_NM` long, `WEDGE_GAP_NM` apart, starting and ending on a dash."""
    length = radius * math.radians(abs(sweep))
    count = max(2, round((length + WEDGE_GAP_NM) / (WEDGE_DASH_NM + WEDGE_GAP_NM)))
    period = sweep / (count - WEDGE_GAP_NM / (WEDGE_DASH_NM + WEDGE_GAP_NM))
    dash = period * WEDGE_DASH_NM / (WEDGE_DASH_NM + WEDGE_GAP_NM)
    return [arc(centre, radius, start + i * period, dash, step) for i in range(count)]


def _within_sweep(heading: float, start: float, sweep: float) -> bool:
    """Whether `heading` lies on the sector from `start` through `sweep`."""
    turned = (heading - start) % 360 if sweep > 0 else (start - heading) % 360
    return turned <= abs(sweep)


@dataclasses.dataclass(frozen=True)
class _Clutter:
    """What a wedge's labels keep clear of: the airport's other labels and
    the other wedges' apexes, as boxes; its lines, as segments, radials
    (wedges among them) apart from the rest; and its other wedges."""

    boxes: list[_Box]
    segments: list[_Segment]
    radial_segments: list[_Segment]
    fans: list[_Fan]

    @classmethod
    def of(
        cls,
        plane: LocalPlane,
        shapes: list[Polyline | Label],
        fans: list[_Fan],
        marks: Sequence[_Box] = (),
    ) -> _Clutter:
        """The clutter of `shapes` and `fans`, and any other `marks` on the
        map as boxes."""
        labels = [
            _box(plane.to_xy(shape.at), _block_half_size([shape.text]))
            for shape in shapes
            if isinstance(shape, Label)
        ]
        apexes = [_box(fan.apex, (LABEL_LINE_NM, LABEL_LINE_NM)) for fan in fans]
        lines = [shape for shape in shapes if isinstance(shape, Polyline)]
        radials = [line for line in lines if line.style is Style.RADIAL]
        others = [line for line in lines if line.style is not Style.RADIAL]
        return cls(
            [*labels, *apexes, *marks],
            _plane_segments(plane, others),
            _plane_segments(plane, radials),
            fans,
        )

    def cost(self, box: _Box) -> float:
        """How badly `box` is cluttered: by other labels it overlaps, or
        would zoomed out, then by lines crossing it, then by standing inside
        another wedge, where it could be read as that wedge's."""
        return (
            LABEL_OVERLAP_COST * sum(_overlap(box, other) for other in self.boxes)
            + LABEL_MARGIN_COST
            * sum(_overlap(_zoomed_out(box), _zoomed_out(o)) for o in self.boxes)
            + LINE_CROSSING_COST * _crossing(self.segments, box)
            + RADIAL_CROSSING_COST * _crossing(self.radial_segments, box)
            + OTHER_WEDGE_COST * any(fan.covers(_centre(box)) for fan in self.fans)
        )


def _label_spot(fan: _Fan, half: Vec, clutter: _Clutter, step: float) -> Vec:
    """Where a block of labels of half-size `half` goes: centred inside the
    wedge or else just beyond its arc, wherever it is least cluttered,
    nearest the apex and the middle of a sector."""
    candidates = [
        (spot, from_middle)
        for heading, from_middle in fan.bearings(step)
        for spot in (
            *(offset(fan.apex, heading, reach) for reach in _label_reaches(fan.radius)),
            _clear_of(fan.apex, fan.radius, heading, half),
        )
    ]

    def cost(candidate: tuple[Vec, float]) -> float:
        spot, from_middle = candidate
        return (
            clutter.cost(_box(spot, half))
            + distance(fan.apex, spot)
            + OFF_MIDDLE_COST * abs(from_middle)
        )

    return min(candidates, key=cost)[0]


def _label_reaches(radius: float) -> list[float]:
    """Distances out from the apex, up to `radius`, at which to try centring
    a label inside the wedge."""
    return [i * LABEL_STEP_NM for i in range(1, math.floor(radius / LABEL_STEP_NM) + 1)]


def _clear_of(p: Vec, reach: float, heading: float, half: Vec) -> Vec:
    """The centre of a box of half-size `half` whose near edge is
    `LABEL_GAP_NM` beyond `reach` NM from `p` along `heading`."""
    ux, uy = heading_to_unit(heading)
    to_edge = min(
        half[0] / abs(ux) if ux else math.inf, half[1] / abs(uy) if uy else math.inf
    )
    return offset(p, heading, reach + LABEL_GAP_NM + to_edge)


def _block_half_size(texts: list[str]) -> Vec:
    """Half the width and height, in NM, of labels stacked `LABEL_LINE_NM`
    apart, at a nominal zoom of some 45 points to the NM (an iPad showing
    about 20 NM across); tall enough for text drawn centred on its point or
    standing on it."""
    width = max(map(len, texts)) * LABEL_CHAR_NM
    height = (len(texts) + 1) * LABEL_LINE_NM
    return width / 2, height / 2


def _block_lines(centre: Vec, count: int) -> list[Vec]:
    """Where each of `count` stacked labels centred on `centre` stands, top
    first."""
    x, y = centre
    return [(x, y + ((count - 1) / 2 - i) * LABEL_LINE_NM) for i in range(count)]


def _zoomed_out(box: _Box) -> _Box:
    """`box` grown about its centre as its label grows against the map when
    zoomed out `ZOOMED_OUT` times."""
    left, bottom, right, top = box
    half = ZOOMED_OUT * (right - left) / 2, ZOOMED_OUT * (top - bottom) / 2
    return _box(_centre(box), half)


def _box(centre: Vec, half: Vec) -> _Box:
    x, y = centre
    return x - half[0], y - half[1], x + half[0], y + half[1]


def _centre(box: _Box) -> Vec:
    left, bottom, right, top = box
    return (left + right) / 2, (bottom + top) / 2


def _corners(box: _Box) -> list[Vec]:
    left, bottom, right, top = box
    return [(left, bottom), (left, top), (right, bottom), (right, top)]


def _plane_segments(plane: LocalPlane, lines: list[Polyline]) -> list[_Segment]:
    return [
        segment
        for line in lines
        for segment in itertools.pairwise(plane.to_xy(p) for p in line.points)
    ]


def _overlap(box: _Box, other: _Box) -> float:
    """The area two boxes share, in square NM."""
    width = min(box[2], other[2]) - max(box[0], other[0])
    height = min(box[3], other[3]) - max(box[1], other[1])
    return max(0.0, width) * max(0.0, height)


def _crossing(segments: list[_Segment], box: _Box) -> float:
    """The length of `segments` inside `box`."""
    return sum(_clipped_length(segment, box) for segment in segments)


def _clipped_length(segment: _Segment, box: _Box) -> float:
    """The length of `segment` inside `box` (Liang–Barsky clipping)."""
    (x, y), (x2, y2) = segment
    dx, dy = x2 - x, y2 - y
    enter, leave = 0.0, 1.0
    for toward, room in (
        (-dx, x - box[0]),
        (dx, box[2] - x),
        (-dy, y - box[1]),
        (dy, box[3] - y),
    ):
        if toward == 0:
            if room < 0:
                return 0.0
        elif toward < 0:
            enter = max(enter, room / toward)
        else:
            leave = min(leave, room / toward)
    return max(0.0, leave - enter) * math.hypot(dx, dy)


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
    _arrowhead(pen, name, Style.ROUTE, target, pen.course)


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
    """Turn onto the radial's course from a position already on it, then track it.

    A route that starts over the airport (a VCOA's) sets out on a schematic
    course, so only a runway's route may take the radial's sense from its own.
    """
    ctx = pen.ctx
    course = pen.course if pen.runway_course is not None else None
    leg = _with_sense(ctx, leg, pen.at, course, direction)
    radial_course = _radial_true(ctx, leg)
    name = _leg_name(pen, direction, radial_phrase(leg))
    points, side = pen.turn_onto(_tracking_course(leg, radial_course), direction)
    _require_on_radial(pen.at, ctx.xy(leg.navaid.ident), radial_course, leg)
    _track_radial(pen, leg, name, points, side)


def _heading_and_radial(
    pen: _Pen, leg: HeadingAndRadial, direction: Turn | None
) -> None:
    """Fly a heading to intercept a radial, rounding the corner, then track it."""
    heading = pen.ctx.heading_true(leg.heading)
    phrase = f"heading {leg.heading:03d} to intercept "
    _intercept_on(pen, leg, heading, direction, phrase, heading_label(leg.heading))


def _hold_heading_to_radial(pen: _Pen, leg: Radial) -> None:
    """Hold the heading the route is left on, labelled where it was turned
    onto, until intercepting the radial, then track it."""
    _intercept_on(pen, leg, pen.course, None, "to intercept ", None)


def _intercept_radial(pen: _Pen, leg: Radial, direction: Turn | None) -> None:
    """Turn to intercept a radial on no printed heading.

    A turn in the published direction that would roll out on the radial's
    track within tolerance of the radial rolls out there and converges on
    it, unless the published turn reaches the converging heading only by
    orbiting past the track. Otherwise the turn is drawn, schematically, onto the heading
    `INTERCEPT_ANGLE_DEG` off the track toward the radial, then flown as a
    heading intercepting it. Either such heading leaves the turn equally far
    abeam the radial, so the side of it that lies on picks the heading. That
    heading is not labelled: the text gives none. An intercept with no
    published turn direction is refused: the text may mean the heading
    already flown, or a turn either way.
    """
    if direction is None:
        raise Degenerate("intercept without a turn", repr(leg))
    ctx = pen.ctx
    leg = _with_sense(ctx, leg, pen.at, None)
    track = _tracking_course(leg, _radial_true(ctx, leg))
    side = _side(direction)
    beside = _abeam_after_turn(pen, leg, track, side, 0.0)
    if abs(beside) <= ON_RADIAL_TOLERANCE_NM:
        converging = (track - CONVERGE_ANGLE_DEG * sign(beside)) % 360
        _require_heading_beside_track(pen, converging, track, direction, leg)
        _roll_out_beside_radial(pen, leg, track, direction)
        return
    abeam = _abeam_after_turn(pen, leg, track, side, INTERCEPT_ANGLE_DEG)
    heading = (track - INTERCEPT_ANGLE_DEG * sign(abeam)) % 360
    _require_heading_beside_track(pen, heading, track, direction, leg)
    _intercept_on(pen, leg, heading, direction, "to intercept ", None)


def _roll_out_beside_radial(
    pen: _Pen, leg: Radial, track: float, direction: Turn
) -> None:
    """Turn onto the radial's track just beside it, converge on the radial
    at `CONVERGE_ANGLE_DEG`, then track it."""
    ctx = pen.ctx
    navaid = ctx.xy(leg.navaid.ident)
    radial_course = _radial_true(ctx, leg)
    name = _leg_name(pen, direction, "to intercept " + radial_phrase(leg))
    points, side = pen.turn_onto(track, direction)
    along, across = along_across(sub(pen.at, navaid), radial_course)
    converge_nm = abs(across) / math.tan(math.radians(CONVERGE_ANGLE_DEG))
    joined = along + (converge_nm if leg.outbound else -converge_nm)
    if along < 0 or joined < 0:
        raise Degenerate("off radial", f"{across:.2f} NM abeam: {leg!r}")
    pen.straight_to(offset(navaid, radial_course, joined))
    pen.course = track
    _track_radial(pen, leg, name, [*points, pen.at], side)


def _require_heading_beside_track(
    pen: _Pen, heading: float, track: float, direction: Turn, leg: Radial
) -> None:
    """Refuse a schematic intercept heading that the published turn reaches
    more than `INTERCEPT_ANGLE_DEG` before or after the radial's track, such
    as one just behind a course already converging on the radial, or only
    by turning more than `MAX_INTERCEPT_TURN_DEG`, when a short turn the
    other way reaches it: only an orbit the text does not describe reaches
    either."""
    to_heading = _signed_turn(pen.course, heading, direction)
    to_track = _signed_turn(pen.course, track, direction)
    if (
        abs(to_heading - to_track) > INTERCEPT_ANGLE_DEG + ANGLE_EPSILON_DEG
        or abs(to_heading) > MAX_INTERCEPT_TURN_DEG
    ):
        raise Degenerate(
            "intercept heading behind the turn",
            f"{to_heading:.0f} deg to it, {to_track:.0f} deg to the track: {leg!r}",
        )


def _abeam_after_turn(
    pen: _Pen, leg: Radial, track: float, side: int, off_track: float
) -> float:
    """How far right of the radial (flown along `track`) a turn to `side`
    leaves the aircraft once it heads `off_track` degrees either way of the
    track: the turn's centre abeam, less the radius projected across it."""
    radius = pen.ctx.radius
    centre = offset(pen.at, pen.course + 90 * side, radius)
    centre_abeam = along_across(sub(centre, pen.ctx.xy(leg.navaid.ident)), track)[1]
    return centre_abeam - side * radius * math.cos(math.radians(off_track))


def _intercept_on[L: (Radial, HeadingAndRadial)](
    pen: _Pen,
    leg: L,
    heading: float,
    direction: Turn | None,
    phrase: str,
    heading_text: str | None,
) -> None:
    """Turn onto the true `heading`, fly it to the radial, round the corner,
    then track the radial; `heading_text` labels the heading, if printed.

    Where a printed heading cannot intercept the radial ahead with room to
    round the corner (it runs within `MIN_INTERCEPT_DEG` of the radial, meets
    it behind, or too close), a runway's route that rolls out on the heading
    already on the radial has joined it there, and turns straight onto it.
    """
    ctx = pen.ctx
    radial_course = _radial_true(ctx, leg)
    navaid = ctx.xy(leg.navaid.ident)
    points, heading_side = pen.turn_onto(heading, direction)
    heading_start = pen.at
    try:
        leg, rounding_start = _rounding_start(pen, leg, heading, radial_course)
    except Degenerate as refusal:
        if not (
            isinstance(leg, HeadingAndRadial)
            and _rolled_out_on_radial(pen, refusal, navaid, radial_course)
        ):
            raise
        leg = _with_sense(ctx, leg, heading_start, heading)
        rounding_start = heading_start
    name = _leg_name(pen, direction, phrase) + radial_phrase(leg)
    track = _tracking_course(leg, radial_course)
    pen.straight_to(rounding_start)
    if heading_text is not None:
        heading_middle = midpoint(heading_start, pen.at)
        _offset_label(pen, heading_text, heading_middle, heading_side)
    rounding, side = pen.turn_onto(
        track, _turn(sign(_signed_turn(heading, track, None)))
    )
    _require_on_radial(pen.at, navaid, radial_course, leg)
    _track_radial(pen, leg, name, [*points, *rounding], side)


def _rolled_out_on_radial(
    pen: _Pen, refusal: Degenerate, navaid: Vec, radial_course: float
) -> bool:
    """Whether a heading refused for meeting its radial nowhere ahead rolled
    out already on it. A VCOA's route sets out from a schematic point, so
    where it rolls out never shows that."""
    return (
        refusal.signature in NO_INTERCEPT_AHEAD
        and pen.runway_course is not None
        and _on_radial(pen.at, navaid, radial_course)
        and _bearing_off_radial(pen.at, navaid, radial_course) <= JOINED_RADIAL_MAX_DEG
    )


def _bearing_off_radial(at: Vec, navaid: Vec, radial_course: float) -> float:
    """Degrees between the radial and the navaid's bearing to `at`, as a
    course deviation indicator tuned to that radial would show it."""
    along, across = along_across(sub(at, navaid), radial_course)
    return math.degrees(math.atan2(abs(across), along))


def _rounding_start[L: (Radial, HeadingAndRadial)](
    pen: _Pen, leg: L, heading: float, radial_course: float
) -> tuple[L, Vec]:
    """`leg` with its sense settled, and where the turn from the heading onto
    the radial begins so that it rolls out on the radial ahead."""
    ctx = pen.ctx
    _require_intercept_angle(heading, radial_course, leg)
    corner = _intercept(pen.at, heading, ctx.xy(leg.navaid.ident), radial_course, leg)
    leg = _with_sense(ctx, leg, corner, heading)
    turn = _signed_turn(heading, _tracking_course(leg, radial_course), None)
    lead = ctx.radius * math.tan(math.radians(abs(turn)) / 2)
    if distance(pen.at, corner) < lead:
        raise Degenerate(INTERCEPT_BEHIND, f"turn needs {lead:.2f} NM: {leg!r}")
    return leg, offset(corner, heading + 180, lead)


def _with_sense[L: (Radial, HeadingAndRadial)](
    ctx: _Context,
    leg: L,
    joined: Vec,
    course: float | None,
    direction: Turn | None = None,
) -> L:
    """`leg` with its sense settled: as printed, else toward where it ends,
    else the way the aircraft turns onto it.

    A radial printed without "inbound" or "outbound" that ends at a fix or a
    DME distance on that radial is flown whichever way along it reaches that
    point from `joined`, where the aircraft joins the radial; one whose end
    is within `MIN_LEG_NM` of the join is refused. One flown to an altitude
    is flown the way an aircraft on `course` turns onto it (see
    `_turned_sense`), and refused when no `course` is given. Any other is
    refused.
    """
    if leg.outbound is not None:
        return leg
    navaid = ctx.xy(leg.navaid.ident)
    radial_course = _radial_true(ctx, leg)
    match leg.until:
        case AtFix(target=target):
            end = along_across(sub(ctx.xy(target.ident), navaid), radial_course)[0]
        case Dme(navaid=dme, nm=nm) if dme.ident == leg.navaid.ident:
            end = nm
        case Altitude() if course is not None:
            outbound = _turned_sense(course, radial_course, direction, leg)
            return dataclasses.replace(leg, outbound=outbound)
        case _:
            raise Degenerate(UNSTATED_SENSE, repr(leg))
    start = along_across(sub(joined, navaid), radial_course)[0]
    if abs(end - start) < MIN_LEG_NM:
        raise Degenerate(UNSTATED_SENSE, repr(leg))
    return dataclasses.replace(leg, outbound=end > start)


def _turned_sense(
    course: float, radial_course: float, direction: Turn | None, leg
) -> bool:
    """Whether an aircraft on `course` turns onto the radial outbound rather
    than inbound.

    With no turn stated, it turns onto whichever of the radial's two courses
    lies within `UNSTATED_SENSE_TURN_DEG` of its own. With a turn stated,
    onto whichever that turn reaches after between `UNSTATED_SENSE_TURN_DEG`
    and its supplement. A shorter stated turn is refused, since procedures
    state turns of over 180° onto a radial and it may mean the other course;
    so is a radial crossing the course more squarely than either allows.
    """
    low, high = 0.0, UNSTATED_SENSE_TURN_DEG
    if direction is not None:
        low, high = UNSTATED_SENSE_TURN_DEG, 180 - UNSTATED_SENSE_TURN_DEG
    tracks = {True: radial_course, False: radial_course + 180}
    for outbound, track in tracks.items():
        if low <= abs(_signed_turn(course, track, direction)) <= high:
            return outbound
    raise Degenerate(UNSTATED_SENSE, repr(leg))


def _require_intercept_angle(heading: float, radial_course: float, leg) -> None:
    angle = min(
        abs(wrap180(heading - radial_course)),
        abs(wrap180(heading - radial_course - 180)),
    )
    if angle < MIN_INTERCEPT_DEG:
        raise Degenerate(RADIAL_COLINEAR, f"{angle:.1f} deg: {leg!r}")


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
        raise Degenerate(INTERCEPT_BEHIND, repr(leg))
    return offset(start, heading, ahead)


def _require_on_radial(at: Vec, navaid: Vec, radial_course: float, leg) -> None:
    """Refuse to track a radial the route is not already established on."""
    if not _on_radial(at, navaid, radial_course):
        across = along_across(sub(at, navaid), radial_course)[1]
        raise Degenerate("off radial", f"{across:.2f} NM abeam: {leg!r}")


def _on_radial(at: Vec, navaid: Vec, radial_course: float) -> bool:
    """Whether `at` lies on the radial itself, not its extension behind the
    navaid, within `ON_RADIAL_TOLERANCE_NM` of it."""
    along, across = along_across(sub(at, navaid), radial_course)
    return along >= 0 and abs(across) <= ON_RADIAL_TOLERANCE_NM


def _track_radial(
    pen: _Pen, leg: Radial | HeadingAndRadial, name: str, points: list[Vec], side: int
) -> None:
    """Track the radial from the current position to the leg's end, drawing
    the route and the radial itself.

    The climb gradient alone places an altitude's end, so the tracked part
    may be any length: an altitude the climb already reached before joining
    the radial ends the leg where it joins, as a heading leg's does where
    its turn ends. One climbed to on the way to a fix or DME distance is
    labelled beside where the leg ends, and the climb goes no higher until a
    later leg climbs on.
    """
    ctx = pen.ctx
    navaid = ctx.xy(leg.navaid.ident)
    joined = pen.at
    end = _tracking_end(pen, leg, navaid)
    if not isinstance(leg.until, Altitude):
        _require_length(along_across(sub(end, joined), pen.course)[0], leg)
    pen.straight_to(end)
    ctx.polyline(name, Style.ROUTE, [*points, end])
    _arrowhead(pen, name, Style.ROUTE, end, pen.course)
    if terminator := _terminator_label(ctx, leg.until):
        ctx.label(terminator, end)
    if leg.altitude is not None:
        _require_climb(pen, leg.altitude)
        pen.level_at(leg.altitude.feet)
        _offset_label(
            pen, format_altitude(leg.altitude, ctx.params.label_style), end, 0
        )
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
            if remaining <= 0:
                return pen.at
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
    """A short dashed stub on course, bent toward the restricted side if any.

    A compass direction ("before proceeding northbound") is read against
    true north: the stub turns onto it in dashes, the shorter way unless
    the text also names the turn's side, then runs straight. Two directions
    ("before proceeding east or southeast bound") name no one course, so
    the route ends without a stub.
    """
    if len(leg.toward) > 1:
        return
    ctx = pen.ctx
    name = _leg_name(pen, None, "proceed on course")
    if leg.toward:
        (point,) = leg.toward
        _dashed_turn(pen, name, COMPASS_BEARINGS[point], leg.turn_restriction)
        course = pen.course
    else:
        course = _bent(pen.course, leg.turn_restriction)
    for i in range(ON_COURSE_DASHES):
        start = offset(pen.at, course, i * ON_COURSE_DASH_SPACING_NM)
        ctx.polyline(
            name, Style.ROUTE, [start, offset(start, course, ON_COURSE_DASH_NM)]
        )
    pen.straight_to(offset(pen.at, course, ON_COURSE_STUB_NM))
    _arrowhead(pen, name, Style.ROUTE, pen.at, pen.course)


def _bent(course: float, turn: Turn | None) -> float:
    return course if turn is None else course + ON_COURSE_BEND_DEG * _side(turn)


def _dashed_turn(pen: _Pen, name: str, course: float, direction: Turn | None) -> None:
    """Turn onto `course` as `_Pen.turn_onto` does, drawn in dashes spaced as
    the on-course stub's."""
    ctx = pen.ctx
    sweep = _signed_turn(pen.course, course, direction)
    side = sign(sweep)
    centre = offset(pen.at, pen.course + 90 * side, ctx.radius)
    start = pen.course - 90 * side
    dash = math.degrees(ON_COURSE_DASH_NM / ctx.radius)
    spacing = math.degrees(ON_COURSE_DASH_SPACING_NM / ctx.radius)
    for i in range(math.ceil(abs(sweep) / spacing)):
        swept = i * spacing
        arc = ctx.arc(
            centre,
            ctx.radius,
            start + side * swept,
            side * min(dash, abs(sweep) - swept),
        )
        ctx.polyline(name, Style.ROUTE, arc)
    pen.turn_onto(course, direction)


def _cross_at(pen: _Pen, leg: CrossAt) -> None:
    """Label the altitude to cross the fix the route has just reached: at
    it, or abeam it within `ON_RADIAL_TOLERANCE_NM`, where a radial flown to
    the fix ends."""
    ctx = pen.ctx
    fix = ctx.xy(leg.fix.ident)
    along, across = along_across(sub(fix, pen.at), pen.course)
    if abs(along) > ARRIVAL_TOLERANCE_NM or abs(across) > ON_RADIAL_TOLERANCE_NM:
        raise Degenerate("crossing off the route", repr(leg))
    _offset_label(pen, format_altitude(leg.altitude, ctx.params.label_style), fix, 0)


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
    _arrowhead(pen, name, Style.HOLD, fix, inbound)
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
    length. A hold to an en-route minimum (MEA, MCA or MOCA), whose figure
    the text does not give, takes one-minute legs; one printed beside a
    figure ("4000 or MEA") goes by that figure."""
    tas = ctx.params.tas_kt
    match leg.until:
        case Dme(navaid=navaid, nm=nm) if navaid.ident == leg.fix.ident:
            return nm
        case Altitude(feet=feet) | EnrouteAltitude(feet=int(feet)) if (
            feet > HOLD_HIGH_ALTITUDE_FT
        ):
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


def _arrowhead(pen: _Pen, name: str, style: Style, end: Vec, course: float) -> None:
    """A two-armed "V" pointing along `course`, its tip `ARROW_SETBACK_NM`
    short of `end` so arrows converging on one fix stay distinguishable.

    The pen keeps it as its latest arrowhead until the route ends."""
    tip = offset(end, course + 180, ARROW_SETBACK_NM)
    shape = _draw_arrow(pen.ctx, name, style, tip, course)
    pen.last_arrow = _Arrowhead(shape, name, style, end, course)


def _end_routes(pens: list[_Pen]) -> None:
    """Where a route ends, its last arrowhead points right at the end of
    its final segment rather than short of it."""
    for pen in pens:
        if arrow := pen.last_arrow:
            pen.ctx.group = pen.group
            pen.ctx.discard(arrow.shape)
            _draw_arrow(pen.ctx, arrow.name, arrow.style, arrow.end, arrow.course)
            pen.last_arrow = None


def _draw_arrow(
    ctx: _Context, name: str, style: Style, tip: Vec, course: float
) -> Polyline:
    return ctx.polyline(f"{name} arrow", style, _arrow_arms(tip, course))


def _turn_arc(
    at: Vec, course: float, delta: float, radius: float, step: float
) -> list[Vec]:
    """The fly-by arc from `at` on `course` turning `delta` degrees (positive
    right) at `radius`; just `at` when there is no turn."""
    side = sign(delta)
    if not side:
        return [at]
    centre = offset(at, course + 90 * side, radius)
    return arc(centre, radius, course - 90 * side, delta, step)


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
