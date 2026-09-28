"""Takeoff-Minimums PDF text extraction into one block per airport."""

import dataclasses
from pathlib import Path

import pytest

from odp_kml.dtpp import AirportMeta, parse_metafile
from odp_kml.extract import (
    blocks_from_pages,
    check_against_metafile,
    extract_blocks,
    named_destinations,
    pdf_text_pages,
)

# Pages 35-38 of the 2609 SW4TO.PDF: SPK, JTC, TYL, TPH, KTNX, TVY, RYN and
# the first part of TUS.
FIXTURES = Path(__file__).parent / "fixtures"
EXCERPT = FIXTURES / "pdf" / "SW4TO-excerpt.pdf"
METAFILE = FIXTURES / "dtpp" / "metafile-excerpt.xml"


@pytest.fixture(scope="module")
def extraction():
    return extract_blocks(EXCERPT, "SW4")


@pytest.fixture(scope="module")
def blocks(extraction):
    return {block.lid: block for block in extraction[0]}


def lines_of(block):
    return block.text.split("\n")


class TestPdfTools:
    def test_one_text_page_per_pdf_page(self):
        pages = pdf_text_pages(EXCERPT)
        assert len(pages) == 4
        assert "TONOPAH (TPH)" in pages[2]

    def test_named_destinations_drop_parens_and_repeat_suffix(self):
        assert named_destinations(EXCERPT) == {
            "SPK",
            "JTC",
            "TYL",
            "TPH",
            "TVY",
            "RYN",
            "TUS",
        }


class TestExtractBlocks:
    def test_airports_in_page_order(self, extraction):
        assert [block.lid for block in extraction[0]] == [
            "SPK",
            "JTC",
            "TYL",
            "TPH",
            "KTNX",
            "TVY",
            "RYN",
            "TUS",
        ]

    def test_no_warnings(self, extraction):
        assert extraction[1] == []

    def test_every_destination_has_a_block(self, blocks):
        assert named_destinations(EXCERPT) <= set(blocks)

    def test_heading_fields(self, blocks):
        tph = blocks["TPH"]
        assert (tph.city, tph.name, tph.volume, tph.pages) == (
            "TONOPAH, NV",
            "TONOPAH",
            "SW4",
            (3,),
        )

    def test_airport_line_before_city_line(self, blocks):
        assert (blocks["KTNX"].city, blocks["KTNX"].name) == (
            "TONOPAH, NV",
            "TONOPAH TEST RANGE",
        )

    def test_airport_sharing_the_previous_city(self, blocks):
        assert blocks["TUS"].city == "TUCSON, AZ"

    def test_block_starts_at_the_takeoff_minimums_title(self, blocks):
        assert lines_of(blocks["TPH"])[:3] == [
            "TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES",
            "AMDT 2 17AUG17 (17229) (FAA)",
            "TAKEOFF MINIMUMS:",
        ]

    def test_block_starts_at_the_dva_title_and_keeps_both(self, blocks):
        tus = lines_of(blocks["TUS"])
        assert tus[0] == "DIVERSE VECTOR AREA (RADAR VECTORS)"
        assert "TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES" in tus
        assert "AMDT 6 30NOV23 (23334) (FAA)" in tus

    def test_wrapped_lines_rejoin_into_one_logical_line(self, blocks):
        tph = lines_of(blocks["TPH"])
        assert any(
            "RT, 246° inbound" in line and line.endswith("before proceeding on course.")
            for line in tph
        )
        assert (
            "Rwy 15, 33, obtain ATC approval for VCOA when requesting IFR clearance. "
            "Climb in visual conditions to cross Tonopah airport at or above 7800 "
            "direct TONOPAH VORTAC, continue climb in TPH holding pattern (NE, RT, "
            "246° inbound) to cross TPH VORTAC at or above 9300 before proceeding "
            "on course." in tph
        )

    def test_each_runway_sentence_on_its_own_line(self, blocks):
        assert lines_of(blocks["TPH"])[3:6] == [
            "Rwys 11, 29, NA - ATC.",
            "Rwy 15, std. with a min. climb of 320' per NM to 9100 or 2500-3 for VCOA.",
            "Rwy 33, std. with a min. climb of 352' per NM to 9100 or 2500-3 for VCOA.",
        ]

    def test_page_footer_splitting_a_section_is_removed(self, blocks):
        assert lines_of(blocks["TPH"])[-2:] == [
            (
                "Rwy 15, transmission line tower 515’ from DER, 473’ left of "
                "centerline, 23’ AGL/5418’ MSL."
            ),
            (
                "Rwy 33, fence beginning 173’ from DER, 401’ right of centerline, "
                "6’ AGL/5438’ MSL."
            ),
        ]

    def test_page_furniture_and_continuation_markers_are_removed(self, blocks):
        for block in blocks.values():
            assert "SEP 2026 to" not in block.text
            assert "CON’T" not in block.text
            assert "TAKEOFF MINS" not in block.text

    def test_continued_runway_note_keeps_its_runway(self, blocks):
        assert (
            "Rwy 29, tree 4191’ from DER, 1292’ left of centerline, 100’ AGL/7141’ MSL."
            in lines_of(blocks["JTC"])
        )
        assert blocks["JTC"].pages == (1, 2)

    def test_continuation_marker_with_a_doubled_apostrophe_is_removed(self):
        page = """\
TONOPAH, NV
TONOPAH (TPH)
TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES
AMDT 1 01JAN20 (20001) (FAA)
DEPARTURE PROCEDURE:
Rwy 15, climb heading 150° to 7000 before proceeding on course. CON''T"""
        blocks, _ = blocks_from_pages([page], {"TPH"}, "SW4")
        assert blocks[0].text.endswith("before proceeding on course.")

    def test_unrecognized_heading_warns_instead_of_vanishing(self):
        page = """\
McCALL, ID
McCALL MUNI (MYL)
TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES
AMDT 1 01JAN20 (20001) (FAA)
DEPARTURE PROCEDURE:
Rwy 16, climb direct Linden (LIN) VOR/DME."""
        blocks, warnings = blocks_from_pages([page], {"MYL", "LIN"}, "NW1")
        assert blocks == []
        assert warnings == ["NW1: destination (MYL) has no block"]


class TestCheckAgainstMetafile:
    @pytest.mark.parametrize(
        "tonopah_test_range_icao",
        ["KTNX", None],
        ids=["by-icao", "by-stripped-K"],
    )
    def test_reports_both_directions(self, extraction, tonopah_test_range_icao):
        airports = parse_metafile(METAFILE).airports
        expected = {
            "TPH": airports["TPH"],
            "TNX": dataclasses.replace(airports["TNX"], icao=tonopah_test_range_icao),
            "05U": AirportMeta(
                lid="05U",
                icao=None,
                name="EUREKA",
                city="EUREKA",
                state="NV",
                volume="SW4",
                pdf_name="SW4TO.PDF",
            ),
        }
        assert check_against_metafile(extraction[0], expected) == [
            "SW4: metafile airport 05U has no block",
            "SW4: block SPK has no metafile airport",
            "SW4: block JTC has no metafile airport",
            "SW4: block TYL has no metafile airport",
            "SW4: block TVY has no metafile airport",
            "SW4: block RYN has no metafile airport",
            "SW4: block TUS has no metafile airport",
        ]
