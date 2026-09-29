"""Tests for odp_kml.geometry: resolved legs become planview shapes."""

import itertools
import math

import pytest

from odp_kml.geo import LatLon, LocalPlane
from odp_kml.geometry import (
    ARROW_SETBACK_NM,
    Degenerate,
    draw,
    format_altitude,
    turn_radius_nm,
)
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    AtFix,
    ClimbHeading,
    ClimbingTurn,
    ClimbInHold,
    CrossAt,
    Direct,
    Dme,
    FixRef,
    HeadingAndRadial,
    HeadingRange,
    HeadingSector,
    HoldSpec,
    MinimumClimb,
    NavaidRef,
    Procedure,
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
from odp_kml.resolved import ResolvedPoint, ResolvedProcedure, RunwayStart
from odp_kml.shapes import Label, Polyline, Style

AIRPORT = LatLon(38.5, -117.5)
PLANE = LocalPlane(AIRPORT)
VARIATION = 13.0
R = turn_radius_nm(150.0, 25.0)


def magnetic(true_heading: int) -> int:
    """The magnetic heading printed for a true heading at the test airport."""
    return (true_heading - int(VARIATION)) % 360


def at(x: float, y: float) -> LatLon:
    """A lat/lon at plane coordinates (NM east, NM north) of the airport."""
    return PLANE.to_latlon(x, y)


def xy(p: LatLon) -> tuple[float, float]:
    return PLANE.to_xy(p)


def point(ident: str, x: float, y: float, declination: float = 0.0):
    return ResolvedPoint(ident, at(x, y), declination)


def runway(name="36", course=0.0, gradient=None, der=(0.0, 0.0)) -> RunwayStart:
    return RunwayStart(name, at(*der), 5000.0, course, gradient)


def resolved(
    *groups: RunwayGroup,
    runways=(),
    points=(),
    shared_tail=None,
    vcoa=(),
    holds=None,
) -> ResolvedProcedure:
    """A hand-built procedure at a flat test airport; defaults to RWY 36 at the field."""
    procedure = Procedure("TST", None, groups, shared_tail, vcoa)
    return ResolvedProcedure(
        procedure=procedure,
        airport_lid="TST",
        airport_name="TEST FIELD",
        airport_position=AIRPORT,
        airport_elevation_ft=5000.0,
        airport_variation_east=VARIATION,
        runways={r.runway: r for r in runways or (runway(),)},
        points={p.ident: p for p in points},
        published_holds=holds or {},
    )


def group(*legs, runways=("36",)) -> RunwayGroup:
    return RunwayGroup(runways, legs)


def polylines(drawing, style=Style.ROUTE) -> list[Polyline]:
    return [s for s in drawing.shapes if isinstance(s, Polyline) and s.style == style]


def labels(drawing) -> list[Label]:
    return [s for s in drawing.shapes if isinstance(s, Label)]


def route_vertices(drawing) -> list[tuple[float, float]]:
    """Every route vertex in drawing order, excluding arrowhead arms."""
    return [
        xy(p)
        for line in polylines(drawing)
        if not line.name.endswith("arrow")
        for p in line.points
    ]


