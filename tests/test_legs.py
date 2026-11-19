"""Leg wordings read by `odp_kml.legs`, each from a published procedure."""

import pytest

from odp_kml.grammar import ParseError, parse_departure_procedure, parse_procedure
from odp_kml.normalize import normalize
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    AtFix,
    ClimbHeading,
    ClimbingTurn,
    ClimbInHold,
    Compass8,
    CrossAt,
    Direct,
    Dme,
    FixRef,
    HeadingAndRadial,
    HeadingRange,
    HeadingSector,
    HoldSpec,
    NavaidRef,
    NavaidType,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
    SpeedRestriction,
    StraightAhead,
    Thence,
    Turn,
)
from odp_kml.sections import Sections


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


def test_climbing_on_a_heading_with_no_turn_is_a_climb():
    text = "Rwy 18, climbing on heading 175° to 4200 before proceeding on course."

    assert legs_of(text) == (
        RunwayGroup(("18",), (ClimbHeading(175, to(4200)), ProceedOnCourse())),
    )


def test_heading_range_may_leave_out_between():
    text = (
        "Rwy 2, climb on a heading 316° CW 052° from DER to 10600 before "
        "proceeding on course."
    )

    assert legs_of(text) == (
        RunwayGroup(
            ("2",),
            (
                HeadingRange((HeadingSector(316, 52, clockwise=True),), to(10600)),
                ProceedOnCourse(),
            ),
        ),
    )


def test_heading_may_be_flown_from_der():
    text = (
        "Rwy 20, climb on heading 185° from DER to 11000 before proceeding on course."
    )

    assert legs_of(text) == (
        RunwayGroup(("20",), (ClimbHeading(185, to(11000)), ProceedOnCourse())),
    )


@pytest.mark.parametrize(
    ("text", "leg"),
    [
        pytest.param(
            "Rwy 36, climb heading 357° to intercept and climb on SWR R-113 to SWR "
            "VOR/DME thence...\n...proceed on course.",
            HeadingAndRadial(
                357,
                NavaidRef("SWR"),
                113,
                outbound=False,
                until=AtFix(NavaidRef("SWR", NavaidType.VOR_DME)),
            ),
            id="TVL climb on",
        ),
        pytest.param(
            "Rwy 4, climbing right turn heading 240° to intercept and track "
            "outbound on HOM VOR/DME R-210 thence...\n...proceed on course.",
            ClimbingTurn(
                Turn.RIGHT,
                HeadingAndRadial(
                    240, NavaidRef("HOM", NavaidType.VOR_DME), 210, outbound=True
                ),
            ),
            id="HOM track outbound on",
        ),
    ],
)
def test_radial_intercepted_and_flown_on(text, leg):
    procedure = parse_departure_procedure(
        normalize(text), airport="XXX", amendment=None
    )

    assert procedure.runway_groups[0].legs == (leg, Thence())


def test_radial_intercepted_and_flown_on_with_a_contrary_sense_is_refused():
    text = (
        "Rwy 4, climbing right turn heading 240° to intercept and track outbound "
        "on HOM VOR/DME R-210 inbound thence...\n...proceed on course."
    )

    assert signature_of(text) == "radial sense printed both ways"


def test_radial_named_again_without_its_navaid_ends_where_it_says():
    text = (
        "Rwys 5, 35, climbing left turn heading 280° to intercept TFD R-063, then "
        "via R-063 to TFD VORTAC before proceeding on course."
    )

    assert legs_of(text) == (
        RunwayGroup(
            ("5", "35"),
            (
                ClimbingTurn(
                    Turn.LEFT,
                    HeadingAndRadial(
                        280,
                        NavaidRef("TFD"),
                        63,
                        outbound=False,
                        until=AtFix(NavaidRef("TFD", NavaidType.VORTAC)),
                    ),
                ),
                ProceedOnCourse(),
            ),
        ),
    )


