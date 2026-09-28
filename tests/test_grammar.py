"""Parsing DEPARTURE PROCEDURE and VCOA text into the procedure AST."""

import json
from pathlib import Path

import pytest

from odp_kml.grammar import (
    ParseError,
    parse_departure_procedure,
    parse_procedure,
    parse_vcoa,
)
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    AtFix,
    ClimbHeading,
    ClimbingTurn,
    ClimbInHold,
    Compass8,
    CrossRadial,
    Direct,
    Dme,
    FixRef,
    GraphicDeparture,
    HeadingAndRadial,
    HoldSpec,
    NavaidRef,
    NavaidType,
    Procedure,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
    SpeedRestriction,
    Turn,
    VcoaGroup,
    from_dict,
)
from odp_kml.sections import Sections

FIXTURES = Path(__file__).parent / "fixtures" / "odp_text"
GOLDEN = sorted((Path(__file__).parent / "fixtures" / "golden").glob("*.json"))


def fixture_text(name: str) -> str | None:
    path = FIXTURES / f"{name}.txt"
    return path.read_text().strip() if path.exists() else None


def expected_procedure(name: str) -> Procedure:
    return Procedure.from_json((FIXTURES / f"{name}.expected.json").read_text())


def fixture_sections(name: str, amendment: str | None) -> Sections:
    return Sections(
        amendment=amendment,
        takeoff_minimums=None,
        departure_procedure=fixture_text(name),
        vcoa=fixture_text(f"{name}.vcoa"),
        obstacle_notes=None,
        dva=None,
    )


def signature_of(text: str) -> str:
    with pytest.raises(ParseError) as error:
        parse_departure_procedure(text, airport="XXX", amendment=None)
    return error.value.signature


def to(feet: int) -> Altitude:
    return Altitude(feet, AltitudeKind.TO, f"to {feet}")


@pytest.mark.parametrize("name", ["tph", "alb", "bam"])
def test_parses_fixture_procedures(name):
    expected = expected_procedure(name)
    sections = fixture_sections(name, expected.amendment)

    assert parse_procedure(sections, airport=name.upper()) == expected


@pytest.mark.parametrize(
    ("text", "groups", "shared_tail"),
    [
        pytest.param(
            "Rwy 22, NA - Obstacles. Rwys 2L/R, NA-ATC.\n"
            "Rwy 4: Climb heading 154° to 2500 before turning left.",
            [
                RunwayGroup(("22",), ()),
                RunwayGroup(("2L", "2R"), ()),
                RunwayGroup(
                    ("4",), (ClimbHeading(154, to(2500)), ProceedOnCourse(Turn.LEFT))
                ),
            ],
            None,
            id="NA groups and climb heading without on",
        ),
        pytest.param(
            "Rwy 16, climb via hdg 154° to 2500, do not exceed 200 KIAS until "
            "established on course.",
            [
                RunwayGroup(
                    ("16",),
                    (
                        ClimbHeading(
                            154,
                            to(2500),
                            speed=SpeedRestriction(200, "established on course"),
                        ),
                    ),
                )
            ],
            None,
            id="speed restriction until established on course",
        ),
        pytest.param(
            "Rwy 4, climb on heading 036° and BAM VORTAC R-036 outbound to BAM "
            "12.5 DME, then climbing LT direct PIXIE.",
            [
                RunwayGroup(
                    ("4",),
                    (
                        HeadingAndRadial(
                            36,
                            NavaidRef("BAM", NavaidType.VORTAC),
                            36,
                            outbound=True,
                            until=Dme(NavaidRef("BAM"), 12.5),
                        ),
                        ClimbingTurn(Turn.LEFT, Direct(FixRef("PIXIE"))),
                    ),
                )
            ],
            None,
            id="DME terminator and fix",
        ),
        pytest.param(
            "Rwy 8, climb heading 080° to cross IPL VORTAC R-009 before turning north.",
            [
                RunwayGroup(
                    ("8",),
                    (
                        ClimbHeading(
                            80, CrossRadial(NavaidRef("IPL", NavaidType.VORTAC), 9)
                        ),
                        ProceedOnCourse(),
                    ),
                )
            ],
            None,
            id="cross radial",
        ),
        pytest.param(
            "Rwy 4, climb direct XY NDB, continue climb in holding pattern "
            "(hold northwest, left turns, 045° inbound) to 3000 before proceeding "
            "on course.",
            [
                RunwayGroup(
                    ("4",),
                    (
                        Direct(NavaidRef("XY", NavaidType.NDB)),
                        ClimbInHold(
                            NavaidRef("XY", NavaidType.NDB),
                            HoldSpec(Compass8.NW, Turn.LEFT, 45),
                            to(3000),
                        ),
                        ProceedOnCourse(),
                    ),
                )
            ],
            None,
            id="hold at the preceding fix",
        ),
    ],
)
def test_parses_leg_shapes(text, groups, shared_tail):
    procedure = parse_departure_procedure(text, airport="XXX", amendment=None)

    assert procedure.runway_groups == tuple(groups)
    assert procedure.shared_tail == shared_tail