def distance(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def feet(n: int, kind=AltitudeKind.TO) -> Altitude:
    return Altitude(n, kind, f"to {n}")


def test_turn_radius_uses_standard_rate_at_150_kt():
    assert turn_radius_nm(150, 25) == pytest.approx(0.80, abs=0.02)
    assert turn_radius_nm(150, 25) == pytest.approx(150 / 188.5)


@pytest.mark.parametrize(("gradient", "stub_nm"), [(None, 2.0), (320.0, 1.25)])
def test_initial_stub_reaches_400_ft_above_der(gradient, stub_nm):
    runway_heading = ClimbHeading(magnetic(0))

    drawing = draw(
        resolved(group(runway_heading), runways=(runway(gradient=gradient),))
    )

    assert route_vertices(drawing)[-1] == pytest.approx((0.0, stub_nm), abs=1e-6)


def test_runway_whose_departure_is_na_is_not_drawn():
    assert draw(resolved(group())).shapes == ()


def test_climb_heading_ends_where_gradient_reaches_altitude():
    leg = ClimbHeading(magnetic(0), feet(7000))

    drawing = draw(resolved(group(leg)))

    end = route_vertices(drawing)[-1]
    assert distance(end, (0.0, 0.0)) == pytest.approx(9.83, abs=0.1)
    altitude_label = next(
        lbl for lbl in labels(drawing) if distance(xy(lbl.at), end) < 1e-6
    )
    assert altitude_label.text == "7000'"
    assert any(lbl.text == f"hdg {magnetic(0):03d}°" for lbl in labels(drawing))


def test_open_heading_and_speed_limit_are_labelled_beside_the_turn():
    speed = SpeedRestriction(200, "reaching 9000")
    leg = ClimbingTurn(Turn.RIGHT, ClimbHeading(magnetic(90), speed=speed))

    drawing = draw(resolved(group(leg)))

    placed = {lbl.text: xy(lbl.at) for lbl in labels(drawing)}
    heading, speed_limit = placed.values()
    assert list(placed) == [
        f"hdg {magnetic(90):03d}°",
        "max 200 KIAS until 9000'",
    ]
    assert heading == pytest.approx((R, 2.0 + R + 0.35), abs=1e-6)
    assert speed_limit == pytest.approx((-0.35, 2.0), abs=1e-6)


def test_climbing_right_turn_direct_arcs_tangentially_onto_the_fix():
    leg = ClimbingTurn(Turn.RIGHT, Direct(NavaidRef("TPH")))

    drawing = draw(resolved(group(leg), points=(point("TPH", 8.0, 0.0),)))

    vertices = route_vertices(drawing)
    assert min(x for x, _ in vertices) >= -1e-9
    arc = vertices[2:-1]
    centre = (R, 2.0)
    assert all(distance(v, centre) == pytest.approx(R, abs=0.05) for v in arc)
    assert vertices[-1] == pytest.approx((8.0, 0.0), abs=1e-6)


def test_direct_to_fix_inside_turn_circle_is_degenerate():
    leg = ClimbingTurn(Turn.RIGHT, Direct(FixRef("ABEAM")))

    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(leg), points=(point("ABEAM", 0.5, 2.0),)))

    assert raised.value.signature == "fix inside turn circle"


def test_heading_intercepts_radial_inbound_and_tracks_to_the_vor():
    leg = HeadingAndRadial(magnetic(90), NavaidRef("VOR"), 180, outbound=False)
    east = runway("09", course=90.0)

    drawing = draw(
        resolved(
            group(leg, runways=("09",)),
            runways=(east,),
            points=(point("VOR", 10.0, 10.0),),
        )
    )

    vertices = route_vertices(drawing)
    corner = [v for v in vertices if 10.0 - R - 1e-6 < v[0] < 10.0 + 1e-6]
    assert corner[0] == pytest.approx((10.0 - R, 0.0), abs=1e-6)
    assert all(distance(v, (10.0 - R, R)) == pytest.approx(R) for v in corner[:-1])
    assert vertices[-1] == pytest.approx((10.0, 10.0), abs=1e-6)
    radial = polylines(drawing, Style.RADIAL)[0]
    assert radial.name == "VOR R-180"
    ends = [coordinate for p in radial.points for coordinate in xy(p)]
    assert ends == pytest.approx([10.0, 10.0, 10.0, R])
    texts = {lbl.text for lbl in labels(drawing)}
    assert texts == {f"hdg {magnetic(90):03d}°", "VOR R-180"}


def test_heading_colinear_with_radial_is_degenerate():
    leg = HeadingAndRadial(magnetic(180), NavaidRef("VOR"), 180, outbound=True)

    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(leg), points=(point("VOR", 0.0, 0.0),)))

    assert raised.value.signature == "radial colinear"


def straight_courses(drawing) -> set[int]:
    """True courses, to the degree, of the route's straight runs over 0.5 NM."""
    vertices = route_vertices(drawing)
    return {
        round(math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360)
        for a, b in itertools.pairwise(vertices)
        if distance(a, b) > 0.5
    }


