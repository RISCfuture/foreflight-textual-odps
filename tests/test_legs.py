"""Leg wordings read by `odp_kml.legs`, each from a published procedure."""

import pytest

from odp_kml.grammar import ParseError, parse_departure_procedure
from odp_kml.normalize import normalize
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    AtFix,
    ClimbHeading,
    ClimbingTurn,
    HeadingAndRadial,
    NavaidRef,
    NavaidType,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
    SpeedRestriction,
    Turn,
)


def to(feet: int) -> Altitude:
    return Altitude(feet, AltitudeKind.TO, f"to {feet}")


def legs_of(text: str):
    """The runway groups of `text` as printed, normalized as the build does."""
    procedure = parse_departure_procedure(
        normalize(text), airport="XXX", amendment=None
    )
    return procedure.runway_groups


def signature_of(text: str) -> str:
    with pytest.raises(ParseError) as error:
        parse_departure_procedure(normalize(text), airport="XXX", amendment=None)
    return error.value.signature


@pytest.mark.parametrize(
    ("text", "groups"),
    [
        pytest.param(
            "Rwy 2, climbing right turn to intercept SAC R-242 to SAC VORTAC to "
            "2000 before proceeding on course.",
            [
                RunwayGroup(
                    ("2",),
                    (
                        ClimbingTurn(
                            Turn.RIGHT,
                            Radial(
                                NavaidRef("SAC"),
                                242,
                                outbound=False,
                                until=AtFix(NavaidRef("SAC", NavaidType.VORTAC)),
                                altitude=to(2000),
                                intercept=True,
                            ),
                        ),
                        ProceedOnCourse(),
                    ),
                )
            ],
            id="VCB radial",
        ),
        pytest.param(
            "Rwy 13, climb on heading 126° and RBL R-344 to RBL VORTAC to 3000 "
            "before proceeding on course.",
            [
                RunwayGroup(
                    ("13",),
                    (
                        HeadingAndRadial(
                            126,
                            NavaidRef("RBL"),
                            344,
                            outbound=False,
                            until=AtFix(NavaidRef("RBL", NavaidType.VORTAC)),
                            altitude=to(3000),
                        ),
                        ProceedOnCourse(),
                    ),
                )
            ],
            id="RDD heading and radial",
        ),
    ],
)
def test_altitude_after_a_fix_is_climbed_to_on_the_way(text, groups):
    assert legs_of(text) == tuple(groups)


def test_altitude_after_a_fix_and_before_it_is_refused():
    text = (
        "Rwy 2, climbing right turn to intercept SAC R-242 to 1500 to SAC VORTAC "
        "to 2000 before proceeding on course."
    )

    assert signature_of(text) == 'unmatched phrase "to <n> before proceeding"'


def test_altitude_before_a_radial_to_intercept_is_climbed_to_on_the_way():
    text = (
        "Rwy 12, climbing left turn to 3000 to intercept MOD VOR/DME R-264 to MOD "
        "VOR/DME before proceeding on course."
    )

    assert legs_of(text) == (
        RunwayGroup(
            ("12",),
            (
                ClimbingTurn(
                    Turn.LEFT,
                    Radial(
                        NavaidRef("MOD", NavaidType.VOR_DME),
                        264,
                        outbound=False,
                        until=AtFix(NavaidRef("MOD", NavaidType.VOR_DME)),
                        altitude=to(3000),
                        intercept=True,
                    ),
                ),
                ProceedOnCourse(),
            ),
        ),
    )


def test_altitude_and_on_a_radial_is_climbed_to_on_the_radial():
    text = (
        "Rwy 29, climb heading 290° to 1700 then climbing left turn to 5000 and on "
        "the HGR VOR R-270 to HGR VOR before proceeding on course."
    )

    assert legs_of(text) == (
        RunwayGroup(
            ("29",),
            (
                ClimbHeading(290, to(1700)),
                ClimbingTurn(
                    Turn.LEFT,
                    Radial(
                        NavaidRef("HGR", NavaidType.VOR),
                        270,
                        outbound=False,
                        until=AtFix(NavaidRef("HGR", NavaidType.VOR)),
                        altitude=to(5000),
                    ),
                ),
                ProceedOnCourse(),
            ),
        ),
    )


