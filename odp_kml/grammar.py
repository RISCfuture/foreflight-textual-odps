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
from collections.abc import Iterable, Iterator, Mapping

from .legs import (
    CONTINUATION_WORDS,
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
    HeadingSector,
    Leg,
    NavaidRef,
    Procedure,
    ProceedOnCourse,
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
    added, refused = _open_runway_vcoa(
        [g for g in vcoa if g not in repeated], procedure.runway_groups
    )
    if refused and not in_part:
        raise refused[0].error
    procedure = dataclasses.replace(procedure, vcoa=procedure.vcoa + added)
    return _carry_radial_senses(procedure), [*unparsed, *refused]


def _open_runway_vcoa(
    vcoa: list[VcoaGroup], groups: tuple[RunwayGroup, ...]
) -> tuple[tuple[VcoaGroup, ...], list[Unparsed]]:
    """The VCOA section's groups split into those from runways the departure
    procedure leaves open and those it refuses."""
    kept, refused = [], []
    for group in vcoa:
        try:
            _require_vcoa_runways_open(group, groups)
            kept.append(group)
        except ParseError as error:
            refused.append(Unparsed(group.runways, error, vcoa=True))
    return tuple(kept), refused


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
_DEPARTURE_WORDS = ("departure", "departures")
_NA_REASONS = ("obstacles", "atc", "terrain", "environmental")


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
        self._visual_climb_end: int | None = None
        self._tail_vcoa: VcoaGroup | None = None
        self._in_part = in_part
        self.unparsed: list[Unparsed] = []
        self._continuations: set[str] = set()
        self._unrouted: set[str] = set()

    @property
    def inline_vcoa(self) -> tuple[VcoaGroup, ...]:
        """VCOA groups the DEPARTURE PROCEDURE section wrote in, in text order."""
        tail = () if self._tail_vcoa is None else (self._tail_vcoa,)
        return (*self._inline_vcoa, *tail)

    # --- Departure procedure -----------------------------------------------

    def departure_procedure(
        self,
    ) -> tuple[tuple[RunwayGroup, ...], tuple[Leg, ...] | None]:
        """procedure := no-departure
                      | [graphic-dp] (statement | runway-group)* [shared-tail]

        A leading graphic-dp names the charted DP every runway flies.
        """
        if self._at_end():
            raise ParseError("empty departure procedure")
        if self._no_departure():
            return (RunwayGroup((), ()),), None
        groups: list[RunwayGroup] = []
        if self._peek_graphic_departure():
            groups.append(RunwayGroup((), (self._graphic_departure(),)))
        shared_tail = None
        tail_error = None
        while not self._at_end():
            start, kept, inline = self._index, len(groups), len(self._inline_vcoa)
            try:
                if self._statement():
                    continue
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
        for group in groups:
            _require_no_dp_for_every_runway(group, groups)
            self._require_sole_reading(group, groups, tail=shared_tail is not None)
        for vcoa in self._inline_vcoa:
            _require_vcoa_runways_open(vcoa, groups)
        return tuple(groups), shared_tail

    def _readable(
        self,
        groups: list[RunwayGroup],
        shared_tail: tuple[Leg, ...] | None,
        tail_error: ParseError | None,
    ) -> tuple[tuple[RunwayGroup, ...], tuple[Leg, ...] | None]:
        """The groups whose whole route was read: a group continuing into a
        tail that failed, or into none, ending in a turn with no route of its
        own and no tail to take one from, beside a charted DP for every
        runway, or read two ways (see `_require_sole_reading`), joins
        `unparsed`; so does a visual climb from a runway with no route, and
        the visual climb written after a tail that failed."""
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
                    _require_no_dp_for_every_runway(group, groups)
                    self._require_sole_reading(
                        group,
                        groups,
                        tail=shared_tail is not None or tail_error is not None,
                    )
                except ParseError as refused:
                    error = refused
            if error is None:
                readable.append(group)
            else:
                self.unparsed.append(Unparsed(group.runways, error))
        self._withhold_vcoa_from_closed_runways(groups)
        if tail_error is not None:
            self._withhold_tail_vcoa(tail_error)
        return tuple(readable), shared_tail

    def _require_sole_reading(
        self, group: RunwayGroup, groups: list[RunwayGroup], *, tail: bool
    ) -> None:
        """A runway given no route ("Rwy 10, NA." or "Rwys 10L/R, right turn
        on departure NA.") and a route elsewhere in the section could fly
        either, so both are refused. So is a runway that departs with no
        route of its own beside a shared tail, which may be its route."""
        if any(
            other is not group
            and not (group.legs and other.legs)
            and set(group.runways) & set(other.runways)
            for other in groups
        ):
            raise ParseError("runway both routed and given no route", "", 0)
        if tail and not group.legs and self._unrouted & set(group.runways):
            raise ParseError("runway with no route beside a shared tail", "", 0)

    def _withhold_vcoa_from_closed_runways(self, groups: list[RunwayGroup]) -> None:
        for vcoa in list(self._inline_vcoa):
            try:
                _require_vcoa_runways_open(vcoa, groups)
            except ParseError as error:
                self._inline_vcoa.remove(vcoa)
                self.unparsed.append(Unparsed(vcoa.runways, error, vcoa=True))
        if self._tail_vcoa is not None:
            try:
                _require_vcoa_runways_open(self._tail_vcoa, groups)
            except ParseError as error:
                self._withhold_tail_vcoa(error)

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

    def _withhold_tail_vcoa(self, error: ParseError) -> None:
        if self._tail_vcoa is not None:
            self.unparsed.append(Unparsed(self._tail_vcoa.runways, error, vcoa=True))
            self._tail_vcoa = None

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
        """runway-group := runway-header [equipment] (not-available
                           | unrouted | diverse-range
                           | graphic-dp | vcoa-only | [diverse-lead-in] legs
                             range-alternative* [vcoa-alternative])
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
        self._equipment_note()
        if self._not_available():
            return [RunwayGroup(runways, ())]
        if self._departs_without_route():
            self._unrouted.update(runways)
            return [RunwayGroup(runways, ())]
        if (diverse_range := self._diverse_range()) is not None:
            return [RunwayGroup(runways, (diverse_range,))]
        if self._peek_graphic_departure():
            return [RunwayGroup(runways, (self._graphic_departure(),))]
        if self._vcoa_only(runways):
            return []
        groups = [RunwayGroup(runways, self._first_legs(runways))]
        while _has_heading_range(groups[-1]) and self._range_alternative():
            groups.append(RunwayGroup(runways, self._alternative_legs(groups)))
        self._vcoa_alternative(runways)
        return groups

    def _may_continue(self, groups: list[RunwayGroup]) -> bool:
        """Whether a new sentence continues the last runway group's route.

        It does when another runway header follows it, when that is the only
        route, when every route ends at the same fix it starts from, or when
        it repeats word for word a sentence read for an earlier runway; after
        the last of several routes that end apart it could otherwise mean all
        of them, so it is left unread. So is one straight after a visual
        climb written into the section, which it may continue instead. A
        speed limit sentence may also limit a heading range, so for one a
        heading range counts as a route; one that could limit more than the
        last group is left unread too.
        """
        speed = self._peek_speed_sentence()
        takes = _takes_speed_limit if speed else _flies_route
        last = groups[-1] if groups else None
        if last is None or not takes(last) or _ends_with_thence(last.legs):
            return False
        if self._follows_visual_climb():
            return False
        if speed and self._speed_limit_may_mean_others(groups):
            return False
        if self._later_runway_header():
            return True
        routes = [group for group in groups if takes(group)]
        ends = {end_fix(group.legs[-1]) for group in routes}
        return (
            len(routes) == 1
            or (len(ends) == 1 and None not in ends)
            or self._sentence() in self._continuations
        )

    def _follows_visual_climb(self) -> bool:
        return self._index == self._visual_climb_end

    def _speed_limit_may_mean_others(self, groups: list[RunwayGroup]) -> bool:
        """Whether a speed limit sentence here could limit more than the last
        group, one of several alternatives for the same runways."""
        last = groups[-1]
        alternatives = [
            group
            for group in groups
            if group.runways == last.runways and _takes_speed_limit(group)
        ]
        return len(alternatives) > 1

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

    def _alternative_legs(self, groups: list[RunwayGroup]) -> tuple[Leg, ...]:
        """The legs flown instead of the heading ranges in `groups`, or the
        minimum climb the headings they leave out need ("or min. climb of 250
        ft per NM to 2000 for all other courses")."""
        if self._peek_minimum_climb():
            return (self._minimum_climb(_range_sectors(groups)),)
        return self._flown_legs()

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
            self._add_inline_vcoa(self._inline_visual_climb(runways))
            return True
        if self._peek("obtain", "atc"):
            self._add_inline_vcoa(self._vcoa_group(runways))
            return True
        return False

    def _vcoa_alternative(self, runways: tuple[str, ...]) -> None:
        """vcoa-alternative := ["," | ";"] ["or"] inline-vcoa, after the legs."""
        for lead in ((), (",",), (";",)):
            for conjunction in ((), ("or",)):
                if self._peek(*lead, *conjunction, *VISUAL_CLIMB):
                    self._index += len(lead) + len(conjunction)
                    self._add_inline_vcoa(self._inline_visual_climb(runways))
                    return

    def _add_inline_vcoa(self, group: VcoaGroup) -> None:
        """Keep a visual climb written into the section.

        A speed limit sentence right after it could limit the runway's route
        as well as the climb: before another runway header, that runway is
        left unread; after the last, the sentence is, which withholds every
        route and visual climb it may modify.
        """
        if self._peek_speed_sentence() and self._later_runway_header():
            raise self._error("speed limit after a visual climb")
        self._inline_vcoa.append(group)
        self._visual_climb_end = self._index

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
        """not-available := [departure-word] "NA" ["-" na-reason] "."

        na-reason := "Obstacles" | "ATC" | "Terrain" | "Environmental"
        """
        start = self._index
        self._accept_any(*_DEPARTURE_WORDS)
        if self._accept("na", ".") or any(
            self._accept("na", "-", reason, ".") for reason in _NA_REASONS
        ):
            return True
        self._index = start
        return False

    def _departs_without_route(self) -> bool:
        """unrouted := turn-not-available | diverse-na
                     | "diverse" departure-word ("authorized" | "auth") "."

        turn-not-available := ("left" | "right") ("turn" | "turns")
                              "on departure NA."

        A runway restricted only in which way it may turn, or whose diverse
        departure is on any heading or none (a charted DP the text does not
        name), departs with no route to draw. It is read only when nothing
        else in the section routes it: see `_require_sole_reading`.
        """
        return (
            self._turn_not_available()
            or self._diverse_departures_na()
            or self._accept_diverse("authorized", ".")
            or self._accept_diverse("auth", ".")
        )

    def _turn_not_available(self) -> bool:
        start = self._index
        if (
            self._accept_any("left", "right")
            and self._accept_any("turn", "turns")
            and self._accept("on", "departure", "na", ".")
        ):
            return True
        self._index = start
        return False

    def _accept_diverse(self, *words: str) -> bool:
        """Consume "diverse departure" or "diverse departures" and `words`."""
        return any(
            self._accept("diverse", departure, *words) for departure in _DEPARTURE_WORDS
        )

    def _diverse_departures_na(self) -> bool:
        """diverse-na := "diverse" departure-word "NA" ("." [use-published]
        | "," use-published)"""
        if not self._accept_diverse("na"):
            return False
        if self._accept(","):
            self._use_published_departure()
            return True
        self._expect(".")
        if self._peek("use", "published") or self._peek("use", "radar"):
            self._use_published_departure()
        return True

    def _use_published_departure(self) -> None:
        """use-published := "use" ["RADAR vectors or"] "published departure"
        ["procedure" | "procedures"] ["(DP)"] ["for obstacle avoidance"] "."

        A charted DP the text does not name, flown instead of a diverse
        departure.
        """
        self._expect("use")
        self._accept("radar", "vectors", "or")
        self._expect("published", "departure")
        self._accept_any("procedure", "procedures")
        self._accept("(", "dp", ")")
        self._accept("for", "obstacle", "avoidance")
        self._expect(".")

    def _diverse_range(self) -> HeadingRange | None:
        """diverse-range := "diverse" departure-word ["authorized"] ["only"]
        ["from" | "between"] diverse-sector "."

        The headings a diverse departure may turn to ("diverse departures
        authorized 300° to 120° CW"), flown as a heading range. ``None`` when
        the text names no headings.
        """
        start = self._index
        if not self._accept_diverse():
            return None
        self._accept("authorized")
        self._accept("only")
        self._accept_any("from", "between")
        if not self._peek_integer():
            self._index = start
            return None
        sector = self._diverse_sector()
        self._expect(".")
        return HeadingRange((sector,))

    def _diverse_sector(self) -> HeadingSector:
        """diverse-sector := sector | nnn ["°"] ("to" | "through") nnn ["°"]
        ("CW" | "CCW")

        A sector printed with no sweep sense ("140° to 290°") could run either
        way round, so it is refused.
        """
        start = self._index
        first = self._compass_heading()
        if not self._accept_any("to", "through"):
            self._index = start
            return self._sector()
        last = self._compass_heading()
        if self._accept("cw"):
            return HeadingSector(first, last, clockwise=True)
        if self._accept("ccw"):
            return HeadingSector(first, last, clockwise=False)
        raise self._error("heading sector without CW or CCW")

    def _first_legs(self, runways: tuple[str, ...]) -> tuple[Leg, ...]:
        """[diverse-lead-in] legs

        diverse-lead-in := "diverse" departure-word "authorized" ","

        A lead-in bounds a diverse departure by the heading range the legs
        climb within ("diverse departure authorized, climb on heading between
        080° CW to 259° from DER"), so the legs must include one.
        """
        start = self._index
        if not self._accept_diverse("authorized", ","):
            return self._flown_legs()
        legs = self._flown_legs()
        if not _has_heading_range(RunwayGroup(runways, legs)):
            self._index = start
            raise self._error("diverse departure beside a route")
        return legs

    def _equipment_note(self) -> None:
        """equipment := "DME required" ("," | "."), which changes nothing drawn."""
        if self._accept("dme", "required"):
            self._expect_any(",", ".")

    def _no_departure(self) -> bool:
        """no-departure := (not-available | "diverse" departure-word
        "authorized all runways.") ↓

        ↓: the sentence is the whole section, so no runway has a route. Beside
        any other text it is left unread, withholding the routes it may modify.
        """
        start = self._index
        if (
            self._not_available()
            or self._accept_diverse("authorized", "all", "runways", ".")
        ) and self._at_end():
            return True
        self._index = start
        return False

    def _statement(self) -> bool:
        """statement := "DME required." | diverse-na

        A sentence naming no runway that changes nothing drawn: an equipment
        requirement, or that no runway has a diverse departure (only the
        routes the runway groups give may be flown).
        """
        return self._accept("dme", "required", ".") or self._diverse_departures_na()

    def _starts_shared_tail(self) -> bool:
        return (
            self._peek("...")
            or self._peek("all", "aircraft")
            or self._peek_tail_after_thence()
        )

    def _peek_tail_after_thence(self) -> bool:
        """Whether the tail's legs follow the last runway group's "thence..."
        directly, with no ellipsis of their own: "thence...continue climb in
        ...". Before a later runway header they follow some other group's."""
        return (
            self._index > 0
            and self._tokens[self._index - 1].text == "..."
            and self._peek_any_of(CONTINUATION_WORDS)
            and not self._later_runway_header()
        )

    def _shared_tail(self, groups: list[RunwayGroup]) -> tuple[tuple[Leg, ...], bool]:
        """shared-tail := ("..." ["thence"] [all-aircraft] | all-aircraft | ↓)
                       legs [tail-vcoa]

        all-aircraft := "All aircraft" ["," | ":"]

        ↓: the legs follow the last group's "thence ..." directly.

        tail-vcoa := "Or" inline-vcoa, a sentence of its own straight after
        tail legs that end on course ("... before proceeding on course. Or for
        climb in visual conditions cross ..."). It is read only when every
        runway group drawing a route continues into the tail, and is for
        those runways. Returns the tail and whether it names "all aircraft",
        which every flown runway group then continues into.
        """
        after_thence = self._peek_tail_after_thence()
        if (self._peek("...") or after_thence) and not _all_flown_end_with_thence(
            groups
        ):
            raise self._error('"..." without a preceding "thence"')
        dotted = self._accept("...") or after_thence
        if dotted:
            self._accept("thence")
        for_all = self._accept("all", "aircraft")
        if for_all:
            self._accept_any(",", ":")
        elif not dotted:
            raise self._unmatched()
        continuing = list(map(_continued_to_tail, groups)) if for_all else groups
        entering = [group for group in continuing if _ends_with_thence(group.legs)]
        self._last_fix = _common_end_fix(entering)
        legs = self._legs([self._leg()])
        ends_on_speed = False
        while self._peek_continuation() or self._peek_speed_sentence():
            if ends_on_speed := self._peek_speed_sentence():
                legs = self._with_speed_sentence(legs)
            else:
                legs += self._continuation()
        if not ends_on_speed and self._peek_tail_vcoa(legs, continuing):
            self._expect("or")
            self._tail_vcoa = self._inline_visual_climb(_runways_of(entering))
        return legs, for_all

    def _peek_tail_vcoa(
        self, tail: tuple[Leg, ...], continuing: list[RunwayGroup]
    ) -> bool:
        return (
            self._follows_period()
            and isinstance(tail[-1], ProceedOnCourse)
            and self._peek("or", *VISUAL_CLIMB)
            and _every_route_enters_tail(continuing)
        )

    def _require_tail_for_thence(
        self, groups: list[RunwayGroup], shared_tail: tuple[Leg, ...] | None
    ) -> None:
        if shared_tail is not None and _ends_with_thence(shared_tail):
            raise ParseError('"thence" inside the shared tail', "", len(self._text))
        if shared_tail is None and any(_ends_with_thence(g.legs) for g in groups):
            raise ParseError('"thence" without a shared tail', "", len(self._text))

    # --- VCOA --------------------------------------------------------------

    def vcoa(self) -> tuple[VcoaGroup, ...]:
        """vcoa := (vcoa-group continuation* [speed-sentence])+

        A sentence going on with a group's route ("... to DSD VORTAC.
        Continue climb in DSD holding pattern ...") belongs to that group
        when it is the section's first or another runway header follows;
        after the last of several groups it could mean any of them, so it
        is left unread.
        """
        if self._at_end():
            raise ParseError("empty VCOA")
        groups = []
        while not self._at_end():
            group = self._vcoa_group()
            while self._peek_continuation() and (
                not groups or self._later_runway_header()
            ):
                group = self._continued_vcoa(group)
            groups.append(self._with_speed_limit(group, first=not groups))
        return tuple(groups)

    def _continued_vcoa(self, group: VcoaGroup) -> VcoaGroup:
        legs = group.then + self._continuation()
        if isinstance(legs[-1], Thence):
            raise self._error("visual climb into the shared tail")
        return dataclasses.replace(group, then=legs)

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

    def _with_speed_limit(self, group: VcoaGroup, *, first: bool) -> VcoaGroup:
        """speed-sentence := speed "."

        The VCOA section's visual climb with the speed limit the sentence
        after it puts on it ("Do not exceed 180 KIAS until reaching 1800
        MSL.") when it is the section's first or another runway header
        follows; after the last of several the sentence could mean any of
        them, so the section is left unread.
        """
        if not self._peek_speed_sentence():
            return group
        if not first and not self._later_runway_header():
            raise self._error("speed limit after several visual climbs")
        speed = self._speed_restriction()
        self._expect(".")
        return dataclasses.replace(group, speed=speed)

    def _vcoa_runways(self) -> tuple[str, ...]:
        """vcoa-runways := runway-header | "All" ("Rwys" | "runways") ("," | ":")

        An empty tuple means every runway.
        """
        if self._peek("rwy") or self._peek("rwys"):
            return self._runway_header()
        if self._accept("all", "rwys") or self._accept("all", "runways"):
            self._expect_any(",", ":")
        return ()

    def _atc_approval(self) -> None:
        """atc-approval := "obtain ATC approval for" ("VCOA" | "climb in visual
        conditions") "when requesting IFR clearance." """
        self._expect("obtain", "atc", "approval", "for")
        if not self._accept("vcoa"):
            self._expect(*VISUAL_CLIMB[1:])
        self._expect("when", "requesting", "ifr", "clearance", ".")

    def _visual_climb(self, runways: tuple[str, ...]) -> VcoaGroup:
        """visual-climb := [equipment] "climb in visual conditions to cross"
        crossing"""
        self._equipment_note()
        self._expect("climb", "in", "visual", "conditions", "to", "cross")
        return self._crossing(runways)

    def _crossing(self, runways: tuple[str, ...]) -> VcoaGroup:
        """crossing := (FIX | airport-name) [bound] ["at"] "or above" nnnn ["MSL"]
        legs

        "at" is sometimes left out ("cross Telluride RGNL airport westbound or
        above 14300"). Legs that continue into the DEPARTURE PROCEDURE's
        shared tail ("..., thence ...") are refused: the tail is drawn from
        the runways.
        """
        cross = self._vcoa_crossing()
        bound = self._bound()
        self._accept("at")
        self._expect("or", "above")
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
    """Every runway group that flies a route (not an NA, charted-DP,
    heading-range or minimum-climb group) ends in "thence"."""
    flown = [group for group in groups if _flies_route(group)]
    return bool(flown) and all(_ends_with_thence(group.legs) for group in flown)


def _withholdable(group: RunwayGroup) -> bool:
    """Whether the group draws anything a later sentence could change."""
    return bool(group.legs) and not group.graphic


def _flies_route(group: RunwayGroup) -> bool:
    return (
        _withholdable(group)
        and not _has_heading_range(group)
        and not group.climb_gradient_only
    )


def _takes_speed_limit(group: RunwayGroup) -> bool:
    """Whether the group flies a route or a heading range, either of which a
    speed limit sentence could apply to."""
    return _withholdable(group) and not group.climb_gradient_only


def _continued_to_tail(group: RunwayGroup) -> RunwayGroup:
    """A group flying a route that "all aircraft" continue from: it ends in
    "thence" whether or not the text said so."""
    if not _flies_route(group) or _ends_with_thence(group.legs):
        return group
    return dataclasses.replace(group, legs=(*group.legs, Thence()))


def _every_route_enters_tail(groups: list[RunwayGroup]) -> bool:
    """At least one runway group continues into the shared tail, and every
    group drawing a route (a heading range included) does."""
    drawn = [group for group in groups if _withholdable(group)]
    return bool(drawn) and all(_ends_with_thence(group.legs) for group in drawn)


def _runways_of(groups: list[RunwayGroup]) -> tuple[str, ...]:
    """Every runway the groups name, in order, each once."""
    return tuple(dict.fromkeys(runway for group in groups for runway in group.runways))


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


def _require_no_dp_for_every_runway(
    group: RunwayGroup, groups: list[RunwayGroup]
) -> None:
    """A runway route beside a charted DP that names no runway could be flown
    instead of the DP, or the DP instead of it ("Rwy 25, diverse departure
    authorized 120° to 330° CW. All Rwys, use NASWI TWO (OBSTACLE)
    DEPARTURE."), so it is refused."""
    if _withholdable(group) and any(
        other.graphic and not other.runways for other in groups
    ):
        raise ParseError("route beside a charted DP for every runway", "", 0)


def _require_vcoa_runways_open(vcoa: VcoaGroup, groups: Iterable[RunwayGroup]) -> None:
    """A visual climb from a runway the section gives no route (NA, or with
    a turn it may not make) is refused, as is one for every runway beside
    such a runway, or any beside a section that gives no runway a route: the
    restriction may apply to it."""
    closed = [group.runways for group in groups if not group.legs]
    if any(
        not runways or not vcoa.runways or set(runways) & set(vcoa.runways)
        for runways in closed
    ):
        raise ParseError("visual climb from a runway with no route", "", 0)


def _has_heading_range(group: RunwayGroup) -> bool:
    """Whether the group climbs within a heading range, which ends its route."""
    return any(
        isinstance(leg.then if isinstance(leg, ClimbingTurn) else leg, HeadingRange)
        for leg in group.legs
    )


def _range_sectors(groups: list[RunwayGroup]) -> tuple[HeadingSector, ...]:
    """The sectors of every heading range the groups climb within."""
    turned = (
        leg.then if isinstance(leg, ClimbingTurn) else leg
        for group in groups
        for leg in group.legs
    )
    return tuple(
        sector
        for leg in turned
        if isinstance(leg, HeadingRange)
        for sector in leg.sectors
    )


def _ends_with_thence(legs: tuple[Leg, ...]) -> bool:
    return bool(legs) and isinstance(legs[-1], Thence)
