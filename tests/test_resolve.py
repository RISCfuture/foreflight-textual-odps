"""Tests for odp_kml.resolve: binding a parsed procedure to NASR positions."""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

import pytest
from pygeomag import GeoMag, calculate_decimal_year

from odp_kml import nasr
from odp_kml.findings import Kind
from odp_kml.geo import LatLon, distance_nm
from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    ClimbInHold,
    Compass8,
    Direct,
    FixRef,
    HoldSpec,
    Leg,
    NavaidRef,
    NavaidType,
    Procedure,
    RunwayGroup,
    Thence,
    Turn,
    VcoaGroup,
)
from odp_kml.resolve import ResolveError, resolve

FIXTURES = Path(__file__).parent / "fixtures" / "nasr"
EMPTY_DATA = nasr.NasrData(airports={}, navaids={}, fixes={}, holds={})
_FEET_PER_NM = 6076.11549


@pytest.fixture(scope="module")
def nasr_data() -> nasr.NasrData:
    return nasr.load(
        FIXTURES / "APT_CSV.zip",
        FIXTURES / "NAV_CSV.zip",
        FIXTURES / "FIX_CSV.zip",
        FIXTURES / "HPF_CSV.zip",
    )


def _airport(
    lid: str,
    position: LatLon,
    elevation_ft: float,
    mag_var_east: float | None,
    runways: tuple[nasr.Runway, ...] = (),
) -> nasr.Airport:
    """A minimal hand-built Airport for tests that don't need a real fixture."""
    return nasr.Airport(
        lid=lid,
        icao=None,
        name=lid,
        city="",
        state="",
        position=position,
        elevation_ft=elevation_ft,
        mag_var_east=mag_var_east,
        runways=runways,
    )


def _procedure_with_leg(*legs: Leg) -> Procedure:
    """A minimal Procedure whose only content is `legs`, inside a VCOA group
    with no runways, so navaid/fix/hold resolution can be tested in
    isolation from runway resolution."""
    return Procedure("TST", None, (), None, (VcoaGroup((), None, 5000, legs),))


class TestRunways:
    def test_runway_15_resolves_der_at_the_33_end(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        procedure = Procedure(
            "TPH", None, (RunwayGroup(("15",), (Thence(),)),), None, ()
        )

        result = resolve(procedure, airport, nasr_data)

        runway = result.runways["15"]
        end_33 = airport.runway_end("33")
        assert runway.der == end_33.position
        assert runway.der_elevation_ft == end_33.elevation_ft
        assert runway.course_true == pytest.approx(
            airport.runway_end("15").true_alignment, abs=1.0
        )

    def test_missing_runway_end_position_raises_runway_missing(self):
        airport = _airport(
            "TST",
            LatLon(40.0, -100.0),
            1000.0,
            10.0,
            (
                nasr.Runway(
                    "09/27",
                    5000,
                    (
                        nasr.RunwayEnd("09", None, None, None, None, None),
                        nasr.RunwayEnd(
                            "27", LatLon(40.0, -99.9), 1000.0, 270, None, None
                        ),
                    ),
                ),
            ),
        )
        procedure = Procedure(
            "TST", None, (RunwayGroup(("09",), (Thence(),)),), None, ()
        )

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, EMPTY_DATA)

        assert excinfo.value.kind == Kind.RUNWAY_MISSING
        assert excinfo.value.signature == "runway end position missing"

    def test_runway_resolves_with_an_unpadded_runway_id(self, nasr_data):
        airport = nasr_data.airports["BAM"]
        procedure = Procedure(
            "BAM", None, (RunwayGroup(("4",), (Thence(),)),), None, ()
        )

        result = resolve(procedure, airport, nasr_data)

        runway = result.runways["4"]
        end_22 = airport.runway_end("22")
        assert runway.der == end_22.position

    def test_short_tora_shifts_der_back_from_the_reciprocal_end(self):
        end_a = nasr.RunwayEnd("09", LatLon(40.0, -100.0), 1000.0, 90, None, 4000)
        end_b = nasr.RunwayEnd("27", LatLon(40.0, -99.9), 1000.0, 270, None, None)
        airport = _airport(
            "TST",
            LatLon(40.0, -100.0),
            1000.0,
            10.0,
            (nasr.Runway("09/27", 5000, (end_a, end_b)),),
        )
        procedure = Procedure(
            "TST", None, (RunwayGroup(("09",), (Thence(),)),), None, ()
        )

        result = resolve(procedure, airport, EMPTY_DATA)

        expected_shift_nm = (5000 - 4000) / _FEET_PER_NM
        assert distance_nm(result.runways["09"].der, end_b.position) == pytest.approx(
            expected_shift_nm, rel=1e-3
        )


