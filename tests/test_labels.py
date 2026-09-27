"""Tests for odp_kml.labels: charted altitude constraints."""

import pytest

from odp_kml.labels import format_altitude
from odp_kml.procedure import Altitude, AltitudeKind

LOW_LINE = "̲"
OVERLINE = "̅"


def decorated(digits: str, marks: str) -> str:
    return "".join(digit + marks for digit in digits)


@pytest.mark.parametrize(
    ("kind", "style", "expected"),
    [
        (AltitudeKind.AT_OR_ABOVE, "chart", decorated("7000", LOW_LINE)),
        (AltitudeKind.AT_OR_BELOW, "chart", decorated("7000", OVERLINE)),
        (AltitudeKind.AT, "chart", decorated("7000", LOW_LINE + OVERLINE)),
        (AltitudeKind.AT_OR_ABOVE, "fms", "7000A"),
        (AltitudeKind.AT_OR_BELOW, "fms", "7000B"),
        (AltitudeKind.AT, "fms", "7000"),
    ],
)
def test_format_altitude(kind, style, expected):
    assert format_altitude(Altitude(7000, kind, "7000"), style) == expected
