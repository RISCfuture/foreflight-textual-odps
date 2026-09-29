"""Extracting published minimum climb gradients from TAKEOFF MINIMUMS text."""

from odp_kml.minimums import parse_takeoff_minimums, speed_limit_for


def test_parses_takeoff_minimum_climb_gradients():
    text = (
        "Rwys 11, 29, NA - ATC. Rwy 15, std. with a min. climb of 320 ft per NM to "
        "9100 or 2500-3 for VCOA. Rwys 4, 35, 300-1 or std. with a min. climb of "
        "352 ft per NM to 9100."
    )

    assert parse_takeoff_minimums(text) == {"15": 320.0, "4": 352.0, "35": 352.0}


def test_speed_limit_binds_the_runways_its_line_names_or_else_every_runway():
    text = (
        "Rwy 10, std. with a min. climb of 400 ft per NM to 4500, do not exceed "
        "210 KIAS until intercepting the ENI R-073.\nRwy 28, NA-Rapidly rising terrain."
    )

    assert speed_limit_for(text, ("10",)) == text.splitlines()[0]
    assert speed_limit_for(text, ("28",)) is None
    assert speed_limit_for(text, ()) is not None
    assert speed_limit_for("NOTE: maximum 180K for VCOA.", ("28",)) is not None
