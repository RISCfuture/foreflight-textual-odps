"""Strict recursive-descent parser from DEPARTURE PROCEDURE and VCOA text to
the procedure AST.

The grammar has no "skip unknown words" rule: every token must be consumed
by a named rule, or `ParseError` names the unmatched phrase and its position.
A wrong parse is worse than no parse, so the parser never guesses a turn
direction, altitude, or navaid.

Input is text as produced by ``normalize``; line breaks count as whitespace.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator, Mapping

from .legs import FIX_LENGTH, NAVAID_TYPE_WORDS, VISUAL_CLIMB, LegParser
from .normalize import normalize
from .procedure import (
    FixRef,
    GraphicDeparture,
    Leg,
    NavaidRef,
    Procedure,
    RunwayGroup,
    Thence,
    VcoaGroup,
)
from .sections import Sections
from .tokens import ParseError, Token, is_ident_word


def parse_departure_procedure(
    text: str, *, airport: str, amendment: str | None
) -> Procedure:
    """Parse normalized DEPARTURE PROCEDURE text into a `Procedure` with no VCOA.

    Raises `ParseError` unless every token is consumed by a grammar rule.
    """
    runway_groups, shared_tail = _Parser(text).departure_procedure()
    return Procedure(airport, amendment, runway_groups, shared_tail, vcoa=())


def parse_vcoa(
    text: str, *, known_navaids: Mapping[str, NavaidRef] | None = None
) -> tuple[VcoaGroup, ...]:
    """Parse normalized VCOA text into its groups.

    ``known_navaids`` maps a navaid name (``TONOPAH``) to the reference that
    carried it with an ident elsewhere in the procedure; a navaid named
    without an ident resolves only through it. Raises `ParseError` unless
    every token is consumed by a grammar rule.
    """
    return _Parser(text, known_navaids).vcoa()


def parse_procedure(sections: Sections, *, airport: str) -> Procedure:
    """Parse an airport's DEPARTURE PROCEDURE and VCOA sections into one
    `Procedure`.

    A VCOA navaid named without an ident resolves to the navaid the
    DEPARTURE PROCEDURE section named with that ident. A procedure with
    only a VCOA section has no runway groups. Raises `ParseError` when
    neither section is present or either fails to parse.
    """
    procedure = _departure_procedure(sections, airport)
    if sections.vcoa is None:
        return procedure
    vcoa = parse_vcoa(normalize(sections.vcoa), known_navaids=_named_navaids(procedure))
    return dataclasses.replace(procedure, vcoa=vcoa)


def _departure_procedure(sections: Sections, airport: str) -> Procedure:
    """The DEPARTURE PROCEDURE section's procedure, or an empty one beside a VCOA."""
    if sections.departure_procedure is not None:
        return parse_departure_procedure(
            normalize(sections.departure_procedure),
            airport=airport,
            amendment=sections.amendment,
        )
    if sections.vcoa is None:
        raise ParseError("no departure procedure section")
    return Procedure(airport, sections.amendment, (), None, vcoa=())


def _named_navaids(procedure: Procedure) -> dict[str, NavaidRef]:
    """Name → reference for every navaid the procedure names unambiguously."""
    by_name: dict[str, set[NavaidRef]] = {}
    for ref in _navaid_refs(procedure):
        if ref.name is not None:
            by_name.setdefault(ref.name, set()).add(ref)
    return {name: next(iter(refs)) for name, refs in by_name.items() if len(refs) == 1}


def _navaid_refs(node) -> Iterator[NavaidRef]:
    if isinstance(node, NavaidRef):
        yield node
    elif isinstance(node, tuple):
        for item in node:
            yield from _navaid_refs(item)
    elif dataclasses.is_dataclass(node):
        for field in dataclasses.fields(node):
            yield from _navaid_refs(getattr(node, field.name))


_RUNWAY = re.compile(r"\d{1,2}[LRC]?")
_DP_NAME_WORD = re.compile(r"[A-Z][A-Z0-9]*")
_DP_NAME_PUNCTUATION = frozenset("()'")
_DP_NAME_MAX_TOKENS = 8


