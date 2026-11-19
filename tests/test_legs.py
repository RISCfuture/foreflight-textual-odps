"""Leg wordings read by `odp_kml.legs`, each from a published procedure."""

import pytest

from odp_kml.grammar import ParseError, parse_departure_procedure
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    AtFix,
    ClimbingTurn,
    HeadingAndRadial,
    NavaidRef,
    NavaidType,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
    Turn,
)


def to(feet: int) -> Altitude:
    return Altitude(feet, AltitudeKind.TO, f"to {feet}")


def legs_of(text: str):
    procedure = parse_departure_procedure(text, airport="XXX", amendment=None)
    return procedure.runway_groups


def signature_of(text: str) -> str:
    with pytest.raises(ParseError) as error:
        parse_departure_procedure(text, airport="XXX", amendment=None)
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
