"""Draft golden-fixture ASTs for ODP text with the Claude API (offline only).

Asks a Claude model to draft a `Procedure` AST for each airport's DEPARTURE
PROCEDURE / VCOA text, checks every draft mechanically, and writes the drafts
for a human to review into ``tests/fixtures/golden/<LID>.json``. Nothing here
is imported or used by the build.

Usage::

    python tools/draft_golden.py sections.json [--model ID] [--limit N]
        [--lids TPH,BUR] [--sync] [--out tests/fixtures/golden/draft]

Needs ``ANTHROPIC_API_KEY`` and ``pip install -r requirements-tools.txt``.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import functools
import hashlib
import json
import os
import re
import sys
import time
import types
import typing
from collections.abc import Iterable, Iterator
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    Leg,
    NavaidRef,
    NavaidType,
    Procedure,
    ProceedOnCourse,
    Radial,
    RunwayGroup,
    SpeedRestriction,
    Thence,
    Turn,
    Until,
    VcoaGroup,
    from_dict,
    to_dict,
)

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
SYNC_LIMIT = 20
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_OUT = Path("tests/fixtures/golden/draft")

type Section = dict[str, str | None]
type ModelReply = dict[str, str]

# ---------------------------------------------------------------------------
# Structured-output schema, derived from the AST dataclasses


_SCALARS = {
    int: "integer",
    float: "number",
    str: "string",
    bool: "boolean",
    type(None): "null",
}
_SPANNED = frozenset(typing.get_args(Leg) + typing.get_args(Until))


def procedure_schema() -> dict:
    """The JSON schema a drafted `Procedure` must satisfy.

    Each AST dataclass becomes one ``$defs`` entry with a ``"node"`` const and
    every field required; leg and until nodes also require a ``source_span``.
    The AST has no recursive nodes, so the ``$ref`` graph is acyclic.
    """
    defs: dict[str, dict] = {}
    _define(Procedure, defs)
    return {**defs.pop("Procedure"), "$defs": defs}


def _define(cls: type, defs: dict[str, dict]) -> dict:
    """Add `cls` (and every node it reaches) to `defs`; return a ref to it."""
    if cls.__name__ not in defs:
        defs[cls.__name__] = {}
        defs[cls.__name__] = _object_schema(cls, defs)
    return {"$ref": f"#/$defs/{cls.__name__}"}


def _object_schema(cls: type, defs: dict[str, dict]) -> dict:
    hints = typing.get_type_hints(cls)
    properties = {"node": {"type": "string", "const": cls.__name__}} | {
        field.name: _type_schema(hints[field.name], defs)
        for field in dataclasses.fields(cls)
    }
    if cls in _SPANNED:
        properties["source_span"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _type_schema(hint, defs: dict[str, dict]) -> dict:
    if dataclasses.is_dataclass(hint):
        return _define(hint, defs)
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return {"type": "string", "enum": [member.value for member in hint]}
    if hint in _SCALARS:
        return {"type": _SCALARS[hint]}
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin is tuple:
        return {"type": "array", "items": _type_schema(args[0], defs)}
    if origin in (types.UnionType, typing.Union):
        return {"anyOf": [_type_schema(arg, defs) for arg in args]}
    raise TypeError(f"no JSON schema for {hint!r}")


# ---------------------------------------------------------------------------
# Mechanical checks

_TOKEN = re.compile(r"\.\.\.|[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*|[^\sA-Za-z0-9]")
_STOP_WORDS = frozenset({"and", "the", "then", "thence"})
_HEADER = re.compile(
    r"\bRwys?\s+\d{1,2}[LRC]?(?:\s*(?:,|&|and)\s*\d{1,2}[LRC]?)*"
    r"|\bAll aircraft\b|\bNA\s*-\s*[A-Za-z]+",
    re.IGNORECASE,
)


def strip_spans(data, path: str = "procedure") -> tuple[object, dict[str, str]]:
    """Remove every ``source_span`` from a drafted tree.

    Returns the stripped tree and the spans keyed by the JSON path of the node
    that carried each one (e.g. ``procedure.runway_groups[0].legs[1]``).
    """
    spans: dict[str, str] = {}

    def walk(value, here):
        if isinstance(value, dict):
            if "source_span" in value:
                spans[here] = value["source_span"]
            return {
                key: walk(item, f"{here}.{key}")
                for key, item in value.items()
                if key != "source_span"
            }
        if isinstance(value, list):
            return [walk(item, f"{here}[{index}]") for index, item in enumerate(value)]
        return value

    return walk(data, path), spans


def uncovered_tokens(text: str, spans: Iterable[str]) -> list[str]:
    """Tokens of `text` that no span occurrence covers.

    Stop words, punctuation, and runway headers (``Rwys 4, 35``,
    ``All aircraft``, ``NA - Obstacles``) need no span.
    """
    covered = bytearray(len(text))
    exempt = [_HEADER.finditer(text)] + [
        re.finditer(re.escape(span), text) for span in spans if span
    ]
    for match in (m for matches in exempt for m in matches):
        covered[match.start() : match.end()] = b"\x01" * (match.end() - match.start())
    return [
        token.group()
        for token in _TOKEN.finditer(text)
        if _needs_span(token.group()) and not all(covered[token.start() : token.end()])
    ]


def _needs_span(token: str) -> bool:
    return token[0].isalnum() and token.lower() not in _STOP_WORDS


def _idents(tree) -> Iterator[str]:
    """Every navaid/fix ident in a stripped procedure tree."""
    if isinstance(tree, dict):
        if tree.get("node") in ("NavaidRef", "FixRef"):
            yield tree["ident"]
        for value in tree.values():
            yield from _idents(value)
    elif isinstance(tree, list):
        for item in tree:
            yield from _idents(item)


def _full_text(section: Section) -> str:
    return "\n".join(filter(None, (section["departure_procedure"], section["vcoa"])))


def build_draft(section: Section, reply: ModelReply) -> dict:
    """Parse and check one model reply; return the draft record to write.

    ``checks`` maps each check to its problems (empty when it passed), and
    ``procedure`` is the parsed AST's dict, or ``None`` if it did not parse.
    """
    checks: dict[str, list[str]] = {
        "end_turn": _stop_reason_problems(reply),
        "parses": [],
        "spans_in_text": [],
        "span_coverage": [],
        "idents_in_text": [],
    }
    procedure, spans = None, {}
    try:
        tree, spans = strip_spans(json.loads(reply["text"]))
        procedure = _parse_procedure(tree, section)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        checks["parses"] = [f"{type(error).__name__}: {error}"]
    else:
        text = _full_text(section)
        checks["spans_in_text"] = [
            f"{path}: {span!r}" for path, span in spans.items() if span not in text
        ]
        gaps = uncovered_tokens(section["departure_procedure"] or "", spans.values())
        checks["span_coverage"] = [f"uncovered: {' '.join(gaps)}"] if gaps else []
        checks["idents_in_text"] = [
            ident
            for ident in dict.fromkeys(_idents(tree))
            if not re.search(rf"\b{re.escape(ident)}\b", text)
        ]
    return {
        **section,
        "procedure": to_dict(procedure) if procedure else None,
        "spans": spans,
        "checks": checks,
    }


def _stop_reason_problems(reply: ModelReply) -> list[str]:
    stop_reason = reply["stop_reason"]
    return [] if stop_reason == "end_turn" else [f"stop_reason {stop_reason}"]


def _parse_procedure(tree: dict, section: Section) -> Procedure:
    tree = tree | {"airport": section["lid"], "amendment": section["amendment"]}
    procedure = from_dict(tree)
    if not isinstance(procedure, Procedure):
        raise TypeError(f"root is {type(procedure).__name__}, not Procedure")
    return procedure


def cluster_key(procedure: Procedure) -> str:
    """The leg-type sequence of a procedure, for grouping similar airports.

    Distinct runway-group sequences are joined by ``" ; "``, legs by
    ``" | "``, and the shared tail follows ``" || "``; a climbing turn shows
    the leg it turns onto, e.g. ``ClimbingTurn>Direct | Thence || ClimbInHold``.
    """
    groups = " ; ".join(
        dict.fromkeys(_legs_key(group.legs) for group in procedure.runway_groups)
    )
    tail = procedure.shared_tail
    return groups if tail is None else f"{groups} || {_legs_key(tail)}"


def _legs_key(legs: tuple[Leg, ...]) -> str:
    return " | ".join(map(_leg_key, legs)) or "(no legs)"


def _leg_key(leg: Leg) -> str:
    name = type(leg).__name__
    return (
        f"{name}>{type(leg.then).__name__}" if isinstance(leg, ClimbingTurn) else name
    )


# ---------------------------------------------------------------------------
# Few-shot examples


@dataclasses.dataclass(frozen=True)
class Example:
    """A worked input/output pair for the system prompt.

    `spans` lists the ``source_span`` of every leg and until node in the
    order `to_dict` emits them (parents before children).
    """

    departure_procedure: str
    vcoa: str | None
    procedure: Procedure
    spans: tuple[str, ...]

    @property
    def section(self) -> Section:
        """The example as a ``sections.json`` entry."""
        return {
            "lid": self.procedure.airport,
            "amendment": self.procedure.amendment,
            "departure_procedure": self.departure_procedure,
            "vcoa": self.vcoa,
        }

    def output(self) -> str:
        """The drafted JSON the model should produce for this example."""
        spans = iter(self.spans)

        def annotate(value):
            if isinstance(value, dict):
                own = (
                    {"source_span": next(spans)}
                    if value.get("node") in _SPANNED_NAMES
                    else {}
                )
                return own | {key: annotate(item) for key, item in value.items()}
            if isinstance(value, list):
                return [annotate(item) for item in value]
            return value

        tree = annotate(to_dict(self.procedure))
        leftover = list(spans)
        if leftover:
            raise ValueError(f"{self.procedure.airport}: unused spans {leftover}")
        return json.dumps(tree, sort_keys=True)


_SPANNED_NAMES = frozenset(cls.__name__ for cls in _SPANNED)


def _procedure(lid, groups, tail=None, vcoa=()) -> Procedure:
    return Procedure(lid, None, tuple(groups), tail, tuple(vcoa))


def _to(feet: int) -> Altitude:
    return Altitude(feet, AltitudeKind.TO, f"to {feet}")


def _at_or_above(feet: int) -> Altitude:
    return Altitude(feet, AltitudeKind.AT_OR_ABOVE, f"at or above {feet}")


_TPH = NavaidRef("TPH", NavaidType.VORTAC, "TONOPAH")
_TPH_HOLD = ClimbInHold(
    NavaidRef("TPH"), HoldSpec(Compass8.NE, Turn.RIGHT, 246), _at_or_above(9300)
)
_TPH_HOLD_SPANS = (
    (
        "continue climb in TPH holding pattern (NE, RT, 246° inbound) to cross TPH "
        "VORTAC at or above 9300"
    ),
    "at or above 9300",
    "before proceeding on course",
)
_BAM = NavaidRef("BAM", NavaidType.VORTAC)
_IPL = NavaidRef("IPL", NavaidType.VORTAC)
_CPN = NavaidRef("CPN", NavaidType.VOR_DME)
_LAS = NavaidRef("LAS", NavaidType.VORTAC)
_GBN = NavaidRef("GBN", NavaidType.VOR)
_BOACH = FixRef("BOACH")
_POC = "before proceeding on course"

EXAMPLES: tuple[Example, ...] = (
    Example(
        "Rwy 15, climbing left turn direct TONOPAH (TPH) VORTAC thence...\n"
        "Rwy 33, climbing right turn direct TONOPAH (TPH) VORTAC thence...\n"
        "...continue climb in TPH holding pattern (NE, RT, 246° inbound) to cross "
        "TPH VORTAC at or above 9300 before proceeding on course.",
        "Rwy 15, 33, obtain ATC approval for VCOA when requesting IFR clearance. "
        "Climb in visual conditions to cross Tonopah airport at or above 7800 "
        "direct TONOPAH VORTAC, continue climb in TPH holding pattern (NE, RT, "
        "246° inbound) to cross TPH VORTAC at or above 9300 before proceeding on "
        "course.",
        _procedure(
            "TPH",
            [
                RunwayGroup(("15",), (ClimbingTurn(Turn.LEFT, Direct(_TPH)), Thence())),
                RunwayGroup(
                    ("33",), (ClimbingTurn(Turn.RIGHT, Direct(_TPH)), Thence())
                ),
            ],
            (_TPH_HOLD, ProceedOnCourse()),
            [
                VcoaGroup(
                    ("15", "33"),
                    None,
                    7800,
                    (Direct(_TPH), _TPH_HOLD, ProceedOnCourse()),
                )
            ],
        ),
        (
            "climbing left turn direct TONOPAH (TPH) VORTAC",
            "direct TONOPAH (TPH) VORTAC",
            "thence",
            "climbing right turn direct TONOPAH (TPH) VORTAC",
            "direct TONOPAH (TPH) VORTAC",
            "thence",
            *_TPH_HOLD_SPANS,
            "direct TONOPAH VORTAC",
            *_TPH_HOLD_SPANS,
        ),
    ),
    Example(
        "Rwy 10, climb on heading 110° to 2000 before turning north.\n"
        "Rwy 28, climb on heading 281° to 2000 before proceeding on course.",
        None,
        _procedure(
            "ALB",
            [
                RunwayGroup(("10",), (ClimbHeading(110, _to(2000)), ProceedOnCourse())),
                RunwayGroup(("28",), (ClimbHeading(281, _to(2000)), ProceedOnCourse())),
            ],
        ),
        (
            "climb on heading 110° to 2000",
            "to 2000",
            "before turning north",
            "climb on heading 281° to 2000",
            "to 2000",
            _POC,
        ),
    ),
    Example(
        "Rwy 13, climbing right turn heading 240° and BAM VORTAC R-210 outbound to "
        "10000 before proceeding on course.\n"
        "Rwy 22, climb direct BAM VORTAC and proceed on BAM R-210 outbound to 10100 "
        "before proceeding on course.",
        None,
        _procedure(
            "BAM",
            [
                RunwayGroup(
                    ("13",),
                    (
                        ClimbingTurn(
                            Turn.RIGHT,
                            HeadingAndRadial(240, _BAM, 210, True, _to(10000)),
                        ),
                        ProceedOnCourse(),
                    ),
                ),
                RunwayGroup(
                    ("22",),
                    (
                        Direct(_BAM),
                        Radial(NavaidRef("BAM"), 210, True, _to(10100)),
                        ProceedOnCourse(),
                    ),
                ),
            ],
        ),
        (
            "climbing right turn heading 240° and BAM VORTAC R-210 outbound to 10000",
            "heading 240° and BAM VORTAC R-210 outbound to 10000",
            "to 10000",
            _POC,
            "climb direct BAM VORTAC",
            "proceed on BAM R-210 outbound to 10100",
            "to 10100",
            _POC,
        ),
    ),
    Example(
        "Rwy 8, climbing right turn heading 120° to intercept IPL VORTAC R-009 to "
        "IPL VORTAC, then climb on course.",
        None,
        _procedure(
            "XIP",
            [
                RunwayGroup(
                    ("8",),
                    (
                        ClimbingTurn(
                            Turn.RIGHT,
                            HeadingAndRadial(120, _IPL, 9, False, AtFix(_IPL)),
                        ),
                        ProceedOnCourse(),
                    ),
                )
            ],
        ),
        (
            (
                "climbing right turn heading 120° to intercept IPL VORTAC R-009 to IPL "
                "VORTAC"
            ),
            "heading 120° to intercept IPL VORTAC R-009 to IPL VORTAC",
            "to IPL VORTAC",
            "then climb on course",
        ),
    ),
    Example(
        "Rwy 22, NA - Obstacles.\n"
        "Rwys 4, 35, climbing right turn via heading 130° and CPN VOR/DME R-340 to "
        "CPN VOR/DME, continue climb-in-hold to 10200 (north, left turn, 166° "
        "inbound) before proceeding on course.",
        None,
        _procedure(
            "XCP",
            [
                RunwayGroup(("22",), ()),
                RunwayGroup(
                    ("4", "35"),
                    (
                        ClimbingTurn(
                            Turn.RIGHT,
                            HeadingAndRadial(130, _CPN, 340, False, AtFix(_CPN)),
                        ),
                        ClimbInHold(
                            _CPN, HoldSpec(Compass8.N, Turn.LEFT, 166), _to(10200)
                        ),
                        ProceedOnCourse(),
                    ),
                ),
            ],
        ),
        (
            (
                "climbing right turn via heading 130° and CPN VOR/DME R-340 to CPN "
                "VOR/DME"
            ),
            "heading 130° and CPN VOR/DME R-340 to CPN VOR/DME",
            "to CPN VOR/DME",
            "continue climb-in-hold to 10200 (north, left turn, 166° inbound)",
            "to 10200",
            _POC,
        ),
    ),
    Example(
        "Rwy 16, climb heading 154° to 2500 before proceeding on course.\n"
        "Rwy 34, climb on heading 336° to MLF 12 DME before proceeding on course.",
        None,
        _procedure(
            "XML",
            [
                RunwayGroup(("16",), (ClimbHeading(154, _to(2500)), ProceedOnCourse())),
                RunwayGroup(
                    ("34",),
                    (
                        ClimbHeading(336, Dme(NavaidRef("MLF"), 12.0)),
                        ProceedOnCourse(),
                    ),
                ),
            ],
        ),
        (
            "climb heading 154° to 2500",
            "to 2500",
            _POC,
            "climb on heading 336° to MLF 12 DME",
            "to MLF 12 DME",
            _POC,
        ),
    ),
    Example(
        "Rwy 7, do not exceed 200 KIAS until LAS VORTAC R-110, climb on heading 070° "
        "to cross LAS VORTAC R-110, then climbing left turn direct BOACH before "
        "proceeding on course.",
        None,
        _procedure(
            "XLS",
            [
                RunwayGroup(
                    ("7",),
                    (
                        ClimbHeading(
                            70,
                            CrossRadial(_LAS, 110),
                            SpeedRestriction(200, "until LAS VORTAC R-110"),
                        ),
                        ClimbingTurn(Turn.LEFT, Direct(_BOACH)),
                        ProceedOnCourse(),
                    ),
                )
            ],
        ),
        (
            (
                "do not exceed 200 KIAS until LAS VORTAC R-110, climb on heading 070° to "
                "cross LAS VORTAC R-110"
            ),
            "to cross LAS VORTAC R-110",
            "climbing left turn direct BOACH",
            "direct BOACH",
            _POC,
        ),
    ),
    Example(
        "Rwy 18, climb direct BOACH, continue climb in BOACH holding pattern (SW, LT, "
        "031° inbound) to 8000 before proceeding on course.",
        None,
        _procedure(
            "XBO",
            [
                RunwayGroup(
                    ("18",),
                    (
                        Direct(_BOACH),
                        ClimbInHold(
                            _BOACH, HoldSpec(Compass8.SW, Turn.LEFT, 31), _to(8000)
                        ),
                        ProceedOnCourse(),
                    ),
                )
            ],
        ),
        (
            "climb direct BOACH",
            "continue climb in BOACH holding pattern (SW, LT, 031° inbound) to 8000",
            "to 8000",
            _POC,
        ),
    ),
    Example(
        "Rwy 3, climb on heading 030° to 1500 before turning left.\n"
        "Rwy 21, climb on heading 210° to 1500 before turning right.",
        None,
        _procedure(
            "XTR",
            [
                RunwayGroup(
                    ("3",), (ClimbHeading(30, _to(1500)), ProceedOnCourse(Turn.LEFT))
                ),
                RunwayGroup(
                    ("21",), (ClimbHeading(210, _to(1500)), ProceedOnCourse(Turn.RIGHT))
                ),
            ],
        ),
        (
            "climb on heading 030° to 1500",
            "to 1500",
            "before turning left",
            "climb on heading 210° to 1500",
            "to 1500",
            "before turning right",
        ),
    ),
    Example(
        "Rwy 9, climb via GBN VOR R-270 inbound to GBN VOR thence...\n"
        "Rwy 27, climbing turn direct GBN VOR thence...\n"
        "...climb direct TUS VORTAC before proceeding on course.",
        None,
        _procedure(
            "XGB",
            [
                RunwayGroup(("9",), (Radial(_GBN, 270, False, AtFix(_GBN)), Thence())),
                RunwayGroup(("27",), (ClimbingTurn(None, Direct(_GBN)), Thence())),
            ],
            (Direct(NavaidRef("TUS", NavaidType.VORTAC)), ProceedOnCourse()),
        ),
        (
            "climb via GBN VOR R-270 inbound to GBN VOR",
            "to GBN VOR",
            "thence",
            "climbing turn direct GBN VOR",
            "direct GBN VOR",
            "thence",
            "climb direct TUS VORTAC",
            _POC,
        ),
    ),
    Example(
        "Rwy 30, climb on heading 300° to at or below 4000, then climb direct OLM NDB "
        "before proceeding on course.",
        None,
        _procedure(
            "XOL",
            [
                RunwayGroup(
                    ("30",),
                    (
                        ClimbHeading(
                            300,
                            Altitude(
                                4000, AltitudeKind.AT_OR_BELOW, "at or below 4000"
                            ),
                        ),
                        Direct(NavaidRef("OLM", NavaidType.NDB)),
                        ProceedOnCourse(),
                    ),
                )
            ],
        ),
        (
            "climb on heading 300° to at or below 4000",
            "at or below 4000",
            "climb direct OLM NDB",
            _POC,
        ),
    ),
    Example(
        "Rwy 5, climb on heading 050° to 6500 before proceeding on course.",
        "Rwy 5, obtain ATC approval for VCOA when requesting IFR clearance. Climb in "
        "visual conditions to cross BOACH at or above 6500 before proceeding on "
        "course.",
        _procedure(
            "XVC",
            [RunwayGroup(("5",), (ClimbHeading(50, _to(6500)), ProceedOnCourse()))],
            vcoa=[VcoaGroup(("5",), _BOACH, 6500, (ProceedOnCourse(),))],
        ),
        ("climb on heading 050° to 6500", "to 6500", _POC, _POC),
    ),
)


# ---------------------------------------------------------------------------
# Prompt

_RULES = """\
You translate the text of an FAA textual obstacle departure procedure (ODP) into
a JSON abstract syntax tree. A pipeline draws the tree on a map for pilots, and a
human reviews every draft, so faithfulness to the text matters more than
completeness.