class _Parser(LegParser):
    """The procedure-level rules: runway groups, the shared tail, and VCOA."""

    def __init__(self, text: str, known_navaids: Mapping[str, NavaidRef] | None = None):
        super().__init__(text, known_navaids)

    # --- Departure procedure -----------------------------------------------

    def departure_procedure(
        self,
    ) -> tuple[tuple[RunwayGroup, ...], tuple[Leg, ...] | None]:
        """procedure := [graphic-dp] runway-group* [shared-tail]

        A leading graphic-dp names the charted DP every runway flies.
        """
        if self._at_end():
            raise ParseError("empty departure procedure")
        groups: list[RunwayGroup] = []
        if self._peek_graphic_departure():
            groups.append(RunwayGroup((), (self._graphic_departure(),)))
        shared_tail = None
        while not self._at_end():
            self._reject_visual_climb_sentence()
            if self._starts_shared_tail():
                shared_tail = self._shared_tail(groups)
                break
            groups.append(self._runway_group())
        self._expect_end()
        self._require_tail_for_thence(groups, shared_tail)
        return tuple(groups), shared_tail

    def _peek_graphic_departure(self) -> bool:
        return (self._peek("use") or self._peek("see")) and any(
            self._peek("departure", offset=offset)
            for offset in range(2, _DP_NAME_MAX_TOKENS + 2)
        )

    def _graphic_departure(self) -> GraphicDeparture:
        """graphic-dp := ("use" | "see") dp-name "departure" ["(" NAME ")"]* "."

        dp-name := (NAME | "(" | ")" | "'")+ with at least one NAME, e.g.
        ``BINAL TWO``, ``ELIM (RNAV)``, ``COEUR D'ALENE``. The trailing
        parenthesized words qualify the DP (``(RNAV)``, ``(OBSTACLE)``).
        """
        self._expect_any("use", "see")
        first = self._index
        while not self._peek("departure"):
            if not self._is_dp_name_token(self._token()):
                raise self._unmatched()
            self._index += 1
        name = self._tokens[first : self._index]
        if not any(_DP_NAME_WORD.fullmatch(token.text) for token in name):
            raise self._unmatched()
        self._expect("departure")
        while self._accept("("):
            if not self._is_dp_name_token(self._token()):
                raise self._unmatched()
            self._index += 1
            self._expect(")")
        self._expect(".")
        return GraphicDeparture(self._text[name[0].start : name[-1].end])

    @staticmethod
    def _is_dp_name_token(token: Token | None) -> bool:
        return token is not None and (
            bool(_DP_NAME_WORD.fullmatch(token.text))
            or token.text in _DP_NAME_PUNCTUATION
        )

    def _reject_visual_climb_sentence(self) -> None:
        if self._peek(*VISUAL_CLIMB):
            raise self._error(f'unsupported inline VCOA "{" ".join(VISUAL_CLIMB)}"')

    def _runway_group(self) -> RunwayGroup:
        """runway-group := runway-header (not-available | graphic-dp | legs)
        | all-runways graphic-dp"""
        if self._accept("all", "rwys") or self._accept("all", "runways"):
            self._expect(",")
            if not self._peek_graphic_departure():
                raise self._error('unsupported "all runways" group')
            return RunwayGroup((), (self._graphic_departure(),))
        runways = self._runway_header()
        if self._not_available():
            return RunwayGroup(runways, ())
        if self._peek_graphic_departure():
            return RunwayGroup(runways, (self._graphic_departure(),))
        self._reject_visual_climb_sentence()
        self._last_fix = None
        return RunwayGroup(runways, self._legs([self._leg()]))

    def _runway_header(self) -> tuple[str, ...]:
        """runway-header := ("Rwy" | "Rwys") runway ("," runway)* ("," | ":")"""
        if not self._accept_any("rwy", "rwys"):
            raise self._unmatched()
        runways = [*self._runway()]
        while not self._accept(":"):
            self._expect(",")
            if not self._peek_runway():
                break
            runways.extend(self._runway())
        return tuple(runways)

    def _peek_runway(self) -> bool:
        token = self._token()
        return token is not None and bool(_RUNWAY.fullmatch(token.text))

    def _runway(self) -> list[str]:
        """runway := nn[LRC] ("/" [LRC])*  e.g. ``2L/R`` → ``2L``, ``2R``"""
        if not self._peek_runway():
            raise self._unmatched()
        number = self._next().text
        runways = [number]
        while self._peek_sibling_runway():
            self._index += 1
            runways.append(number.rstrip("LRC") + self._next().text)
        return runways

    def _peek_sibling_runway(self) -> bool:
        side = self._token(1)
        return self._peek("/") and side is not None and side.text in ("L", "R", "C")

    def _not_available(self) -> bool:
        """not-available := "NA" "-" ("Obstacles" | "ATC") "." """
        if not self._accept("na"):
            return False
        self._expect("-")
        if not self._accept_any("atc", "obstacles"):
            raise self._unmatched()
        self._expect(".")
        return True

    def _starts_shared_tail(self) -> bool:
        return self._peek("...") or self._peek("all", "aircraft")

    def _shared_tail(self, groups: list[RunwayGroup]) -> tuple[Leg, ...]:
        """shared-tail := ("..." | "All aircraft") legs"""
        if self._peek("...") and not _all_flown_end_with_thence(groups):
            raise self._error('"..." without a preceding "thence"')
        if not self._accept("..."):
            self._expect("all", "aircraft")
        self._last_fix = None
        return self._legs([self._leg()])

    def _require_tail_for_thence(
        self, groups: list[RunwayGroup], shared_tail: tuple[Leg, ...] | None
    ) -> None:
        if shared_tail is not None and _ends_with_thence(shared_tail):
            raise ParseError('"thence" inside the shared tail', "", len(self._text))
        if shared_tail is None and any(_ends_with_thence(g.legs) for g in groups):
            raise ParseError('"thence" without a shared tail', "", len(self._text))

    # --- VCOA --------------------------------------------------------------

    def vcoa(self) -> tuple[VcoaGroup, ...]:
        """vcoa := vcoa-group+"""
        if self._at_end():
            raise ParseError("empty VCOA")
        groups = []
        while not self._at_end():
            groups.append(self._vcoa_group())
        return tuple(groups)

    def _vcoa_group(self) -> VcoaGroup:
        """vcoa-group := [vcoa-runways] atc-approval visual-climb [notify-atc]"""
        runways = self._vcoa_runways()
        self._atc_approval()
        group = self._visual_climb(runways)
        self._notify_atc()
        return group

    def _vcoa_runways(self) -> tuple[str, ...]:
        """vcoa-runways := runway-header | "All" ("Rwys" | "runways") ","

        An empty tuple means every runway.
        """
        if self._peek("rwy") or self._peek("rwys"):
            return self._runway_header()
        if self._accept("all", "rwys") or self._accept("all", "runways"):
            self._expect(",")
        return ()

    def _atc_approval(self) -> None:
        """atc-approval := "obtain ATC approval for" ("VCOA" | "climb in visual
        conditions") "when requesting IFR clearance." """
        self._expect("obtain", "atc", "approval", "for")
        if not self._accept("vcoa"):
            self._expect(*VISUAL_CLIMB[1:])
        self._expect("when", "requesting", "ifr", "clearance", ".")

    def _visual_climb(self, runways: tuple[str, ...]) -> VcoaGroup:
        """visual-climb := "climb in visual conditions to cross" crossing
        "at or above" nnnn ["MSL"] legs"""
        self._expect("climb", "in", "visual", "conditions", "to", "cross")
        cross = self._vcoa_crossing()
        self._expect("at", "or", "above")
        feet = self._integer()
        self._accept("msl")
        self._last_fix = None
        return VcoaGroup(runways, cross, feet, self._legs([]))

    def _vcoa_crossing(self) -> FixRef | None:
        """crossing := FIX | airport-name — ``None`` means the departure airport."""
        token = self._token()
        if (
            token is not None
            and is_ident_word(token.text)
            and len(token.text) == FIX_LENGTH
            and self._peek("at", offset=1)
        ):
            self._index += 1
            return FixRef(token.text)
        if self._peek_navaid() and self._peek_navaid_type(offset=1):
            raise self._error("unsupported VCOA crossing a navaid")
        self._airport_name()
        return None

    def _airport_name(self) -> None:
        """airport-name := (Capitalized-word | "/" | "(" | ")" | "-")+ ["airport"]

        The name has at least one mixed-case word and no navaid type, so a
        navaid or fix is never mistaken for the airport.
        """
        start = self._index
        while not self._peek("at") and self._is_airport_name_token(self._token()):
            self._index += 1
        name = self._tokens[start : self._index]
        if not any(token.text[1:].islower() for token in name):
            self._index = start
            raise self._unmatched()

    @staticmethod
    def _is_airport_name_token(token: Token | None) -> bool:
        return token is not None and (
            (token.text[0].isupper() and token.lower not in NAVAID_TYPE_WORDS)
            or token.text in ("/", "(", ")", "-")
            or token.lower == "airport"
        )

    def _notify_atc(self) -> None:
        """notify-atc := "When executing VCOA, notify ATC prior to departure." """
        if self._accept("when", "executing", "vcoa", ","):
            self._expect("notify", "atc", "prior", "to", "departure", ".")


def _all_flown_end_with_thence(groups: list[RunwayGroup]) -> bool:
    """Every runway group that flies legs (not an NA group) ends in "thence"."""
    flown = [group for group in groups if group.legs and not group.graphic]
    return bool(flown) and all(_ends_with_thence(group.legs) for group in flown)


def _ends_with_thence(legs: tuple[Leg, ...]) -> bool:
    return bool(legs) and isinstance(legs[-1], Thence)
