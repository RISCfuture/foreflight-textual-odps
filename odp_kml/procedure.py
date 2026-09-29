"""The ODP abstract syntax tree: what the text grammar produces and the
geometry/KML code consumes.

Every node is a frozen, hashable dataclass so a parsed procedure can be
compared, deduplicated, and cached. `to_dict`/`from_dict` (and the JSON
wrappers on `Procedure`) serialize any node generically from its dataclass
fields, tagged with its class name, which makes this module's JSON form the
golden-fixture format for tests.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import typing
from enum import StrEnum


class Turn(StrEnum):
    LEFT = "L"
    RIGHT = "R"


class Compass8(StrEnum):
    N = "N"
    NE = "NE"
    E = "E"
    SE = "SE"
    S = "S"
    SW = "SW"
    W = "W"
    NW = "NW"


class NavaidType(StrEnum):
    VOR = "VOR"
    VORTAC = "VORTAC"
    VOR_DME = "VOR/DME"
    NDB = "NDB"
    DME = "DME"


class AltitudeKind(StrEnum):
    """How a leg's altitude constrains the climb.

    ``TO`` is a climb-to altitude ("climb heading 090 to 7000") with no
    at/above/below qualifier; ``AT`` is a mandatory "at 7000" crossing.
    """

    AT_OR_ABOVE = "at_or_above"
    AT_OR_BELOW = "at_or_below"
    AT = "at"
    TO = "to"


@dataclasses.dataclass(frozen=True)
class NavaidRef:
    """A named navigation aid, e.g. ``TONOPAH (TPH) VORTAC``."""

    ident: str
    type: NavaidType | None = None
    name: str | None = None


@dataclasses.dataclass(frozen=True)
class FixRef:
    """A five-letter fix, intersection, or waypoint identifier."""

    ident: str


@dataclasses.dataclass(frozen=True)
class Altitude:
    """A leg ends at an altitude, e.g. "at or above 9300"."""

    feet: int
    kind: AltitudeKind
    phrase: str


@dataclasses.dataclass(frozen=True)
class EnrouteAltitude:
    """A leg ends at an en-route minimum rather than a figure, e.g. "at or
    above MEA/MCA for route of flight", or at a figure or a minimum, "at or
    above 4000 or MEA for route of flight".

    `names` are the minimums as printed, in order (``("MEA", "MCA")``);
    `feet` is the figure printed before them, if any; `phrase` is the exact
    words.
    """

    names: tuple[str, ...]
    kind: AltitudeKind
    phrase: str
    feet: int | None = None


@dataclasses.dataclass(frozen=True)
class AtFix:
    """A leg ends on reaching a fix or navaid."""

    target: NavaidRef | FixRef


@dataclasses.dataclass(frozen=True)
class Dme:
    """A leg ends at a DME distance from a navaid.

    `fix` is the fix the text names at that distance ("to CARRO INT/OLM
    19.43 DME"), which the drawing checks against the distance.
    """

    navaid: NavaidRef
    nm: float
    fix: FixRef | None = None


@dataclasses.dataclass(frozen=True)
class CrossRadial:
    """A leg ends on crossing a navaid's radial."""

    navaid: NavaidRef
    radial: int


Until = Altitude | EnrouteAltitude | AtFix | Dme | CrossRadial


@dataclasses.dataclass(frozen=True)
class SpeedRestriction:
    """A speed limit in effect until some point, e.g. "250K until 3000"."""

    kias: int
    until_phrase: str


@dataclasses.dataclass(frozen=True)
class ClimbHeading:
    """Climb on a magnetic heading."""

    heading: int
    until: Until | None = None
    speed: SpeedRestriction | None = None


@dataclasses.dataclass(frozen=True)
class RunwayHeading:
    """Climb on the departure runway's own heading ("climb runway heading to
    1400"), whatever its magnetic value."""

    until: Until | None = None
    speed: SpeedRestriction | None = None


@dataclasses.dataclass(frozen=True)
class StraightAhead:
    """Climb straight ahead to an altitude, naming no heading or route: "climb
    to 1200 before turning left", "climb straight ahead to 2300"."""

    until: Altitude
    speed: SpeedRestriction | None = None


@dataclasses.dataclass(frozen=True)
class HeadingSector:
    """Headings from `start` sweeping `clockwise` (or counterclockwise) to
    `end`, magnetic, as printed: "between 213° CCW to 353°"."""

    start: int
    end: int
    clockwise: bool


@dataclasses.dataclass(frozen=True)
class HeadingRange:
    """Climb on any heading within the sectors, e.g. "climb on a heading
    between 350° CW to 162° from DER" (more than one sector when joined by
    "or"). Nothing is flown after it but "before proceeding on course"."""

    sectors: tuple[HeadingSector, ...]
    until: Until | None = None


@dataclasses.dataclass(frozen=True)
class Direct:
    """Proceed direct to a navaid or fix."""

    target: NavaidRef | FixRef
    speed: SpeedRestriction | None = None


@dataclasses.dataclass(frozen=True)
class Radial:
    """Fly a navaid's radial, inbound or outbound.

    `outbound` is ``None`` when the text prints neither; the drawing then
    takes it from where the leg ends, or refuses the leg. `altitude` is the
    altitude climbed to on the way to a fix or DME distance that ends the
    leg: "to 3000 via FSM R-064 to FSM VORTAC".
    """

    navaid: NavaidRef
    radial: int
    outbound: bool | None
    until: Until | None = None
    speed: SpeedRestriction | None = None
    altitude: Altitude | None = None


@dataclasses.dataclass(frozen=True)
class HeadingAndRadial:
    """Fly a heading until intercepting a navaid's radial.

    `altitude` is as for `Radial`: "to 6000 via heading 310° and ENI R-073 to
    ENI VORTAC".
    """

    heading: int
    navaid: NavaidRef
    radial: int
    outbound: bool | None
    until: Until | None = None
    speed: SpeedRestriction | None = None
    altitude: Altitude | None = None


@dataclasses.dataclass(frozen=True)
class ClimbingTurn:
    """Climb in a turn before flying the next leg.

    `then` is ``None`` for a turn whose route is the shared tail's first leg
    ("Rwy 20, climbing left turn, thence... ...direct ALS VORTAC"); such a
    turn is a runway group's last leg.
    """

    direction: Turn | None
    then: Direct | HeadingAndRadial | Radial | ClimbHeading | HeadingRange | None


@dataclasses.dataclass(frozen=True)
class HoldSpec:
    """A holding pattern's shape: entry side, turn direction, inbound course."""

    direction_from_fix: Compass8 | None
    turns: Turn
    inbound_course: int


@dataclasses.dataclass(frozen=True)
class ClimbInHold:
    """Climb in a holding pattern at a fix."""

    fix: NavaidRef | FixRef
    hold: HoldSpec | None
    until: Until | None = None


@dataclasses.dataclass(frozen=True)
class ProceedOnCourse:
    """Continue on course, e.g. "climb heading 090 to 7000 before turning left".

    `turn_restriction` is the direction of that eventual turn, deferred
    until the preceding leg's altitude or fix is reached; `None` if the
    procedure names no such turn.
    """

    turn_restriction: Turn | None = None


@dataclasses.dataclass(frozen=True)
class CrossAt:
    """Cross the fix the route has just reached at an altitude, e.g. "Cross
    LIN VOR/DME at or above 5000"."""

    fix: NavaidRef | FixRef
    altitude: Altitude | EnrouteAltitude


@dataclasses.dataclass(frozen=True)
class Thence:
    """A no-op leg marking a transition to the shared tail."""


@dataclasses.dataclass(frozen=True)
class GraphicDeparture:
    """Fly a charted (graphic) departure procedure instead, e.g. "use LUNDI
    DEPARTURE". Charted DPs are not drawn by this layer.

    `name` is the DP as printed before the word DEPARTURE, e.g. ``ELIM (RNAV)``.
    """

    name: str


Leg = (
    ClimbHeading
    | RunwayHeading
    | StraightAhead
    | HeadingRange
    | Direct
    | Radial
    | HeadingAndRadial
    | ClimbingTurn
    | ClimbInHold
    | ProceedOnCourse
    | CrossAt
    | Thence
    | GraphicDeparture
)


@dataclasses.dataclass(frozen=True)
class RunwayGroup:
    """The legs flown from one or more runways sharing the same departure."""

    runways: tuple[str, ...]
    legs: tuple[Leg, ...]

    @property
    def graphic(self) -> bool:
        """Whether these runways fly a charted DP rather than text legs."""
        return any(isinstance(leg, GraphicDeparture) for leg in self.legs)


@dataclasses.dataclass(frozen=True)
class VcoaGroup:
    """A visual climb over airport: climb over the airport or a fix, then proceed.

    An empty ``runways`` means every runway. ``bound`` is the direction to
    cross in ("cross … southeast bound at or above 8200"), if the text gives one.
    """

    runways: tuple[str, ...]
    cross: FixRef | None
    at_or_above: int
    then: tuple[Leg, ...]
    bound: Compass8 | None = None


@dataclasses.dataclass(frozen=True)
class Procedure:
    """A complete textual obstacle departure procedure for one airport."""

    airport: str
    amendment: str | None
    runway_groups: tuple[RunwayGroup, ...]
    shared_tail: tuple[Leg, ...] | None
    vcoa: tuple[VcoaGroup, ...]

    @property
    def graphic_only(self) -> bool:
        """Whether every runway flies a charted DP (or is NA), leaving no text
        procedure to draw."""
        return (
            not self.vcoa
            and self.shared_tail is None
            and any(group.graphic for group in self.runway_groups)
            and all(group.graphic or not group.legs for group in self.runway_groups)
        )

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize to JSON with sorted keys, for stable golden-fixture diffs."""
        return json.dumps(to_dict(self), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> Procedure:
        """Parse a `Procedure` from JSON produced by `to_json`."""
        return from_dict(json.loads(text))


def to_dict(node) -> dict:
    """Serialize any node to a plain dict tagged with its class name."""
    return {
        "node": type(node).__name__,
        **{
            field.name: _serialize(getattr(node, field.name))
            for field in dataclasses.fields(node)
        },
    }


def _serialize(value):
    """Recursively serialize one field value for `to_dict`."""
    if value is None:
        return None
    if dataclasses.is_dataclass(value):
        return to_dict(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    return value


def from_dict(data: dict):
    """Reconstruct a node from a dict produced by `to_dict`.

    A field missing from `data` takes its default, so JSON written before a
    defaulted field was added still loads. Raises `ValueError` if
    `data["node"]` names no known node class.
    """
    cls = _NODE_CLASSES.get(data.get("node"))
    if cls is None:
        raise ValueError(f"unknown node type: {data.get('node')!r}")
    hints = typing.get_type_hints(cls)
    kwargs = {
        field.name: _deserialize(data[field.name], hints[field.name])
        for field in dataclasses.fields(cls)
        if field.name in data
    }
    return cls(**kwargs)


def _deserialize(value, hint):
    """Recursively restore one field value for `from_dict`, using `hint` only
    to tell an enum's member string apart from a plain string."""
    if value is None:
        return None
    if isinstance(value, dict) and "node" in value:
        return from_dict(value)
    if isinstance(value, list):
        return tuple(_deserialize(item, None) for item in value)
    if isinstance(value, str):
        enum_type = _enum_type(hint)
        if enum_type is not None:
            return enum_type(value)
    return value


def _enum_type(hint) -> type[enum.Enum] | None:
    """The `Enum` subtype named by `hint`, unwrapping an `X | None` union."""
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return hint
    return next(
        (
            arg
            for arg in typing.get_args(hint)
            if isinstance(arg, type) and issubclass(arg, enum.Enum)
        ),
        None,
    )


_NODE_CLASSES: dict[str, type] = {
    obj.__name__: obj
    for obj in globals().values()
    if isinstance(obj, type) and dataclasses.is_dataclass(obj)
}
