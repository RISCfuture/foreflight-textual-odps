"""NASR CSV loaders: airports, runways, navaids, fixes, and holding patterns."""

import datetime as dt
from pathlib import Path

import pytest

from odp_kml.cycle import Cycle
from odp_kml.nasr import (
    _load_hold,
    fetch,
    group_url,
    load,
    parse_mag_var,
    parse_optional_float,
    parse_optional_int,
    parse_optional_latlon,
)

FIXTURES = Path(__file__).parent / "fixtures" / "nasr"
CYCLE = Cycle.containing(dt.date(2026, 9, 3))


@pytest.fixture(scope="module")
def nasr_data():
    return load(
        FIXTURES / "APT_CSV.zip",
        FIXTURES / "NAV_CSV.zip",
        FIXTURES / "FIX_CSV.zip",
        FIXTURES / "HPF_CSV.zip",
    )


class TestGroupUrl:
    def test_builds_the_faa_subject_zip_url_for_a_group(self):
        assert group_url(CYCLE, "APT") == (
            "https://nfdc.faa.gov/webContent/28DaySub/extra/03_Sep_2026_APT_CSV.zip"
        )

    def test_url_changes_with_group(self):
        assert group_url(CYCLE, "HPF").endswith("_HPF_CSV.zip")


class TestParseHelpers:
    def test_blank_float_is_none(self):
        assert parse_optional_float("") is None
        assert parse_optional_float("  ") is None

    def test_present_float_is_parsed(self):
        assert parse_optional_float("5430.3") == pytest.approx(5430.3)

    def test_blank_int_is_none(self):
        assert parse_optional_int("") is None

    def test_present_int_is_parsed(self):
        assert parse_optional_int("138") == 138

    def test_blank_latlon_is_none_never_zero(self):
        assert parse_optional_latlon("", "") is None
        assert parse_optional_latlon("37.5", "") is None

    def test_present_latlon_is_parsed(self):
        point = parse_optional_latlon("37.51453516", "-122.25255625")
        assert point.lat == pytest.approx(37.51453516)
        assert point.lon == pytest.approx(-122.25255625)

    def test_blank_mag_var_is_none(self):
        assert parse_mag_var("", "E") is None

    def test_east_mag_var_is_positive(self):
        assert parse_mag_var("15", "E") == pytest.approx(15.0)

    def test_west_mag_var_is_negative(self):
        assert parse_mag_var("15", "W") == pytest.approx(-15.0)


class TestAirports:
    def test_sql_runway_end_12_has_true_alignment_and_position(self, nasr_data):
        end = nasr_data.airports["SQL"].runway_end("12")
        assert end.true_alignment == 138
        assert end.position.lat == pytest.approx(37.51453516)
        assert end.position.lon == pytest.approx(-122.25255625)

    def test_sql_airport_mag_var_is_signed_east(self, nasr_data):
        assert nasr_data.airports["SQL"].mag_var_east == pytest.approx(15.0)

    def test_trk_end_20_has_a_displaced_threshold(self, nasr_data):
        end = nasr_data.airports["TRK"].runway_end("20")
        assert end.displaced_threshold is not None
        assert end.displaced_threshold.lat == pytest.approx(39.32531402)

    def test_trk_end_20_tora_is_none_when_not_published(self, nasr_data):
        assert nasr_data.airports["TRK"].runway_end("20").tora_ft is None

    def test_bur_end_15_tora_is_published(self, nasr_data):
        assert nasr_data.airports["BUR"].runway_end("15").tora_ft == 6885

    def test_reciprocal_end_returns_the_opposite_runway_end(self, nasr_data):
        assert nasr_data.airports["TPH"].reciprocal_end("15").id == "33"

    def test_reciprocal_end_of_the_reciprocal_is_the_original(self, nasr_data):
        assert nasr_data.airports["TPH"].reciprocal_end("33").id == "15"

    def test_unknown_runway_end_is_none(self, nasr_data):
        assert nasr_data.airports["SQL"].runway_end("99") is None

    def test_single_ended_helipad_pseudo_runway_is_excluded(self, nasr_data):
        runway_ids = {r.id for r in nasr_data.airports["TPH"].runways}
        assert runway_ids == {"11/29", "15/33"}

    def test_closed_airport_is_excluded(self, nasr_data):
        assert "13CL" not in nasr_data.airports

    def test_non_airport_site_type_is_excluded(self, nasr_data):
        assert "26CN" not in nasr_data.airports


class TestNavaids:
    def test_tph_navaid_is_a_vortac_with_a_station_declination(self, nasr_data):
        (tph,) = nasr_data.navaids["TPH"]
        assert tph.type == "VORTAC"
        assert tph.mag_var_east == pytest.approx(17.0)


class TestFixes:
    def test_mecca_fix_is_present(self, nasr_data):
        (mecca,) = nasr_data.fixes["MECCA"]
        assert mecca.state == "CA"
        assert mecca.position.lat == pytest.approx(33.53651944)
        assert mecca.position.lon == pytest.approx(-116.094475)


class TestHolds:
    def test_vny_hold_has_inbound_course_295_and_turn_left(self, nasr_data):
        matching = [h for h in nasr_data.holds["VNY"] if h.inbound_course == 295]
        assert len(matching) == 1
        assert matching[0].turn == "L"

    def test_hold_at_a_named_fix_is_keyed_by_the_fix(self, nasr_data):
        (hold,) = nasr_data.holds["TOBEY"]
        assert hold.fix_ident == "TOBEY"
        assert hold.navaid_ident == "TPH"

    def test_hold_directly_at_a_navaid_falls_back_to_the_navaid_key(self, nasr_data):
        holds_at_tph = nasr_data.holds["TPH"]
        assert all(h.fix_ident == "TPH" for h in holds_at_tph)
        assert len(holds_at_tph) == 4

    def test_hold_with_no_fix_or_navaid_identifier_is_skipped(self):
        row = {
            "HP_NAME": "ORPHAN HOLD",
            "FIX_ID": "",
            "NAV_ID": "",
            "COURSE_INBOUND_DEG": "90",
            "TURN_DIRECTION": "R",
            "LEG_LENGTH_DIST": "",
            "HOLD_DIRECTION": "",
        }
        assert _load_hold(row) is None


class TestFetch:
    def test_fetch_downloads_each_group_and_loads_them(self, monkeypatch, tmp_path):
        requested = []

        def fake_download(url, cache_dir):
            requested.append(url)
            group = url.rsplit("_", 2)[1]
            return FIXTURES / f"{group}_CSV.zip"

        monkeypatch.setattr("odp_kml.nasr.download", fake_download)
        data = fetch(CYCLE, tmp_path)

        assert set(requested) == {
            group_url(CYCLE, group) for group in ("APT", "NAV", "FIX", "HPF")
        }
        assert "SQL" in data.airports