@pytest.mark.parametrize(
    ("vor_y", "courses"),
    [(0.6, {225, 270}), (2 * R - 0.3, {250, 270})],
    ids=["45 degrees back to the radial", "converging from beside it"],
)
def test_turn_to_intercept_joins_the_radial_from_where_it_turns_to(vor_y, courses):
    """RWY 09 turns left onto R-090 (true) inbound: from well beside the
    radial it heads 45° back to it; rolling out just beside it, it converges
    at 20°. Either way the radial is drawn on its own course."""
    vor = NavaidRef("VOR")
    leg = ClimbingTurn(Turn.LEFT, Radial(vor, 90, False, AtFix(vor), intercept=True))
    east = runway("09", course=90.0)

    drawing = draw(
        resolved(
            group(leg, runways=("09",)),
            runways=(east,),
            points=(point("VOR", -10.0, vor_y),),
        )
    )

    assert straight_courses(drawing) >= courses
    assert route_vertices(drawing)[-1] == pytest.approx((-10.0, vor_y), abs=1e-6)
    [radial] = polylines(drawing, Style.RADIAL)
    assert all(xy(p)[1] == pytest.approx(vor_y, abs=1e-6) for p in radial.points)
    assert [lbl.text for lbl in labels(drawing)] == ["VOR R-090"]


@pytest.mark.parametrize(
    ("first", "leg", "vor", "signature"),
    [
        (
            ClimbHeading(magnetic(0), feet(7000)),
            Radial(
                NavaidRef("VOR"), 90, False, AtFix(NavaidRef("VOR")), intercept=True
            ),
            (-10.0, 12.0),
            "intercept without a turn",
        ),
        (
            None,
            ClimbingTurn(
                Turn.LEFT, Radial(NavaidRef("VOR"), 0, True, feet(7000), intercept=True)
            ),
            (3.0, -10.0),
            "intercept heading behind the turn",
        ),
        (
            None,
            ClimbingTurn(
                Turn.LEFT,
                Radial(NavaidRef("VOR"), 10, True, feet(8000), intercept=True),
            ),
            (-2.26, -10.0),
            "intercept heading behind the turn",
        ),
        (
            None,
            ClimbingTurn(
                Turn.LEFT,
                Radial(NavaidRef("VOR"), 10, True, feet(8000), intercept=True),
            ),
            (-0.75, -10.0),
            "intercept heading behind the turn",
        ),
    ],
    ids=[
        "no turn direction",
        "heading only an orbit reaches",
        "converging only after an orbit",
        "heading the other way round",
    ],
)
def test_turn_to_intercept_is_degenerate(first, leg, vor, signature):
    legs = (leg,) if first is None else (first, leg)

    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(*legs), points=(point("VOR", *vor),)))

    assert raised.value.signature == signature


def test_heading_with_no_terminator_is_held_until_the_tail_radial():
    """Both runways hold their headings until R-270 (true), 5 NM north."""
    vor = NavaidRef("VOR")
    procedure = resolved(
        group(ClimbingTurn(Turn.RIGHT, ClimbHeading(magnetic(45))), Thence()),
        group(
            ClimbingTurn(Turn.LEFT, ClimbHeading(magnetic(60))),
            Thence(),
            runways=("18",),
        ),
        runways=(runway(), runway("18", course=180.0, der=(0.0, 3.0))),
        points=(point("VOR", 20.0, 5.0),),
        shared_tail=(Radial(vor, 270, False, AtFix(vor)),),
    )

    drawing = draw(procedure)

    intercepts = [
        line
        for line in polylines(drawing)
        if line.name.endswith("to intercept VOR R-270 inbound")
    ]
    assert [line.name.split(":")[0] for line in intercepts] == ["RWY 36", "RWY 18"]
    assert all(
        xy(line.points[-1]) == pytest.approx((20.0, 5.0), abs=1e-6)
        for line in intercepts
    )


@pytest.mark.parametrize(
    ("until", "end_nm", "terminator"),
    [(feet(7000), 9.825, "7000'"), (Dme(NavaidRef("VOR"), 12.0), 12.0, "VOR 12 DME")],
)
def test_radial_outbound_tracks_the_radial_to_its_terminator(until, end_nm, terminator):
    leg = Radial(NavaidRef("VOR"), 360 - int(VARIATION), outbound=True, until=until)

    drawing = draw(resolved(group(leg), points=(point("VOR", 0.0, 0.0, VARIATION),)))

    end = (0.0, end_nm)
    assert route_vertices(drawing)[-1] == pytest.approx(end, abs=1e-6)
    placed = {lbl.text: xy(lbl.at) for lbl in labels(drawing)}
    assert placed[terminator] == pytest.approx(end, abs=1e-6)
    assert f"VOR R-{360 - int(VARIATION):03d}" in placed


