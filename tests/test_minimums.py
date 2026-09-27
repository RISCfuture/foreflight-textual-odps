"""Extracting published minimum climb gradients from TAKEOFF MINIMUMS text."""

from odp_kml.minimums import parse_takeoff_minimums


def test_parses_takeoff_minimum_climb_gradients():
    text = (
        "Rwys 11, 29, NA - ATC. Rwy 15, std. with a min. climb of 320 ft per NM to "
        "9100 or 2500-3 for VCOA. Rwys 4, 35, 300-1 or std. with a min. climb of "
        "352 ft per NM to 9100."
    )

    assert parse_takeoff_minimums(text) == {"15": 320.0, "4": 352.0, "35": 352.0}