@pytest.mark.parametrize(
    ("text", "signature"),
    [
        (fixture_text("bur"), 'unsupported routing "then westbound on"'),
        (fixture_text("bwc"), 'unsupported "to <alt> to <fix>" double terminator'),
        (fixture_text("3u3"), 'unsupported "to <alt>" with fix terminator'),
        (
            (
                "Rwy 7, climb direct CHE VOR/DME, or for climb in visual "
                "conditions, cross Craig-Moffat Airport at or above 8500 then proceed "
                "on CHE R-247 to CHE VOR/DME, thence ... ...climb on course."
            ),
            "visual climb into the shared tail",
        ),
        (
            "For climb in visual conditions: cross Central Airport at or above 3700.",
            "visual climb without a runway",
        ),
        (
            "All runways, climb heading 360° to 5000.",
            'unsupported "all runways" group',
        ),
        (
            "Use published departure procedures.",
            'unmatched phrase "published departure procedures."',
        ),
        ("Rwy 15, climb direct", 'unexpected end after "direct"'),
        ("Rwy 15, climb direct TPH", 'unexpected end after "<id>"'),
        (
            (
                "Rwy 17, climbing left turn heading 100° to intercept TRM VORTAC R-136 "
                "to MECCA, thence... ...climb on course."
            ),
            'radial without "inbound" or "outbound"',
        ),
        (
            (
                "Rwy 16, climb heading 154° to 2500, do not exceed 200 KIAS until "
                "turning left direct ABC VOR."
            ),
            'unmatched phrase "turning left direct <id>"',
        ),
        (
            (
                "Rwy 4, climb direct ABC VOR, then climb via ABC R-090 outbound to "
                "5000, continue climb-in-hold to 6000 (north, left turns, 270° inbound)."
            ),
            "climb in hold without a preceding fix",
        ),
        (
            (
                "Rwy 33, climb heading 330° to 5000. Rwy 22, NA - ATC. Rwy 15, "
                "climbing left turn direct TPH VORTAC thence... ...climb on course."
            ),
            '"..." without a preceding "thence"',
        ),
        (
            "Rwy 8, climb heading 080° and ABC VOR R-080 outbound to MECCA 12.0 DME.",
            "DME from a fix",
        ),
        (
            "Rwy 20, climb on ALW VOR/DME R-201 to 2500.",
            'radial without "inbound" or "outbound"',
        ),
        (
            "Rwy 15, climbing left turn direct TPH VORTAC thence...",
            '"thence" without a shared tail',
        ),
    ],
)
def test_rejects_unsupported_text(text, signature):
    assert signature_of(text) == signature


@pytest.mark.parametrize(
    ("text", "groups"),
    [
        pytest.param(
            fixture_text("afo"),
            [
                RunwayGroup(("16",), (GraphicDeparture("LUNDI"),)),
                RunwayGroup(("34",), (GraphicDeparture("AFTON"),)),
            ],
            id="one charted DP per runway",
        ),
        pytest.param(
            "Use VAMPS (RNAV) DEPARTURE.",
            [RunwayGroup((), (GraphicDeparture("VAMPS (RNAV)"),))],
            id="every runway",
        ),
        pytest.param(
            "Use COEUR D'ALENE DEPARTURE (RNAV1).",
            [RunwayGroup((), (GraphicDeparture("COEUR D'ALENE"),))],
            id="apostrophe and trailing qualifier",
        ),
        pytest.param(
            "See EBSIH DEPARTURE.",
            [RunwayGroup((), (GraphicDeparture("EBSIH"),))],
            id="see",
        ),
        pytest.param(
            "All Rwys, use NASWI TWO (OBSTACLE) DEPARTURE.",
            [RunwayGroup((), (GraphicDeparture("NASWI TWO (OBSTACLE)"),))],
            id="all runways",
        ),
        pytest.param(
            "Rwy 8, climb heading 080° to 5000 before proceeding on course.\n"
            "Rwy 26, use SQUAT DEPARTURE.",
            [
                RunwayGroup(("8",), (ClimbHeading(80, to(5000)), ProceedOnCourse())),
                RunwayGroup(("26",), (GraphicDeparture("SQUAT"),)),
            ],
            id="beside a text procedure",
        ),
    ],
)
def test_parses_charted_dp_references(text, groups):
    procedure = parse_departure_procedure(text, airport="XXX", amendment=None)

    assert procedure.runway_groups == tuple(groups)
    assert procedure.graphic_only == all(group.graphic for group in groups)


