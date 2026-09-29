"""Text drawn on the planview: altitude constraints, headings, radials,
and the human phrases that name polylines.

Labels are plain text: ForeFlight renders neither combining characters nor
rich text, so a constraint takes a precomposed sign ("≥9300'") rather than
the chart's underline/overline marks. It elides the middle of a label
longer than `LABEL_LIMIT` characters."""

from __future__ import annotations

import re

from .procedure import (
    Altitude,
    AltitudeKind,
    ClimbHeading,
    Compass8,
    Dme,
    EnrouteAltitude,
    HeadingAndRadial,
    HeadingRange,
    HeadingSector,
    HoldSpec,
    NavaidRef,
    Radial,
    SpeedRestriction,
    Turn,
    Until,
)

LABEL_LIMIT = 22
"""The most characters of a label ForeFlight shows; it elides a longer one
from the middle."""

ALL_RUNWAYS = "ALL RWYS"
PLAIN_PREFIXES = {
    AltitudeKind.AT_OR_ABOVE: "≥",
    AltitudeKind.AT_OR_BELOW: "≤",
    AltitudeKind.AT: "at ",
    AltitudeKind.TO: "",
}
FMS_SUFFIXES = {
    AltitudeKind.AT_OR_ABOVE: "A",
    AltitudeKind.AT_OR_BELOW: "B",
    AltitudeKind.AT: "",
    AltitudeKind.TO: "",
}


def format_altitude(alt: Altitude | EnrouteAltitude, style: str) -> str:
    """Render an altitude in plain text ("plain": ≥7000', ≤7000') or as an
    FMS constraint ("fms": 7000A, 7000B, 7000).

    A climb-to altitude (`AltitudeKind.TO`) is the bare figure in both styles.
    An FMS has no suffix for a mandatory altitude, so `AltitudeKind.AT` is
    also bare digits in the "fms" style; only the plain style tells it apart.
    An en-route minimum reads as its names, e.g. ``≥MEA/MCA`` or ``MEA/MCA A``,
    after any figure printed with them, which drops its foot mark to keep a
    hold's label short: ``≥4000/MEA``.
    """
    if isinstance(alt, EnrouteAltitude):
        names = "/".join(alt.names)
        if alt.feet is not None:
            names = f"{alt.feet}/{names}"
        if style == "fms":
            return f"{names} {FMS_SUFFIXES[alt.kind]}".rstrip()
        return f"{PLAIN_PREFIXES[alt.kind]}{names}"
    return format_feet(alt.feet, alt.kind, style)


def format_feet(feet: int, kind: AltitudeKind, style: str) -> str:
    """`format_altitude` for a bare figure and constraint kind."""
    if style == "fms":
        return f"{feet}{FMS_SUFFIXES[kind]}"
    return f"{PLAIN_PREFIXES[kind]}{feet}'"


def fits(label: str) -> bool:
    """Whether ForeFlight shows all of `label`."""
    return len(label) <= LABEL_LIMIT


def heading_label(magnetic: int) -> str:
    """A heading to fly, e.g. ``hdg 077°``."""
    return f"hdg {magnetic:03d}°"


def heading_range_label(leg: HeadingRange, direction: Turn | None, style: str) -> str:
    """The sectors as printed, e.g. ``hdg 350° CW 162°`` or, with a published
    turn and altitude, ``LT hdg 256° CW 054° or 179° CW 254° 7700'``."""
    return " ".join(heading_range_phrases(leg, direction, style))


def heading_range_phrases(
    leg: HeadingRange, direction: Turn | None, style: str
) -> tuple[str, ...]:
    """`heading_range_label` in the pieces a label may break between: the
    first sector with any turn onto it, each further sector and any altitude,
    e.g. ``("LT hdg 256° CW 054°", "or 179° CW 254°", "7700'")``."""
    turn = f"{direction}T " if direction else ""
    first, *others = map(_sector_phrase, leg.sectors)
    altitude = (
        (format_altitude(leg.until, style),) if isinstance(leg.until, Altitude) else ()
    )
    return (f"{turn}hdg {first}", *(f"or {other}" for other in others), *altitude)