def test_another_radial_named_without_its_navaid_is_refused():
    text = (
        "Rwys 5, 35, climbing left turn heading 280° to intercept TFD R-063, then "
        "via R-090 to TFD VORTAC before proceeding on course."
    )

    assert signature_of(text) == 'unmatched phrase "via r-<n> to <id>"'


def test_radial_ends_at_the_fix_it_crosses():
    text = (
        "Rwy 2, climbing right turn heading 204° and on COE R-360 to cross COE "
        "VOR/DME at or above 9000."
    )
    coe = NavaidRef("COE", NavaidType.VOR_DME)

    assert legs_of(text) == (
        RunwayGroup(
            ("2",),
            (
                ClimbingTurn(
                    Turn.RIGHT,
                    HeadingAndRadial(
                        204, NavaidRef("COE"), 360, outbound=False, until=AtFix(coe)
                    ),
                ),
                CrossAt(
                    coe,
                    Altitude(9000, AltitudeKind.AT_OR_ABOVE, "at or above 9000"),
                ),
            ),
        ),
    )


def test_fix_named_by_distance_alone_is_from_the_radials_navaid():
    text = (
        "Rwy 8, DME required. Climb on RIL VOR/DME R-083 to ZOBAK/7.41 DME, then "
        "climbing left turn direct RIL VOR/DME."
    )
    ril = NavaidRef("RIL", NavaidType.VOR_DME)

    assert legs_of(text) == (
        RunwayGroup(
            ("8",),
            (
                Radial(
                    ril,
                    83,
                    outbound=None,
                    until=Dme(NavaidRef("RIL"), 7.41, FixRef("ZOBAK")),
                ),
                ClimbingTurn(Turn.LEFT, Direct(ril)),
            ),
        ),
    )


def test_intersection_spelled_out_marks_a_fix():
    text = "Rwy 5, climbing right turn to intercept SEA R-230 to CARRO intersection."

    assert legs_of(text) == (
        RunwayGroup(
            ("5",),
            (
                ClimbingTurn(
                    Turn.RIGHT,
                    Radial(
                        NavaidRef("SEA"),
                        230,
                        outbound=None,
                        until=AtFix(FixRef("CARRO")),
                        intercept=True,
                    ),
                ),
            ),
        ),
    )


def test_proceed_to_a_fix_goes_direct():
    sections = Sections(
        amendment=None,
        takeoff_minimums=None,
        departure_procedure=normalize(
            "Rwy 6, climb heading 057° to 6500, then climbing right turn direct "
            "BQU VOR/DME."
        ),
        vcoa=normalize(
            "Rwys 6, 24, obtain ATC approval for climb in visual conditions when "
            "requesting IFR clearance. Climb in visual conditions to cross Elko "
            "Rgnl airport at or above 7000, then proceed to BQU VOR/DME and "
            "continue climb in BQU VOR/DME holding pattern (hold south, left "
            "turns, 340° inbound) to cross BQU VOR/DME at or above MEA/MCA for "
            "route of flight."
        ),
        obstacle_notes=None,
        dva=None,
    )

    (vcoa,) = parse_procedure(sections, airport="XXX").vcoa

    assert vcoa.then[0] == Direct(NavaidRef("BQU", NavaidType.VOR_DME))


def test_turn_to_a_fix_turns_direct_to_it():
    text = (
        "Rwy 7, climbing left turn to BGQ VORTAC, thence...\n"
        "...climb in BGQ VORTAC holding pattern (hold S, left turn, 356° inbound) "
        "to MCA/MEA for route of flight."
    )

    (group,) = legs_of(text)

    assert group.legs == (
        ClimbingTurn(Turn.LEFT, Direct(NavaidRef("BGQ", NavaidType.VORTAC))),
        Thence(),
    )


