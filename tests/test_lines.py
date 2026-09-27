"""Rejoining wrapped block text into logical lines."""

import pytest

from odp_kml.lines import join_lines


class TestJoinLines:
    def test_inline_section_text_moves_to_its_own_line(self):
        assert join_lines(["DEPARTURE PROCEDURE: Use TRICITIES DEPARTURE."]) == [
            "DEPARTURE PROCEDURE:",
            "Use TRICITIES DEPARTURE.",
        ]

    @pytest.mark.parametrize(
        ("spelling", "canonical"),
        [
            ("TAKOFF MINIMUMS:", "TAKEOFF MINIMUMS:"),
            ("TAKEOFF MINIMUMS;", "TAKEOFF MINIMUMS:"),
            ("TAKE-OFF MINIMUMS:", "TAKEOFF MINIMUMS:"),
            ("DEPARTURE PROCEDURES:", "DEPARTURE PROCEDURE:"),
            ("DEPATURE PROCEDURE:", "DEPARTURE PROCEDURE:"),
            ("TAKEOFF OBSTACLE NOTES :", "TAKEOFF OBSTACLE NOTES:"),
            ("TAKEOFF OBSTACLE NOTES", "TAKEOFF OBSTACLE NOTES:"),
            ("AKEOFF OBSTACLE NOTES:", "TAKEOFF OBSTACLE NOTES:"),
            (
                "TAKEOFF MINIMUMS AND (OBSTACLE)DEPARTURE PROCEDURES",
                "TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES",
            ),
        ],
    )
    def test_header_spellings_are_canonical(self, spelling, canonical):
        assert join_lines([spelling]) == [canonical]

    def test_vcoa_header_with_runways_before_the_colon(self):
        assert join_lines(
            [
                "DEPARTURE PROCEDURE:",
                "Rwy 3, climb heading 030° to 1600 before proceeding on course.",
                "VCOA Rwys 3, 28: obtain ATC approval for VCOA.",
            ]
        )[-2:] == ["VCOA:", "Rwys 3, 28: obtain ATC approval for VCOA."]

    def test_runway_line_after_unterminated_line_starts_a_new_line(self):
        assert join_lines(
            ["TAKEOFF MINIMUMS:", "Rwys 7, 25, NA - Obstacles", "Rwys 13, 31, 2600-3."]
        ) == ["TAKEOFF MINIMUMS:", "Rwys 7, 25, NA - Obstacles", "Rwys 13, 31, 2600-3."]

    def test_sentence_wrapped_at_a_period_stays_with_its_runway(self):
        assert join_lines(
            [
                "DEPARTURE PROCEDURE:",
                "Rwy 11, climb heading 110° to 3400 before proceeding on course.",
                "When executing VCOA, notify ATC prior to departure.",
            ]
        ) == [
            "DEPARTURE PROCEDURE:",
            (
                "Rwy 11, climb heading 110° to 3400 before proceeding on course. "
                "When executing VCOA, notify ATC prior to departure."
            ),
        ]

    def test_shared_continuation_after_ellipsis_is_its_own_line(self):
        assert join_lines(
            [
                "DEPARTURE PROCEDURE:",
                "Rwy 3, climb direct ELP VORTAC, thence...",
                "Continue climb in ELP holding pattern before proceeding on course.",
            ]
        )[1:] == [
            "Rwy 3, climb direct ELP VORTAC, thence...",
            "Continue climb in ELP holding pattern before proceeding on course.",
        ]

    def test_lowercase_continuation_after_abbreviation_joins(self):
        assert join_lines(
            [
                "TAKEOFF MINIMUMS:",
                "Rwy 8, 300-1 or std. with a min.",
                "climb of 320' per NM until passing 8500.",
            ]
        )[1:] == [
            "Rwy 8, 300-1 or std. with a min. climb of 320' per NM until passing 8500."
        ]

    def test_each_obstacle_note_is_its_own_line(self):
        assert join_lines(
            [
                "TAKEOFF OBSTACLE NOTES:",
                "Rwy 4, tree 44’ from DER, 113’ left of centerline, 5723’ MSL.",
                "Building 745’ from DER, 694’ left of centerline, 23’ AGL/5739’ MSL.",
            ]
        )[1:] == [
            "Rwy 4, tree 44’ from DER, 113’ left of centerline, 5723’ MSL.",
            "Building 745’ from DER, 694’ left of centerline, 23’ AGL/5739’ MSL.",
        ]