@pytest.mark.parametrize(("turns", "side"), [(Turn.RIGHT, 1), (Turn.LEFT, -1)])
def test_hold_racetrack_extends_two_radii_to_the_turning_side(turns, side):
    hold = HoldSpec(None, turns, 360 - int(VARIATION))
    leg = ClimbInHold(NavaidRef("TPH"), hold, feet(9000))

    drawing = draw(resolved(group(leg), points=(point("TPH", 0.0, 6.0, VARIATION),)))

    racetrack = [xy(p) for p in polylines(drawing, Style.HOLD)[0].points]
    leg_nm = 150.0 / 60
    assert racetrack[0] == pytest.approx((0.0, 6.0), abs=1e-6)
    assert racetrack[-1] == pytest.approx(racetrack[0], abs=1e-6)
    xs = [x * side for x, _ in racetrack]
    assert min(xs) == pytest.approx(0.0, abs=1e-6)
    assert max(xs) == pytest.approx(2 * R, abs=1e-3)
    assert min(y for _, y in racetrack) == pytest.approx(6.0 - leg_nm - R, abs=1e-3)
    assert route_vertices(drawing)[-1] == pytest.approx((0.0, 6.0), abs=1e-6)
    assert any(lbl.text.startswith("Hold ") for lbl in labels(drawing))


def test_hold_falls_back_to_the_published_hold_else_is_degenerate():
    leg = ClimbInHold(FixRef("TPH"), None)
    tph = point("TPH", 0.0, 6.0)
    published = {"TPH": HoldSpec(None, Turn.RIGHT, 0)}

    drawing = draw(resolved(group(leg), points=(tph,), holds=published))

    assert polylines(drawing, Style.HOLD)[0].name == "HOLD TPH"
    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(leg), points=(tph,)))
    assert raised.value.signature == "hold unspecified"


def test_proceed_on_course_is_a_dashed_stub_bent_toward_the_restriction():
    drawing = draw(resolved(group(ProceedOnCourse(Turn.RIGHT))))

    dashes = [line for line in polylines(drawing) if "on course" in line.name]
    dashes = [line for line in dashes if not line.name.endswith("arrow")]
    assert len(dashes) == 4
    bent_end = (math.sqrt(0.5), 2.0 + math.sqrt(0.5))
    assert xy(dashes[-1].points[-1]) == pytest.approx(bent_end, abs=1e-6)


def test_vcoa_circle_surrounds_the_airport():
    vcoa = VcoaGroup(("36",), None, 7000, (Direct(NavaidRef("VOR")),))

    drawing = draw(resolved(vcoa=(vcoa,), points=(point("VOR", 0.0, 15.0),)))

    circle = [xy(p) for line in polylines(drawing, Style.VCOA) for p in line.points]
    assert len(polylines(drawing, Style.VCOA)) == 36
    assert all(distance(v, (0.0, 0.0)) == pytest.approx(2.0, abs=0.01) for v in circle)
    assert any(
        lbl.text
        == "VCOA ("
        + format_altitude(feet(7000, AltitudeKind.AT_OR_ABOVE), "plain")
        + ")"
        for lbl in labels(drawing)
    )
    assert route_vertices(drawing)[0] == pytest.approx((0.0, 2.0), abs=1e-6)


def converging(second_leg) -> ResolvedProcedure:
    """RWY 36 direct VOR and RWY 27 flying `second_leg`, sharing one tail."""
    tail = (Radial(NavaidRef("VOR"), 360, outbound=True, until=feet(9000)),)
    return resolved(
        group(Direct(NavaidRef("VOR")), Thence()),
        group(second_leg, Thence(), runways=("27",)),
        runways=(runway(), runway("27", course=270.0, der=(1.0, 0.0))),
        points=(point("VOR", 0.0, 15.0),),
        shared_tail=tail,
    )


