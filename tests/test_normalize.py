"""Text normalization of Takeoff-Minimums block text."""

import pytest

from odp_kml.normalize import normalize


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("515’ from DER", "515 from DER"),
            ("the ‘A’ and “B”", "the 'A' and \"B\""),
            ("heading 325º CW", "heading 325° CW"),
            ("thence…", "thence..."),
            ("std. w/min. climb", "std. with a min. climb"),
            ("std. w/ min. climb", "std. with a min. climb"),
            ("std w/min climb", "std with a min. climb"),
            ("std. with a min. climb", "std. with a min. climb"),
            ("300-1¼", "300-1 1/4"),
            ("400-2½", "400-2 1/2"),
            ("2600-2¾ for VCOA", "2600-2 3/4 for VCOA"),
            ("¾ SM", "3/4 SM"),
            ("⅛ ⅜ ⅝ ⅞", "1/8 3/8 5/8 7/8"),
            ("at or above 9800' MSL", "at or above 9800 MSL"),
            ("climb of 320' per NM", "climb of 320 ft per NM"),
            ("climb of 230’/NM", "climb of 230 ft/NM"),
            ("Rwys 13, 31 ,\t 2600-3", "Rwys 13, 31 , 2600-3"),
            ("direct of of TPH", "direct of TPH"),
            ("  Rwy 4, std.  ", "Rwy 4, std."),
            ("Rwy 4, std.\nRwy 22, NA.", "Rwy 4, std.\nRwy 22, NA."),
            ("Rwy 4,  std.  \n  Rwy 22, NA.", "Rwy 4, std.\nRwy 22, NA."),
            ("Rwy 4, std.\n\n \n\nRwy 22, NA.", "Rwy 4, std.\n\nRwy 22, NA."),
        ],
    )
    def test_substitution(self, raw, expected):
        assert normalize(raw) == expected
