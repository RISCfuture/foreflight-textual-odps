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

from .legs import (
    FIX_LENGTH,
    HEADER_LEG_WORDS,
    NAVAID_TYPE_WORDS,
    RANGE_ALTERNATIVES,
    VISUAL_CLIMB,
    LegParser,
    end_fix,
)
from .normalize import normalize
from .procedure import (
    ClimbingTurn,
    FixRef,
    GraphicDeparture,
    HeadingAndRadial,
    HeadingRange,
    Leg,
    NavaidRef,
    Procedure,
    Radial,
    RunwayGroup,
    Thence,
    VcoaGroup,
)
from .sections import Sections
from .tokens import ParseError, Token, is_ident_word


@dataclasses.dataclass(frozen=True)
class Unparsed:
    """Text for some runways that the grammar could not read.

    `runways` are those its runway header named, empty when it named none
    the grammar could read; `vcoa` marks the VCOA section.
    """

    runways: tuple[str, ...]
    error: ParseError
    vcoa: bool = False


def parse_departure_procedure(
    text: str, *, airport: str, amendment: str | None
) -> Procedure:
    """Parse normalized DEPARTURE PROCEDURE text into a `Procedure` with no VCOA.

    A visual climb written into the section ("..., or for climb in visual
    conditions: cross ...") becomes one of the procedure's VCOA groups.
    Raises `ParseError` unless every token is consumed by a grammar rule.
    """
    procedure, _ = _parse_departure_procedure(
        text, airport=airport, amendment=amendment, in_part=False
    )
    return procedure


def _parse_departure_procedure(
    text: str, *, airport: str, amendment: str | None, in_part: bool
) -> tuple[Procedure, list[Unparsed]]:
    parser = _Parser(text, in_part=in_part)
    runway_groups, shared_tail = parser.departure_procedure()
    procedure = Procedure(
        airport, amendment, runway_groups, shared_tail, vcoa=parser.inline_vcoa
    )
    return _carry_radial_senses(procedure), parser.unparsed


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
    DEPARTURE PROCEDURE section named with that ident. VCOA groups written
    into the DEPARTURE PROCEDURE section come before the VCOA section's, and
    a VCOA section group repeating one of them is dropped. A procedure with
    only a VCOA section has no runway groups. Raises `ParseError` when
    neither section is present or either fails to parse.
    """
    procedure, _ = _parse_procedure(sections, airport, in_part=False)
    return procedure


def parse_procedure_in_part(
    sections: Sections, *, airport: str
) -> tuple[Procedure, list[Unparsed]]:
    """`parse_procedure`, keeping every runway group the grammar can read.

    A runway group, the shared tail or the VCOA section that fails to parse
    is left out and reported as `Unparsed` rather than failing the whole
    procedure; so are the groups that continue into a tail that failed.
    Raises `ParseError` only when neither section is present.
    """
    return _parse_procedure(sections, airport, in_part=True)


def _parse_procedure(
    sections: Sections, airport: str, *, in_part: bool
) -> tuple[Procedure, list[Unparsed]]:
    procedure, unparsed = _departure_procedure(sections, airport, in_part=in_part)
    if sections.vcoa is None:
        return procedure, unparsed
    known = _named_navaids(procedure)
    try:
        vcoa = parse_vcoa(normalize(sections.vcoa), known_navaids=known)
    except ParseError as error:
        if not in_part:
            raise
        return procedure, [*unparsed, Unparsed((), error, vcoa=True)]
    repeated = set(procedure.vcoa)
    procedure = dataclasses.replace(
        procedure, vcoa=procedure.vcoa + tuple(g for g in vcoa if g not in repeated)
    )
    return _carry_radial_senses(procedure), unparsed


def _departure_procedure(
    sections: Sections, airport: str, *, in_part: bool
) -> tuple[Procedure, list[Unparsed]]:
    """The DEPARTURE PROCEDURE section's procedure, or an empty one beside a VCOA."""
    if sections.departure_procedure is not None:
        return _parse_departure_procedure(
            normalize(sections.departure_procedure),
            airport=airport,
            amendment=sections.amendment,
            in_part=in_part,
        )
    if sections.vcoa is None:
        raise ParseError("no departure procedure section")
    return Procedure(airport, sections.amendment, (), None, vcoa=()), []