def test_fly_heading_is_a_heading_leg():
    sections = Sections(
        amendment=None,
        takeoff_minimums=None,
        departure_procedure=normalize(
            "Rwy 21, DME required, climb on heading 212° to intercept MTJ VOR/DME "
            "R-297 to BRICK/MTJ 23.4 DME..."
            "\n...climb-in-hold (hold NW, RT, 117° inbound) to cross BRICK at or "
            "above 11300."
        ),
        vcoa=normalize(
            "Obtain ATC approval for climb in visual conditions when requesting "
            "IFR clearance. DME required, climb in visual conditions to cross "
            "Blake Fld airport at or above 8300, fly heading 248° and MTJ VOR/DME "
            "R-297 to BRICK/MTJ 23.44 DME, climb-in-hold (hold NW, RT, 117° "
            "inbound) to cross BRICK at or above 11300."
        ),
        obstacle_notes=None,
        dva=None,
    )

    (vcoa,) = parse_procedure(sections, airport="XXX").vcoa

    assert vcoa.then[0] == HeadingAndRadial(
        248,
        NavaidRef("MTJ", NavaidType.VOR_DME),
        297,
        outbound=None,
        until=Dme(NavaidRef("MTJ"), 23.44, FixRef("BRICK")),
    )


def test_until_reaching_an_altitude_ends_the_leg_there():
    text = (
        "Rwy 14, climb heading 136° and ECS VOR R-143, until reaching 5500, "
        "thence…\n...proceed on course."
    )

    (group,) = legs_of(text)

    assert group.legs == (
        HeadingAndRadial(
            136,
            NavaidRef("ECS", NavaidType.VOR),
            143,
            outbound=None,
            until=Altitude(5500, AltitudeKind.TO, "until reaching 5500"),
        ),
        Thence(),
    )


@pytest.mark.parametrize(
    ("text", "legs"),
    [
        pytest.param(
            "Rwy 11, climb heading 096° to 5200, then climbing right turn to 7000 "
            "direct RAP VORTAC.",
            (
                ClimbHeading(96, to(5200)),
                ClimbingTurn(
                    Turn.RIGHT,
                    Direct(NavaidRef("RAP", NavaidType.VORTAC), altitude=to(7000)),
                ),
            ),
            id="49B turn",
        ),
        pytest.param(
            "Rwy 1, climb to 7000 direct IDA VOR/DME, before proceeding on course.",
            (
                Direct(NavaidRef("IDA", NavaidType.VOR_DME), altitude=to(7000)),
                ProceedOnCourse(),
            ),
            id="U02 climb",
        ),
        pytest.param(
            "Rwy 15, climbing left turn direct IGN VOR/DME to 1400 before "
            "proceeding on course.",
            (
                ClimbingTurn(
                    Turn.LEFT,
                    Direct(NavaidRef("IGN", NavaidType.VOR_DME), altitude=to(1400)),
                ),
                ProceedOnCourse(),
            ),
            id="POU after the fix",
        ),
    ],
)
def test_altitude_climbed_to_direct_to_a_fix(text, legs):
    (group,) = legs_of(text)

    assert group.legs == legs


def test_altitude_before_and_after_a_direct_fix_is_refused():
    text = "Rwy 1, climb to 7000 direct IDA VOR/DME to 8000."

    assert signature_of(text) == "leg with two altitudes"


def test_speed_limit_after_proceeding_on_course_limits_the_leg_before():
    text = (
        "Rwys 17L/R, climbing right turn on LAS R-157 to LAS VORTAC before "
        "proceeding on course, do not exceed 200K until passing LAS VORTAC."
    )

    (group,) = legs_of(text)

    assert group.legs == (
        ClimbingTurn(
            Turn.RIGHT,
            Radial(
                NavaidRef("LAS"),
                157,
                outbound=False,
                until=AtFix(NavaidRef("LAS", NavaidType.VORTAC)),
                speed=SpeedRestriction(200, "passing LAS VORTAC"),
            ),
        ),
        ProceedOnCourse(),
    )


M57 = (
    "Rwy 6, climb heading 075° to 4700 before proceeding on course. For climb in "
    "visual conditions, cross Rangeley Lake airport at or above 4000 before "
    "proceeding on course, do not exceed 210 KIAS until leaving 4000. When "
    "executing VCOA, notify ATC prior to departure."
)


