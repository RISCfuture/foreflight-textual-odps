"""Text drawn on the planview: altitude constraints, headings, radials,
mileages, and the human phrases that name polylines."""

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

UNDERLINE = "̲"  # combining low line
OVERLINE = "̅"  # combining overline
CHART_MARKS = {
    AltitudeKind.AT_OR_ABOVE: UNDERLINE,
    AltitudeKind.AT_OR_BELOW: OVERLINE,
    AltitudeKind.AT: UNDERLINE + OVERLINE,
    AltitudeKind.TO: "",
}
FMS_SUFFIXES = {
    AltitudeKind.AT_OR_ABOVE: "A",
    AltitudeKind.AT_OR_BELOW: "B",
    AltitudeKind.AT: "",
    AltitudeKind.TO: "",
}


def format_altitude(alt: Altitude, style: str) -> str:
    """Render an altitude as charted ("chart": digits under/overlined) or as
    an FMS constraint ("fms": 7000A, 7000B, 7000).

    A climb-to altitude (`AltitudeKind.TO`) is plain digits in both styles.
    An FMS has no suffix for a mandatory altitude, so `AltitudeKind.AT` is
    also bare digits in the "fms" style; only the chart style tells it apart.
    """
    return format_feet(alt.feet, alt.kind, style)


def format_feet(feet: int, kind: AltitudeKind, style: str) -> str:
    """`format_altitude` for a bare figure and constraint kind."""
    if style == "fms":
        return f"{feet}{FMS_SUFFIXES[kind]}"
    return "".join(digit + CHART_MARKS[kind] for digit in str(feet))


def heading_label(magnetic: int) -> str:
    """A heading as printed, e.g. ``077°``."""
    return f"{magnetic:03d}°"


def radial_label(radial: int) -> str:
    """A radial as printed, e.g. ``R-210``."""
    return f"R-{radial:03d}"


def mileage_label(nm: float) -> str:
    """A segment's charted mileage, e.g. ``(12)``."""
    return f"({nm:.0f})"


def hold_label(until: Until | None, style: str) -> str:
    """``HOLD`` plus the altitude to climb to in the hold, if one is given."""
    if isinstance(until, Altitude):
        return f"HOLD {format_altitude(until, style)}"
    return "HOLD"


def vcoa_label(at_or_above: int, style: str) -> str:
    """``VCOA`` plus the altitude to cross the airport at or above."""
    return f"VCOA {format_feet(at_or_above, AltitudeKind.AT_OR_ABOVE, style)}"


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
