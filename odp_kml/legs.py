"""The leg grammar of DEPARTURE PROCEDURE and VCOA text: climbs, turns,
headings, radials, leg terminators, holds, and navaid or fix references.

`LegParser` has no "skip unknown words" rule; every rule either consumes its
phrase or raises `ParseError`.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from typing import TypeGuard

from .procedure import (
    Altitude,
    AltitudeKind,
    AtFix,
    ClimbHeading,
    ClimbingTurn,
    ClimbInHold,
    Compass8,
    CrossAt,
    CrossRadial,
    Direct,
    Dme,
    EnrouteAltitude,
    FixRef,
    HeadingAndRadial,
    HeadingRange,
    HeadingSector,
    HoldSpec,
    Leg,
    MinimumClimb,
    NavaidRef,
    NavaidType,
    ProceedOnCourse,
    Radial,
    RunwayHeading,
    SpeedRestriction,
    StraightAhead,
    Thence,
    Turn,
    Until,
)
from .tokens import NUMBER, ParseError, TokenStream, is_ident_word, phrase_signature

_MAX_IDENT_LENGTH = 4
INTERSECTION = "INT"
MAX_HEADING = 360
_MAX_DME_IDENT_LENGTH = 3
FIX_LENGTH = 5

COMPASS_WORDS = {
    "north": Compass8.N,
    "northeast": Compass8.NE,
    "east": Compass8.E,
    "southeast": Compass8.SE,
    "south": Compass8.S,
    "southwest": Compass8.SW,
    "west": Compass8.W,
    "northwest": Compass8.NW,
    **{point.value.lower(): point for point in Compass8},
}
_TURN_WORDS = {"left": Turn.LEFT, "right": Turn.RIGHT}
_TURN_ABBREVIATIONS = {"lt": Turn.LEFT, "rt": Turn.RIGHT}
# What may follow a turn that has no route of its own.
_ROUTE_ENDS = frozenset({",", ".", ";", "thence", "..."})
# What may follow an altitude printed before the route that climbs to it.
_ROUTE_LEADS = frozenset({"via", "on", "heading", "hdg"})
_LEG_SEPARATORS = frozenset({",", ";"})
# What leads from a climb that names no route into what is flown after it.
_SEQUENCE_WORDS = frozenset({"then", "thence", "..."})
# What may follow a runway header printed without its comma.
HEADER_LEG_WORDS = frozenset({"climb", "climbing"})
# What leads a leg printed without "climb": "then on SLI VORTAC R-210".
_ROUTE_PREPOSITIONS = frozenset({"on", "via"})
_INTERCEPT_WORDS = frozenset({"intercept", "join"})
_NAVAID_TYPES = (
    (("vor", "/", "dme"), NavaidType.VOR_DME),
    (("vortac",), NavaidType.VORTAC),
    (("vor",), NavaidType.VOR),
    (("ndb",), NavaidType.NDB),
    (("dme",), NavaidType.DME),
)
NAVAID_TYPE_WORDS = frozenset(words[0] for words, _ in _NAVAID_TYPES)
VISUAL_CLIMB = ("for", "climb", "in", "visual", "conditions")
# Leg words, printed in lowercase, that continue a sentence without a separator.
_UNSEPARATED_LEG_WORDS = frozenset({"climb", "climbing"})
ENROUTE_MINIMUMS = frozenset({"mea", "mca", "moca"})
CONTINUATION_WORDS = frozenset(
    {"climb", "climbing", "continue", "proceed", "direct", "cross"}
)
ENROUTE_LEADS = frozenset({"airway", "appropriate"})
_CROSSINGS = frozenset({"cross", "depart"})
_COMPASS_POINT_DEG = 45
_SIDE_TOLERANCE_DEG = 30
BOUND_WORDS = {
    f"{word}bound": point for word, point in COMPASS_WORDS.items() if len(word) > 2
}
_BOUND_TOLERANCE_DEG = 67.5


class LegParser(TokenStream):
    """The leg grammar, one method per rule, each named after the phrase it accepts.

    ``known_navaids`` maps a navaid name to the reference that carried it
    with an ident elsewhere in the procedure.
    """

    def __init__(self, text: str, known_navaids: Mapping[str, NavaidRef] | None = None):
        super().__init__(text)
        self._known_navaids = known_navaids or {}
        self._last_fix: NavaidRef | FixRef | None = None

    # --- Leg sequences -----------------------------------------------------

    def _legs(self, legs: list[Leg]) -> tuple[Leg, ...]:
        """legs := leg ((separator leg) | before | speed | and-hold
                   | fix-crossing)* ("." | thence | ↓)

        ↓: the legs also end, unconsumed, where an inline VCOA alternative
        ("..., or for climb in visual conditions") begins, and where the text
        ends right after a "before ..." clause that lacks its period. Tracks
        the fix each leg ends at, for a hold that names none.
        """
        self._last_fix = end_fix(legs[-1]) if legs else None
        while True:
            if self._peek_before() or (self._peek(",") and self._peek_before_at(1)):
                self._accept(",")
                self._append(legs, self._before())
                if self._at_end():
                    return tuple(legs)
            elif (
                (legs and self._peek_vcoa_alternative())
                or self._peek_range_alternative(legs)
                or self._accept(".")
                or self._ends_on_course_unpunctuated(legs)
            ):
                return tuple(legs)
            elif self._thence():
                return (*legs, Thence())
            elif legs and self._peek_speed_restriction():
                legs[-1] = self._with_speed(legs[-1], self._speed_restriction())
            elif legs and self._peek("and", "hold"):
                self._append(legs, self._and_hold())
            elif legs and self._peek_fix_crossing():
                self._append(legs, self._fix_crossing())
            else:
                self._reject_airway_routing()
                self._leg_separator(required=bool(legs))
                self._append(legs, self._next_leg(legs))

    def _accept_on_course(self) -> bool:
        """on-course := "proceed on course" | "on assigned route", a leg that
        leaves the procedure as "before proceeding on course" does: "..., then
        proceed on course.", "...; then on assigned route."."""
        return self._accept("proceed", "on", "course") or self._accept(
            "on", "assigned", "route"
        )

    def _ends_on_course_unpunctuated(self, legs: list[Leg]) -> bool:
        """A route that goes on course may omit its period where the text ends
        ("... before turning left") or a runway header opens the next line
        ("... on course⏎Rwy 26, climb ..."); a route ending anywhere else is
        not taken as whole without one."""
        return ends_on_course(legs) and (
            self._at_end() or self._peek_runway_header_line()
        )

    def _peek_runway_header_line(self) -> bool:
        token, runway = self._token(), self._token(1)
        return (
            token is not None
            and runway is not None
            and token.lower in ("rwy", "rwys")
            and runway.text[0].isdigit()
            and "\n" in self._text[self._tokens[self._index - 1].end : token.start]
        )

    def _append(self, legs: list[Leg], leg: Leg) -> None:
        legs.append(leg)
        self._last_fix = end_fix(leg)

    def _thence(self) -> bool:
        """thence := ["," | ";"] "thence" "..." | ["," | ";"] "..."

        An ellipsis alone ("to 7000...") also leads into the shared tail.
        """
        lead = 1 if self._peek(",") or self._peek(";") else 0
        if not (self._peek("thence", offset=lead) or self._peek("...", offset=lead)):
            return False
        self._index += lead
        self._accept("thence")
        self._expect("...")
        return True

    def _leg_separator(self, *, required: bool) -> None:
        """separator := ("," | ";") ["then"] | "then" | "and" | ↓

        ↓: none before a lowercase "climb" or "climbing", which goes on with
        the same sentence ("climb heading 256° to 800 climbing right turn
        ..."); a capitalized one may open a sentence for other runways.
        """
        if self._accept_any(",", ";"):
            self._accept("then")
        elif (
            not self._accept_any("then", "and")
            and required
            and not self._peek_unseparated_leg()
        ):
            raise self._unmatched()

    def _peek_unseparated_leg(self) -> bool:
        token = self._token()
        return token is not None and token.text in _UNSEPARATED_LEG_WORDS

    def _peek_range_alternative(self, legs: list[Leg]) -> bool:
        """After a heading range: ``, all other courses ...`` or ``or climb …``."""
        if not any(isinstance(_turned(leg), HeadingRange) for leg in legs):
            return False
        return any(
            self._peek(*lead, *words)
            for lead in ((), (",",), (";",))
            for words in RANGE_ALTERNATIVES
        )

    def _peek_vcoa_alternative(self, offset: int = 0) -> bool:
        """``, or for climb in visual conditions``, ``; or for …``, ``, for …``"""
        return any(
            self._peek(*lead, *VISUAL_CLIMB, offset=offset)
            for lead in (("or",), (",", "or"), (";", "or"), (",",))
        )

    def _reject_airway_routing(self) -> None:
        """``then westbound on V326 to GINNA`` routes along an airway."""
        offset = 1 if self._peek(",") else 0
        bound = self._token(offset + 1)
        if (
            self._peek("then", offset=offset)
            and bound is not None
            and bound.lower.endswith("bound")
            and self._peek("on", offset=offset + 2)
        ):
            self._index += offset
            raise self._error(f'unsupported routing "then {bound.lower} on"')

    def _leg(self) -> Leg:
        """leg := climbing-turn | climb | continue-climb | proceed | direct
        | bare-turn | cross-at | if-required | on-route | intercept"""
        if self._peek("climbing"):
            return self._climbing_turn()
        if self._peek("climb"):
            return self._climb()
        if self._peek("continue"):
            return self._continue_climb()
        if self._accept_on_course():
            return ProceedOnCourse()
        if self._peek("proceed"):
            return self._proceed()
        if self._peek("direct"):
            return self._direct()
        if self._peek_bare_turn():
            return self._bare_turn()
        if self._peek("cross") and self._peek_navaid(1):
            return self._cross_at()
        if self._peek_if_required():
            return self._if_required()
        if self._peek_on_route():
            return self._on_route()
        if self._peek_any_of(_INTERCEPT_WORDS):
            return self._intercept()
        raise self._unmatched()

    def _cross_at(self) -> CrossAt:
        """cross-at := "cross" target altitude-constraint"""
        self._expect("cross")
        return CrossAt(self._target(), self._altitude_constraint())

    def _peek_fix_crossing(self) -> bool:
        offset = 1 if self._peek(",") else 0
        return self._peek("to", "cross", offset=offset) and self._peek_navaid(
            offset + 2
        )

    def _fix_crossing(self) -> CrossAt:
        """fix-crossing := [","] "to" cross-at, after a leg flown to that fix:
        "direct ALW VOR/DME to cross ALW VOR/DME at or above MEA"; the drawing
        refuses one the route has not reached."""
        self._accept(",")
        self._expect("to")
        return self._cross_at()

    def _peek_continuation(self) -> bool:
        """A new sentence that goes on flying the route: "... direct RSK VORTAC.
        Continue climb in RSK VORTAC holding pattern ..."."""
        return (
            self._follows_period()
            and (self._peek_any_of(CONTINUATION_WORDS) or self._peek_if_required())
            and not self._peek(*VISUAL_CLIMB[1:])
        )

    def _follows_period(self) -> bool:
        return self._index > 0 and self._tokens[self._index - 1].text == "."

    def _continuation(self, legs: tuple[Leg, ...]) -> tuple[Leg, ...]:
        """`legs` and the legs of a continuation sentence that goes on flying them."""
        return legs + self._legs([self._next_leg(legs)])

    def _next_leg(self, legs: Sequence[Leg]) -> Leg:
        """The leg flown after `legs`. A route that ends on course ("... before
        turning right") has none: where the turn leaves the aircraft is not
        known, so a leg read after it is refused."""
        start = self._index
        leg = self._leg()
        if ends_on_course(legs):
            self._index = start
            raise self._error("leg after proceeding on course")
        return leg

    def _peek_if_required(self) -> bool:
        return self._peek("if", "required", "continue", "climb") or self._peek(
            "if", "required", ",", "continue", "climb"
        )

    def _if_required(self) -> ClimbInHold:
        """if-required := "if required" [","] continue-climb

        A climb in a hold ends once its altitude is reached, so "If required,
        continue climb in ABQ VORTAC holding pattern ..." is that climb.
        """
        self._expect("if", "required")
        self._accept(",")
        return self._continue_climb()

    def _peek_before(self) -> bool:
        return self._peek_before_at(0)

    def _peek_before_at(self, offset: int) -> bool:
        return self._peek("before", offset=offset) or self._peek(
            "prior", "to", offset=offset
        )

    def _before(self) -> ProceedOnCourse | Direct | ClimbingTurn:
        """before := ("before" | "prior to") (
              "proceeding" ("on course" | "enroute" | direction | direct)
            | "climbing on course"
            | "turning" [turn-word] (direct | [direction] ["on course"])
            | [turn-word] "turn")

        "Before turning left direct CPN VOR/DME" turns onto that Direct leg.
        """
        if not self._accept("before"):
            self._expect("prior", "to")
        if self._accept("proceeding"):
            if self._peek("direct"):
                return self._direct()
            if self._accept("on", "course") or self._accept("enroute"):
                return ProceedOnCourse()
            return ProceedOnCourse(toward=self._directions())
        if self._accept("climbing", "on", "course") or self._accept("turn"):
            return ProceedOnCourse()
        for word, turn in _TURN_WORDS.items():
            if self._accept(word, "turn"):
                return ProceedOnCourse(turn)
        self._expect("turning")
        token = self._token()
        turn = None
        if token is not None and token.lower in _TURN_WORDS:
            self._index += 1
            turn = _TURN_WORDS[token.lower]
        if self._peek("direct"):
            return ClimbingTurn(turn, self._direct())
        toward = self._directions() if self._peek_direction() else ()
        self._accept("on", "course")
        return ProceedOnCourse(turn, toward)

    # --- Compass directions ------------------------------------------------

    def _peek_direction(self, offset: int = 0) -> bool:
        token = self._token(offset)
        return token is not None and (
            token.lower in COMPASS_WORDS or token.lower in BOUND_WORDS
        )

    def _directions(self) -> tuple[Compass8, ...]:
        """direction := point ("or" point)*, e.g. "east or southeast bound".

        point := compass ["bound"] | "northbound" | "southeastbound" | …
        """
        points = [self._direction_point()]
        while self._peek("or") and self._peek_direction(1):
            self._expect("or")
            points.append(self._direction_point())
        return tuple(points)

    def _direction_point(self) -> Compass8:
        if not self._peek_direction():
            raise self._unmatched()
        return self._bound() or COMPASS_WORDS[self._next().lower]

    def _peek_bound(self, offset: int = 0) -> bool:
        """``southeast bound`` or ``westbound``, a direction of flight."""
        token = self._token(offset)
        if token is None:
            return False
        if token.lower in BOUND_WORDS:
            return True
        return (
            token.lower in COMPASS_WORDS
            and len(token.text) > 2
            and self._peek("bound", offset=offset + 1)
        )

    def _bound(self) -> Compass8 | None:
        """bound := compass "bound" | "northbound" | "southeastbound" | …"""
        if not self._peek_bound():
            return None
        token = self._next()
        if token.lower in BOUND_WORDS:
            return BOUND_WORDS[token.lower]
        self._expect("bound")
        return COMPASS_WORDS[token.lower]
        self._expect("turning")
        token = self._token()
        if token is not None and token.lower in _TURN_WORDS:
            self._index += 1
            return ProceedOnCourse(_TURN_WORDS[token.lower])
        if token is not None and token.lower in COMPASS_WORDS:
            self._index += 1
        return ProceedOnCourse()

    # --- Speed restrictions ------------------------------------------------

    def _peek_speed_restriction(self) -> bool:
        return self._peek("do", "not", "exceed") or self._peek(
            ",", "do", "not", "exceed"
        )

    def _speed_restriction(self) -> SpeedRestriction:
        """speed := [","] "do not exceed" n "KIAS until" speed-until"""
        self._accept(",")
        self._expect("do", "not", "exceed")
        kias = self._integer()
        self._expect("kias", "until")
        start = self._position()
        self._speed_until()
        return SpeedRestriction(kias, self._slice_from(start))

    def _speed_until(self) -> None:
        """speed-until := "established on" ("course" | heading | radial-course
        [bound]) | "established" flight-direction | "reaching" nnnn ["MSL"]
        | nn.n "DME" | ["crossing" | "passing"] target"""
        if self._accept("established", "on"):
            self._accept("the")
            if self._accept("course"):
                return
            if self._peek_heading():
                self._heading()
                return
            self._radial_course()
            self._bound()
            return
        if self._accept("established"):
            self._flight_direction()
            return
        if self._accept("reaching"):
            self._integer()
            self._accept("msl")
            return
        token = self._token()
        if token is not None and NUMBER.fullmatch(token.text):
            self._number()
            self._expect("dme")
            return
        self._accept_any("crossing", "passing")
        self._target()

    def _flight_direction(self) -> None:
        """flight-direction := bound | compass ("-" | "/") compass "bound",
        a point of the sixteen that a cardinal and the intercardinal beside
        it name: "south-southwest bound" or "south/southwest bound"."""
        if self._bound() is not None:
            return
        start = self._index
        cardinal = self._compass()
        self._expect_any("-", "/")
        if not _names_sixteenth_point(cardinal, self._compass()):
            self._index = start
            raise self._unmatched()
        self._expect("bound")

    def _peek_speed_sentence(self) -> bool:
        """ "... on course. Do not exceed 150 KIAS until reaching 1700 MSL." """
        return self._follows_period() and self._peek("do", "not", "exceed")

    def _with_speed_sentence(self, legs: tuple[Leg, ...]) -> tuple[Leg, ...]:
        """`legs` with a speed-limit sentence applied to the last leg flown."""
        speed = self._speed_restriction()
        self._expect(".")
        for index in reversed(range(len(legs))):
            if not isinstance(legs[index], ProceedOnCourse | Thence | CrossAt):
                leg = legs[index]
                if _speed_restriction_of(leg) is not None:
                    raise self._error("two speed restrictions on one leg")
                return (*legs[:index], self._with_speed(leg, speed), *legs[index + 1 :])
        raise self._error("speed restriction without a leg")

    def _with_speed(self, leg: Leg, speed: SpeedRestriction) -> Leg:
        if isinstance(leg, ClimbingTurn) and leg.then is not None:
            return dataclasses.replace(leg, then=self._with_speed(leg.then, speed))
        if not any(field.name == "speed" for field in dataclasses.fields(leg)):
            raise self._error(f"speed restriction after {type(leg).__name__}")
        return dataclasses.replace(leg, speed=speed)

    # --- Climbs ------------------------------------------------------------

    def _climbing_turn(self) -> ClimbingTurn:
        """climbing-turn := "climbing" [turn] "turn" ([to-altitude] turn-leg | ↓)

        turn-leg := direct | ["on" | "via" | "to"] (heading-range | heading-leg
          | radial-leg), "to" only before a heading ("turn to heading 200°")

        A leading altitude comes before "via", "on" or a heading ("climbing
        right turn to 2400 heading 100°"). ↓: a turn with no route of its own
        ("climbing right turn, thence...") turns onto the shared tail's first
        leg.
        """
        self._expect("climbing")
        return self._turn(self._turn_direction())

    def _bare_turn(self) -> ClimbingTurn:
        """bare-turn := "turn" ("left" | "right") | ("left" | "right") "turn",
        then as a climbing turn: "Rwy 9, turn right." """
        if self._accept("turn"):
            direction = _TURN_WORDS[self._next().lower]
        else:
            direction = _TURN_WORDS[self._next().lower]
            self._expect("turn")
        return self._turn(direction)

    def _peek_bare_turn(self) -> bool:
        token, following = self._token(), self._token(1)
        if token is None or following is None:
            return False
        return (token.lower == "turn" and following.lower in _TURN_WORDS) or (
            token.lower in _TURN_WORDS and following.lower == "turn"
        )

    def _turn(self, direction: Turn | None) -> ClimbingTurn:
        if self._at_end() or self._peek_any_of(_ROUTE_ENDS):
            return ClimbingTurn(direction, None)
        altitude = self._altitude_before_route()
        leg = self._turn_leg()
        if altitude is not None:
            leg = self._with_leading_altitude(leg, altitude)
        return ClimbingTurn(direction, leg)

    def _turn_direction(self) -> Turn | None:
        """turn := ("left" | "right") "turn" | ("LT" | "RT") ["turn"] | "turn" """
        token = self._token()
        if token is not None and token.lower in _TURN_ABBREVIATIONS:
            self._index += 1
            self._accept("turn")
            return _TURN_ABBREVIATIONS[token.lower]
        direction = None
        if token is not None and token.lower in _TURN_WORDS:
            self._index += 1
            direction = _TURN_WORDS[token.lower]
        self._expect("turn")
        return direction

    def _altitude_before_route(self) -> Altitude | None:
        """``to 10200 via heading …``, ``to 2000 heading 071°``, ``to 2500 on
        CMA R-265``: the altitude a climb or turn climbs to precedes its route.

        One before "direct" ("climbing right turn to 1900 direct AUG VOR/DME")
        or set off from its route by a comma ("climb to 3600, direct to BZA
        VORTAC") could end the leg short of the route's end or be only the
        altitude to reach on the way, so both are refused.
        """
        if not (self._peek("to") and self._peek_integer(1)):
            return None
        if self._peek("direct", offset=2):
            raise self._error('unsupported "to <alt>" before "direct"')
        if self._peek_any_of(_LEG_SEPARATORS, 2):
            raise self._error('unsupported "to <alt>," before a route')
        if not self._peek_any_of(_ROUTE_LEADS, 2):
            return None
        return self._to_altitude()

    def _with_leading_altitude(
        self,
        leg: HeadingAndRadial | Radial | ClimbHeading | RunwayHeading | HeadingRange,
        altitude: Altitude,
    ) -> HeadingAndRadial | Radial | ClimbHeading | RunwayHeading | HeadingRange:
        """A leading altitude terminates a leg that has no other terminator;
        a radial leg flown to a fix or DME distance climbs to it on the way."""
        if _ends_at_a_point(leg):
            return self._with_altitude(leg, altitude)
        if isinstance(leg.until, AtFix):
            raise self._error('unsupported "to <alt>" with fix terminator')
        if leg.until is not None:
            raise self._error('unsupported "to <alt>" before another terminator')
        return dataclasses.replace(leg, until=altitude)

    def _turn_leg(
        self,
    ) -> Direct | HeadingAndRadial | Radial | ClimbHeading | HeadingRange:
        if self._peek("direct"):
            return self._direct()
        if self._peek_to_intercept():
            return self._intercept()
        if not self._accept_to_heading():
            self._accept_any("on", "via")
        if self._peek_heading_range():
            return self._heading_range()
        if self._peek_heading():
            return self._heading_leg()
        return self._radial_leg()

    def _accept_to_heading(self) -> bool:
        """``climbing right turn to heading 200°``: the heading turned to."""
        if not (self._peek("to") and self._peek_heading(1)):
            return False
        self._expect("to")
        return True

    def _climb(self) -> Leg:
        """climb := "climb" ("on course" | direct | in-hold | straight-ahead
        | [to-altitude] ["on"|"via"] (runway-heading | heading-range
          | heading-leg | radial-leg))

        runway-heading := ("runway heading" | "rwy hdg") [until]

        A leading altitude ("climb to 1000 on heading 164°", "climb to 2000
        heading 071°") ends the leg as a trailing one would.
        """
        self._expect("climb")
        if self._accept("on", "course"):
            return ProceedOnCourse()
        if self._peek("direct"):
            return self._direct()
        if self._peek("in") or self._peek("-", "in", "-"):
            return self._climb_in_hold()
        if self._peek_straight_ahead():
            return self._straight_ahead()
        altitude = self._altitude_before_route()
        leg = self._climb_route()
        return leg if altitude is None else self._with_leading_altitude(leg, altitude)

    def _climb_route(
        self,
    ) -> RunwayHeading | HeadingRange | ClimbHeading | HeadingAndRadial | Radial:
        self._accept_any("on", "via")
        if self._accept("runway", "heading") or self._accept("rwy", "hdg"):
            return RunwayHeading(self._until())
        if self._peek_heading_range():
            return self._heading_range()
        if self._peek_heading():
            return self._heading_leg()
        return self._radial_leg()

    def _peek_straight_ahead(self) -> bool:
        offset = 2 if self._peek("straight", "ahead") else 0
        return (
            self._peek("to", offset=offset)
            and self._peek_integer(offset + 1)
            and self._peek_straight_ahead_end(offset + 2)
        )

    def _peek_straight_ahead_end(self, offset: int) -> bool:
        """The end of the sentence, a VCOA alternative, or ([","|";"]) what is
        flown after the climb: "then", "thence", "...", "before", "prior to".

        A comma alone does not end it: "climb to 3600, direct to BZA VORTAC"
        may climb to 3600 on the way to BZA.
        """
        if self._peek(".", offset=offset) or self._peek_vcoa_alternative(offset):
            return True
        if self._peek_any_of(_LEG_SEPARATORS, offset):
            offset += 1
        return self._peek_any_of(_SEQUENCE_WORDS, offset) or self._peek_before_at(
            offset
        )

    def _straight_ahead(self) -> StraightAhead:
        """straight-ahead := ["straight ahead"] to-altitude, and then only the
        end of the sentence or what is flown after it: "climb to 1200 before
        turning left"."""
        self._accept("straight", "ahead")
        return StraightAhead(self._to_altitude())

    def _continue_climb(self) -> Direct | ClimbInHold:
        """continue-climb := continue-climb-words (direct | climb-in-hold-rest)

        e.g. "Continue climb to 13000 in RLG holding pattern (...)".
        """
        self._expect_continue_climb()
        if self._peek("direct"):
            return self._direct()
        return self._climb_in_hold_rest()

    def _continue_climb_in_hold(self) -> ClimbInHold:
        """continue-climb-in-hold := continue-climb-words climb-in-hold-rest"""
        self._expect_continue_climb()
        return self._climb_in_hold_rest()

    def _expect_continue_climb(self) -> None:
        """continue-climb-words := "continue" ("climb" | "climbing")"""
        self._expect("continue")
        self._expect_any("climb", "climbing")

    def _climb_in_hold_rest(self) -> ClimbInHold:
        """climb-in-hold-rest := [hold-until] in-hold"""
        leading, crossed = self._hold_until()
        if crossed is not None:
            self._index -= 1
            raise self._error('unsupported crossing before "in holding pattern"')
        return self._climb_in_hold(leading)

    def _peek_on_route(self) -> bool:
        return self._peek_any_of(_ROUTE_PREPOSITIONS) and (
            self._peek("heading", offset=1)
            or self._peek("hdg", offset=1)
            or self._peek("the", offset=1)
            or self._peek_navaid(1)
        )

    def _on_route(self) -> ClimbHeading | HeadingAndRadial | Radial:
        """on-route := ("on" | "via") (heading-leg | radial-leg), a leg printed
        without "climb": "direct SLI VORTAC then on SLI VORTAC R-210 to PADDR
        INT", "...via heading 280° to intercept MZB R-160"."""
        self._expect_any(*_ROUTE_PREPOSITIONS)
        if self._peek_heading():
            return self._heading_leg()
        return self._radial_leg()

    def _proceed(self) -> Radial | Direct:
        """proceed := "proceed" (("on" | "via") radial-leg | direct)"""
        self._expect("proceed")
        if self._peek("direct"):
            return self._direct()
        if not self._accept_any("on", "via"):
            raise self._unmatched()
        return self._radial_leg()

    def _direct(self) -> Direct:
        """direct := "direct" ["to"] target"""
        self._expect("direct")
        self._accept("to")
        return Direct(self._target())

    # --- Headings and radials ----------------------------------------------

    def _peek_heading(self, offset: int = 0) -> bool:
        return self._peek("heading", offset=offset) or self._peek("hdg", offset=offset)

    def _heading(self) -> int:
        """heading := ("heading" | "hdg") ["of"] nnn ["°"]"""
        if not self._accept_any("heading", "hdg"):
            raise self._unmatched()
        self._accept("of")
        heading = self._integer()
        self._accept("°")
        return heading

    def _peek_heading_range(self) -> bool:
        offset = 1 if self._peek("a") else 0
        return (
            self._peek("heading", offset=offset) or self._peek("hdg", offset=offset)
        ) and self._peek("between", offset=offset + 1)

    def _heading_range(self) -> HeadingRange:
        """heading-range := ["a"] ("heading" | "hdg") "between" sector
        ("or" ["between"] sector)* ["from" "DER"] [until]"""
        self._accept("a")
        self._expect_any("heading", "hdg")
        self._expect("between")
        sectors = [self._sector()]
        while self._peek("or") and (
            self._peek_integer(1) or self._peek("between", offset=1)
        ):
            self._expect("or")
            self._accept("between")
            sectors.append(self._sector())
        self._accept("from", "der")
        return HeadingRange(tuple(sectors), self._until())

    def _sector(self) -> HeadingSector:
        """sector := nnn ["°"] ("CW" | "clockwise" | "CCW" | "counter clockwise")
        ["to"] ["heading" | "hdg"] nnn ["°"]"""
        start = self._compass_heading()
        if self._accept_any("cw", "clockwise"):
            clockwise = True
        elif self._accept("ccw") or self._accept("counter", "clockwise"):
            clockwise = False
        else:
            raise self._unmatched()
        self._accept("to")
        self._accept_any("heading", "hdg")
        return HeadingSector(start, self._compass_heading(), clockwise)

    def _peek_minimum_climb(self) -> bool:
        return any(self._peek(*words) for words in MINIMUM_CLIMB)

    def _minimum_climb(self, ranges: tuple[HeadingSector, ...]) -> MinimumClimb:
        """minimum-climb := ("min" ["."] | "minimum") "climb of" nnn "ft"
        ("per" | "/") "NM" to-altitude "for" other-headings "."

        `ranges` are the sectors of the heading ranges it is the alternative to.
        """
        if not self._accept("minimum"):
            self._expect("min")
            self._accept(".")
        self._expect("climb", "of")
        ft_per_nm = self._integer()
        self._expect("ft")
        self._expect_any("per", "/")
        self._expect("nm")
        until = self._to_altitude()
        self._expect("for")
        self._other_headings(ranges)
        self._expect(".")
        return MinimumClimb(ft_per_nm, until)

    def _other_headings(self, ranges: tuple[HeadingSector, ...]) -> None:
        """other-headings := "all other" ("courses" | "headings") | named-headings

        Named headings must be exactly those `ranges` leave out.
        """
        if self._accept("all", "other"):
            self._expect_any("courses", "headings")
            return
        start = self._index
        if _headings(self._named_headings()) != _ALL_HEADINGS - _headings(*ranges):
            self._index = start
            raise self._error("minimum climb headings not left out by the range")

    def _named_headings(self) -> HeadingSector:
        """named-headings := "headings" ("from" sector | nnn ["°"] "through"
        nnn ["°"]) | "a heading between" sector

        "A through B" counts the headings up from A, clockwise.
        """
        if self._accept("a", "heading", "between"):
            return self._sector()
        self._expect("headings")
        if self._accept("from"):
            return self._sector()
        start = self._compass_heading()
        self._expect("through")
        return HeadingSector(start, self._compass_heading(), clockwise=True)

    def _compass_heading(self) -> int:
        """nnn ["°"], a magnetic heading from 0 to 360."""
        if not self._peek_integer():
            raise self._unmatched()
        heading = self._integer()
        if heading > MAX_HEADING:
            self._index -= 1
            raise self._unmatched()
        self._accept("°")
        return heading

    def _heading_leg(self) -> ClimbHeading | HeadingAndRadial:
        """heading-leg := heading [("and" ["on"] | "to" ("intercept" | "join"))
        radial-course [altitude-before-fix]] [until]"""
        heading = self._heading()
        if self._peek_and_radial():
            self._expect("and")
            self._accept("on")
            return self._heading_and_radial(heading)
        if self._peek_to_intercept():
            self._expect("to")
            self._expect_any(*_INTERCEPT_WORDS)
            return self._heading_and_radial(heading)
        return ClimbHeading(heading, self._until())

    def _peek_to_intercept(self) -> bool:
        """``to intercept`` or ``to join``."""
        return self._peek("to") and self._peek_any_of(_INTERCEPT_WORDS, offset=1)

    def _peek_and_radial(self) -> bool:
        """``and SNS VORTAC R-225`` or ``and on [the] PDZ R-278`` after a
        heading."""
        navaid = 2 if self._peek("and", "on") else 1
        if self._peek("the", offset=navaid):
            navaid += 1
        return self._peek("and") and self._peek_navaid(navaid)

    def _heading_and_radial(self, heading: int) -> HeadingAndRadial:
        navaid, radial, direction = self._radial_course()
        altitude = self._altitude_before_fix()
        until = self._until()
        outbound = self._outbound(direction, navaid, until)
        return HeadingAndRadial(
            heading, navaid, radial, outbound, until, altitude=altitude
        )

    def _with_altitude(
        self, leg: Radial | HeadingAndRadial, altitude: Altitude
    ) -> Radial | HeadingAndRadial:
        """`leg`, ending at a fix or DME distance, climbing to `altitude` on
        the way there."""
        if leg.altitude is not None:
            raise self._error("leg with two altitudes")
        return dataclasses.replace(leg, altitude=altitude)

    def _radial_leg(self) -> Radial:
        """radial-leg := radial-course [altitude-before-fix] [until]

        A radial printed without a sense, flown from its own navaid just
        reached ("direct VXV VORTAC then on VXV VORTAC R-053 to 4100"), is
        outbound.
        """
        navaid, radial, direction = self._radial_course()
        altitude = self._altitude_before_fix()
        if direction is None and self._just_reached(navaid):
            direction = True
        until = self._until()
        outbound = self._outbound(direction, navaid, until)
        return Radial(navaid, radial, outbound, until, altitude=altitude)

    def _altitude_before_fix(self) -> Altitude | None:
        """altitude-before-fix := to-altitude, before an until naming a fix or
        DME distance: "R-009 to 3000 to IPL VORTAC" ends at the fix, climbing
        to the altitude on the way."""
        if not (
            self._peek("to")
            and self._peek_integer(1)
            and self._peek("to", offset=2)
            and self._peek_navaid(3)
        ):
            return None
        return self._to_altitude()

    def _just_reached(self, navaid: NavaidRef) -> bool:
        return self._last_fix is not None and _same_facility(self._last_fix, navaid)

    def _intercept(self) -> Radial:
        """intercept := ["to"] ("intercept" | "join") radial-leg, a radial joined
        on no printed heading: "climbing left turn to intercept PUB R-274 to
        PUB VORTAC", "...all aircraft, intercept FHU VOR/DME R-021 ..."."""
        self._accept("to")
        self._expect_any(*_INTERCEPT_WORDS)
        return dataclasses.replace(self._radial_leg(), intercept=True)

    def _radial_course(self) -> tuple[NavaidRef, int, bool | None]:
        """radial-course := ["the"] navaid "R-nnn" ["outbound" | "inbound"]"""
        self._accept("the")
        navaid = self._navaid()
        radial = self._radial()
        if self._accept("outbound"):
            return navaid, radial, True
        if self._accept("inbound"):
            return navaid, radial, False
        if self._peek_bound():
            return navaid, radial, self._bound_outbound(radial)
        return navaid, radial, None

    def _bound_outbound(self, radial: int) -> bool:
        """A direction of flight printed after the radial in place of "inbound"
        or "outbound" ("R-350 southbound"): whether it is outbound. It must lie
        within 67.5° of one way along the radial, so that neither the compass
        point's coarseness nor magnetic variation can flip it."""
        start = self._index
        bound = _compass_bearing(self._bound())
        off_outbound = abs((bound - radial + 180) % 360 - 180)
        if off_outbound <= _BOUND_TOLERANCE_DEG:
            return True
        if off_outbound >= 180 - _BOUND_TOLERANCE_DEG:
            return False
        self._index = start
        raise self._error("direction of flight across the radial")

    def _radial(self) -> int:
        token = self._token()
        if token is None or not re.fullmatch(r"R-\d+", token.text):
            raise self._unmatched()
        self._index += 1
        return int(token.text.removeprefix("R-"))

    def _outbound(
        self, direction: bool | None, navaid: NavaidRef, until: Until | None
    ) -> bool | None:
        """As printed; a radial flown to its own navaid is inbound; otherwise
        ``None``, for the grammar or the drawing to settle."""
        to_navaid = isinstance(until, AtFix) and _same_facility(until.target, navaid)
        if direction and to_navaid:
            raise self._error("radial flown outbound to its own navaid")
        if direction is not None:
            return direction
        return False if to_navaid else None

    # --- Leg terminators ---------------------------------------------------

    def _until(self) -> Until | None:
        """until := "to" (altitude | cross-radial | dme | target) | until-altitude"""
        if self._peek("until") and self._peek_integer(1):
            return self._until_altitude()
        if not self._peek("to"):
            return None
        if self._peek_integer(1):
            altitude = self._to_altitude()
            if self._peek("to"):
                raise self._error('unsupported "to <alt> to <fix>" double terminator')
            return altitude
        if self._peek("to", "cross"):
            return self._cross_radial()
        if self._peek_dme():
            return self._dme()
        if self._peek_fix_dme():
            return self._fix_dme()
        self._expect("to")
        return AtFix(self._target())

    def _to_altitude(self) -> Altitude:
        """to-altitude := "to" nnnn"""
        start = self._position()
        self._expect("to")
        feet = self._integer()
        return Altitude(feet, AltitudeKind.TO, self._slice_from(start))

    def _until_altitude(self) -> Altitude:
        """until-altitude := "until" nnnn, a climb-to altitude: "climb on
        heading 208° until 5500"."""
        start = self._position()
        self._expect("until")
        feet = self._integer()
        return Altitude(feet, AltitudeKind.TO, self._slice_from(start))

    def _cross_radial(self) -> CrossRadial:
        """cross-radial := "to cross" navaid "R-nnn" """
        self._expect("to", "cross")
        return CrossRadial(self._navaid(), self._radial())

    def _peek_dme(self) -> bool:
        ident, distance = self._token(1), self._token(2)
        return (
            ident is not None
            and is_ident_word(ident.text)
            and distance is not None
            and bool(NUMBER.fullmatch(distance.text))
            and self._peek("dme", offset=3)
        )

    def _peek_fix_dme(self) -> bool:
        fix = self._token(1)
        slash = 3 if self._peek("int", offset=2) else 2
        return (
            fix is not None
            and is_ident_word(fix.text)
            and len(fix.text) == FIX_LENGTH
            and self._peek("/", offset=slash)
            and self._peek_navaid(slash + 1)
        )

    def _fix_dme(self) -> Dme:
        """fix-dme := "to" FIX ["INT"] "/" navaid-ident [type] nn.n "DME" """
        self._expect("to")
        fix = FixRef(self._next().text)
        self._accept("int")
        self._expect("/")
        ident = self._ident()
        self._navaid_type()
        nm = self._number()
        self._expect("dme")
        return Dme(NavaidRef(ident), nm, fix)

    def _dme(self) -> Dme:
        """dme := "to" navaid-ident nn.n "DME" """
        self._expect("to")
        if len(self._token().text) > _MAX_DME_IDENT_LENGTH:
            raise self._error("DME from a fix")
        navaid = NavaidRef(self._next().text)
        nm = self._number()
        self._expect("dme")
        return Dme(navaid, nm)

    # --- Holding -----------------------------------------------------------

    def _climb_in_hold(
        self, leading: Altitude | EnrouteAltitude | None = None
    ) -> ClimbInHold:
        """in-hold := hold-fix [hold-until] [[","] hold-spec [","]] [[","] hold-until]

        The comma before the last hold-until is read only after a hold-spec or
        before "to cross" or "to depart".

        The hold's fix is whichever of these the text names, and they must
        agree: before "holding pattern", inside the hold-spec ("(GKN VOR/DME
        hold northwest, ...)"), the fix crossed ("to cross BRK VOR/DME at or
        above ..."), or else the fix the previous leg reached.
        `leading` is an altitude read before "in", as after "continue climb".
        """
        named = self._hold_fix()
        until, crossed = self._hold_until()
        spec_fix, hold = None, None
        if self._peek("(") or self._peek(",", "("):
            self._accept(",")
            spec_fix, hold = self._hold_spec()
            if until is None and self._peek(",") and self._peek_hold_until(1):
                self._accept(",")
        if until is None:
            if self._peek(",", "to", "cross") or self._peek(",", "to", "depart"):
                self._expect(",")
            until, crossed = self._hold_until()
        if leading is not None:
            if until is not None:
                raise self._error("hold with two altitudes")
            until = leading
        fix = self._one_hold_fix(named, spec_fix, crossed)
        return ClimbInHold(fix, hold, until)

    def _hold_fix(self) -> NavaidRef | FixRef | None:
        """hold-fix := ("-in-hold" | "in hold") [named-hold]
        | ("-in-holding" | "in holding") ["pattern"] | named-hold

        named-hold := "in" ["the"] target "holding pattern"

        e.g. "climb-in-hold in FHR NDB holding pattern", "climb in holding
        (SW, ...)". ``None`` when the text names no fix here.
        """
        if self._accept("-", "in", "-", "hold") or self._accept("in", "hold"):
            return self._named_hold() if self._peek("in") else None
        if self._accept("-", "in", "-", "holding") or self._accept("in", "holding"):
            self._accept("pattern")
            return None
        return self._named_hold()

    def _named_hold(self) -> NavaidRef | FixRef:
        self._expect("in")
        self._accept("the")
        fix = self._target()
        self._expect("holding", "pattern")
        return fix

    def _and_hold(self) -> ClimbInHold:
        """and-hold := "and hold" [hold-spec] [","] continue-climb

        "... to MQO VORTAC and hold, continue climb in MQO holding pattern
        (...) to ...": one climb in hold at the fix the route has just
        reached, its pattern given by either phrase, or by both alike.
        """
        reached = self._last_fix
        self._expect("and", "hold")
        spec_fix, hold = self._hold_spec() if self._peek("(") else (None, None)
        if reached is None:
            raise self._error("hold without a preceding fix")
        self._accept(",")
        if not self._peek("continue"):
            raise self._unmatched()
        climb = self._continue_climb_in_hold()
        fix = self._one_hold_fix(reached, spec_fix, climb.fix)
        if None not in (hold, climb.hold) and hold != climb.hold:
            raise self._error("hold specified twice, differently")
        return dataclasses.replace(climb, fix=fix, hold=hold or climb.hold)

    def _one_hold_fix(self, *named: NavaidRef | FixRef | None) -> NavaidRef | FixRef:
        fixes = [fix for fix in named if fix is not None]
        if any(not _same_facility(fix, fixes[0]) for fix in fixes):
            raise self._error("hold crossing names a different fix")
        if fixes:
            return fixes[0]
        if self._last_fix is None:
            raise self._error("climb in hold without a preceding fix")
        return self._last_fix

    def _hold_until(
        self,
    ) -> tuple[Altitude | EnrouteAltitude | None, NavaidRef | FixRef | None]:
        """hold-until := "to" (nnnn | enroute) | "until" altitude-constraint
        | "to" ("cross" | "depart") <fix> altitude-constraint

        Returns the altitude and the fix crossed, if one is named.
        """
        if not self._peek_hold_until():
            return None, None
        if self._peek("to") and self._peek_integer(1):
            return self._to_altitude(), None
        if self._peek("to") and self._peek_enroute(1):
            start = self._position()
            self._expect("to")
            return self._enroute(AltitudeKind.TO, start), None
        if self._accept("until"):
            return self._altitude_constraint(), None
        self._expect("to")
        self._expect_any(*_CROSSINGS)
        crossed = self._target()
        return self._altitude_constraint(), crossed

    def _peek_hold_until(self, offset: int = 0) -> bool:
        return (
            self._peek("to", offset=offset)
            and (
                self._peek_integer(offset + 1)
                or self._peek_enroute(offset + 1)
                or self._peek_any_of(_CROSSINGS, offset + 1)
            )
        ) or self._peek("until", "at", offset=offset)

    def _altitude_constraint(self) -> Altitude | EnrouteAltitude:
        """altitude-constraint := "at" ["or" ("above" | "below")] (nnnn ["MSL"]
        ["or" enroute] | enroute)"""
        start = self._position()
        self._expect("at")
        kind = AltitudeKind.AT
        if self._accept("or", "above"):
            kind = AltitudeKind.AT_OR_ABOVE
        elif self._accept("or", "below"):
            kind = AltitudeKind.AT_OR_BELOW
        if self._peek_enroute():
            return self._enroute(kind, start)
        feet = self._integer()
        self._accept("msl")
        if self._peek("or") and self._peek_enroute(1):
            self._expect("or")
            return dataclasses.replace(self._enroute(kind, start), feet=feet)
        return Altitude(feet, kind, self._slice_from(start))

    def _peek_enroute(self, offset: int = 0) -> bool:
        return self._peek_any_of(ENROUTE_MINIMUMS, offset + self._enroute_leads(offset))

    def _enroute_leads(self, offset: int = 0) -> int:
        """How many words of ``["the"] ["airway" | "appropriate"]`` come next."""
        leads = int(self._peek("the", offset=offset))
        return leads + int(self._peek_any_of(ENROUTE_LEADS, offset + leads))

    def _peek_any_of(self, words: frozenset[str], offset: int = 0) -> bool:
        token = self._token(offset)
        return token is not None and token.lower in words

    def _enroute(self, kind: AltitudeKind, start: int) -> EnrouteAltitude:
        """enroute := ["the"] ["airway" | "appropriate"] minimum (("/" | "or")
        minimum)* [("for" ["the"] ("route" | "direction") "of flight")
        | "of intended route"]

        minimum := "MEA" | "MCA" | "MOCA"
        """
        self._index += self._enroute_leads()
        names = [self._next().text.upper()]
        while (self._peek("/") or self._peek("or")) and self._peek_any_of(
            ENROUTE_MINIMUMS, 1
        ):
            self._index += 1
            names.append(self._next().text.upper())
        if self._accept("for"):
            self._accept("the")
            self._expect_any("route", "direction")
            self._expect("of", "flight")
        elif self._peek("of", "intended"):
            self._expect("of", "intended", "route")
        return EnrouteAltitude(tuple(names), kind, self._slice_from(start))

    def _hold_spec(self) -> tuple[NavaidRef | FixRef | None, HoldSpec]:
        """hold-spec := "(" [target] ["hold" [","]] compass [","] hold-turns [","]
        nnn ["°"] [","] "inbound" ")"

        The commas are sometimes left out or doubled ("right turns 147°
        Inbound", "258°, inbound"). A side that disagrees with the inbound
        course's reciprocal contradicts the course. Returns the
        fix named inside the parentheses, if any, and the hold.
        """
        start = self._index
        self._expect("(")
        fix = None
        if self._peek_navaid() and not self._peek_any_of(frozenset(COMPASS_WORDS)):
            fix = self._target()
        if self._accept("hold"):
            self._accept(",")
        direction = self._compass()
        self._accept(",")
        turns = self._hold_turns()
        self._accept(",")
        inbound = self._integer()
        self._accept("°")
        self._accept(",")
        self._expect("inbound", ")")
        if not _side_agrees(direction, inbound):
            self._index = start
            raise self._error("hold side contradicts its inbound course")
        return fix, HoldSpec(direction, turns, inbound)

    def _compass(self) -> Compass8:
        token = self._token()
        if token is None or token.lower not in COMPASS_WORDS:
            raise self._unmatched()
        self._index += 1
        return COMPASS_WORDS[token.lower]

    def _hold_turns(self) -> Turn:
        """hold-turns := "RT" | "LT" | ("left" | "right") ("turn" | "turns")"""
        token = self._token()
        if token is not None and token.lower in _TURN_ABBREVIATIONS:
            self._index += 1
            return _TURN_ABBREVIATIONS[token.lower]
        if token is None or token.lower not in _TURN_WORDS:
            raise self._unmatched()
        self._index += 1
        if not self._accept_any("turn", "turns"):
            raise self._unmatched()
        return _TURN_WORDS[token.lower]

    # --- Navaids and fixes -------------------------------------------------

    def _peek_navaid(self, offset: int = 0) -> bool:
        token = self._token(offset)
        return token is not None and is_ident_word(token.text)

    def _navaid(self) -> NavaidRef:
        target = self._target()
        if not isinstance(target, NavaidRef):
            raise self._error("radial of a fix")
        return target

    def _target(self) -> NavaidRef | FixRef:
        """target := NAME+ "(" IDENT ")" [type] | IDENT [type] | FIX ["INT"]
        | NAME+ type

        "INT" (intersection) after a five-letter name marks it as a fix.
        """
        start = self._position()
        words = self._uppercase_words()
        if not words:
            raise self._unmatched()
        if self._accept("("):
            ident = self._ident()
            self._expect(")")
            return NavaidRef(ident, self._navaid_type(), " ".join(words))
        if words[-1] == INTERSECTION and len(words) == 2:
            words.pop()
            if len(words[0]) != FIX_LENGTH:
                self._index -= 1
                raise self._unmatched()
            return FixRef(words[0])
        navaid_type = self._navaid_type()
        if len(words) == 1 and len(words[0]) <= _MAX_IDENT_LENGTH:
            return NavaidRef(words[0], navaid_type)
        if len(words) == 1 and len(words[0]) == FIX_LENGTH and navaid_type is None:
            return FixRef(words[0])
        if navaid_type is not None:
            return self._named_navaid(" ".join(words), navaid_type, start)
        raise ParseError(
            f'unmatched phrase "{phrase_signature(" ".join(words))}"',
            " ".join(words),
            start,
        )

    def _uppercase_words(self) -> list[str]:
        words = []
        while self._peek_navaid():
            words.append(self._next().text)
        return words

    def _ident(self) -> str:
        token = self._token()
        if token is None or not (
            is_ident_word(token.text) and len(token.text) <= _MAX_IDENT_LENGTH
        ):
            raise self._unmatched()
        self._index += 1
        return token.text

    def _peek_navaid_type(self, offset: int = 0) -> bool:
        token = self._token(offset)
        return token is not None and token.lower in NAVAID_TYPE_WORDS

    def _navaid_type(self) -> NavaidType | None:
        for words, navaid_type in _NAVAID_TYPES:
            if self._accept(*words):
                return navaid_type
        return None

    def _named_navaid(
        self, name: str, navaid_type: NavaidType, start: int
    ) -> NavaidRef:
        """A navaid named without an ident, resolved by an earlier mention."""
        known = self._known_navaids.get(name)
        if known is None or known.type != navaid_type:
            phrase = f"{name} {navaid_type.value}"
            raise ParseError("navaid without ident", phrase, start)
        return known


