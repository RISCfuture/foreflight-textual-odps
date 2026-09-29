"""Tests for odp_kml.labels: altitude, hold, VCOA and heading-range label
wording."""

import pytest

from odp_kml.labels import (
    format_altitude,
    heading_range_lines,
    heading_range_phrases,
    hold_label,
    not_shown_lines,
    speed_label,
    vcoa_label,
)
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    Compass8,
    EnrouteAltitude,
    HeadingRange,
    HeadingSector,
    HoldSpec,
    SpeedRestriction,
    Turn,
)


@pytest.mark.parametrize(
    ("kind", "style", "expected"),
    [
        (AltitudeKind.TO, "plain", "7000'"),
        (AltitudeKind.AT_OR_ABOVE, "plain", "≥7000'"),
        (AltitudeKind.AT_OR_BELOW, "plain", "≤7000'"),
        (AltitudeKind.AT, "plain", "at 7000'"),
        (AltitudeKind.AT_OR_ABOVE, "fms", "7000A"),
        (AltitudeKind.AT_OR_BELOW, "fms", "7000B"),
        (AltitudeKind.AT, "fms", "7000"),
        (AltitudeKind.TO, "fms", "7000"),
    ],
)
def test_format_altitude(kind, style, expected):
    assert format_altitude(Altitude(7000, kind, "7000"), style) == expected


@pytest.mark.parametrize(
    ("names", "feet", "kind", "style", "expected"),
    [
        (("MEA", "MCA"), None, AltitudeKind.AT_OR_ABOVE, "plain", "≥MEA/MCA"),
        (("MEA",), None, AltitudeKind.TO, "plain", "MEA"),
        (("MEA", "MCA"), None, AltitudeKind.AT_OR_ABOVE, "fms", "MEA/MCA A"),
        (("MCA",), None, AltitudeKind.TO, "fms", "MCA"),
        (("MEA",), 4000, AltitudeKind.AT_OR_ABOVE, "plain", "≥4000/MEA"),
        (("MEA",), 4000, AltitudeKind.AT_OR_ABOVE, "fms", "4000/MEA A"),
    ],
)
def test_format_enroute_minimum(names, feet, kind, style, expected):
    altitude = EnrouteAltitude(names, kind, "phrase", feet)

    assert format_altitude(altitude, style) == expected


def test_hold_label_gives_inbound_course_turns_and_altitude():
    until = Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300")
    spec = HoldSpec(None, Turn.LEFT, 46)
    assert hold_label(spec, until, "plain") == "Hold 046° LT ≥9300'"
    assert hold_label(spec, None, "plain") == "Hold 046° LT"
    mea = EnrouteAltitude(("MEA",), AltitudeKind.AT_OR_ABOVE, "at or above MEA")
    assert hold_label(spec, mea, "plain") == "Hold 046° LT ≥MEA"


def test_vcoa_label_wraps_the_altitude_in_parentheses():
    assert vcoa_label(7800, "plain") == "VCOA (≥7800')"
    assert vcoa_label(8200, "plain", Compass8.SE) == "VCOA (≥8200' SE bound)"
    assert vcoa_label(12500, "plain", Compass8.SE) == "VCOA (≥12500')"


@pytest.mark.parametrize(
    ("until_phrase", "expected"),
    [
        ("reaching 9000 MSL", "≤200 KIAS until 9000'"),
        ("established on course", "≤200 KIAS"),
    ],
)
def test_speed_label_gives_only_a_reaching_altitude_in_feet(until_phrase, expected):
    assert speed_label(SpeedRestriction(200, until_phrase)) == expected


def test_heading_range_phrases_break_between_sectors_and_before_the_altitude():
    leg = HeadingRange(
        (
            HeadingSector(256, 54, clockwise=True),
            HeadingSector(213, 353, clockwise=False),
        ),
        Altitude(7700, AltitudeKind.TO, "to 7700"),
    )

    assert heading_range_phrases(leg, Turn.LEFT, "plain") == (
        "LT hdg 256° CW 054°",
        "or 213° CCW 353°",
        "7700'",
    )


@pytest.mark.parametrize(
    ("runways", "phrases", "expected"),
    [
        (("7",), ("hdg 315° CW 218°",), ["7 hdg 315° CW 218°"]),
        (("11",), ("hdg 320° CW 220°", "3000'"), ["11 320° CW 220° 3000'"]),
        (
            ("30",),
            ("LT hdg 267° CW 300°", "10000'"),
            ["30 LT hdg 267° CW 300°", "10000'"],
        ),
        (("35R", "35L"), ("hdg 313° CW 172°",), ["35L/R hdg 313° CW 172°"]),
        (("16L", "16R"), ("hdg 213° CCW 353°",), ["16L/R 213° CCW 353°"]),
        (
            ("16L", "16R", "17L"),
            ("LT hdg 213° CCW 353°",),
            ["RWY 16L/R,17L", "LT hdg 213° CCW 353°"],
        ),
        (
            ("25",),
            ("hdg 317° CW 083°", "or 206° CCW 083°"),
            ["25 hdg 317° CW 083°", "or 206° CCW 083°"],
        ),
        (
            ("16L", "16R", "17L", "17R", "34L", "34R", "35L", "35R"),
            ("hdg 313° CW 172°",),
            ["hdg 313° CW 172°"],
        ),
        ((), ("hdg 107° CW 250°",), ["ALL RWYS 107° CW 250°"]),
    ],
)
def test_heading_range_lines_fill_as_few_whole_foreflight_labels_as_they_can(
    runways, phrases, expected
):
    """Parallels share their number; the runways lead the first line, which
    drops its "hdg" only to make room for them or save a line, else stand
    apart, and are left out when too many to name."""
    assert heading_range_lines(runways, phrases) == expected


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (["RWY 17L/17R", "VCOA"], ["ODP NOT SHOWN", "RWY 17L/17R, VCOA"]),
        (
            ["RWY 17L/17R", "RWY 35L/35R"],
            ["ODP NOT SHOWN", "RWY 17L/17R,", "RWY 35L/35R"],
        ),
        (
            ["RWY 1L/1R/14L/14R/19L/19R", "RWY 32L/32R", "VCOA"],
            ["ODP NOT SHOWN", "RWY 1L/1R/14L/14R/19L/", "19R, RWY 32L/32R, VCOA"],
        ),
    ],
)
def test_not_shown_lines_keep_every_part_whole_in_foreflight(parts, expected):
    """No line is elided: parts pack into lines of at most 22 characters,
    and only a part too long for a line breaks, after a "/"."""
    assert not_shown_lines(parts) == expected
