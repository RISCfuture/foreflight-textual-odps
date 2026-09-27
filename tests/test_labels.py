"""Tests for odp_kml.labels: altitude, hold and VCOA label wording."""

import pytest

from odp_kml.labels import format_altitude, hold_label, vcoa_label
from odp_kml.procedure import Altitude, AltitudeKind


@pytest.mark.parametrize(
    ("kind", "style", "expected"),
    [
        (AltitudeKind.TO, "plain", "7000'"),
        (AltitudeKind.AT_OR_ABOVE, "plain", "at or above 7000'"),
        (AltitudeKind.AT_OR_BELOW, "plain", "at or below 7000'"),
        (AltitudeKind.AT, "plain", "at 7000'"),
        (AltitudeKind.AT_OR_ABOVE, "fms", "7000A"),
        (AltitudeKind.AT_OR_BELOW, "fms", "7000B"),
        (AltitudeKind.AT, "fms", "7000"),
        (AltitudeKind.TO, "fms", "7000"),
    ],
)
def test_format_altitude(kind, style, expected):
    assert format_altitude(Altitude(7000, kind, "7000"), style) == expected


def test_hold_label_wraps_the_altitude_in_parentheses():
    until = Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300")
    assert hold_label(until, "plain") == "Hold (at or above 9300')"
    assert hold_label(None, "plain") == "Hold"


def test_vcoa_label_wraps_the_altitude_in_parentheses():
    assert vcoa_label(7800, "plain") == "VCOA (at or above 7800')"