def _sector_phrase(sector: HeadingSector) -> str:
    """e.g. ``350° CW 162°``."""
    return (
        f"{sector.start:03d}° {'CW' if sector.clockwise else 'CCW'} {sector.end:03d}°"
    )


def heading_range_lines(
    runways: tuple[str, ...], phrases: tuple[str, ...]
) -> list[str]:
    """The labels for a heading range flown from `runways`, top to bottom:
    its `phrases` after the runways, filling as few lines as show whole in
    ForeFlight. The runways lead the first line, e.g.
    ``["35L/R hdg 333° CW 162°"]``, dropping its ``hdg`` if that is what
    lets them, e.g. ``["16L/R 213° CCW 353°"]``, or saves a line, e.g.
    ``["11 320° CW 220° 3000'"]``; otherwise they stand on a line of their
    own. They are left out when they do not fit a label."""
    tag = runway_tag(runways)
    if tag is None:
        return _wrapped(phrases)
    first, *rest = phrases
    leading = [
        _wrapped((line, *rest))
        for line in (f"{tag} {first}", f"{tag} {_bare(first)}")
        if fits(line)
    ]
    return min([*leading, [_standing(tag), *_wrapped(phrases)]], key=len)


NOT_SHOWN = "ODP NOT SHOWN"


def not_shown_lines(parts: list[str]) -> list[str]:
    """The labels naming the `parts` of an airport's procedure left undrawn,
    top to bottom, each whole within `LABEL_LIMIT`: ``ODP NOT SHOWN``, then
    the parts packed into as few lines as fit, e.g. ``["ODP NOT SHOWN",
    "RWY 17L/17R, VCOA"]``. Only a part too long for a line of its own
    breaks, after a ``/`` in its runway list."""
    last = len(parts) - 1
    pieces = [
        piece
        for index, part in enumerate(parts)
        for piece in _unbroken(part + ("," if index < last else ""))
    ]
    lines: list[str] = []
    for piece in pieces:
        if lines and fits(joined := _joined(lines[-1], piece)):
            lines[-1] = joined
        else:
            lines.append(piece)
    return [NOT_SHOWN, *lines]


def _unbroken(part: str) -> list[str]:
    """`part` whole if it fits a line, else broken after each ``/``."""
    return [part] if fits(part) else re.findall(r"[^/]+/?", part)


def _joined(line: str, piece: str) -> str:
    """`piece` after `line`: straight on after a ``/``, else after a space."""
    return f"{line}{piece}" if line.endswith("/") else f"{line} {piece}"


def _bare(phrase: str) -> str:
    """A range's first phrase without its ``hdg``, e.g. ``LT 256° CW 054°``."""
    return phrase.replace("hdg ", "", 1)


def runway_tag(runways: tuple[str, ...]) -> str | None:
    """`runways` as tightly as a label can name them, e.g. ``35L/R``, or
    ``ALL RWYS`` when the text names none; ``None`` when they do not fit."""
    tag = _designators(runways) if runways else ALL_RUNWAYS
    return tag if fits(tag) else None


def _standing(tag: str) -> str:
    """A runway tag as a line of its own, e.g. ``RWY 35L/R``."""
    standing = f"RWY {tag}"
    return standing if tag != ALL_RUNWAYS and fits(standing) else tag


def _wrapped(phrases: tuple[str, ...]) -> list[str]:
    """`phrases` in order, as many to a line as fit a label."""
    lines: list[str] = []
    for phrase in phrases:
        if lines and fits(joined := f"{lines[-1]} {phrase}"):
            lines[-1] = joined
        else:
            lines.append(phrase)
    return lines


RUNWAY_DESIGNATOR = re.compile(r"(\d*)(.*)")
PARALLEL_ORDER = {"L": 0, "C": 1, "R": 2}


