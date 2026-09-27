"""Text drawn on the planview: altitude constraints, headings, radials,
and the human phrases that name polylines.

Labels are plain text: ForeFlight renders neither combining characters nor
rich text, so a constraint is spelled out ("at or above 9300'") rather than
drawn with the chart's underline/overline marks."""

from __future__ import annotations

from .procedure import (
    Altitude,
    AltitudeKind,
    ClimbHeading,
    HeadingAndRadial,
    Radial,
    Turn,
    Until,
)

PLAIN_PREFIXES = {
    AltitudeKind.AT_OR_ABOVE: "at or above ",
    AltitudeKind.AT_OR_BELOW: "at or below ",
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
    """Render an altitude spelled out ("plain": at or above 7000') or as an
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
    """A heading as printed, e.g. ``077°``."""
    return f"{magnetic:03d}°"


def radial_label(radial: int) -> str:
    """A radial as printed, e.g. ``R-210``."""
    return f"R-{radial:03d}"


def hold_label(until: Until | None, style: str) -> str:
    """``Hold`` plus, in parentheses, the altitude to climb to in the hold."""
    if isinstance(until, Altitude):
        return f"Hold ({format_altitude(until, style)})"
    return "Hold"


def vcoa_label(at_or_above: int, style: str) -> str:
    """``VCOA`` plus, in parentheses, the altitude to cross the airport at."""
    return f"VCOA ({format_feet(at_or_above, AltitudeKind.AT_OR_ABOVE, style)})"


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
