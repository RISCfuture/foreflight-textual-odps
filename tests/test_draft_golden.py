"""The offline golden-set drafting tool: schema, mechanical checks, caching."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from odp_kml.procedure import (
    Altitude,
    AltitudeKind,
    ClimbingTurn,
    ClimbInHold,
    Compass8,
    Direct,
    HoldSpec,
    NavaidRef,
    NavaidType,
    Procedure,
    ProceedOnCourse,
    RunwayGroup,
    Thence,
    Turn,
)
from tools import draft_golden

ALBANY_TEXT = "Rwy 10, climb on heading 110° to 2000 before turning north."


def _refs(node) -> list[str]:
    """Every `$ref` target inside a schema fragment."""
    if isinstance(node, dict):
        own = [node["$ref"]] if "$ref" in node else []
        return own + [ref for value in node.values() for ref in _refs(value)]
    if isinstance(node, list):
        return [ref for item in node for ref in _refs(item)]
    return []


def _objects(node):
    """Every object-typed schema fragment inside a schema."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _objects(item)


def _has_cycle(defs: dict) -> bool:
    graph = {name: {ref.split("/")[-1] for ref in _refs(d)} for name, d in defs.items()}

    def reaches(start, target, seen):
        return any(
            nxt == target or (nxt not in seen and reaches(nxt, target, seen | {nxt}))
            for nxt in graph[start]
        )

    return any(reaches(name, name, {name}) for name in graph)


@pytest.fixture(scope="module")
def schema():
    return draft_golden.procedure_schema()


class TestProcedureSchema:
    def test_refs_resolve_without_cycles(self, schema):
        defs = schema["$defs"]
        assert {ref.split("/")[-1] for ref in _refs(schema)} <= defs.keys()
        assert not _has_cycle(defs)

    def test_every_object_forbids_additional_properties(self, schema):
        objects = list(_objects(schema))
        assert len(objects) > 10
        assert all(obj["additionalProperties"] is False for obj in objects)

    def test_leg_and_until_nodes_require_a_source_span(self, schema):
        defs = schema["$defs"]
        for name in ("ClimbingTurn", "Thence", "Altitude", "CrossRadial"):
            assert "source_span" in defs[name]["required"]
        assert "source_span" not in defs["NavaidRef"]["properties"]

    def test_variants_carry_a_node_const_and_enums(self, schema):
        defs = schema["$defs"]
        assert defs["HoldSpec"]["properties"]["node"]["const"] == "HoldSpec"
        assert defs["HoldSpec"]["properties"]["turns"]["enum"] == ["L", "R"]


class TestSpanCoverage:
    def test_spans_covering_every_content_word_leave_no_gaps(self):
        spans = ["climb on heading 110° to 2000", "before turning north"]
        assert draft_golden.uncovered_tokens(ALBANY_TEXT, spans) == []

    def test_words_outside_every_span_are_reported(self):
        spans = ["climb on heading 110° to 2000"]
        assert draft_golden.uncovered_tokens(ALBANY_TEXT, spans) == [
            "before",
            "turning",
            "north",
        ]


class TestFewShotExamples:
    @pytest.mark.parametrize(
        "example", draft_golden.EXAMPLES, ids=lambda e: e.procedure.airport
    )
    def test_each_example_passes_every_mechanical_check(self, example):
        draft = draft_golden.build_draft(
            example.section, {"stop_reason": "end_turn", "text": example.output()}
        )
        assert draft["checks"] == {name: [] for name in draft["checks"]}
        assert draft["procedure"] is not None


class FakeClient:
    """Stands in for `anthropic.Anthropic`, answering every request with `reply`."""

    def __init__(self, reply: str):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self._reply = reply

    def _create(self, **params):
        self.requests.append(params)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=self._reply)],
        )


class TestDraftSections:
    def test_a_second_run_over_the_same_text_is_served_from_cache(self, tmp_path):
        example = draft_golden.EXAMPLES[0]
        client = FakeClient(example.output())

        for _ in range(2):
            draft_golden.draft_sections(
                [example.section], client=client, out_dir=tmp_path
            )

        assert len(client.requests) == 1
        written = json.loads((tmp_path / f"{example.section['lid']}.json").read_text())
        assert written["procedure"]["node"] == "Procedure"


def _tonopah() -> Procedure:
    tph = NavaidRef("TPH", NavaidType.VORTAC, "TONOPAH")
    hold = ClimbInHold(
        NavaidRef("TPH"),
        HoldSpec(Compass8.NE, Turn.RIGHT, 246),
        Altitude(9300, AltitudeKind.AT_OR_ABOVE, "at or above 9300"),
    )
    return Procedure(
        "TPH",
        None,
        (
            RunwayGroup(("15",), (ClimbingTurn(Turn.LEFT, Direct(tph)), Thence())),
            RunwayGroup(("33",), (ClimbingTurn(Turn.RIGHT, Direct(tph)), Thence())),
        ),
        (hold, ProceedOnCourse()),
        (),
    )


def test_cluster_key_names_the_leg_type_sequence():
    assert (
        draft_golden.cluster_key(_tonopah())
        == "ClimbingTurn>Direct | Thence || ClimbInHold | ProceedOnCourse"
    )
