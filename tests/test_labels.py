"""Tests for odp_kml.labels: altitude, hold and VCOA label wording."""

import pytest

from odp_kml.labels import format_altitude, hold_label, speed_label, vcoa_label
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    Compass8,
    EnrouteAltitude,
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


@pytest.mark.parametrize(
    ("until_phrase", "expected"),
    [
        ("reaching 9000 MSL", "max 200 KIAS until 9000'"),
        ("established on course", "max 200 KIAS until established on course"),
    ],
)
def test_speed_label_spells_a_reaching_altitude_in_feet(until_phrase, expected):
    assert speed_label(SpeedRestriction(200, until_phrase)) == expected
