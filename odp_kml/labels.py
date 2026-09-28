"""Text drawn on the planview: altitude constraints, headings, radials,
and the human phrases that name polylines.

Labels are plain text: ForeFlight renders neither combining characters nor
rich text, so a constraint takes a precomposed sign ("≥9300'") rather than
the chart's underline/overline marks."""

from __future__ import annotations

import re

from .procedure import (
    Altitude,
    AltitudeKind,
    ClimbHeading,
    Compass8,
    Dme,
    HeadingAndRadial,
    HeadingRange,
    HoldSpec,
    NavaidRef,
    Radial,
    SpeedRestriction,
    Turn,
    Until,
)

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


def format_altitude(alt: Altitude, style: str) -> str:
    """Render an altitude in plain text ("plain": ≥7000', ≤7000') or as an
    FMS constraint ("fms": 7000A, 7000B, 7000).

    A climb-to altitude (`AltitudeKind.TO`) is the bare figure in both styles.
    An FMS has no suffix for a mandatory altitude, so `AltitudeKind.AT` is
    also bare digits in the "fms" style; only the plain style tells it apart.
    """
    return format_feet(alt.feet, alt.kind, style)


def format_feet(feet: int, kind: AltitudeKind, style: str) -> str:
    """`format_altitude` for a bare figure and constraint kind."""
    if style == "fms":
        return f"{feet}{FMS_SUFFIXES[kind]}"
    return f"{PLAIN_PREFIXES[kind]}{feet}'"


def heading_label(magnetic: int) -> str:
    """A heading to fly, e.g. ``hdg 077°``."""
    return f"hdg {magnetic:03d}°"


def heading_range_label(leg: HeadingRange, direction: Turn | None, style: str) -> str:
    """The sectors as printed, e.g. ``hdg 350° CW 162°`` or, with a published
    turn and altitude, ``LT hdg 256° CW 054° or 179° CW 254° 7700'``."""
    sectors = " or ".join(
        f"{s.start:03d}° {'CW' if s.clockwise else 'CCW'} {s.end:03d}°"
        for s in leg.sectors
    )
    label = f"hdg {sectors}"
    if direction is not None:
        label = f"{direction}T {label}"
    if isinstance(leg.until, Altitude):
        label += f" {format_altitude(leg.until, style)}"
    return label


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
    if isinstance(until, Altitude):
        label += f" {format_altitude(until, style)}"
    return label


REACHING_ALTITUDE = re.compile(r"reaching (\d+)(?: MSL)?", re.IGNORECASE)


def speed_label(speed: SpeedRestriction) -> str:
    """e.g. ``max 200 KIAS until 9000'`` for "until reaching 9000 MSL", or
    ``max 200 KIAS until established on course``."""
    return f"max {speed.kias} KIAS until {_speed_until(speed.until_phrase)}"


def _speed_until(phrase: str) -> str:
    if altitude := REACHING_ALTITUDE.fullmatch(phrase):
        return f"{altitude[1]}'"
    return phrase


def vcoa_label(at_or_above: int, style: str, bound: Compass8 | None = None) -> str:
    """``VCOA`` plus, in parentheses, the altitude to cross the airport at and
    any direction to cross it in, e.g. ``VCOA (≥8200' SE bound)``."""
    altitude = format_feet(at_or_above, AltitudeKind.AT_OR_ABOVE, style)
    return f"VCOA ({altitude} {bound} bound)" if bound else f"VCOA ({altitude})"


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