def _vcoa(runways, feet, then=None, cross=None, bound=None):
    return VcoaGroup(runways, cross, feet, then or (ProceedOnCourse(),), bound)


@pytest.mark.parametrize(
    ("text", "groups", "vcoa"),
    [
        pytest.param(
            "Rwy 15, climb heading 148° to 1100 before proceeding on course or "
            "for climb in visual conditions: cross Central Maine/ Norridgewock "
            "at or above 1500 before proceeding on course.",
            [RunwayGroup(("15",), (ClimbHeading(148, to(1100)), ProceedOnCourse()))],
            [_vcoa(("15",), 1500)],
            id="or-alternative after the legs",
        ),
        pytest.param(
            "Rwy 10, climbing right turn heading 120° to 2000 before turning north, "
            "or for climb in visual conditions, cross Cyril E King airport at or "
            "above 2000 before proceeding on course. When executing VCOA, notify "
            "ATC prior to departure.\nRwy 28, climb heading 280° to 2000 before "
            "turning north.",
            [
                RunwayGroup(
                    ("10",),
                    (
                        ClimbingTurn(Turn.RIGHT, ClimbHeading(120, to(2000))),
                        ProceedOnCourse(),
                    ),
                ),
                RunwayGroup(("28",), (ClimbHeading(280, to(2000)), ProceedOnCourse())),
            ],
            [_vcoa(("10",), 2000)],
            id="comma-or alternative and notify ATC",
        ),
        pytest.param(
            "Rwys 8, 26, for climb in visual conditions, cross Central Airport "
            "at or above 3700 before proceeding on course.",
            [],
            [_vcoa(("8", "26"), 3700)],
            id="visual climb as the runway's only procedure",
        ),
        pytest.param(
            "Rwy 10, climb on heading 147° to 12200 before proceeding on course.\n"
            "Rwys 10, 28, for climb in visual conditions: cross Yampa Valley "
            "airport at or above 9700 before proceeding on course.",
            [RunwayGroup(("10",), (ClimbHeading(147, to(12200)), ProceedOnCourse()))],
            [_vcoa(("10", "28"), 9700)],
            id="separate sentence per runway list",
        ),
        pytest.param(
            "Rwy 36, climb heading 360° to 1200 before proceeding on course. For "
            "climb in visual conditions: cross Augusta Rgnl at Bush Fld airport at "
            "or above 1700 MSL, before proceeding on course.",
            [RunwayGroup(("36",), (ClimbHeading(360, to(1200)), ProceedOnCourse()))],
            [_vcoa(("36",), 1700)],
            id="sentence alternative, connector in the name, comma before",
        ),
        pytest.param(
            "Rwy 3, climbing right turn heading 160° to 9000 before proceeding on "
            "course, or for climb in visual conditions: Cross Durango-La Plata "
            "County Airport Southeast bound at or above 8200 MSL, then proceed on "
            "DRO VOR/DME R-125 outbound to RESER.",
            [
                RunwayGroup(
                    ("3",),
                    (
                        ClimbingTurn(Turn.RIGHT, ClimbHeading(160, to(9000))),
                        ProceedOnCourse(),
                    ),
                )
            ],
            [
                _vcoa(
                    ("3",),
                    8200,
                    (
                        Radial(
                            NavaidRef("DRO", NavaidType.VOR_DME),
                            125,
                            True,
                            AtFix(FixRef("RESER")),
                        ),
                    ),
                    bound=Compass8.SE,
                )
            ],
            id="crossing direction",
        ),
        pytest.param(
            "All runways, obtain ATC approval for VCOA when requesting IFR "
            "clearance. Climb in visual conditions to cross ADKIN westbound at or "
            "above 5600 before proceeding on course.",
            [],
            [_vcoa((), 5600, cross=FixRef("ADKIN"), bound=Compass8.W)],
            id="full VCOA wording inside the section, crossing a fix",
        ),
    ],
)
def test_parses_visual_climbs_written_into_the_departure_procedure(text, groups, vcoa):
    procedure = parse_departure_procedure(text, airport="XXX", amendment=None)

    assert procedure.runway_groups == tuple(groups)
    assert procedure.vcoa == tuple(vcoa)