Input: an airport identifier, the normalized DEPARTURE PROCEDURE section, and the
VCOA (visual climb over airport) section or "(none)".

Output: one Procedure object matching the response schema. Every object carries
"node" naming its type. Rules:

- runway_groups: one group per runway header ("Rwy 15," / "Rwys 4, 35,"), runway
  ids as strings in text order. A runway marked "NA - Obstacles" (or similar)
  gets a group with no legs. A runway told to "use LUNDI DEPARTURE" (a charted
  DP) gets a group whose only leg is GraphicDeparture, name as printed before
  the word DEPARTURE ("ELIM (RNAV)"); with no runway header, or "All Rwys,",
  the group's runways are empty.
- Legs, in the order flown: ClimbHeading (climb on a magnetic heading),
  RunwayHeading ("climb runway heading", whatever its number),
  StraightAhead ("climb to 1200 before turning left": a climb to an altitude
  that names no heading or route, followed by the sentence's end, "then",
  "thence" or "before ..."),
  HeadingRange (climb on any heading "between 350° CW to 162°": one
  HeadingSector per range joined by "or", start and end as printed, clockwise
  false for CCW; nothing but ProceedOnCourse follows it),
  Direct (proceed direct to a navaid or fix), Radial (fly a navaid's radial,
  outbound true when the text says outbound, false for inbound or when flown
  to that navaid, null when the text says neither), HeadingAndRadial (fly a
  heading until intercepting a radial), ClimbingTurn (a climbing turn, or "turn
  right", onto its "then" leg; direction null when the text gives none; then
  null for "climbing right turn, thence..." whose route is the shared tail's
  first leg), ClimbInHold, CrossAt ("Cross LIN VOR/DME at or above 5000" after
  reaching LIN), ProceedOnCourse
  (turn_restriction L/R only for "before turning left/right"; null otherwise),
  and Thence (the "thence..." marker leading into a shared tail).
