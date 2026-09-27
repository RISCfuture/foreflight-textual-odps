"""The end-to-end build over fixture sources: blocks to drawings and findings."""

import dataclasses
from pathlib import Path

import pytest

from odp_kml import nasr
from odp_kml.cycle import Cycle
from odp_kml.dtpp import AirportMeta, parse_metafile
from odp_kml.extract import extract_blocks
from odp_kml.findings import Kind
from odp_kml.pipeline import BuildOptions, build_from_sources, process_block
from odp_kml.shapes import Label, Polyline, Style

FIXTURES = Path(__file__).parent / "fixtures"
CYCLE = Cycle.from_iso("2026-09-03")
UNDERLINE = "\u0332"  # COMBINING LOW LINE under label altitudes


def fixture_nasr() -> nasr.NasrData:
    return nasr.load(
        *(
            FIXTURES / "nasr" / f"{group}_CSV.zip"
            for group in ("APT", "NAV", "FIX", "HPF")
        )
    )


def fixture_metafile():
    """The metafile excerpt plus an SW4 airport that the PDF excerpt lacks."""
    metafile = parse_metafile(FIXTURES / "dtpp" / "metafile-excerpt.xml")
    eureka = AirportMeta("05U", None, "EUREKA", "EUREKA", "NV", "SW4", "SW4TO.PDF")
    return dataclasses.replace(
        metafile, airports={**metafile.airports, eureka.lid: eureka}
    )


def build_on_fixtures(options: BuildOptions):
    """`pipeline.build` over the SW4 excerpt, the metafile and the NASR fixtures."""
    return build_from_sources(
        fixture_metafile(),
        {"SW4": FIXTURES / "pdf" / "SW4TO-excerpt.pdf"},
        fixture_nasr(),
        options,
    )


def fixture_build(airports: frozenset[str] | None = None):
    return build_on_fixtures(
        BuildOptions(cycle=CYCLE, cache_dir=Path("unused"), airports=airports)
    )


@pytest.fixture(scope="module")
def result():
    return fixture_build()


def findings_for(result, airport, kind=None):
    return [
        finding
        for finding in result.report.findings
        if finding.airport == airport and kind in (None, finding.kind)
    ]


def label_texts(drawing):
    return [
        shape.text.replace(UNDERLINE, "")
        for shape in drawing.shapes
        if isinstance(shape, Label)
    ]


class TestDrawings:
    def test_only_tph_is_drawn(self, result):
        assert [drawing.lid for drawing in result.drawings] == ["TPH"]

    def test_tph_draws_a_hold_route_legs_and_its_hold_altitude(self, result):
        (tph,) = result.drawings
        styles = [shape.style for shape in tph.shapes if isinstance(shape, Polyline)]
        assert Style.HOLD in styles
        assert styles.count(Style.ROUTE) >= 2
        assert any("9300" in text for text in label_texts(tph))

    def test_airport_found_by_its_icao_id(self):
        blocks, _ = extract_blocks(FIXTURES / "pdf" / "SW4TO-excerpt.pdf", "SW4")
        tph = next(block for block in blocks if block.lid == "TPH")
        options = BuildOptions(cycle=CYCLE, cache_dir=Path("unused"))

        outcome = process_block(
            dataclasses.replace(tph, lid="KTPH"), fixture_nasr(), options
        )

        assert outcome.finding is None
        assert outcome.drawing.lid == "TPH"


class TestFindings:
    def test_grammar_failure_is_a_parse_finding_with_verbatim_text(self, result):
        (finding,) = findings_for(result, "SPK", Kind.PARSE_FAILED)
        assert finding.signature == 'graphic DP reference "use ... departure"'
        assert "SPANISH FORK DEPARTURE" in finding.verbatim_text
        assert finding.cycle == "2026-09-03"

    def test_airport_missing_from_nasr(self, result):
        kinds = {(f.kind, f.signature) for f in findings_for(result, "JTC")}
        assert (Kind.UNRESOLVED_REF, "airport not in NASR") in kinds

    def test_metafile_mismatches_in_both_directions(self, result):
        signatures = {(f.airport, f.signature) for f in result.report.findings}
        assert ("05U", "metafile airport has no block") in signatures
        assert ("JTC", "airport not in metafile") in signatures
        assert ("TPH", "airport not in metafile") not in signatures


class TestReport:
    def test_counts_are_consistent(self, result):
        report = result.report
        labels = sum(len(label_texts(drawing)) for drawing in result.drawings)
        assert (report.cycle, report.airports_with_text) == ("2026-09-03", 7)
        assert report.drawn == len(result.drawings) == 1
        assert report.label_count == labels > 0

    def test_airport_subset_limits_blocks_and_findings(self):
        subset = fixture_build(frozenset({"TPH"}))
        assert [drawing.lid for drawing in subset.drawings] == ["TPH"]
        assert (subset.report.airports_with_text, subset.report.findings) == (1, [])


class TestSectionsDump:
    def test_one_normalized_entry_per_block_with_text(self, result):
        by_lid = {entry["lid"]: entry for entry in result.sections_dump}
        assert "TYL" not in by_lid
        assert len(by_lid) == 7
        tph = by_lid["TPH"]
        assert set(tph) == {"lid", "amendment", "departure_procedure", "vcoa"}
        assert tph["amendment"] == "AMDT 2 17AUG17 (17229) (FAA)"
        assert tph["departure_procedure"].startswith("Rwy 15, climbing left turn")
        assert tph["vcoa"].startswith("Rwy 15, 33, obtain ATC approval")