def test_shared_tail_is_drawn_once_from_where_the_groups_converge():
    drawing = draw(converging(ClimbingTurn(Turn.RIGHT, Direct(NavaidRef("VOR")))))

    tails = [
        line for line in polylines(drawing) if line.name.endswith("R-360 outbound")
    ]
    assert len(tails) == 1
    assert xy(tails[0].points[0]) == pytest.approx((0.0, 15.0), abs=1e-6)
    with pytest.raises(Degenerate) as raised:
        draw(converging(ClimbHeading(magnetic(270), feet(7000))))
    assert raised.value.signature == "shared tail start mismatch"


VCOA_LABEL = (
    "VCOA (" + format_altitude(feet(7000, AltitudeKind.AT_OR_ABOVE), "plain") + ")",
    (0.0, 2.0),
)


@pytest.mark.parametrize(
    ("speed", "placed"),
    [
        (None, [VCOA_LABEL]),
        (
            SpeedRestriction(180, "reaching 7000 MSL"),
            [VCOA_LABEL, ("max 180 KIAS until 7000'", (0.0, -2.0))],
        ),
    ],
)
def test_vcoa_then_on_course_draws_only_the_circle_and_labels(speed, placed):
    vcoa = VcoaGroup((), None, 7000, (ProceedOnCourse(),), speed=speed)

    drawing = draw(resolved(vcoa=(vcoa,)))

    assert {line.name for line in polylines(drawing, Style.VCOA)} == {"ALL RWYS: VCOA"}
    assert len(polylines(drawing, Style.VCOA)) == 36
    assert polylines(drawing) == []
    assert [(lbl.text, xy(lbl.at)) for lbl in labels(drawing)] == [
        (text, pytest.approx(at_xy, abs=1e-6)) for text, at_xy in placed
    ]


def test_unsupported_construction_signature_names_the_leg_type():
    vcoa = VcoaGroup(("36",), None, 7000, (ProceedOnCourse(), Direct(FixRef("X"))))

    with pytest.raises(Degenerate) as raised:
        draw(resolved(vcoa=(vcoa,), points=(point("X", 0.0, 9.0),)))

    assert raised.value.signature == "unsupported construction: ProceedOnCourse"


def test_climb_heading_to_an_altitude_reached_before_turn_start_ends_there():
    leg = ClimbHeading(magnetic(0), feet(5300))

    drawing = draw(resolved(group(leg)))

    turn_start = (0.0, 2.0)
    assert route_vertices(drawing)[-1] == pytest.approx(turn_start, abs=1e-6)
    (altitude_label,) = [lbl for lbl in labels(drawing) if lbl.text == "5300'"]
    assert xy(altitude_label.at) == pytest.approx(turn_start, abs=1e-6)


def hold_after_climb(vor_xy) -> ResolvedProcedure:
    """RWY 36 climbs north to 7000, then holds at a VOR placed at `vor_xy`."""
    hold = ClimbInHold(NavaidRef("VOR"), HoldSpec(None, Turn.RIGHT, 0), feet(9000))
    climb = ClimbHeading(magnetic(0), feet(7000))
    return resolved(group(climb, hold), points=(point("VOR", *vor_xy),))


def test_hold_fix_ahead_is_approached_through_a_tangent_arc():
    drawing = draw(hold_after_climb((4.0, 16.0)))

    (approach,) = [
        line for line in polylines(drawing) if line.name.endswith("direct VOR")
    ]
    route = [xy(p) for p in approach.points]
    climb_end = route[0]
    assert len(route) > 2
    assert all(
        distance(v, (climb_end[0] + R, climb_end[1])) == pytest.approx(R, abs=1e-6)
        for v in route[:-1]
    )
    assert route[-1] == pytest.approx((4.0, 16.0), abs=1e-6)


def test_hold_fix_behind_is_degenerate():
    with pytest.raises(Degenerate) as raised:
        draw(hold_after_climb((1.0, 3.0)))

    assert raised.value.signature == "hold fix behind"


@pytest.mark.parametrize("altitude", [4000, 5000, 5035])
def test_climb_heading_to_an_altitude_not_above_the_climb_start_is_degenerate(
    altitude,
):
    leg = ClimbHeading(magnetic(0), feet(altitude))

    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(leg)))

    assert raised.value.signature == "altitude not above current"


