"""Tests for odp_kml.geometry: resolved legs become planview shapes."""

import math

import pytest

from odp_kml.geo import LatLon, LocalPlane
from odp_kml.geometry import (
    Degenerate,
    draw,
    format_altitude,
    turn_radius_nm,
)
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    ClimbHeading,
    ClimbingTurn,
    ClimbInHold,
    Direct,
    FixRef,
    HeadingAndRadial,
    HoldSpec,
    NavaidRef,
    Procedure,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
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
    drawing = draw(resolved(group(), runways=(runway(gradient=gradient),)))

    assert route_vertices(drawing)[-1] == pytest.approx((0.0, stub_nm), abs=1e-6)


def test_climb_heading_ends_where_gradient_reaches_altitude():
    leg = ClimbHeading(magnetic(0), feet(7000))

    drawing = draw(resolved(group(leg)))

    end = route_vertices(drawing)[-1]
    assert distance(end, (0.0, 0.0)) == pytest.approx(9.83, abs=0.1)
    altitude_label = next(
        lbl for lbl in labels(drawing) if distance(xy(lbl.at), end) < 1e-6
    )
    assert altitude_label.text == "7000"
    assert any(lbl.text == f"{magnetic(0):03d}°" for lbl in labels(drawing))


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


def test_heading_colinear_with_radial_is_degenerate():
    leg = HeadingAndRadial(magnetic(180), NavaidRef("VOR"), 180, outbound=True)

    with pytest.raises(Degenerate) as raised:
        draw(resolved(group(leg), points=(point("VOR", 0.0, 0.0),)))

    assert raised.value.signature == "radial colinear"


def test_radial_outbound_climbs_along_the_radial_to_altitude():
    leg = Radial(
        NavaidRef("VOR"), 360 - int(VARIATION), outbound=True, until=feet(7000)
    )

    drawing = draw(resolved(group(leg), points=(point("VOR", 0.0, 0.0, VARIATION),)))

    assert route_vertices(drawing)[-1] == pytest.approx((0.0, 9.825), abs=1e-6)
    assert any(lbl.text == f"R-{360 - int(VARIATION):03d}" for lbl in labels(drawing))


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
    assert any(lbl.text.startswith("HOLD 9") for lbl in labels(drawing))


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
        == "VCOA " + format_altitude(feet(7000, AltitudeKind.AT_OR_ABOVE), "chart")
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


def test_vcoa_then_on_course_draws_only_the_circle_and_label():
    vcoa = VcoaGroup((), None, 7000, (ProceedOnCourse(),))

    drawing = draw(resolved(vcoa=(vcoa,)))

    assert {line.name for line in polylines(drawing, Style.VCOA)} == {"ALL RWYS: VCOA"}
    assert len(polylines(drawing, Style.VCOA)) == 36
    assert polylines(drawing) == []
    assert [lbl.text for lbl in labels(drawing)] == [
        "VCOA " + format_altitude(feet(7000, AltitudeKind.AT_OR_ABOVE), "chart")
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
    (altitude_label,) = [lbl for lbl in labels(drawing) if lbl.text == "5300"]
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