def _carry_radial_senses(procedure: Procedure) -> Procedure:
    """Give a radial printed without "inbound" or "outbound" the sense of the
    leg after it when that leg flies the same navaid's same radial, e.g.
    "heading 022° to intercept GLL VOR/DME R-221 to 7000... ...proceed on GLL
    VOR/DME R-221 to GLL VOR/DME" (inbound). A runway group's last leg
    before "thence" is followed by the shared tail's first leg.
    """
    tail = procedure.shared_tail
    return dataclasses.replace(
        procedure,
        runway_groups=tuple(
            dataclasses.replace(group, legs=_carried(group.legs, tail))
            for group in procedure.runway_groups
        ),
        shared_tail=_carried(tail, None) if tail is not None else None,
        vcoa=tuple(
            dataclasses.replace(group, then=_carried(group.then, None))
            for group in procedure.vcoa
        ),
    )


def _carried(
    legs: tuple[Leg, ...], shared_tail: tuple[Leg, ...] | None
) -> tuple[Leg, ...]:
    carried = list(legs)
    for index in reversed(range(len(carried))):
        following = carried[index + 1] if index + 1 < len(carried) else None
        if isinstance(following, Thence) and shared_tail:
            following = shared_tail[0]
        carried[index] = _with_sense_of(carried[index], following)
    return tuple(carried)


def _with_sense_of(leg: Leg, following: Leg | None) -> Leg:
    if isinstance(leg, ClimbingTurn):
        return dataclasses.replace(leg, then=_with_sense_of(leg.then, following))
    if isinstance(following, ClimbingTurn):
        following = following.then
    if (
        isinstance(leg, Radial | HeadingAndRadial)
        and leg.outbound is None
        and isinstance(following, Radial | HeadingAndRadial)
        and following.outbound is not None
        and following.navaid.ident == leg.navaid.ident
        and following.radial == leg.radial
    ):
        return dataclasses.replace(leg, outbound=following.outbound)
    return leg


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
_RUNWAY_SIDES = ("L", "R", "C")
_DP_NAME_WORD = re.compile(r"[A-Z][A-Z0-9]*")
_DP_NAME_PUNCTUATION = frozenset("()'")
_DP_NAME_MAX_TOKENS = 8
# Lowercase words inside an airport name: "Augusta Rgnl at Bush Fld",
# "Prairie du Chien Muni".
_AIRPORT_NAME_CONNECTORS = frozenset({"at", "du"})