def test_arrowhead_stands_back_from_the_leg_end():
    leg = ClimbHeading(magnetic(0), feet(7000))

    drawing = draw(resolved(group(leg)))

    end = route_vertices(drawing)[-1]
    arrow = next(line for line in polylines(drawing) if line.name.endswith("arrow"))
    tip = xy(arrow.points[1])
    assert distance(tip, end) == pytest.approx(ARROW_SETBACK_NM, abs=1e-6)
    assert tip[1] < end[1]


def test_identical_shapes_are_drawn_once():
    """Two runways' VCOA groups circle the same airport: one label, not two."""
    groups = tuple(
        VcoaGroup((rwy,), None, 7000, (ProceedOnCourse(),)) for rwy in ("36", "18")
    )

    drawing = draw(resolved(vcoa=groups))

    assert len(labels(drawing)) == 1
    assert len(drawing.shapes) == len(set(drawing.shapes))


def test_heading_range_is_a_wedge_from_the_turn_start():
    """RWY 36 may climb on any heading from 000° clockwise to 090° true."""
    sector = HeadingSector(magnetic(0), magnetic(90), clockwise=True)

    drawing = draw(resolved(group(HeadingRange((sector,), feet(7000)))))

    apex = (0.0, 2.0)
    wedge = [xy(p) for line in polylines(drawing, Style.RADIAL) for p in line.points]
    assert apex in [pytest.approx(v, abs=1e-6) for v in wedge]
    rim = [v for v in wedge if distance(v, apex) > 0.1]
    assert all(distance(v, apex) == pytest.approx(3.0, abs=1e-6) for v in rim)
    assert all(v[0] >= -1e-6 and v[1] >= apex[1] - 1e-6 for v in rim)
    assert [lbl.text for lbl in labels(drawing)] == [
        f"hdg {magnetic(0):03d}° CW {magnetic(90):03d}° "
        + format_altitude(feet(7000), "plain")
    ]


def test_counterclockwise_sector_sweeps_the_other_way():
    sector = HeadingSector(magnetic(90), magnetic(0), clockwise=False)

    drawing = draw(resolved(group(HeadingRange((sector,)))))

    rim = [
        xy(p)
        for line in polylines(drawing, Style.RADIAL)
        for p in line.points
        if distance(xy(p), (0.0, 2.0)) > 0.1
    ]
    assert all(v[0] >= -1e-6 and v[1] >= 2.0 - 1e-6 for v in rim)


def test_only_proceed_on_course_may_follow_a_heading_range():
    sector = HeadingSector(magnetic(0), magnetic(90), clockwise=True)
    after = ClimbHeading(magnetic(45), feet(9000))

    draw(resolved(group(HeadingRange((sector,)), ProceedOnCourse())))
    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(HeadingRange((sector,)), after)))

    assert raised.value.signature == "leg after a heading range"


def test_minimum_climb_on_the_headings_a_range_leaves_out_draws_nothing():
    other_headings = group(MinimumClimb(415, feet(1600)))

    assert draw(resolved(other_headings)).shapes == ()


def test_empty_heading_sector_is_degenerate():
    sector = HeadingSector(90, 90, clockwise=True)

    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(HeadingRange((sector,)))))

    assert raised.value.signature == "empty heading sector"


def test_runway_heading_follows_the_runway_course_not_a_printed_number():
    rwy = runway("33", course=327.5)

    drawing = draw(
        resolved(group(RunwayHeading(feet(7000)), runways=("33",)), runways=(rwy,))
    )

    route = route_vertices(drawing)
    bearing = math.degrees(math.atan2(route[-1][0], route[-1][1])) % 360
    assert bearing == pytest.approx(327.5, abs=1e-6)
    assert "rwy hdg" in [lbl.text for lbl in labels(drawing)]


def test_straight_ahead_climbs_on_along_the_runway_course_labelled_only_by_altitude():
    rwy = runway("33", course=327.5)

    drawing = draw(
        resolved(group(StraightAhead(feet(7000)), runways=("33",)), runways=(rwy,))
    )

    end = route_vertices(drawing)[-1]
    assert math.degrees(math.atan2(end[0], end[1])) % 360 == pytest.approx(327.5)
    assert distance(end, (0.0, 0.0)) == pytest.approx(9.83, abs=0.1)
    assert [lbl.text for lbl in labels(drawing)] == ["7000'"]


