"""Procedure AST: node construction and JSON round-tripping."""

from __future__ import annotations

import pytest

from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    AtFix,
    ClimbHeading,
    ClimbingTurn,
    ClimbInHold,
    Compass8,
    CrossRadial,
    Direct,
    Dme,
    FixRef,
    HeadingAndRadial,
    HoldSpec,
    NavaidRef,
    NavaidType,
    Procedure,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
    Thence,
    Turn,
    VcoaGroup,
    from_dict,
    to_dict,
)

TPH = NavaidRef("TPH", NavaidType.VORTAC, "TONOPAH")


def _tonopah_procedure() -> Procedure:
    """A Procedure mirroring Tonopah's runway 15/33 ODP with a shared tail and VCOA."""
    hold = ClimbInHold(
        TPH,
        HoldSpec(Compass8.NE, Turn.RIGHT, 246),
        Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300"),
    )
    shared_tail = (hold, ProceedOnCourse())
    runway_groups = (
        RunwayGroup(("15",), (ClimbingTurn(Turn.LEFT, Direct(TPH)), Thence())),
        RunwayGroup(("33",), (ClimbingTurn(Turn.RIGHT, Direct(TPH)), Thence())),
    )
    vcoa = (VcoaGroup(("15", "33"), None, 7800, (Direct(TPH), hold)),)
    return Procedure("TPH", None, runway_groups, shared_tail, vcoa)


class TestProcedureJsonRoundTrip:
    def test_json_round_trips_to_an_equal_procedure(self):
        procedure = _tonopah_procedure()
        assert Procedure.from_json(procedure.to_json()) == procedure

    def test_json_tags_nested_nodes_with_their_type(self):
        assert '"node": "ClimbInHold"' in _tonopah_procedure().to_json()


UNTIL_VARIANTS = [
    Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300"),
    AtFix(FixRef("BOACH")),
    Dme(TPH, 15.0),
    CrossRadial(TPH, 270),
]

LEG_VARIANTS = [
    ClimbHeading(
        200, until=Altitude(3000, AltitudeKind.AT_OR_BELOW, "at or below 3000")
    ),
    Direct(FixRef("BOACH")),
    Radial(TPH, 270, outbound=True),
    HeadingAndRadial(200, TPH, 270, outbound=False),
    ClimbingTurn(Turn.LEFT, Direct(TPH)),
    ClimbInHold(TPH, HoldSpec(Compass8.NE, Turn.RIGHT, 246)),
    ProceedOnCourse(Turn.LEFT),
    Thence(),
]


class TestNodeDictRoundTrip:
    @pytest.mark.parametrize("node", UNTIL_VARIANTS)
    def test_until_variant_round_trips(self, node):
        assert from_dict(to_dict(node)) == node

    @pytest.mark.parametrize("node", LEG_VARIANTS)
    def test_leg_variant_round_trips(self, node):
        assert from_dict(to_dict(node)) == node


class TestFromDictErrors:
    def test_unknown_type_raises_value_error(self):
        with pytest.raises(ValueError):
            from_dict({"node": "Bogus"})