class _Parser(LegParser):
    """The procedure-level rules: runway groups, the shared tail, and VCOA."""

    def __init__(
        self,
        text: str,
        known_navaids: Mapping[str, NavaidRef] | None = None,
        *,
        in_part: bool = False,
    ):
        super().__init__(text, known_navaids)
        self._inline_vcoa: list[VcoaGroup] = []
        self._in_part = in_part
        self.unparsed: list[Unparsed] = []
        self._continuations: set[str] = set()

    @property
    def inline_vcoa(self) -> tuple[VcoaGroup, ...]:
        """VCOA groups the DEPARTURE PROCEDURE section wrote in, in text order."""
        return tuple(self._inline_vcoa)

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
        tail_error = None
        while not self._at_end():
            start, kept, inline = self._index, len(groups), len(self._inline_vcoa)
            try:
                if self._peek(*VISUAL_CLIMB):
                    raise self._error("visual climb without a runway")
                if self._starts_shared_tail():
                    shared_tail, for_all = self._shared_tail(groups)
                    if for_all:
                        groups = [_continued_to_tail(group) for group in groups]
                    break
                groups.extend(self._runway_groups())
                while (
                    self._peek_continuation() or self._peek_speed_sentence()
                ) and self._may_continue(groups):
                    last = groups[-1]
                    self._continuations.add(self._sentence())
                    if self._peek_speed_sentence():
                        legs = self._with_speed_sentence(last.legs)
                    else:
                        legs = last.legs + self._continuation()
                    groups[-1] = dataclasses.replace(last, legs=legs)
                if self._peek_thence_sentence():
                    groups[-1] = self._thence_sentence(groups[kept:], inline)
            except ParseError as error:
                if not self._in_part:
                    raise
                del groups[kept:]
                del self._inline_vcoa[inline:]
                if self._starts_shared_tail_at(start):
                    tail_error = error
                    if self._names_all_aircraft_at(start):
                        groups = [_continued_to_tail(group) for group in groups]
                    break
                runways = self._runways_at(start)
                if runways or not any(map(_withholdable, groups)):
                    self.unparsed.append(Unparsed(runways, error))
                else:
                    self._withhold(
                        groups, error, trailing=not self._header_after(start)
                    )
                self._skip_to_next_group(start)
        if self._in_part:
            if tail_error is None and not self._at_end():
                tail_error, shared_tail = self._unmatched(), None
            return self._readable(groups, shared_tail, tail_error)
        self._expect_end()
        self._require_tail_for_thence(groups, shared_tail)
        _require_routes_for_turns(groups, shared_tail)
        return tuple(groups), shared_tail

    def _readable(
        self,
        groups: list[RunwayGroup],
        shared_tail: tuple[Leg, ...] | None,
        tail_error: ParseError | None,
    ) -> tuple[tuple[RunwayGroup, ...], tuple[Leg, ...] | None]:
        """The groups whose whole route was read: a group continuing into a
        tail that failed, or into none, or ending in a turn with no route
        of its own and no tail to take one from, joins `unparsed`."""
        if shared_tail is not None and _ends_with_thence(shared_tail):
            tail_error = ParseError('"thence" inside the shared tail')
            shared_tail = None
        if tail_error is not None and not any(
            _ends_with_thence(group.legs) for group in groups
        ):
            self.unparsed.append(Unparsed((), tail_error))
        readable = []
        for group in groups:
            error = None
            if _ends_with_thence(group.legs) and shared_tail is None:
                error = tail_error or ParseError('"thence" without a shared tail')
            else:
                try:
                    _require_routes_for_turns([group], shared_tail)
                except ParseError as routeless:
                    error = routeless
            if error is None:
                readable.append(group)
            else:
                self.unparsed.append(Unparsed(group.runways, error))
        return tuple(readable), shared_tail

    def _runways_at(self, index: int) -> tuple[str, ...]:
        """The runways a header at token `index` names, or none if it names
        none the grammar can read."""
        resume = self._index
        self._index = index
        try:
            return self._runway_header()
        except ParseError:
            return ()
        finally:
            self._index = resume

    def _withhold(
        self, groups: list[RunwayGroup], error: ParseError, *, trailing: bool
    ) -> None:
        """Leave out the routes an unreadable sentence naming no runway may
        modify: after the last runway header, every route and the visual
        climbs written into the section; between headers, the runways just
        before it. A route is never drawn without a sentence it may have."""
        flown = [group for group in groups if _withholdable(group)]
        runways = None if trailing else flown[-1].runways
        for group in flown:
            if trailing or group.runways == runways:
                groups.remove(group)
                self.unparsed.append(Unparsed(group.runways, error))
        for vcoa in list(self._inline_vcoa):
            if trailing or vcoa.runways == runways:
                self._inline_vcoa.remove(vcoa)
                self.unparsed.append(Unparsed(vcoa.runways, error, vcoa=True))

    def _header_after(self, index: int) -> bool:
        """Whether a runway header opens a line or sentence after `index`."""
        return any(
            self._tokens[later].lower in ("rwy", "rwys") and self._opens_group(later)
            for later in range(index + 1, len(self._tokens))
        )

    def _names_all_aircraft_at(self, index: int) -> bool:
        """Whether the tail at token `index` is for "all aircraft", which
        every flown runway group continues into."""
        words = [token.lower for token in self._tokens[index : index + 4]]
        while words and words[0] in ("...", "thence"):
            words.pop(0)
        return words[:2] == ["all", "aircraft"]

    def _starts_shared_tail_at(self, index: int) -> bool:
        resume = self._index
        self._index = index
        try:
            return self._starts_shared_tail()
        finally:
            self._index = resume

    def _skip_to_next_group(self, start: int) -> None:
        """Resume at the next runway header or shared tail that opens a line
        or sentence after `start`, or at the end."""
        for index in range(start + 1, len(self._tokens)):
            if self._opens_group(index):
                self._index = index
                return
        self._index = len(self._tokens)

    def _opens_group(self, index: int) -> bool:
        token = self._tokens[index]
        previous = self._tokens[index - 1]
        at_boundary = (
            previous.text in (".", "...", ":", ";")
            or "\n" in (self._text[previous.end : token.start])
        )
        following = self._tokens[index + 1] if index + 1 < len(self._tokens) else None
        return at_boundary and (
            (
                token.lower in ("rwy", "rwys")
                and following is not None
                and bool(_RUNWAY.fullmatch(following.text))
            )
            or (token.text == "..." and "\n" in self._text[previous.end : token.start])
            or (
                token.lower == "all"
                and following is not None
                and following.lower in ("aircraft", "rwys", "runways")
            )
        )

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

    def _runway_groups(self) -> list[RunwayGroup]:
        """runway-group := runway-header (not-available | graphic-dp | vcoa-only
                           | legs range-alternative* [vcoa-alternative])
                         | all-runways (graphic-dp | vcoa-only)

        A runway whose only procedure is a visual climb yields no runway group;
        its VCOA group joins `inline_vcoa`. Each alternative to a heading range
        ("All other courses: …", "or climb on a heading between …") is another
        group for the same runways.
        """
        if self._accept("all", "rwys") or self._accept("all", "runways"):
            self._expect(",")
            return self._all_runways_group()
        runways = self._runway_header()
        if self._not_available():
            return [RunwayGroup(runways, ())]
        if self._peek_graphic_departure():
            return [RunwayGroup(runways, (self._graphic_departure(),))]
        if self._vcoa_only(runways):
            return []
        groups = [RunwayGroup(runways, self._flown_legs())]
        while _has_heading_range(groups[-1]) and self._range_alternative():
            groups.append(RunwayGroup(runways, self._flown_legs()))
        self._vcoa_alternative(runways)
        return groups

    def _may_continue(self, groups: list[RunwayGroup]) -> bool:
        """Whether a new sentence continues the last runway group's route.

        It does when another runway header follows it, when that is the only
        route, when every route ends at the same fix it starts from, or when
        it repeats word for word a sentence read for an earlier runway; after
        the last of several routes that end apart it could otherwise mean all
        of them, so it is left unread.
        """
        last = groups[-1] if groups else None
        if last is None or not _flies_route(last) or _ends_with_thence(last.legs):
            return False
        if self._later_runway_header():
            return True
        routes = [group for group in groups if _flies_route(group)]
        ends = {end_fix(group.legs[-1]) for group in routes}
        return (
            len(routes) == 1
            or (len(ends) == 1 and None not in ends)
            or self._sentence() in self._continuations
        )

    def _sentence(self) -> str:
        """The words from here to the end of the sentence, spaced as one line."""
        end = next(
            (
                index
                for index in range(self._index, len(self._tokens))
                if self._tokens[index].text == "."
            ),
            len(self._tokens) - 1,
        )
        return " ".join(token.text for token in self._tokens[self._index : end + 1])

    def _later_runway_header(self) -> bool:
        return any(
            token.lower in ("rwy", "rwys")
            and (following := self._tokens[index + 1 : index + 2])
            and _RUNWAY.fullmatch(following[0].text)
            for index, token in enumerate(self._tokens[self._index :], self._index)
        )

    def _peek_thence_sentence(self) -> bool:
        """``... direct RLY VOR/DME. Thence ...``: "thence" opening a sentence."""
        return (
            self._index > 0
            and self._tokens[self._index - 1].text == "."
            and self._peek("thence", "...")
        )

    def _thence_sentence(self, read: list[RunwayGroup], inline: int) -> RunwayGroup:
        """thence-sentence := "Thence" "...", after the sentences of the runway
        groups just `read`; returns the last of them, which it continues into
        the shared tail.

        A visual climb read beside that route ("..., or for climb in visual
        conditions, cross ... direct OED VORTAC. When executing VCOA, notify
        ATC prior to departure. Thence...") would continue into the tail too,
        so it is refused: the tail is drawn from the runways.
        """
        last = read[-1] if read else None
        if last is None or not _flies_route(last) or _ends_with_thence(last.legs):
            raise self._error('"thence" without a route')
        self._refuse_visual_climbs_into_tail(inline)
        self._expect("thence", "...")
        return _continued_to_tail(last)

    def _refuse_visual_climbs_into_tail(self, inline: int) -> None:
        """Leave out every visual climb read since the `inline`-th, as unread."""
        refused = self._inline_vcoa[inline:]
        if not refused:
            return
        error = self._error("visual climb into the shared tail")
        if not self._in_part:
            raise error
        del self._inline_vcoa[inline:]
        self.unparsed.extend(
            Unparsed(vcoa.runways, error, vcoa=True) for vcoa in refused
        )

    def _flown_legs(self) -> tuple[Leg, ...]:
        self._last_fix = None
        return self._legs([self._leg()])

    def _range_alternative(self) -> bool:
        """range-alternative := ["," | ";"] ("all other" ("courses" | "headings")
        [":" | ","] | "or"), leading the legs flown instead of a heading range.

        Only a group whose legs include a heading range is followed by one.
        """
        for lead in ((), (",",), (";",)):
            for words in RANGE_ALTERNATIVES:
                if self._peek(*lead, *words):
                    self._index += len(lead)
                    if self._accept("or"):
                        return True
                    self._index += len(words)
                    self._accept_any(":", ",")
                    return True
        return False

    def _all_runways_group(self) -> list[RunwayGroup]:
        if self._peek_graphic_departure():
            return [RunwayGroup((), (self._graphic_departure(),))]
        if self._vcoa_only(()):
            return []
        raise self._error('unsupported "all runways" group')

    def _vcoa_only(self, runways: tuple[str, ...]) -> bool:
        """vcoa-only := inline-vcoa | atc-approval visual-climb [notify-atc]"""
        if self._peek(*VISUAL_CLIMB):
            self._inline_vcoa.append(self._inline_visual_climb(runways))
            return True
        if self._peek("obtain", "atc"):
            self._inline_vcoa.append(self._vcoa_group(runways))
            return True
        return False

    def _vcoa_alternative(self, runways: tuple[str, ...]) -> None:
        """vcoa-alternative := ["," | ";"] ["or"] inline-vcoa, after the legs."""
        for lead in ((), (",",), (";",)):
            for conjunction in ((), ("or",)):
                if self._peek(*lead, *conjunction, *VISUAL_CLIMB):
                    self._index += len(lead) + len(conjunction)
                    self._inline_vcoa.append(self._inline_visual_climb(runways))
                    return

    def _inline_visual_climb(self, runways: tuple[str, ...]) -> VcoaGroup:
        """inline-vcoa := "for climb in visual conditions" [":" | ","] ["to"]
        "cross" crossing [notify-atc]"""
        self._expect(*VISUAL_CLIMB)
        self._accept_any(":", ",")
        self._accept("to")
        self._expect("cross")
        group = self._crossing(runways)
        self._notify_atc()
        return group

    def _runway_header(self) -> tuple[str, ...]:
        """runway-header := ("Rwy" | "Rwys") runway ("," runway)* ("," | ":" | ↓)

        ↓: the header also ends, unconsumed, at a climb printed without the
        comma before it ("Rwys 20C, 20R climb heading 201°").
        """
        if not self._accept_any("rwy", "rwys"):
            raise self._unmatched()
        runways = [*self._runway()]
        while not self._accept(":"):
            if self._peek_any_of(HEADER_LEG_WORDS):
                break
            self._expect(",")
            if not self._peek_runway():
                break
            runways.extend(self._runway())
        return tuple(runways)

    def _peek_runway(self) -> bool:
        token = self._token()
        return token is not None and bool(_RUNWAY.fullmatch(token.text))

    def _runway(self) -> list[str]:
        """runway := nn[LRC] ("/" [LRC])*  e.g. ``2L/R`` → ``2L``, ``2R``

        The sides of a pair may be printed apart from the number: ``2 L/R``.
        """
        if not self._peek_runway():
            raise self._unmatched()
        number = self._next().text
        if number.isdigit() and self._peek_spaced_side():
            number += self._next().text
        runways = [number]
        while self._peek_sibling_runway():
            self._index += 1
            runways.append(number.rstrip("LRC") + self._next().text)
        return runways

    def _peek_sibling_runway(self, offset: int = 0) -> bool:
        side = self._token(offset + 1)
        return (
            self._peek("/", offset=offset)
            and side is not None
            and side.text in _RUNWAY_SIDES
        )

    def _peek_spaced_side(self) -> bool:
        """``L/R`` printed apart from its runway number, as in ``35 L/R``."""
        side = self._token()
        return (
            side is not None
            and side.text in _RUNWAY_SIDES
            and self._peek_sibling_runway(1)
        )

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

    def _shared_tail(self, groups: list[RunwayGroup]) -> tuple[tuple[Leg, ...], bool]:
        """shared-tail := ("..." ["thence"] [all-aircraft] | all-aircraft) legs

        all-aircraft := "All aircraft" ["," | ":"]

        Returns the tail and whether it names "all aircraft", which every
        flown runway group then continues into.
        """
        if self._peek("...") and not _all_flown_end_with_thence(groups):
            raise self._error('"..." without a preceding "thence"')
        dotted = self._accept("...")
        if dotted:
            self._accept("thence")
        for_all = self._accept("all", "aircraft")
        if for_all:
            self._accept_any(",", ":")
        elif not dotted:
            raise self._unmatched()
        entering = [
            group
            for group in (map(_continued_to_tail, groups) if for_all else groups)
            if _ends_with_thence(group.legs)
        ]
        self._last_fix = _common_end_fix(entering)
        legs = self._legs([self._leg()])
        while self._peek_continuation() or self._peek_speed_sentence():
            if self._peek_speed_sentence():
                legs = self._with_speed_sentence(legs)
            else:
                legs += self._continuation()
        return legs, for_all

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

    def _vcoa_group(self, runways: tuple[str, ...] | None = None) -> VcoaGroup:
        """vcoa-group := [vcoa-runways] atc-approval visual-climb [notify-atc]

        ``runways`` is the runway header already read, if any.
        """
        if runways is None:
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
        """visual-climb := "climb in visual conditions to cross" crossing"""
        self._expect("climb", "in", "visual", "conditions", "to", "cross")
        return self._crossing(runways)

    def _crossing(self, runways: tuple[str, ...]) -> VcoaGroup:
        """crossing := (FIX | airport-name) [bound] "at or above" nnnn ["MSL"] legs

        Legs that continue into the DEPARTURE PROCEDURE's shared tail
        ("..., thence ...") are refused: the tail is drawn from the runways.
        """
        cross = self._vcoa_crossing()
        bound = self._bound()
        self._expect("at", "or", "above")
        feet = self._integer()
        self._accept("msl")
        self._last_fix = None
        legs = self._legs([])
        if legs and isinstance(legs[-1], Thence):
            raise ParseError("visual climb into the shared tail", "", self._position())
        return VcoaGroup(runways, cross, feet, legs, bound)

    def _vcoa_crossing(self) -> FixRef | None:
        """crossing := FIX | airport-name — ``None`` means the departure airport."""
        token = self._token()
        if (
            token is not None
            and is_ident_word(token.text)
            and len(token.text) == FIX_LENGTH
            and (self._peek("at", offset=1) or self._peek_bound(offset=1))
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
        navaid or fix is never mistaken for the airport. A lowercase "at" or
        "du" joins two capitalized words ("Augusta Rgnl at Bush Fld"); the
        name ends before "at or" and before a direction such as
        "southeast bound".
        """
        start = self._index
        while not self._peek("at", "or") and not self._peek_bound():
            if not (
                self._is_airport_name_token(self._token())
                or self._peek_name_connector()
            ):
                break
            self._index += 1
        name = self._tokens[start : self._index]
        if not any(token.text[1:].islower() for token in name):
            self._index = start
            raise self._unmatched()

    def _peek_name_connector(self) -> bool:
        token, following = self._token(), self._token(1)
        return (
            token is not None
            and token.text in _AIRPORT_NAME_CONNECTORS
            and following is not None
            and following.text[0].isupper()
        )

    @staticmethod
    def _is_airport_name_token(token: Token | None) -> bool:
        return token is not None and (
            (token.text[0].isupper() and token.lower not in NAVAID_TYPE_WORDS)
            or token.text in ("/", "(", ")", "-")
            or token.lower == "airport"
        )

    def _notify_atc(self) -> None:
        """notify-atc := "When executing" ["the"] "VCOA" [","] "notify ATC prior
        to departure." """
        if self._accept("when", "executing"):
            self._accept("the")
            self._expect("vcoa")
            self._accept(",")
            self._expect("notify", "atc", "prior", "to", "departure", ".")


def _all_flown_end_with_thence(groups: list[RunwayGroup]) -> bool:
    """Every runway group that flies a route (not an NA, charted-DP or
    heading-range group) ends in "thence"."""
    flown = [
        group
        for group in groups
        if group.legs and not group.graphic and not _has_heading_range(group)
    ]
    return bool(flown) and all(_ends_with_thence(group.legs) for group in flown)


def _withholdable(group: RunwayGroup) -> bool:
    """Whether the group draws anything a later sentence could change."""
    return bool(group.legs) and not group.graphic


def _flies_route(group: RunwayGroup) -> bool:
    return bool(group.legs) and not group.graphic and not _has_heading_range(group)


def _continued_to_tail(group: RunwayGroup) -> RunwayGroup:
    """A group flying a route that "all aircraft" continue from: it ends in
    "thence" whether or not the text said so."""
    if (
        not group.legs
        or group.graphic
        or _has_heading_range(group)
        or _ends_with_thence(group.legs)
    ):
        return group
    return dataclasses.replace(group, legs=(*group.legs, Thence()))


def _common_end_fix(groups: list[RunwayGroup]) -> NavaidRef | FixRef | None:
    """The fix every group reaches before "thence", if they all reach one."""
    ends = {
        end_fix(group.legs[-2]) if len(group.legs) > 1 else None for group in groups
    }
    return ends.pop() if len(ends) == 1 else None


def _require_routes_for_turns(
    groups: list[RunwayGroup], shared_tail: tuple[Leg, ...] | None
) -> None:
    """A turn with no route of its own must be its group's last leg before a
    shared tail whose first leg it turns onto."""
    for group in groups:
        legs = [leg for leg in group.legs if not isinstance(leg, Thence)]
        for index, leg in enumerate(legs):
            if isinstance(leg, ClimbingTurn) and leg.then is None:
                last = index == len(legs) - 1
                if not (last and shared_tail and _ends_with_thence(group.legs)):
                    raise ParseError("turn without a route", "", 0)


def _has_heading_range(group: RunwayGroup) -> bool:
    """Whether the group climbs within a heading range, which ends its route."""
    return any(
        isinstance(leg.then if isinstance(leg, ClimbingTurn) else leg, HeadingRange)
        for leg in group.legs
    )


def _ends_with_thence(legs: tuple[Leg, ...]) -> bool:
    return bool(legs) and isinstance(legs[-1], Thence)