@pytest.mark.parametrize(
    ("procedure", "leg"),
    [
        pytest.param(
            resolved(vcoa=(VcoaGroup((), None, 7000, (RunwayHeading(feet(9000)),)),)),
            "RunwayHeading",
            id="runway heading after a VCOA",
        ),
        pytest.param(
            resolved(vcoa=(VcoaGroup((), None, 7000, (StraightAhead(feet(9000)),)),)),
            "StraightAhead",
            id="straight ahead after a VCOA",
        ),
        pytest.param(
            resolved(
                group(ClimbHeading(magnetic(90), feet(7000)), StraightAhead(feet(9000)))
            ),
            "StraightAhead",
            id="straight ahead off the runway course",
        ),
        pytest.param(
            resolved(
                group(Direct(NavaidRef("VOR")), StraightAhead(feet(9000))),
                points=(point("VOR", 0.0, 6.0),),
            ),
            "StraightAhead",
            id="straight ahead past a fix on the runway centreline",
        ),
        pytest.param(
            resolved(
                group(ClimbingTurn(Turn.LEFT, None), Thence()),
                shared_tail=(StraightAhead(feet(9000)),),
            ),
            "StraightAhead",
            id="straight ahead owing a turn",
        ),
        pytest.param(
            resolved(
                group(RunwayHeading(feet(7000)), Thence()),
                shared_tail=(StraightAhead(feet(9000)),),
            ),
            "StraightAhead",
            id="straight ahead in a shared tail",
        ),
    ],
)
def test_runway_course_leg_away_from_the_runway_is_unsupported(procedure, leg):
    with pytest.raises(Degenerate) as raised:
        draw(procedure)

    assert raised.value.signature == f"unsupported construction: {leg}"


@pytest.mark.parametrize(("fix_nm", "draws"), [(12.2, True), (13.0, False)])
def test_dme_terminator_naming_a_fix_must_find_it_there(fix_nm, draws):
    until = Dme(NavaidRef("VOR"), 12.0, FixRef("CARRO"))
    leg = Radial(NavaidRef("VOR"), 360 - int(VARIATION), outbound=True, until=until)
    points = (point("VOR", 0.0, 0.0, VARIATION), point("CARRO", 0.0, fix_nm))

    if draws:
        drawing = draw(resolved(group(leg), points=points))
        assert route_vertices(drawing)[-1] == pytest.approx((0.0, 12.0), abs=1e-6)
    else:
        with pytest.raises(Degenerate) as raised:
            draw(resolved(group(leg), points=points))
        assert raised.value.signature == "DME fix mismatch"


@pytest.mark.parametrize(
    ("until", "outbound"),
    [
        (AtFix(FixRef("FAR")), True),
        (AtFix(FixRef("NEAR")), False),
        (Dme(NavaidRef("VOR"), 15.0), True),
        (Dme(NavaidRef("VOR"), 2.0), False),
    ],
)
def test_unstated_radial_sense_is_toward_where_the_leg_ends(until, outbound):
    """Heading east, the aircraft joins R-360 (true) about 9 NM north of the VOR."""
    leg = HeadingAndRadial(magnetic(90), NavaidRef("VOR"), magnetic(0), None, until)
    points = (
        point("VOR", 3.0, -6.0, VARIATION),
        point("FAR", 3.0, 9.0),
        point("NEAR", 3.0, -3.0),
    )

    drawing = draw(resolved(group(leg), points=points))

    sense = "outbound" if outbound else "inbound"
    assert any(line.name.endswith(sense) for line in polylines(drawing))


def test_unstated_radial_sense_with_an_altitude_terminator_is_degenerate():
    leg = Radial(NavaidRef("VOR"), 360 - int(VARIATION), None, feet(7000))

    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(leg), points=(point("VOR", 0.0, 0.0, VARIATION),)))

    assert raised.value.signature == 'radial without "inbound" or "outbound"'


LEFT_INTO_TAIL = ClimbingTurn(Turn.LEFT, None)


def turning_into(tail, second=LEFT_INTO_TAIL):
    """RWY 36 turns right and RWY 18 flies `second` into a shared `tail`."""
    return resolved(
        group(ClimbingTurn(Turn.RIGHT, None), Thence()),
        group(second, Thence(), runways=("18",)),
        runways=(runway(), runway("18", course=180.0, der=(0.0, 3.0))),
        points=(point("VOR", 6.0, 1.5),),
        shared_tail=tail,
    )


