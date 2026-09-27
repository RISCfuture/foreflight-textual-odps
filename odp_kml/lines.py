"""Logical lines of Takeoff-Minimums block text and their structural vocabulary.

A block's printed sentences wrap at the column edge with no hanging indent,
and its titles and section headers come in several spellings. This module
names the canonical titles and headers and rejoins wrapped physical lines
into logical lines: one line per title, amendment, section header and
runway sentence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

DVA_TITLE = "DIVERSE VECTOR AREA (RADAR VECTORS)"
TOM_TITLE = "TAKEOFF MINIMUMS AND (OBSTACLE) DEPARTURE PROCEDURES"
TAKEOFF_MINIMUMS = "TAKEOFF MINIMUMS:"
DEPARTURE_PROCEDURE = "DEPARTURE PROCEDURE:"
VCOA = "VCOA:"
TAKEOFF_OBSTACLE_NOTES = "TAKEOFF OBSTACLE NOTES:"
_BLOCK_TITLES = frozenset({DVA_TITLE, TOM_TITLE})
_SECTION_HEADERS = frozenset(
    {TAKEOFF_MINIMUMS, DEPARTURE_PROCEDURE, VCOA, TAKEOFF_OBSTACLE_NOTES}
)

_TITLES = (
    (re.compile(r"DIVERSE VECTOR AREA \(RADAR VECTORS\)"), DVA_TITLE),
    (
        re.compile(r"TAKEOFF MINIMUMS? AND \(OBSTACLE\) ?DEPARTURE PROCEDURES?"),
        TOM_TITLE,
    ),
)

# Each header's observed spellings, typos included. The header text may
# continue on the same line after the colon.
_HEADERS = (
    (re.compile(r"(?:T?AKE-?OFF|TAKOFF) MIN[A-Z]*"), TAKEOFF_MINIMUMS),
    (re.compile(r"(?:DEPA[A-Z]*|DEPARTING) PROCEDURES?"), DEPARTURE_PROCEDURE),
    (re.compile(r"VCOA"), VCOA),
    (re.compile(r"T?AKEOFF OBSTACLES?(?: NOTES)?"), TAKEOFF_OBSTACLE_NOTES),
)
_AFTER_HEADER = re.compile(r"\s*(?:[:;]\.?\s*(?P<rest>.*)|$)")
# ``VCOA Rwys 3, 28: obtain ...`` and ``VCOA All Runways: obtain ...``.
_VCOA_WITH_RUNWAYS = re.compile(r"VCOA\s+(?P<rest>(?:All Runways|Rwys?\b)[^:]*:.*)")

# ``AMDT 2 17AUG17 (17229) (FAA)``, ``ORIG-A ...``, ``AMDT1 ...``, and the
# prefix-less ``28DEC23 (23362) (USN)`` or ``(21196) (USAF)``.
_AMENDMENT = re.compile(
    r"(?:ORIG|AMDT)\S*(?:\s.*)?"
    r"|(?:\d{2}[A-Z]{3}\d{2}\s+)?\(\s?\d{5}\)(?:\s*\([A-Z-]+\))?"
)

_RUNWAY_START = re.compile(r"Rwys?\b")
_ELLIPSIS_START = re.compile(r"(?:\.\.\.|…|\. \. \.)")
_ELLIPSIS_END = re.compile(r"(?:\.\.\.|…|\. \. \.)$")
# Words that open a new item (rather than continue a sentence) in the
# minimums, departure, VCOA and DVA sections.
_ITEM_START = re.compile(
    r"(?:Rwys?|Runways?|All|Use|Obtain|VCOA|NOTE|Note|CAUTION|Helipad|Sea Lane"
    r"|Diverse|TAKEOFF)\b"
)


def join_lines(lines: Iterable[str]) -> list[str]:
    """Rejoin wrapped physical lines of block text into logical lines.

    Titles, amendment lines and section headers (with typo'd spellings
    canonicalized) each get a line of their own; text after a header's colon
    moves to the next line. A runway line (``Rwy``/``Rwys``) or a shared
    continuation (``...continue climb``) always starts a new line. Otherwise,
    in TAKEOFF OBSTACLE NOTES every line after a sentence end starts a new
    note; elsewhere a line starts a new item only after an ellipsis (when it
    is capitalized) or after a sentence end when it opens with an item word
    such as ``All``, ``Use`` or ``NOTE``, and continues the previous line
    otherwise.
    """
    logical: list[str] = []
    section: str | None = None
    after_structure = True
    for line in _split_headers(lines):
        if (structure := _structural(line)) is not None:
            logical.append(structure)
            section = structure if structure in _SECTION_HEADERS else section
            section = None if structure in _BLOCK_TITLES else section
            after_structure = True
        elif after_structure or _starts_new_line(logical[-1], line, section):
            logical.append(line)
            after_structure = False
        else:
            logical[-1] = f"{logical[-1]} {line}"
    return logical


def canonical_title(line: str) -> str | None:
    """``DVA_TITLE`` or ``TOM_TITLE`` when ``line`` is a spelling of one."""
    for spelling, canonical in _TITLES:
        if spelling.fullmatch(line):
            return canonical
    return None


def is_amendment(text: str) -> bool:
    """Whether ``text`` is a block's ``ORIG``/``AMDT`` amendment line."""
    return bool(_AMENDMENT.fullmatch(text))


def _split_headers(lines: Iterable[str]) -> Iterator[str]:
    """Split ``HEADER: text`` into the header line and its text line."""
    for line in lines:
        header, rest = _header(line)
        if header is None:
            yield line
            continue
        yield header
        if rest:
            yield rest


def _header(line: str) -> tuple[str | None, str]:
    """The canonical section header opening ``line`` and the text after it."""
    if vcoa := _VCOA_WITH_RUNWAYS.fullmatch(line):
        return VCOA, vcoa["rest"]
    for spelling, canonical in _HEADERS:
        if (match := spelling.match(line)) and (
            after := _AFTER_HEADER.fullmatch(line, match.end())
        ):
            return canonical, (after["rest"] or "").strip()
    return None, line


def _structural(line: str) -> str | None:
    """The canonical form of a title, amendment or section-header line."""
    if (title := canonical_title(line)) is not None:
        return title
    if is_amendment(line):
        return line
    return line if line in _SECTION_HEADERS else None


def _starts_new_line(previous: str, line: str, section: str | None) -> bool:
    if _RUNWAY_START.match(line) or _ELLIPSIS_START.match(line):
        return True
    ends_sentence = previous.endswith((".", ":"))
    if section == TAKEOFF_OBSTACLE_NOTES:
        return ends_sentence
    if _ELLIPSIS_END.search(previous):
        return line[0].isupper()
    return ends_sentence and bool(_ITEM_START.match(line))