MINIMUM_CLIMB = (("min", ".", "climb"), ("min", "climb"), ("minimum", "climb"))
RANGE_ALTERNATIVES = (
    ("all", "other", "courses"),
    ("all", "other", "headings"),
    ("or", "climb"),
    ("or", "climbing"),
    *(("or", *words) for words in MINIMUM_CLIMB),
)
_ALL_HEADINGS = frozenset(range(360))


def _headings(*sectors: HeadingSector) -> frozenset[int]:
    """The whole-degree headings the sectors sweep, ends included, with 360
    counted as 0."""
    swept = set()
    for sector in sectors:
        first, last = (
            (sector.start, sector.end)
            if sector.clockwise
            else (sector.end, sector.start)
        )
        swept.update((first + step) % 360 for step in range((last - first) % 360 + 1))
    return frozenset(swept)


def _names_sixteenth_point(cardinal: Compass8, intercardinal: Compass8) -> bool:
    """Whether "<cardinal>-<intercardinal>" names a point of the sixteen, as
    "south-southwest" does."""
    return len(cardinal) == 1 and len(intercardinal) == 2 and cardinal in intercardinal


def _compass_bearing(point: Compass8) -> int:
    """``N`` → 0, ``NE`` → 45, … ``NW`` → 315."""
    return list(Compass8).index(point) * 45