def test_turn_without_a_route_flies_the_tail_first_leg_from_each_runway():
    drawing = draw(turning_into((Direct(NavaidRef("VOR")),)))

    names = [line.name for line in polylines(drawing) if line.name.endswith("VOR")]
    assert names == [
        "RWY 36: climbing right turn direct VOR",
        "RWY 18: climbing left turn direct VOR",
    ]
    ends = [line.points[-1] for line in polylines(drawing) if line.name in names]
    assert all(xy(p) == pytest.approx((6.0, 1.5), abs=1e-6) for p in ends)


def test_turns_into_the_tail_must_be_every_group_or_none():
    with pytest.raises(Degenerate) as raised:
        draw(turning_into((Direct(NavaidRef("VOR")),), Direct(NavaidRef("VOR"))))

    assert raised.value.signature == "shared tail start mismatch"


def test_crossing_is_labelled_at_the_fix_the_route_reached():
    vor = NavaidRef("VOR")
    crossing = CrossAt(vor, feet(9000, AltitudeKind.AT_OR_ABOVE))
    points = (point("VOR", 0.0, 12.0),)

    drawing = draw(resolved(group(Direct(vor), crossing), points=points))

    texts = [lbl.text for lbl in labels(drawing)]
    assert format_altitude(feet(9000, AltitudeKind.AT_OR_ABOVE), "plain") in texts
    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(crossing), points=points))
    assert raised.value.signature == "crossing off the route"


def test_altitude_reached_before_joining_a_radial_ends_the_leg_at_the_join():
    """At 200 ft/NM, 5500 ft comes about 2.3 NM out, before R-180 is joined."""
    leg = HeadingAndRadial(
        magnetic(90), NavaidRef("VOR"), magnetic(180), False, feet(5500)
    )

    drawing = draw(resolved(group(leg), points=(point("VOR", 3.0, 20.0, VARIATION),)))

    end = route_vertices(drawing)[-1]
    assert end[0] == pytest.approx(3.0, abs=1e-6)
    assert "5500'" in [lbl.text for lbl in labels(drawing)]


def test_altitude_climbed_on_the_way_to_a_fix_is_labelled_and_held_there():
    """5500 ft comes before R-180 is joined, yet the leg still ends at the VOR,
    and the climb to 7500 after it starts from 5500 there."""
    vor = NavaidRef("VOR")
    leg = HeadingAndRadial(
        magnetic(90), vor, magnetic(180), False, AtFix(vor), altitude=feet(5500)
    )
    after = ClimbHeading(magnetic(90), feet(7500))

    drawing = draw(
        resolved(group(leg, after), points=(point("VOR", 3.0, 20.0, VARIATION),))
    )

    placed = {lbl.text: xy(lbl.at) for lbl in labels(drawing)}
    assert distance(placed["5500'"], (3.0, 20.0)) == pytest.approx(0.35)
    straight = 2000 / 200 - R * math.pi / 2
    assert route_vertices(drawing)[-1] == pytest.approx(
        (3.0 + R + straight, 20.0 + R), abs=1e-6
    )


def test_tail_along_a_radial_starts_where_the_first_route_joined_it():
    """RWY 36 joins R-180 (true) south of where RWY 18 does; both fly it north."""
    vor = NavaidRef("VOR")
    radial = magnetic(180)
    procedure = resolved(
        group(HeadingAndRadial(magnetic(90), vor, radial, False, feet(5500)), Thence()),
        group(
            HeadingAndRadial(magnetic(90), vor, radial, False, feet(5500)),
            Thence(),
            runways=("18",),
        ),
        runways=(runway(), runway("18", course=180.0, der=(0.0, 6.0))),
        points=(point("VOR", 3.0, 20.0, VARIATION),),
        shared_tail=(Radial(vor, radial, False, AtFix(vor)),),
    )

    drawing = draw(procedure)

    (tail,) = [
        line
        for line in polylines(drawing)
        if line.name.startswith("RWY 36/18") and not line.name.endswith("arrow")
    ]
    assert xy(tail.points[0])[1] < 4.0
    assert xy(tail.points[-1]) == pytest.approx((3.0, 20.0), abs=1e-6)
