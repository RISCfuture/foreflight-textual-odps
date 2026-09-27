"""Splitting an airport block's text into its named sections."""

from odp_kml.sections import Sections, split_sections

TONOPAH = """\
TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES
AMDT 2 17AUG17 (17229) (FAA)
TAKEOFF MINIMUMS:
Rwys 11, 29, NA - ATC.
Rwy 15, std. with a min. climb of 320' per NM to 9100 or 2500-3 for VCOA.
Rwy 33, std. with a min. climb of 352' per NM to 9100 or 2500-3 for VCOA.
DEPARTURE PROCEDURE:
Rwy 15, climbing left turn direct TONOPAH (TPH) VORTAC thence...
Rwy 33, climbing right turn direct TONOPAH (TPH) VORTAC thence...
...continue climb in TPH holding pattern (NE, RT, 246° inbound) to cross TPH VORTAC \
at or above 9300 before proceeding on course.
VCOA:
Rwy 15, 33, obtain ATC approval for VCOA when requesting IFR clearance.
TAKEOFF OBSTACLE NOTES:
Rwy 15, transmission line tower 515’ from DER, 473’ left of centerline, 23’ AGL/5418’ MSL.
Rwy 33, fence beginning 173’ from DER, 401’ right of centerline, 6’ AGL/5438’ MSL."""

TUCSON = """\
DIVERSE VECTOR AREA (RADAR VECTORS)
AMDT 1 30NOV23 (23334) (FAA)
Rwy 4, heading as assigned by ATC; requires min climb of 228’ per NM to 3000.
Rwy 12, heading as assigned by ATC.
TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES
AMDT 6 30NOV23 (23334) (FAA)
DEPARTURE PROCEDURE:
Rwy 30, climbing right turn direct TUS VORTAC."""

TONOPAH_TEST_RANGE = """\
TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES
AMDT 1 19JUL18 (18200)
DEPARTURE PROCEDURE:
Rwy 14, 1000-3 with min. climb of 320 ft/NM to 10,700 or 2700-3 for Climb in Visual \
Conditions. Climb on a heading between 325º CW to 155º from DER.
TAKEOFF OBSTACLE NOTES:
Rwy 14, terrain 1204’ from DER, 823’ right of cntrln, 5582’ MSL."""

# Lines printed with no section header, and an amendment printed above its title.
UNHEADED = """\
AMDT 1 21JAN98 (22111) (FAA)
TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES
Rwy 12, 30, 6700-3* * Or standard with minimum climb of 270/NM to 9100.
Rwy 8, climb on hdg between 360° CW to 078° from DER.
Diverse departure NA. Use published departure procedures for obstacle avoidance."""


class TestSplitSections:
    def test_all_four_takeoff_sections(self):
        assert split_sections(TONOPAH) == Sections(
            amendment="AMDT 2 17AUG17 (17229) (FAA)",
            takeoff_minimums=(
                "Rwys 11, 29, NA - ATC.\n"
                "Rwy 15, std. with a min. climb of 320' per NM to 9100 or 2500-3 for VCOA.\n"
                "Rwy 33, std. with a min. climb of 352' per NM to 9100 or 2500-3 for VCOA."
            ),
            departure_procedure=(
                "Rwy 15, climbing left turn direct TONOPAH (TPH) VORTAC thence...\n"
                "Rwy 33, climbing right turn direct TONOPAH (TPH) VORTAC thence...\n"
                "...continue climb in TPH holding pattern (NE, RT, 246° inbound) to "
                "cross TPH VORTAC at or above 9300 before proceeding on course."
            ),
            vcoa="Rwy 15, 33, obtain ATC approval for VCOA when requesting IFR clearance.",
            obstacle_notes=(
                "Rwy 15, transmission line tower 515’ from DER, 473’ left of "
                "centerline, 23’ AGL/5418’ MSL.\n"
                "Rwy 33, fence beginning 173’ from DER, 401’ right of centerline, "
                "6’ AGL/5438’ MSL."
            ),
            dva=None,
        )

    def test_dva_before_the_takeoff_block_keeps_the_takeoff_amendment(self):
        sections = split_sections(TUCSON)
        assert sections.dva == (
            "Rwy 4, heading as assigned by ATC; requires min climb of 228’ per NM "
            "to 3000.\nRwy 12, heading as assigned by ATC."
        )
        assert sections.amendment == "AMDT 6 30NOV23 (23334) (FAA)"
        assert sections.departure_procedure == (
            "Rwy 30, climbing right turn direct TUS VORTAC."
        )

    def test_departure_header_without_minimums_header(self):
        sections = split_sections(TONOPAH_TEST_RANGE)
        assert sections.takeoff_minimums is None
        assert sections.departure_procedure.startswith("Rwy 14, 1000-3 with min.")
        assert sections.obstacle_notes.startswith("Rwy 14, terrain")

    def test_unheaded_lines_go_by_whether_they_instruct_a_departure(self):
        sections = split_sections(UNHEADED)
        assert sections.amendment == "AMDT 1 21JAN98 (22111) (FAA)"
        assert sections.takeoff_minimums == (
            "Rwy 12, 30, 6700-3* * Or standard with minimum climb of 270/NM to 9100."
        )
        assert sections.departure_procedure == (
            "Rwy 8, climb on hdg between 360° CW to 078° from DER.\n"
            "Diverse departure NA. Use published departure procedures for obstacle "
            "avoidance."
        )
