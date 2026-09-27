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
    AT_OR_ABOVE = "at_or_above"
    AT_OR_BELOW = "at_or_below"
    AT = "at"


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
class AtFix:
    """A leg ends on reaching a fix or navaid."""

    target: NavaidRef | FixRef


@dataclasses.dataclass(frozen=True)
class Dme:
    """A leg ends at a DME distance from a navaid."""

    navaid: NavaidRef
    nm: float


@dataclasses.dataclass(frozen=True)
class CrossRadial:
    """A leg ends on crossing a navaid's radial."""

    navaid: NavaidRef
    radial: int


Until = Altitude | AtFix | Dme | CrossRadial


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
class Direct:
    """Proceed direct to a navaid or fix."""

    target: NavaidRef | FixRef
    speed: SpeedRestriction | None = None


@dataclasses.dataclass(frozen=True)
class Radial:
    """Fly a navaid's radial, inbound or outbound."""

    navaid: NavaidRef
    radial: int
    outbound: bool
    until: Until | None = None
    speed: SpeedRestriction | None = None


@dataclasses.dataclass(frozen=True)
class HeadingAndRadial:
    """Fly a heading until intercepting a navaid's radial."""

    heading: int
    navaid: NavaidRef
    radial: int
    outbound: bool
    until: Until | None = None
    speed: SpeedRestriction | None = None


@dataclasses.dataclass(frozen=True)
class ClimbingTurn:
    """Climb in a turn before flying the next leg."""

    direction: Turn | None
    then: Direct | HeadingAndRadial | Radial | ClimbHeading


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
class Thence:
    """A no-op leg marking a transition to the shared tail."""


Leg = (
    ClimbHeading
    | Direct
    | Radial
    | HeadingAndRadial
    | ClimbingTurn
    | ClimbInHold
    | ProceedOnCourse
    | Thence
)


@dataclasses.dataclass(frozen=True)
class RunwayGroup:
    """The legs flown from one or more runways sharing the same departure."""

    runways: tuple[str, ...]
    legs: tuple[Leg, ...]


@dataclasses.dataclass(frozen=True)
class VcoaGroup:
    """A visual climb over airport: climb over the airport or a fix, then proceed.

    An empty ``runways`` means every runway.
    """

    runways: tuple[str, ...]
    cross: FixRef | None
    at_or_above: int
    then: tuple[Leg, ...]


@dataclasses.dataclass(frozen=True)
class Procedure:
    """A complete textual obstacle departure procedure for one airport."""

    airport: str
    amendment: str | None
    runway_groups: tuple[RunwayGroup, ...]
    shared_tail: tuple[Leg, ...] | None
    vcoa: tuple[VcoaGroup, ...]

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

    Raises `ValueError` if `data["node"]` names no known node class.
    """
    cls = _NODE_CLASSES.get(data.get("node"))
    if cls is None:
        raise ValueError(f"unknown node type: {data.get('node')!r}")
    hints = typing.get_type_hints(cls)
    kwargs = {
        field.name: _deserialize(data[field.name], hints[field.name])
        for field in dataclasses.fields(cls)
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