class TestNavaids:
    def test_tph_navaid_resolves_as_vortac_with_nasr_declination(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        procedure = _procedure_with_leg(Direct(NavaidRef("TPH", NavaidType.VORTAC)))

        result = resolve(procedure, airport, nasr_data)

        point = result.points["TPH"]
        navaid = nasr_data.navaids["TPH"][0]
        assert point.position == navaid.position
        assert point.declination_east == pytest.approx(17.0)

    def test_unknown_navaid_raises_unresolved_ref(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        procedure = _procedure_with_leg(Direct(NavaidRef("ZZZZZ")))

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, nasr_data)

        assert excinfo.value.kind == Kind.UNRESOLVED_REF
        assert excinfo.value.signature == "navaid not found"

    def test_navaid_ambiguous_when_two_match_within_range(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        original = nasr_data.navaids["TPH"][0]
        duplicate = dataclasses.replace(original, position=LatLon(38.06, -117.05))
        data = dataclasses.replace(
            nasr_data,
            navaids={**nasr_data.navaids, "TPH": (original, duplicate)},
        )
        procedure = _procedure_with_leg(Direct(NavaidRef("TPH", NavaidType.VORTAC)))

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, data)

        assert excinfo.value.kind == Kind.AMBIGUOUS_REF
        assert excinfo.value.signature == "navaid ambiguous"


class TestFixes:
    def test_fix_beyond_150nm_is_excluded_and_unresolved(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        procedure = _procedure_with_leg(Direct(FixRef("GINNA")))

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, nasr_data)

        assert excinfo.value.kind == Kind.UNRESOLVED_REF
        assert excinfo.value.signature == "fix not found"

    def test_fix_within_range_resolves_with_airport_declination(self, nasr_data):
        airport = _airport("LAX", LatLon(34.2, -118.4), 100.0, 12.0)
        procedure = _procedure_with_leg(Direct(FixRef("GINNA")))

        result = resolve(procedure, airport, nasr_data)

        point = result.points["GINNA"]
        assert point.position == nasr_data.fixes["GINNA"][0].position
        assert point.declination_east == pytest.approx(12.0)


def _vny_area_data(nasr_data: nasr.NasrData) -> nasr.NasrData:
    """`nasr_data` with a hand-added "CANOG" fix near Van Nuys, so the real
    HPF-fixture hold record for CANOG (which belongs to the VNY navaid) can
    be looked up in tests without a full Van Nuys APT/FIX fixture."""
    canog = nasr.Fix("CANOG", "CA", LatLon(34.15, -118.45))
    return dataclasses.replace(nasr_data, fixes={**nasr_data.fixes, "CANOG": (canog,)})


class TestHolds:
    def test_tph_hold_in_text_is_used_as_is(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        hold = HoldSpec(Compass8.NE, Turn.RIGHT, 246)
        leg = ClimbInHold(
            NavaidRef("TPH", NavaidType.VORTAC),
            hold,
            Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300"),
        )
        procedure = _procedure_with_leg(leg)

        result = resolve(procedure, airport, nasr_data)

        assert "TPH" not in result.published_holds

    def test_hold_ambiguous_when_multiple_published_and_text_omits_it(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        leg = ClimbInHold(NavaidRef("TPH", NavaidType.VORTAC), None)
        procedure = _procedure_with_leg(leg)

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, nasr_data)

        assert excinfo.value.kind == Kind.HOLD_AMBIGUOUS

    def test_hold_from_hpf_when_text_omits_it(self, nasr_data):
        airport = _airport("VNY", LatLon(34.2098, -118.4899), 802.0, 14.0)
        data = _vny_area_data(nasr_data)
        # The procedure must resolve the CANOG hold's own navaid (VNY)
        # somewhere else, or the hold is filtered out as belonging to a
        # possibly unrelated, nationally reused fix identifier.
        procedure = _procedure_with_leg(
            Direct(NavaidRef("VNY", NavaidType.VOR_DME)),
            ClimbInHold(FixRef("CANOG"), None),
        )

        result = resolve(procedure, airport, data)

        assert result.published_holds["CANOG"] == HoldSpec(Compass8.W, Turn.RIGHT, 75)

    def test_hold_at_an_unrelated_navaid_is_filtered_and_raises_ambiguous(
        self, nasr_data
    ):
        airport = _airport("VNY", LatLon(34.2098, -118.4899), 802.0, 14.0)
        data = _vny_area_data(nasr_data)
        # Nothing else in this procedure resolves VNY, so CANOG's published
        # hold (which belongs to the VNY navaid) must not be trusted as
        # this procedure's hold at CANOG.
        leg = ClimbInHold(FixRef("CANOG"), None)
        procedure = _procedure_with_leg(leg)

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, data)

        assert excinfo.value.kind == Kind.HOLD_AMBIGUOUS

    def test_hold_contradicting_nasr_raises_hold_ambiguous(self, nasr_data):
        airport = _airport("VNY", LatLon(34.2098, -118.4899), 802.0, 14.0)
        data = _vny_area_data(nasr_data)
        contradicting = HoldSpec(Compass8.W, Turn.LEFT, 75)  # NASR: turn R, not L
        procedure = _procedure_with_leg(
            Direct(NavaidRef("VNY", NavaidType.VOR_DME)),
            ClimbInHold(FixRef("CANOG"), contradicting),
        )

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, data)

        assert excinfo.value.kind == Kind.HOLD_AMBIGUOUS
        assert excinfo.value.signature == "hold contradicts NASR"

    def test_hold_contradicts_when_none_of_several_published_holds_agree(
        self, nasr_data
    ):
        airport = nasr_data.airports["TPH"]
        # All 4 published TPH holds turn right; a left-turning text hold
        # cannot agree with any of them, regardless of inbound course.
        contradicting = HoldSpec(Compass8.NE, Turn.LEFT, 246)
        leg = ClimbInHold(NavaidRef("TPH", NavaidType.VORTAC), contradicting)
        procedure = _procedure_with_leg(leg)

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, nasr_data)

        assert excinfo.value.kind == Kind.HOLD_AMBIGUOUS
        assert excinfo.value.signature == "hold contradicts NASR"

    def test_hold_record_with_unparseable_turn_raises_hold_ambiguous(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        bogus_fix = nasr.Fix("BOGUS", "NV", airport.position)
        bogus_hold = nasr.Hold(
            name="BOGUS HOLD",
            fix_ident="BOGUS",
            navaid_ident=None,
            inbound_course=100,
            turn="Q",  # not a valid Turn value
            leg_length_nm=None,
            direction=None,
        )
        data = dataclasses.replace(
            nasr_data,
            fixes={**nasr_data.fixes, "BOGUS": (bogus_fix,)},
            holds={**nasr_data.holds, "BOGUS": (bogus_hold,)},
        )
        leg = ClimbInHold(FixRef("BOGUS"), None)
        procedure = _procedure_with_leg(leg)

        with pytest.raises(ResolveError) as excinfo:
            resolve(procedure, airport, data)

        assert excinfo.value.kind == Kind.HOLD_AMBIGUOUS
        assert excinfo.value.signature == "hold record unparseable"


class TestAirportVariation:
    def test_published_mag_var_is_used_directly(self, nasr_data):
        airport = nasr_data.airports["TPH"]
        procedure = _procedure_with_leg()

        result = resolve(procedure, airport, nasr_data)

        assert result.airport_variation_east == 15.0

    def test_blank_mag_var_falls_back_to_the_wmm_value_at_the_airport(self, nasr_data):
        airport = dataclasses.replace(nasr_data.airports["TPH"], mag_var_east=None)
        procedure = _procedure_with_leg()

        result = resolve(procedure, airport, nasr_data)

        decimal_year = calculate_decimal_year(
            datetime.datetime.now(tz=datetime.UTC).date()
        )
        expected = (
            GeoMag()
            .calculate(
                glat=airport.position.lat,
                glon=airport.position.lon,
                alt=0,
                time=decimal_year,
                allow_date_outside_lifespan=True,
            )
            .d
        )
        assert result.airport_variation_east == pytest.approx(expected)
        # Real declination drifts from NASR's survey-epoch published value.
        assert abs(result.airport_variation_east - 15.0) < 5.0