def test_heading_climbing_to_an_altitude_ends_there():
    text = (
        "Rwy 9, climbing right turn heading 110°, climbing to 5000 before "
        "proceeding on course."
    )

    assert legs_of(text) == (
        RunwayGroup(
            ("9",),
            (ClimbingTurn(Turn.RIGHT, ClimbHeading(110, to(5000))), ProceedOnCourse()),
        ),
    )


ROW = (
    "Rwy 35, climbing left turn to 6000 on heading 277° to intercept CME R-323, "
    "upon reaching 6000 proceed on course."
)


def test_upon_reaching_the_altitude_climbed_to_proceeds_on_course():
    assert legs_of(ROW) == (
        RunwayGroup(
            ("35",),
            (
                ClimbingTurn(
                    Turn.LEFT,
                    HeadingAndRadial(
                        277, NavaidRef("CME"), 323, outbound=None, until=to(6000)
                    ),
                ),
                ProceedOnCourse(),
            ),
        ),
    )


def test_upon_reaching_another_altitude_is_refused():
    text = ROW.replace("reaching 6000", "reaching 7000")

    assert signature_of(text) == "upon reaching an altitude the leg does not end at"


def test_speed_limit_until_leaving_an_altitude():
    text = (
        "Rwy 6, climb heading 075° to 4700, do not exceed 210 KIAS until leaving 4000."
    )

    assert legs_of(text) == (
        RunwayGroup(
            ("6",),
            (ClimbHeading(75, to(4700), SpeedRestriction(210, "leaving 4000")),),
        ),
    )


def test_speed_limit_until_established_on_a_heading_printed_number_first():
    text = (
        "Rwy 9, climbing left turn heading 059° to 2200 before proceeding on "
        "course. Do not exceed 180K until established on 059° heading."
    )

    assert legs_of(text) == (
        RunwayGroup(
            ("9",),
            (
                ClimbingTurn(
                    Turn.LEFT,
                    ClimbHeading(
                        59,
                        to(2200),
                        SpeedRestriction(180, "established on 059° heading"),
                    ),
                ),
                ProceedOnCourse(),
            ),
        ),
    )


def test_turn_to_a_radial_turns_onto_it():
    text = (
        "Rwy 8, climb heading 081° to 6900, then climbing left turn to BKE VOR/DME "
        "R-066 to BKE VOR/DME before proceeding on course."
    )

    assert legs_of(text) == (
        RunwayGroup(
            ("8",),
            (
                ClimbHeading(81, to(6900)),
                ClimbingTurn(
                    Turn.LEFT,
                    Radial(
                        NavaidRef("BKE", NavaidType.VOR_DME),
                        66,
                        outbound=False,
                        until=AtFix(NavaidRef("BKE", NavaidType.VOR_DME)),
                    ),
                ),
                ProceedOnCourse(),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("text", "leg"),
    [
        pytest.param(
            "Rwy 3, climbing left turn on DDY R-202, to DDY VOR/DME before "
            "proceeding on course.",
            ClimbingTurn(
                Turn.LEFT,
                Radial(
                    NavaidRef("DDY"),
                    202,
                    outbound=False,
                    until=AtFix(NavaidRef("DDY", NavaidType.VOR_DME)),
                ),
            ),
            id="CPR comma",
        ),
        pytest.param(
            "Rwy 28, climb on heading 282° to intercept VXV VORTAC R-090 then to "
            "VXV VORTAC to 5000 before proceeding on course.",
            HeadingAndRadial(
                282,
                NavaidRef("VXV", NavaidType.VORTAC),
                90,
                outbound=False,
                until=AtFix(NavaidRef("VXV", NavaidType.VORTAC)),
                altitude=to(5000),
            ),
            id="GKT then",
        ),
    ],
)
def test_radial_ends_at_its_own_navaid_set_off_by_a_comma_or_then(text, leg):
    (group,) = legs_of(text)

    assert group.legs == (leg, ProceedOnCourse())


def test_radial_does_not_end_at_another_navaid_set_off_by_a_comma():
    text = (
        "Rwy 3, climbing left turn on DDY R-202, to CPR VOR/DME before proceeding "
        "on course."
    )

    assert signature_of(text) == 'unmatched phrase "to <id> vor/"'
