"""An airport block's text split into its named sections."""

from __future__ import annotations

import dataclasses
import re

from .lines import (
    DEPARTURE_PROCEDURE,
    DVA_TITLE,
    TAKEOFF_MINIMUMS,
    TAKEOFF_OBSTACLE_NOTES,
    TOM_TITLE,
    VCOA,
    is_amendment,
)

_FIELD_BY_HEADER = {
    TAKEOFF_MINIMUMS: "takeoff_minimums",
    DEPARTURE_PROCEDURE: "departure_procedure",
    VCOA: "vcoa",
    TAKEOFF_OBSTACLE_NOTES: "obstacle_notes",
}

_DEPARTURE_INSTRUCTION = re.compile(
    r"\b(?:climb(?:ing)? (?:on|via|direct|heading|runway|to)|hdg|heading|turn"
    r"|direct|departures?|proceed(?:ing)? on course)\b",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class Sections:
    """The sections of one airport block; ``None`` where the block has none."""

    amendment: str | None
    takeoff_minimums: str | None
    departure_procedure: str | None
    vcoa: str | None
    obstacle_notes: str | None
    dva: str | None


def split_sections(block_text: str) -> Sections:
    """Split block text (as produced by ``extract``) into its sections.

    Lines after the DIVERSE VECTOR AREA title belong to ``dva`` until the
    TAKEOFF MINIMUMS title. ``amendment`` is the first ``ORIG``/``AMDT`` line
    outside the DVA, whether printed below the title or above it. A
    ``SECTION:`` header routes the lines after it to that section. A line in
    the takeoff block before any header goes to ``departure_procedure`` when
    it gives a departure instruction (a climb on a course or heading, a turn,
    ``direct``, a departure, or ``proceed on course``) and to
    ``takeoff_minimums`` otherwise.
    """
    lines: dict[str, list[str]] = {
        name: [] for name in (*_FIELD_BY_HEADER.values(), "dva")
    }
    amendment: str | None = None
    in_dva = False
    field: str | None = None
    for line in block_text.split("\n"):
        if line in (DVA_TITLE, TOM_TITLE):
            in_dva, field = line == DVA_TITLE, None
        elif line in _FIELD_BY_HEADER:
            field = _FIELD_BY_HEADER[line]
        elif is_amendment(line):
            if not in_dva and amendment is None:
                amendment = line
        elif in_dva:
            lines["dva"].append(line)
        else:
            lines[field or _unheaded_field(line)].append(line)
    return Sections(
        amendment=amendment,
        **{name: "\n".join(text) or None for name, text in lines.items()},
    )


def _unheaded_field(line: str) -> str:
    if _DEPARTURE_INSTRUCTION.search(line):
        return "departure_procedure"
    return "takeoff_minimums"