def test_speed_limit_after_a_visual_climb_that_only_goes_on_course_limits_it():
    sections = Sections(
        amendment=None,
        takeoff_minimums=None,
        departure_procedure=normalize(M57),
        vcoa=None,
        obstacle_notes=None,
        dva=None,
    )

    (vcoa,) = parse_procedure(sections, airport="XXX").vcoa

    assert vcoa.then == (ProceedOnCourse(),)
    assert vcoa.speed == SpeedRestriction(210, "leaving 4000")


def test_speed_limit_after_a_route_that_only_goes_on_course_is_refused():
    with pytest.raises(ParseError):
        legs_of(
            "Rwy 6, before proceeding on course, do not exceed 210 KIAS until "
            "leaving 4000."
        )


def test_speed_limit_until_established_direct_a_navaid():
    text = (
        "Rwy 22, climbing right turn to intercept RIW VOR R-220 to 8500 before "
        "proceeding on course. Do not exceed 240K until established direct RIW VOR."
    )

    (group,) = legs_of(text)

    assert group.legs[0].then.speed == SpeedRestriction(
        240, "established direct RIW VOR"
    )


def test_before_proceeding_bound_on_a_radial_flies_it():
    sections = Sections(
        amendment=None,
        takeoff_minimums=None,
        departure_procedure=normalize(
            "Rwy 14, climb to 2500 on a heading between 110° CW to 315° from DER "
            "before proceeding on course."
        ),
        vcoa=normalize(
            "Obtain ATC approval for VCOA when requesting IFR clearance.\n"
            "Rwys 2, 14, 20, 32, climb in visual conditions to cross Charles M "
            "Schulz/Sonoma County airport at or above 1400 before proceeding "
            "southbound on PYE R-349 to PYE VOR/DME."
        ),
        obstacle_notes=None,
        dva=None,
    )

    (vcoa,) = parse_procedure(sections, airport="XXX").vcoa

    assert vcoa.then == (
        Radial(
            NavaidRef("PYE"),
            349,
            outbound=False,
            until=AtFix(NavaidRef("PYE", NavaidType.VOR_DME)),
        ),
    )


def test_before_proceeding_across_a_radial_is_refused():
    text = (
        "Rwy 14, climb heading 140° to 2500 before proceeding eastbound on PYE "
        "R-349 to PYE VOR/DME."
    )

    assert signature_of(text) == "direction of flight across the radial"


def test_navaid_named_in_mixed_case_before_its_ident():
    text = (
        "Rwy 31, climb via heading 311° to 3000, then climbing left turn via "
        "Linden (LIN) VOR/DME R-029 to LIN VOR/DME."
    )

    (group,) = legs_of(text)

    assert group.legs[1] == ClimbingTurn(
        Turn.LEFT,
        Radial(
            NavaidRef("LIN", NavaidType.VOR_DME, "Linden"),
            29,
            outbound=False,
            until=AtFix(NavaidRef("LIN", NavaidType.VOR_DME)),
        ),
    )


ABQ = (
    "Rwy 8, climbing right turn direct ABQ VORTAC. If required, continue climb in "
    "ABQ VORTAC holding pattern (hold W, left turns, 077° inbound) to cross ABQ "
    "VORTAC at or above MEA/MCA for route of flight. (Do not exceed 250 KTS until "
    "ABQ VORTAC)."
)


def test_speed_limit_until_a_fix_binds_the_leg_before_a_hold_there():
    (group,) = legs_of(ABQ)

    abq = NavaidRef("ABQ", NavaidType.VORTAC)
    assert group.legs[0] == ClimbingTurn(
        Turn.RIGHT, Direct(abq, SpeedRestriction(250, "ABQ VORTAC"))
    )


def test_speed_limit_until_another_fix_does_not_skip_the_hold():
    text = ABQ.replace("until ABQ VORTAC", "until XYZ VORTAC")

    assert signature_of(text) == "speed restriction after ClimbInHold"