- "All other courses: ..." (or "or climb on a heading between ...") after a
  HeadingRange is a further runway group for the same runways.
- shared_tail: the legs after a leading "..." or "All aircraft" line that every
  group continues with; null when there is none. Before an "All aircraft" tail
  every group with legs ends in Thence, said or not.
- until ends a leg: Altitude ("to 2000" is kind "to", "at 2000" is "at",
  "at or above 9300" is "at_or_above", "at or below" is "at_or_below"; phrase
  is the exact words),
  EnrouteAltitude ("at or above MEA/MCA for route of flight": names ["MEA",
  "MCA"] as printed, kind as for Altitude), AtFix ("to RESER INT" is the fix RESER), Dme ("to MLF 12 DME"; "to CARRO
  INT/OLM 19.43 DME" also sets fix CARRO), or
  CrossRadial ("to cross LAS VORTAC R-110").
- Navaids: ident is the three-letter identifier; name only when the text spells
  it out ("TONOPAH (TPH) VORTAC" gives name TONOPAH); type only when the text
  states it next to that mention. Fixes are five-letter identifiers. A navaid
  named without an ident takes the ident given for that name elsewhere in the
  same procedure.
- "Rwys 35 L/R," is 35L, 35R. A direction of flight after a radial settles
  outbound: "R-340 northwest bound" flies away from the navaid, "R-350
  southbound" toward it. "Then on assigned route" is ProceedOnCourse, and
  "Thence..." opening a sentence of its own is still Thence.
- Headings, radials, and inbound courses are integers (R-009 is 9, 070° is 70).
- vcoa: one group per VCOA runway list; cross is the fix crossed, or null when
  climbing over the airport; at_or_above is the crossing altitude; bound is the
  direction to cross in ("southeast bound" is SE), null when not given; then is
  the legs that follow. A visual climb written into the DEPARTURE PROCEDURE
  section ("..., or for climb in visual conditions: cross ...") is a vcoa group
  for that runway, listed before the VCOA section's groups.
- source_span on every leg and until node: the exact words of the input the node
  came from, copied character for character (including °, parentheses, and
  capitalization). Together the spans must cover every word of the DEPARTURE
  PROCEDURE text except runway headers, "and", "the", "then", and "thence".
- Never guess a turn direction, altitude, heading, or navaid that the text does
  not state. When a phrase has no faithful representation in the schema (airway
  routings, a leg with two terminating conditions), leave it out rather than force-fitting it; the
  uncovered words flag the draft for review.

Worked examples follow.
"""


def user_message(section: Section) -> str:
    """The per-airport request text."""
    return (
        f"Airport: {section['lid']}\n\n"
        f"DEPARTURE PROCEDURE:\n{section['departure_procedure']}\n\n"
        f"VCOA:\n{section['vcoa'] or '(none)'}"
    )


def system_prompt() -> str:
    """The frozen system prompt: rules followed by every worked example."""
    examples = "\n\n".join(
        f"<example>\n<input>\n{user_message(example.section)}\n</input>\n"
        f"<output>\n{example.output()}\n</output>\n</example>"
        for example in EXAMPLES
    )
    return f"{_RULES}\n{examples}"


@functools.cache
def prompt_version() -> str:
    """A digest of the system prompt and schema; changes invalidate the cache."""
    frozen = system_prompt() + json.dumps(procedure_schema(), sort_keys=True)
    return hashlib.sha256(frozen.encode()).hexdigest()[:16]


def request_params(section: Section, model: str) -> dict:
    """Messages API parameters drafting one section."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_message(section)}],
        "output_config": {
            "format": {"type": "json_schema", "schema": procedure_schema()}
        },
    }


