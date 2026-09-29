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
            ("climb heading130° to", "climb heading 130° to"),
            ("heading 045.00 and SNS", "heading 045 and SNS"),
            ("thence…", "thence..."),
            ("thence . . .", "thence ..."),
            ("thence. . .\n. . .direct", "thence...\n...direct"),
            ("thence ....\n.... Aircraft", "thence ...\n... Aircraft"),
            ("GLL VOR/DME r-221 to 7000", "GLL VOR/DME R-221 to 7000"),
            ("OCN VORTAC R- 083", "OCN VORTAC R-083"),
            ("HOM VOR/DME R·210", "HOM VOR/DME R-210"),
            ("cross CVO VOR/ DME at", "cross CVO VOR/DME at"),
            ("ADK NDB/ DME", "ADK NDB/DME"),
            ("continue climb-in hold (hold S,", "continue climb-in-hold (hold S,"),
            ("Climb-in- hold to", "Climb-in-hold to"),
            ("climb in- holding pattern", "climb-in-holding pattern"),
            ("cross BVL VORTAC at/above MEA", "cross BVL VORTAC at or above MEA"),
            ("climb to 13,000 in", "climb to 13000 in"),
            ("no later than 1,600 prior", "no later than 1600 prior"),
            ("Rwys 2, 20, use", "Rwys 2, 20, use"),
            ("1,200,300", "1,200,300"),
            ("23.4 DME.", "23.4 DME."),
            ("do not exceed 180K until", "do not exceed 180 KIAS until"),
            ("do not exceed 180 knots until", "do not exceed 180 KIAS until"),
            ("Do not exceed 250 KTS until", "Do not exceed 250 KIAS until"),
            ("do not exceed 200 KIAS until", "do not exceed 200 KIAS until"),
            ("before turning left..", "before turning left."),
            ("before preceding on course.", "before proceeding on course."),
            (
                "LIN VOR/DME, continue climbing on course.",
                "LIN VOR/DME, climb on course.",
            ),
            ("CVV VOR/DME. Continue climb on course.", "CVV VOR/DME. Climb on course."),
            ("to 7000...", "to 7000..."),
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
            (
                "ABQ VORTAC holding pattern. (Hold W, left turns",
                "ABQ VORTAC holding pattern (Hold W, left turns",
            ),
            ("  Rwy 4, std.  ", "Rwy 4, std."),
            ("Rwy 4, std.\nRwy 22, NA.", "Rwy 4, std.\nRwy 22, NA."),
            ("Rwy 4,  std.  \n  Rwy 22, NA.", "Rwy 4, std.\nRwy 22, NA."),
            ("Rwy 4, std.\n\n \n\nRwy 22, NA.", "Rwy 4, std.\n\nRwy 22, NA."),
        ],
    )
    def test_substitution(self, raw, expected):
        assert normalize(raw) == expected
