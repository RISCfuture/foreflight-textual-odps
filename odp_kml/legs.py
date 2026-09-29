"""The leg grammar of DEPARTURE PROCEDURE and VCOA text: climbs, turns,
headings, radials, leg terminators, holds, and navaid or fix references.

`LegParser` has no "skip unknown words" rule; every rule either consumes its
phrase or raises `ParseError`.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping

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
_NAVAID_TYPES = (
    (("vor", "/", "dme"), NavaidType.VOR_DME),
    (("vortac",), NavaidType.VORTAC),
    (("vor",), NavaidType.VOR),
    (("ndb",), NavaidType.NDB),
    (("dme",), NavaidType.DME),
)
NAVAID_TYPE_WORDS = frozenset(words[0] for words, _ in _NAVAID_TYPES)
VISUAL_CLIMB = ("for", "climb", "in", "visual", "conditions")
ENROUTE_MINIMUMS = frozenset({"mea", "mca"})
CONTINUATION_WORDS = frozenset(
    {"climb", "climbing", "continue", "proceed", "direct", "cross"}
)
ENROUTE_LEADS = frozenset({"the", "airway", "appropriate"})
BOUND_WORDS = {
    f"{word}bound": point for word, point in COMPASS_WORDS.items() if len(word) > 2
}


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
        """legs := leg ((separator leg) | before | speed)* ("." | thence | ↓)

        ↓: the legs also end, unconsumed, where an inline VCOA alternative
        ("..., or for climb in visual conditions") begins. Tracks the fix
        each leg ends at, for a hold that names none.
        """
        self._last_fix = end_fix(legs[-1]) if legs else None
        while True:
            if self._peek_before() or (self._peek(",") and self._peek_before_at(1)):
                self._accept(",")
                self._append(legs, self._before())
            elif (
                (legs and self._peek_vcoa_alternative())
                or self._peek_range_alternative(legs)
                or self._accept(".")
            ):
                return tuple(legs)
            elif self._thence():
                return (*legs, Thence())
            elif legs and self._peek_speed_restriction():
                legs[-1] = self._with_speed(legs[-1], self._speed_restriction())
            else:
                self._reject_airway_routing()
                self._leg_separator(required=bool(legs))
                self._append(legs, self._leg())

    def _append(self, legs: list[Leg], leg: Leg) -> None:
        legs.append(leg)
        self._last_fix = end_fix(leg)

    def _thence(self) -> bool:
        """thence := [","] "thence" "..." | [","] "..."

        An ellipsis alone ("to 7000...") also leads into the shared tail.
        """
        if not (
            self._peek("thence")
            or self._peek(",", "thence")
            or self._peek("...")
            or self._peek(",", "...")
        ):
            return False
        self._accept(",")
        self._accept("thence")
        self._expect("...")
        return True

    def _leg_separator(self, *, required: bool) -> None:
        """separator := ("," | ";") ["then"] | "then" | "and" """
        if self._accept_any(",", ";"):
            self._accept("then")
        elif not self._accept_any("then", "and") and required:
            raise self._unmatched()

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
        | bare-turn | cross-at"""
        if self._peek("climbing"):
            return self._climbing_turn()
        if self._peek("climb"):
            return self._climb()
        if self._peek("continue"):
            return self._continue_climb()
        if self._peek("proceed"):
            return self._proceed()
        if self._peek("direct"):
            return self._direct()
        if self._peek_bare_turn():
            return self._bare_turn()
        if self._peek("cross") and self._peek_navaid(1):
            return self._cross_at()
        raise self._unmatched()

    def _cross_at(self) -> CrossAt:
        """cross-at := "cross" target altitude-constraint"""
        self._expect("cross")
        return CrossAt(self._target(), self._altitude_constraint())

    def _peek_continuation(self) -> bool:
        """A new sentence that goes on flying the route: "... direct RSK VORTAC.
        Continue climb in RSK VORTAC holding pattern ..."."""
        return (
            self._index > 0
            and self._tokens[self._index - 1].text == "."
            and self._peek_any_of(CONTINUATION_WORDS)
            and not self._peek(*VISUAL_CLIMB[1:])
        )

    def _continuation(self) -> tuple[Leg, ...]:
        return self._legs([self._leg()])

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
            | "turn")

        A compass direction ("before turning southbound") is read but not
        drawn; a turn direction bends the on-course stub. "Before turning
        left direct CPN VOR/DME" turns onto that Direct leg.
        """
        if not self._accept("before"):
            self._expect("prior", "to")
        if self._accept("proceeding"):
            if self._peek("direct"):
                return self._direct()
            if not (self._accept("on", "course") or self._accept("enroute")):
                self._direction()
            return ProceedOnCourse()
        if self._accept("climbing", "on", "course") or self._accept("turn"):
            return ProceedOnCourse()
        self._expect("turning")
        token = self._token()
        turn = None
        if token is not None and token.lower in _TURN_WORDS:
            self._index += 1
            turn = _TURN_WORDS[token.lower]
        if self._peek("direct"):
            return ClimbingTurn(turn, self._direct())
        if self._peek_direction():
            self._direction()
        self._accept("on", "course")
        return ProceedOnCourse(turn)

    # --- Compass directions ------------------------------------------------

    def _peek_direction(self, offset: int = 0) -> bool:
        token = self._token(offset)
        return token is not None and (
            token.lower in COMPASS_WORDS or token.lower in BOUND_WORDS
        )

    def _direction(self) -> Compass8:
        """direction := compass ["bound"] | "northbound" | "southeastbound" | …"""
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
        """speed-until := "established on" ("course" | heading | radial-course)
        | "reaching" nnnn ["MSL"] | nn.n "DME" | ["crossing" | "passing"] target"""
        if self._accept("established", "on"):
            self._accept("the")
            if self._accept("course"):
                return
            if self._peek_heading():
                self._heading()
                return
            self._radial_course()
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

    def _peek_speed_sentence(self) -> bool:
        """ "... on course. Do not exceed 150 KIAS until reaching 1700 MSL." """
        return (
            self._index > 0
            and self._tokens[self._index - 1].text == "."
            and self._peek("do", "not", "exceed")
        )

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
        """A leading altitude terminates a leg that has no other terminator."""
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
        """continue-climb := "continue climb" (direct | [hold-until] in-hold)

        e.g. "Continue climb to 13000 in RLG holding pattern (...)".
        """
        self._expect("continue", "climb")
        if self._peek("direct"):
            return self._direct()
        leading, crossed = self._hold_until()
        if crossed is not None:
            self._index -= 1
            raise self._error('unsupported crossing before "in holding pattern"')
        return self._climb_in_hold(leading)

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
        """heading := ("heading" | "hdg") nnn ["°"]"""
        if not self._accept_any("heading", "hdg"):
            raise self._unmatched()
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
        """heading-leg := heading [("and" | "to intercept") radial-course] [until]"""
        heading = self._heading()
        if self._peek("and") and self._peek_navaid(offset=1):
            self._expect("and")
            return self._heading_and_radial(heading)
        if self._accept("to", "intercept"):
            return self._heading_and_radial(heading)
        return ClimbHeading(heading, self._until())

    def _heading_and_radial(self, heading: int) -> HeadingAndRadial:
        navaid, radial, direction = self._radial_course()
        until = self._until()
        outbound = self._outbound(direction, navaid, until)
        return HeadingAndRadial(heading, navaid, radial, outbound, until)

    def _radial_leg(self) -> Radial:
        """radial-leg := radial-course [until]"""
        navaid, radial, direction = self._radial_course()
        until = self._until()
        return Radial(navaid, radial, self._outbound(direction, navaid, until), until)

    def _radial_course(self) -> tuple[NavaidRef, int, bool | None]:
        """radial-course := navaid "R-nnn" ["outbound" | "inbound"]"""
        navaid = self._navaid()
        radial = self._radial()
        if self._accept("outbound"):
            return navaid, radial, True
        if self._accept("inbound"):
            return navaid, radial, False
        return navaid, radial, None

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
        if direction is not None:
            return direction
        if isinstance(until, AtFix) and _same_facility(until.target, navaid):
            return False
        return None

    # --- Leg terminators ---------------------------------------------------

    def _until(self) -> Until | None:
        """until := "to" (altitude | cross-radial | dme | target)"""
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
        """in-hold := hold-fix [hold-until] [hold-spec] [hold-until]

        The hold's fix is whichever of these the text names, and they must
        agree: before "holding pattern", inside the hold-spec ("(GKN VOR/DME
        hold northwest, ...)"), the fix crossed ("to cross BRK VOR/DME at or
        above ..."), or else the fix the previous leg reached.
        `leading` is an altitude read before "in", as after "continue climb".
        """
        named = self._hold_fix()
        until, crossed = self._hold_until()
        spec_fix, hold = self._hold_spec() if self._peek("(") else (None, None)
        if until is None:
            until, crossed = self._hold_until()
        if leading is not None:
            if until is not None:
                raise self._error("hold with two altitudes")
            until = leading
        fix = self._one_hold_fix(named, spec_fix, crossed)
        return ClimbInHold(fix, hold, until)

    def _hold_fix(self) -> NavaidRef | FixRef | None:
        """hold-fix := "-in-hold" | "-in-holding pattern" | "in holding pattern"
        | "in" ["the"] target "holding pattern"

        ``None`` when the text names no fix here.
        """
        if (
            self._accept("-", "in", "-", "hold")
            or self._accept("-", "in", "-", "holding", "pattern")
            or self._accept("in", "holding", "pattern")
        ):
            return None
        self._expect("in")
        self._accept("the")
        fix = self._target()
        self._expect("holding", "pattern")
        return fix

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
        if self._peek("to") and self._peek_integer(1):
            return self._to_altitude(), None
        if self._peek("to") and self._peek_enroute(1):
            start = self._position()
            self._expect("to")
            return self._enroute(AltitudeKind.TO, start), None
        if self._peek("until", "at"):
            self._expect("until")
            return self._altitude_constraint(), None
        if not (self._peek("to", "cross") or self._peek("to", "depart")):
            return None, None
        self._expect("to")
        self._expect_any("cross", "depart")
        crossed = self._target()
        return self._altitude_constraint(), crossed

    def _altitude_constraint(self) -> Altitude | EnrouteAltitude:
        """altitude-constraint := "at" ["or" ("above" | "below")] (nnnn ["MSL"] | enroute)"""
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
        return Altitude(feet, kind, self._slice_from(start))

    def _peek_enroute(self, offset: int = 0) -> bool:
        if self._peek_any_of(ENROUTE_LEADS, offset):
            offset += 1
        return self._peek_any_of(ENROUTE_MINIMUMS, offset)

    def _peek_any_of(self, words: frozenset[str], offset: int = 0) -> bool:
        token = self._token(offset)
        return token is not None and token.lower in words

    def _enroute(self, kind: AltitudeKind, start: int) -> EnrouteAltitude:
        """enroute := ["the" | "airway" | "appropriate"] minimum (("/" | "or")
        minimum)* [("for" ["the"] ("route" | "direction") "of flight")
        | "of intended route"]

        minimum := "MEA" | "MCA"
        """
        if self._peek_any_of(ENROUTE_LEADS):
            self._index += 1
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
        """hold-spec := "(" [target] ["hold"] compass "," hold-turns "," nnn ["°"]
        "inbound" ")"

        Returns the fix named inside the parentheses, if any, and the hold.
        """
        self._expect("(")
        fix = None
        if self._peek_navaid() and not self._peek_any_of(frozenset(COMPASS_WORDS)):
            fix = self._target()
        self._accept("hold")
        direction = self._compass()
        self._expect(",")
        turns = self._hold_turns()
        self._expect(",")
        inbound = self._integer()
        self._accept("°")
        self._expect("inbound", ")")
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


RANGE_ALTERNATIVES = (
    ("all", "other", "courses"),
    ("all", "other", "headings"),
    ("or", "climb"),
    ("or", "climbing"),
)


def _speed_restriction_of(leg: Leg) -> SpeedRestriction | None:
    turned = leg.then if isinstance(leg, ClimbingTurn) else leg
    return getattr(turned, "speed", None)


def _turned(leg: Leg) -> Leg | None:
    """The leg a climbing turn turns onto, else the leg itself."""
    return leg.then if isinstance(leg, ClimbingTurn) else leg


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
    until = getattr(leg, "until", None)
    return until.target if isinstance(until, AtFix) else None