def _designators(runways: tuple[str, ...]) -> str:
    """Runway designators as tightly as a chart gives them, parallels
    sharing their number, e.g. ``35L/R`` or ``16L/R,17L``."""
    parallels: dict[str, list[str]] = {}
    for runway in runways:
        number, side = RUNWAY_DESIGNATOR.fullmatch(runway).groups()
        parallels.setdefault(number, []).append(side)
    return ",".join(
        number + "/".join(sorted(sides, key=_parallel_rank))
        for number, sides in parallels.items()
    )


def _parallel_rank(side: str) -> int:
    """L, C, R in that order; any other suffix after them."""
    return PARALLEL_ORDER.get(side, len(PARALLEL_ORDER))


def radial_label(radial: int) -> str:
    """A radial as printed, e.g. ``R-210``."""
    return f"R-{radial:03d}"


def navaid_radial_label(navaid: NavaidRef, radial: int) -> str:
    """A radial with the navaid it belongs to, e.g. ``SAU R-035``."""
    return f"{navaid.ident} {radial_label(radial)}"


def dme_label(dme: Dme) -> str:
    """A DME distance with its navaid, e.g. ``BAM 10 DME``."""
    return f"{dme.navaid.ident} {dme.nm:g} DME"


def hold_label(spec: HoldSpec, until: Until | None, style: str) -> str:
    """The hold's inbound course, turn direction in the ODP's own ``RT``/``LT``
    abbreviation, and the altitude to climb to in it, e.g. ``Hold 246° RT
    ≥9300'``.

    ForeFlight elides long labels, so the fix (named on its own chart) and
    the word "inbound" (implied by a hold's course) are left out.
    """
    label = f"Hold {spec.inbound_course:03d}° {spec.turns}T"
    if isinstance(until, Altitude | EnrouteAltitude):
        label += f" {format_altitude(until, style)}"
    return label


REACHING_ALTITUDE = re.compile(r"reaching (\d+)(?: MSL)?", re.IGNORECASE)


def speed_label(speed: SpeedRestriction) -> str:
    """The limit and, when it holds to an altitude, that altitude: ``≤200 KIAS
    until 9000'`` for "until reaching 9000 MSL". Any other end ("until
    established on course") does not fit `LABEL_LIMIT`, so reads ``≤200 KIAS``."""
    label = f"≤{speed.kias} KIAS"
    if altitude := REACHING_ALTITUDE.fullmatch(speed.until_phrase):
        label += f" until {altitude[1]}'"
    return label


def vcoa_label(at_or_above: int, style: str, bound: Compass8 | None = None) -> str:
    """``VCOA`` plus, in parentheses, the altitude to cross the airport at and
    any direction to cross it in, e.g. ``VCOA (≥8200' SE bound)``. The
    direction is left to the FAA text where the label would not fit
    `LABEL_LIMIT` with it: ``VCOA (≥12500')``."""
    altitude = format_feet(at_or_above, AltitudeKind.AT_OR_ABOVE, style)
    bound_label = f"VCOA ({altitude} {bound} bound)"
    return bound_label if bound and fits(bound_label) else f"VCOA ({altitude})"


def turn_phrase(direction: Turn | None) -> str:
    """``climbing right turn `` (with trailing space) or nothing."""
    if direction is None:
        return ""
    return f"climbing {'right' if direction is Turn.RIGHT else 'left'} turn "


def heading_phrase(leg: ClimbHeading) -> str:
    """e.g. ``heading 077 to 7000``."""
    phrase = f"heading {leg.heading:03d}"
    if isinstance(leg.until, Altitude):
        phrase += f" to {leg.until.feet}"
    return phrase


def radial_phrase(leg: Radial | HeadingAndRadial) -> str:
    """e.g. ``TPH R-210 inbound``."""
    sense = "outbound" if leg.outbound else "inbound"
    return f"{leg.navaid.ident} {radial_label(leg.radial)} {sense}"
