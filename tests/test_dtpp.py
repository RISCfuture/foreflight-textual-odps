"""d-TPP metafile parsing and Takeoff-Minimums URL construction."""

import datetime as dt
from pathlib import Path

import pytest

from odp_kml.cycle import Cycle
from odp_kml.dtpp import AirportMeta, metafile_url, parse_metafile, to_pdf_url

METAFILE = Path(__file__).parent / "fixtures" / "dtpp" / "metafile-excerpt.xml"
CYCLE = Cycle(dt.date(2026, 9, 3))


class TestUrls:
    def test_metafile_url(self):
        assert (
            metafile_url(CYCLE)
            == "https://aeronav.faa.gov/d-tpp/2609/xml_data/d-tpp_Metafile.xml"
        )

    def test_to_pdf_url(self):
        assert (
            to_pdf_url(CYCLE, "SW4") == "https://aeronav.faa.gov/d-tpp/2609/SW4TO.PDF"
        )


@pytest.fixture(scope="module")
def metafile():
    return parse_metafile(METAFILE)


class TestParseMetafile:
    def test_cycle_and_effective_dates(self, metafile):
        assert (metafile.cycle_code, metafile.from_edate, metafile.to_edate) == (
            "2609",
            "0901Z  09/03/26",
            "0901Z  10/01/26",
        )

    def test_airport_from_its_takeoff_minimums_record(self, metafile):
        assert metafile.airports["TPH"] == AirportMeta(
            lid="TPH",
            icao="KTPH",
            name="TONOPAH",
            city="TONOPAH",
            state="NV",
            volume="SW4",
            pdf_name="SW4TO.PDF",
        )

    def test_only_airports_with_takeoff_minimums_are_kept(self, metafile):
        assert sorted(metafile.airports) == ["TNX", "TPH"]

    def test_accepts_raw_bytes(self):
        assert parse_metafile(METAFILE.read_bytes()) == parse_metafile(METAFILE)