# ---------------------------------------------------------------------------
# Querying, caching, and output


def _cache_path(cache_dir: Path, section: Section, model: str) -> Path:
    key = _full_text(section) + model + prompt_version()
    return cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


def _read_cache(path: Path) -> ModelReply | None:
    return json.loads(path.read_text()) if path.exists() else None


def _reply(message) -> ModelReply:
    text = "".join(block.text for block in message.content if block.type == "text")
    return {"stop_reason": message.stop_reason, "text": text}


def _query_sync(client, sections: list[Section], model: str) -> dict[str, ModelReply]:
    return {
        section["lid"]: _reply(
            client.beta.messages.create(
                **request_params(section, model),
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        )
        for section in sections
    }


def _query_batch(client, sections: list[Section], model: str) -> dict[str, ModelReply]:
    batch = client.messages.batches.create(
        requests=[
            {"custom_id": section["lid"], "params": request_params(section, model)}
            for section in sections
        ]
    )
    _wait_for_batch(client, batch.id)
    return {
        result.custom_id: _batch_reply(result.result)
        for result in client.messages.batches.results(batch.id)
    }


def _wait_for_batch(client, batch_id: str) -> None:
    delay = 30.0
    while client.messages.batches.retrieve(batch_id).processing_status != "ended":
        print(f"batch {batch_id} still processing; next check in {delay:.0f}s")
        time.sleep(delay)
        delay = min(delay * 2, 600.0)


def _batch_reply(result) -> ModelReply:
    if result.type == "succeeded":
        return _reply(result.message)
    return {"stop_reason": f"batch {result.type}", "text": ""}


def _query(client, sections: list[Section], model: str, *, sync: bool):
    if not sections:
        return {}
    client = client or _default_client()
    use_sync = sync or len(sections) <= SYNC_LIMIT
    return (_query_sync if use_sync else _query_batch)(client, sections, model)


def _default_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set; export it to draft golden fixtures.")
    import anthropic

    return anthropic.Anthropic()


def draft_sections(
    sections: list[Section],
    *,
    client=None,
    model: str = MODEL,
    out_dir: Path = DEFAULT_OUT,
    sync: bool = False,
) -> list[dict]:
    """Draft, check, and write an AST for every section.

    Replies already in ``out_dir/.cache`` are reused; the rest are requested
    through `client` (an ``anthropic.Anthropic``, created on demand), in one
    Message Batch when there are more than `SYNC_LIMIT` and `sync` is false.
    Only ``end_turn`` replies are cached, so failed requests are retried.
    """
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {s["lid"]: _cache_path(cache_dir, s, model) for s in sections}
    replies = {lid: _read_cache(path) for lid, path in paths.items()}
    pending = [s for s in sections if replies[s["lid"]] is None]
    for lid, reply in _query(client, pending, model, sync=sync).items():
        replies[lid] = reply
        if reply["stop_reason"] == "end_turn":
            paths[lid].write_text(json.dumps(reply))
    missing = {"stop_reason": "no reply", "text": ""}
    drafts = [build_draft(s, replies[s["lid"]] or missing) for s in sections]
    _write_outputs(drafts, out_dir)
    return drafts


def _write_outputs(drafts: list[dict], out_dir: Path) -> None:
    for draft in drafts:
        (out_dir / f"{draft['lid']}.json").write_text(
            json.dumps(draft, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )
    (out_dir / "clusters.md").write_text(_clusters_markdown(drafts))
    (out_dir / "failed.md").write_text(_failed_markdown(drafts))


def _clusters_markdown(drafts: list[dict]) -> str:
    clusters: dict[str, list[str]] = {}
    for draft in drafts:
        if draft["procedure"] is not None:
            key = cluster_key(from_dict(draft["procedure"]))
            clusters.setdefault(key, []).append(draft["lid"])
    ranked = sorted(clusters.items(), key=lambda item: (-len(item[1]), item[0]))
    sections = [
        f"## `{key}`\n\n{len(lids)} airports: {', '.join(lids)}\n"
        for key, lids in ranked
    ]
    return "# Drafts by leg-type sequence\n\n" + "\n".join(sections)


def _failed_markdown(drafts: list[dict]) -> str:
    lines = [
        f"- {draft['lid']}: {name}: {'; '.join(problems)}"
        for draft in drafts
        for name, problems in draft["checks"].items()
        if problems
    ]
    return "# Drafts failing a mechanical check\n\n" + "".join(
        f"{line}\n" for line in lines
    )


# ---------------------------------------------------------------------------
# CLI


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sections", type=Path, help="sections.json from the CLI")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--limit", type=int, help="draft at most N airports")
    parser.add_argument("--lids", help="comma-separated airports to draft")
    parser.add_argument("--sync", action="store_true", help="never use batches")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args(argv)


def _select(sections: list[Section], lids: str | None, limit: int | None):
    wanted = set(lids.split(",")) if lids else None
    chosen = [s for s in sections if wanted is None or s["lid"] in wanted]
    return chosen[:limit]


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    args = _parse_args(argv)
    sections = _select(json.loads(args.sections.read_text()), args.lids, args.limit)
    drafts = draft_sections(
        sections, model=args.model, out_dir=args.out, sync=args.sync
    )
    failed = sum(any(d["checks"].values()) for d in drafts)
    print(f"{len(drafts)} drafts written to {args.out}; {failed} failed a check")


if __name__ == "__main__":
    main()