INW = (
    "Rwy 22, climb to 6000, then right turn direct INW VORTAC and hold, thence...\n"
    "Rwys 11, 29, climb to 6000, then direct INW VORTAC and hold, thence...\n"
    "...continue climb-in-hold (hold West, right turns, 068° inbound) to 8900 "
    "before proceeding on course."
)


def test_hold_announced_before_thence_is_flown_in_the_shared_tail():
    procedure = parse_departure_procedure(normalize(INW), airport="XXX", amendment=None)

    inw = NavaidRef("INW", NavaidType.VORTAC)
    assert procedure.runway_groups[1].legs == (
        StraightAhead(to(6000)),
        Direct(inw),
        Thence(),
    )
    assert procedure.shared_tail == (
        ClimbInHold(inw, HoldSpec(Compass8.W, Turn.RIGHT, 68), to(8900)),
        ProceedOnCourse(),
    )


def test_hold_announced_before_thence_needs_the_tail_to_fly_it():
    text = INW.replace(
        "...continue climb-in-hold (hold West, right turns, 068° inbound) to 8900",
        "...climb on heading 068° to 8900",
    )

    assert signature_of(text) == "announced hold not flown in the shared tail"


SHN = (
    "Rwy 5, climbing right turn to intercept SEA R-230 to CARRO intersection. Or "
    "for climb in visual conditions: cross Sanderson Fld northeast bound at or "
    "above 1600, thence via SEA R-230 to cross CARRO intersection at or above 4000."
)


def test_thence_before_a_leg_on_its_line_is_then():
    sections = Sections(
        amendment=None,
        takeoff_minimums=None,
        departure_procedure=normalize(SHN),
        vcoa=None,
        obstacle_notes=None,
        dva=None,
    )

    (vcoa,) = parse_procedure(sections, airport="XXX").vcoa

    carro = FixRef("CARRO")
    assert vcoa.then == (
        Radial(NavaidRef("SEA"), 230, outbound=None, until=AtFix(carro)),
        CrossAt(carro, Altitude(4000, AltitudeKind.AT_OR_ABOVE, "at or above 4000")),
    )


def test_thence_ending_its_line_still_needs_an_ellipsis():
    text = "Rwy 5, climbing right turn heading 200°, thence\nclimb direct SEA VORTAC."

    with pytest.raises(ParseError):
        legs_of(text)


BVS = (
    "Rwy 11, climbing right turn heading 245° to intercept CVV VOR/DME R-022 "
    "thence…\n"
    "Rwy 29, climb heading 290° to 1000, then climbing left turn on heading 165° "
    "to intercept CVV R-015, thence…\n"
    "...to CVV VOR/DME, climb in holding pattern (hold south, left turns, 346° "
    "inbound) to cross CVV VOR/DME at or above MEA for route of flight."
)


def test_tail_opening_with_a_terminator_ends_the_radials_entering_it():
    procedure = parse_departure_procedure(normalize(BVS), airport="XXX", amendment=None)

    cvv = NavaidRef("CVV", NavaidType.VOR_DME)
    assert procedure.runway_groups[0].legs == (
        ClimbingTurn(
            Turn.RIGHT,
            HeadingAndRadial(245, cvv, 22, outbound=False, until=AtFix(cvv)),
        ),
        Thence(),
    )
    assert procedure.runway_groups[1].legs[-2] == ClimbingTurn(
        Turn.LEFT,
        HeadingAndRadial(165, NavaidRef("CVV"), 15, outbound=False, until=AtFix(cvv)),
    )
    assert isinstance(procedure.shared_tail[0], ClimbInHold)


def test_tail_opening_with_a_terminator_for_another_navaid_is_refused():
    text = BVS.replace("...to CVV VOR/DME,", "...to ABC VOR/DME,")

    assert signature_of(text) == "tail opens with a terminator for no radial"