def _speed_restriction_of(leg: Leg) -> SpeedRestriction | None:
    turned = leg.then if isinstance(leg, ClimbingTurn) else leg
    return getattr(turned, "speed", None)


def _turned(leg: Leg) -> Leg | None:
    """The leg a climbing turn turns onto, else the leg itself."""
    return leg.then if isinstance(leg, ClimbingTurn) else leg


def _side_agrees(side: Compass8, inbound: int) -> bool:
    """Whether a hold printed on `side` of its fix lies where its inbound
    course puts it, along the course's reciprocal: within half a compass
    point, with margin for a course printed between points. A side a whole
    point off could be a misprint of either, so it contradicts the course."""
    bearing = list(Compass8).index(side) * _COMPASS_POINT_DEG
    return abs((bearing - inbound) % 360 - 180) <= _SIDE_TOLERANCE_DEG


def _ends_at_a_point(leg: Leg) -> TypeGuard[Radial | HeadingAndRadial]:
    """Whether a radial leg ends at a fix or DME distance, which an altitude
    climbed to on the way does not move."""
    return isinstance(leg, Radial | HeadingAndRadial) and isinstance(
        leg.until, AtFix | Dme
    )


def _same_facility(target: NavaidRef | FixRef, navaid: NavaidRef | FixRef) -> bool:
    return target.ident == navaid.ident


def end_fix(leg: Leg) -> NavaidRef | FixRef | None:
    """The fix a leg ends at, or ``None`` when it ends anywhere else."""
    if isinstance(leg, ClimbingTurn):
        return end_fix(leg.then) if leg.then is not None else None
    if isinstance(leg, Direct):
        return leg.target
    if isinstance(leg, ClimbInHold | CrossAt):
        return leg.fix
    match getattr(leg, "until", None):
        case AtFix(target=target):
            return target
        case Dme(fix=fix):
            return fix
    return None


def ends_on_course(legs: Sequence[Leg]) -> bool:
    """Whether the route ends by proceeding on course."""
    return bool(legs) and isinstance(legs[-1], ProceedOnCourse)