def test_inline_visual_climbs_come_before_the_vcoa_section():
    sections = Sections(
        amendment=None,
        takeoff_minimums=None,
        departure_procedure=(
            "Rwy 4, climb heading 040° to 3000 before proceeding on course, or for "
            "climb in visual conditions: cross Test airport at or above 2500 before "
            "proceeding on course."
        ),
        vcoa=(
            "Rwy 22, obtain ATC approval for VCOA when requesting IFR clearance. "
            "Climb in visual conditions to cross Test airport at or above 2600 "
            "before proceeding on course."
        ),
        obstacle_notes=None,
        dva=None,
    )

    procedure = parse_procedure(sections, airport="XXX")

    assert procedure.vcoa == (_vcoa(("4",), 2500), _vcoa(("22",), 2600))


def test_vcoa_section_repeating_an_inline_visual_climb_is_drawn_once():
    sections = Sections(
        amendment=None,
        takeoff_minimums=None,
        departure_procedure=(
            "Rwy 10, for climb in visual conditions, cross Test airport at or above "
            "1400 before proceeding on course."
        ),
        vcoa=(
            "Rwy 10, obtain ATC approval for VCOA when requesting IFR clearance. "
            "Climb in visual conditions to cross Test airport at or above 1400 "
            "before proceeding on course."
        ),
        obstacle_notes=None,
        dva=None,
    )

    assert parse_procedure(sections, airport="XXX").vcoa == (_vcoa(("10",), 1400),)


@pytest.mark.parametrize(
    "until",
    ["reaching 4000", "crossing ABC VOR", "ABC VOR", "12 DME", "established on course"],
)
def test_speed_restriction_until_named_shapes(until):
    text = f"Rwy 16, climb heading 154° to 2500, do not exceed 200 KIAS until {until}."

    (group,) = parse_departure_procedure(
        text, airport="X", amendment=None
    ).runway_groups

    assert group.legs == (ClimbHeading(154, to(2500), SpeedRestriction(200, until)),)


def test_rejects_any_unconsumed_word():
    text = "Rwy 10, climb on heading 110° to 2000 before turning north banana."

    with pytest.raises(ParseError) as error:
        parse_departure_procedure(text, airport="ALB", amendment=None)

    assert error.value.signature == 'unmatched phrase "banana."'
    assert error.value.position == text.index("banana")


def test_vcoa_navaid_named_without_ident_needs_the_departure_procedure():
    with pytest.raises(ParseError) as error:
        parse_vcoa(fixture_text("tph.vcoa"))

    assert error.value.signature == "navaid without ident"
    assert error.value.detail == "TONOPAH VORTAC"


def test_parses_vcoa_for_all_runways():
    text = (
        "All runways, obtain ATC approval for VCOA when requesting IFR clearance. "
        "Climb in visual conditions to cross Roanoke/Blacksburg Rgnl (Woodrum Fld) "
        "at or above 3600 MSL before proceeding on course. When executing VCOA, "
        "notify ATC prior to departure."
    )

    assert parse_vcoa(text) == (VcoaGroup((), None, 3600, (ProceedOnCourse(),)),)


def test_vcoa_crossing_a_navaid_is_not_the_airport():
    text = (
        "All runways, obtain ATC approval for VCOA when requesting IFR clearance. "
        "Climb in visual conditions to cross CVO VOR/ DME at or above 3500 before "
        "proceeding on course."
    )

    with pytest.raises(ParseError) as error:
        parse_vcoa(text)

    assert error.value.signature == "unsupported VCOA crossing a navaid"


@pytest.mark.parametrize(
    "path",
    GOLDEN
    or [pytest.param(None, marks=pytest.mark.skip(reason="no reviewed golden files"))],
    ids=lambda path: path.stem if path else "none",
)
def test_golden_set_parses_to_its_reviewed_ast_or_is_refused(path):
    """Each reviewed golden file parses to exactly its stored AST, or not at all.

    A golden file is ``tests/fixtures/golden/<LID>.json``: a draft written by
    ``tools/draft_golden.py`` after human review, keeping the keys ``lid``,
    ``amendment``, ``departure_procedure`` and ``vcoa`` (normalized section
    text, either may be null) and ``procedure`` (the reviewed `Procedure` in
    `procedure.to_dict` form). Refusing with `ParseError` passes, because a
    refused procedure is reported rather than drawn; a different AST fails.
    """
    golden = json.loads(path.read_text())
    sections = Sections(
        amendment=golden["amendment"],
        takeoff_minimums=None,
        departure_procedure=golden["departure_procedure"],
        vcoa=golden["vcoa"],
        obstacle_notes=None,
        dva=None,
    )
    try:
        parsed = parse_procedure(sections, airport=golden["lid"])
    except ParseError:
        return

    assert parsed == from_dict(golden["procedure"])
