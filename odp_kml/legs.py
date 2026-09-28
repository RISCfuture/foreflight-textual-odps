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
    CrossRadial,
    Direct,
    Dme,
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
    SpeedRestriction,
    Thence,
    Turn,
    Until,
)
from .tokens import NUMBER, ParseError, TokenStream, is_ident_word, phrase_signature

_MAX_IDENT_LENGTH = 4
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
_NAVAID_TYPES = (
    (("vor", "/", "dme"), NavaidType.VOR_DME),
    (("vortac",), NavaidType.VORTAC),
    (("vor",), NavaidType.VOR),
    (("ndb",), NavaidType.NDB),
    (("dme",), NavaidType.DME),
)
NAVAID_TYPE_WORDS = frozenset(words[0] for words, _ in _NAVAID_TYPES)
VISUAL_CLIMB = ("for", "climb", "in", "visual", "conditions")


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
        self._last_fix = _end_fix(legs[-1]) if legs else None
        while True:
            if self._peek("before") or self._peek(",", "before"):
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
        self._last_fix = _end_fix(leg)

    def _thence(self) -> bool:
        """thence := [","] "thence" "..." """
        if not (self._peek("thence") or self._peek(",", "thence")):
            return False
        self._accept(",")
        self._expect("thence", "...")
        return True

    def _leg_separator(self, *, required: bool) -> None:
        """separator := "," ["then"] | "then" | "and" """
        if self._accept(","):
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

    def _peek_vcoa_alternative(self) -> bool:
        """``, or for climb in visual conditions``, ``; or for …``, ``, for …``"""
        return any(
            self._peek(*lead, *VISUAL_CLIMB)
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
        """leg := climbing-turn | climb | continue-climb | proceed | direct"""
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
        raise self._unmatched()

    def _before(self) -> ProceedOnCourse:
        """before := "before" ("proceeding on course" | "turning" [turn | compass])"""
        self._expect("before")
        if self._accept("proceeding", "on", "course"):
            return ProceedOnCourse()
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
        """speed-until := "established on course" | "reaching" nnnn ["MSL"]
        | nn.n "DME" | ["crossing"] target"""
        if self._accept("established", "on", "course"):
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
        self._accept("crossing")
        self._target()

    def _with_speed(self, leg: Leg, speed: SpeedRestriction) -> Leg:
        if isinstance(leg, ClimbingTurn):
            return dataclasses.replace(leg, then=self._with_speed(leg.then, speed))
        if not any(field.name == "speed" for field in dataclasses.fields(leg)):
            raise self._error(f"speed restriction after {type(leg).__name__}")
        return dataclasses.replace(leg, speed=speed)

    # --- Climbs ------------------------------------------------------------

    def _climbing_turn(self) -> ClimbingTurn:
        """climbing-turn := "climbing" [turn] "turn" [to-altitude ("via"|"on")] turn-leg"""
        self._expect("climbing")
        direction = self._turn_direction()
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
        """``to 10200 via heading …``: the turn's altitude precedes its route."""
        if not (
            self._peek("to")
            and self._peek_integer(1)
            and (self._peek("via", offset=2) or self._peek("on", offset=2))
        ):
            return None
        return self._to_altitude()

    def _with_leading_altitude(
        self,
        leg: Direct | HeadingAndRadial | Radial | ClimbHeading | HeadingRange,
        altitude: Altitude,
    ) -> Direct | HeadingAndRadial | Radial | ClimbHeading | HeadingRange:
        """A leading altitude terminates a leg that has no other terminator."""
        if isinstance(leg, Direct):
            raise self._error('unsupported "to <alt>" before "direct"')
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
        self._accept_any("on", "via")
        if self._peek_heading_range():
            return self._heading_range()
        if self._peek_heading():
            return self._heading_leg()
        return self._radial_leg()

    def _climb(self) -> Leg:
        """climb := "climb" ("on course" | direct | in-hold | ["on"|"via"] (heading-leg | radial-leg))"""
        self._expect("climb")
        if self._accept("on", "course"):
            return ProceedOnCourse()
        if self._peek("direct"):
            return self._direct()
        if self._peek("in") or self._peek("-", "in", "-", "hold"):
            return self._climb_in_hold()
        self._accept_any("on", "via")
        if self._peek_heading_range():
            return self._heading_range()
        if self._peek_heading():
            return self._heading_leg()
        return self._radial_leg()

    def _continue_climb(self) -> ClimbInHold:
        """continue-climb := "continue climb" in-hold"""
        self._expect("continue", "climb")
        return self._climb_in_hold()

    def _proceed(self) -> Radial:
        """proceed := "proceed" ("on" | "via") radial-leg"""
        self._expect("proceed")
        if not self._accept_any("on", "via"):
            raise self._unmatched()
        return self._radial_leg()

    def _direct(self) -> Direct:
        """direct := "direct" target"""
        self._expect("direct")
        return Direct(self._target())

    # --- Headings and radials ----------------------------------------------

    def _peek_heading(self) -> bool:
        return self._peek("heading") or self._peek("hdg")

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
    ) -> bool:
        """A radial flown to its own navaid is inbound; any other needs the word."""
        if direction is not None:
            return direction
        if isinstance(until, AtFix) and _same_facility(until.target, navaid):
            return False
        raise self._error('radial without "inbound" or "outbound"')

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

    def _climb_in_hold(self) -> ClimbInHold:
        """in-hold := hold-fix [hold-until] [hold-spec] [hold-until]"""
        fix = self._hold_fix()
        until = self._hold_until(fix)
        hold = self._hold_spec() if self._peek("(") else None
        if until is None:
            until = self._hold_until(fix)
        return ClimbInHold(fix, hold, until)

    def _hold_fix(self) -> NavaidRef | FixRef:
        """hold-fix := "-in-hold" | "in holding pattern" | "in" target "holding pattern"

        The first two hold at the fix the previous leg reached.
        """
        if self._accept("-", "in", "-", "hold") or self._accept(
            "in", "holding", "pattern"
        ):
            return self._preceding_fix()
        self._expect("in")
        fix = self._target()
        self._expect("holding", "pattern")
        return fix

    def _preceding_fix(self) -> NavaidRef | FixRef:
        if self._last_fix is None:
            raise self._error("climb in hold without a preceding fix")
        return self._last_fix

    def _hold_until(self, fix: NavaidRef | FixRef) -> Altitude | None:
        """hold-until := "to" nnnn | "to cross" <fix> altitude-constraint"""
        if self._peek("to") and self._peek_integer(1):
            return self._to_altitude()
        if not self._peek("to", "cross"):
            return None
        self._expect("to", "cross")
        crossed = self._target()
        if not _same_facility(crossed, fix):
            raise self._error("hold crossing names a different fix")
        return self._altitude_constraint()

    def _altitude_constraint(self) -> Altitude:
        """altitude-constraint := "at" ["or" ("above" | "below")] nnnn ["MSL"]"""
        start = self._position()
        self._expect("at")
        kind = AltitudeKind.AT
        if self._accept("or", "above"):
            kind = AltitudeKind.AT_OR_ABOVE
        elif self._accept("or", "below"):
            kind = AltitudeKind.AT_OR_BELOW
        feet = self._integer()
        self._accept("msl")
        return Altitude(feet, kind, self._slice_from(start))

    def _hold_spec(self) -> HoldSpec:
        """hold-spec := "(" ["hold"] compass "," hold-turns "," nnn ["°"] "inbound" ")" """
        self._expect("(")
        self._accept("hold")
        direction = self._compass()
        self._expect(",")
        turns = self._hold_turns()
        self._expect(",")
        inbound = self._integer()
        self._accept("°")
        self._expect("inbound", ")")
        return HoldSpec(direction, turns, inbound)

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
        """target := NAME+ "(" IDENT ")" [type] | IDENT [type] | FIX | NAME+ type"""
        start = self._position()
        words = self._uppercase_words()
        if not words:
            raise self._unmatched()
        if self._accept("("):
            ident = self._ident()
            self._expect(")")
            return NavaidRef(ident, self._navaid_type(), " ".join(words))
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


def _turned(leg: Leg) -> Leg:
    """The leg a climbing turn turns onto, else the leg itself."""
    return leg.then if isinstance(leg, ClimbingTurn) else leg


def _same_facility(target: NavaidRef | FixRef, navaid: NavaidRef | FixRef) -> bool:
    return target.ident == navaid.ident


def _end_fix(leg: Leg) -> NavaidRef | FixRef | None:
    """The fix a leg ends at, or ``None`` when it ends anywhere else."""
    if isinstance(leg, ClimbingTurn):
        return _end_fix(leg.then)
    if isinstance(leg, Direct):
        return leg.target
    if isinstance(leg, ClimbInHold):
        return leg.fix
    until = getattr(leg, "until", None)
    return until.target if isinstance(until, AtFix) else None
