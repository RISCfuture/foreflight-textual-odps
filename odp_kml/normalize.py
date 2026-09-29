"""Canonical spelling for Takeoff-Minimums text before grammar parsing.

The FAA text mixes typographic and ASCII punctuation, several spellings of
"with a minimum climb", vulgar fractions and foot marks. ``normalize`` folds
these into one spelling each, as an ordered list of small named substitutions.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "º": "°"})

_FRACTIONS = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}


def _straighten_quotes(text: str) -> str:
    """Curly quotes and apostrophes become straight; ``º`` becomes ``°``."""
    return text.translate(_QUOTES)


def _expand_ellipsis(text: str) -> str:
    """``…``, spaced ``. . .`` and a run of four or more dots all read ``...``.

    Only spaces and tabs may separate the dots, so an ellipsis ending one line
    and another opening the next stay two.
    """
    text = text.replace("…", "...")
    return re.sub(r"\.(?:[ \t]?\.){2,}", "...", text)


def _spell_radials(text: str) -> str:
    """``r-221``, ``R- 083`` and ``R·210`` all read ``R-nnn``."""
    return re.sub(r"\b[Rr][-·]\s*(?=\d{3}\b)", "R-", text)


def _join_navaid_slash(text: str) -> str:
    """``VOR/ DME``, a line break inside the type joined back, reads ``VOR/DME``."""
    return re.sub(r"\b(VOR|NDB)/\s+DME\b", r"\1/DME", text)


def _hyphenate_climb_in_hold(text: str) -> str:
    """``climb-in hold``, ``climb-in- hold``, ``climb-in -hold`` and ``climb
    in- hold``, a hyphen spaced or dropped at a line break, read
    ``climb-in-hold`` (and ``climb-in-holding``)."""
    return re.sub(
        r"\b(climb)(?:-in\s*-?\s*|\s+in\s*-\s*)(hold)",
        r"\1-in-\2",
        text,
        flags=re.IGNORECASE,
    )


def _spell_at_or_above(text: str) -> str:
    """``at/above`` reads ``at or above``."""
    return re.sub(r"\bat/above\b", "at or above", text, flags=re.IGNORECASE)


def _spell_speed_limits(text: str) -> str:
    """``exceed 180K``, ``exceed 180 knots`` and ``exceed 250 KTS`` read
    ``exceed 180 KIAS``."""
    return re.sub(
        r"\b(exceed \d+) ?(?:K|KT|KTS|kts|knots)\b",
        r"\1 KIAS",
        text,
        flags=re.IGNORECASE,
    )


def _single_periods(text: str) -> str:
    """A doubled period (``course..``) is one; an ellipsis is left alone."""
    return re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)


def _space_headings(text: str) -> str:
    """``heading130°`` reads ``heading 130°``."""
    return re.sub(r"\b(heading|hdg)(?=\d)", r"\1 ", text, flags=re.IGNORECASE)


def _drop_heading_decimals(text: str) -> str:
    """``heading 045.00`` reads ``heading 045``."""
    return re.sub(
        r"\b((?:heading|hdg) \d{1,3})\.0+\b", r"\1", text, flags=re.IGNORECASE
    )


def _drop_thousands_separators(text: str) -> str:
    """``10,000`` becomes ``10000``; a list such as ``1,200,300`` is left alone."""
    return re.sub(r"(?<![\d,])(\d{1,2}),(\d{3})(?![\d,])", r"\1\2", text)


def _spell_out_min_climb(text: str) -> str:
    """``w/min.``, ``w/ min.``, ``w/min`` and ``with a min.`` all read ``with a min.``."""
    return re.sub(r"\b(?:w/ ?|with a )min\b\.?", "with a min.", text)


def _spell_vcoa(text: str) -> str:
    """``VOCA``, its letters transposed, reads ``VCOA``."""
    return re.sub(r"\bVOCA\b", "VCOA", text)


def _climb_runway_heading_to(text: str) -> str:
    """``runway heading 3200``, the ``to`` left out before a number too large
    to be a heading, reads ``runway heading to 3200``."""
    return re.sub(
        r"\b(runway heading)\s+(?=[1-9]\d{3,4}\b)", r"\1 to ", text, flags=re.IGNORECASE
    )


def _expand_fractions(text: str) -> str:
    """``2¾`` becomes ``2 3/4`` and a bare ``¾`` becomes ``3/4``."""
    for glyph, ascii_fraction in _FRACTIONS.items():
        text = re.sub(rf"(?<=\d){glyph}", f" {ascii_fraction}", text)
        text = text.replace(glyph, ascii_fraction)
    return text


def _drop_foot_marks(text: str) -> str:
    """``320' per NM`` becomes ``320 ft per NM``; any other ``5000'`` becomes ``5000``."""
    text = re.sub(r"(?<=\d)'(?=\s*(?:per NM|/NM))", " ft", text)
    return re.sub(r"(?<=\d)'", "", text)


def _collapse_horizontal_whitespace(text: str) -> str:
    """Runs of spaces and tabs become one space; line breaks stay."""
    return re.sub(r"[^\S\n]+", " ", text)


def _strip_lines(text: str) -> str:
    return "\n".join(line.strip() for line in text.split("\n"))


def _spell_proceeding(text: str) -> str:
    """``before preceding on course`` reads ``before proceeding on course``."""
    return re.sub(r"\bpreceding on course\b", "proceeding on course", text)


def _climb_on_course(text: str) -> str:
    """``continue climb on course`` and ``continue climbing on course`` read
    ``climb on course``."""
    return re.sub(
        r"\b([Cc])ontinue climb(?:ing)? on course\b",
        lambda match: f"{'C' if match[1] == 'C' else 'c'}limb on course",
        text,
    )


def _collapse_blank_lines(text: str) -> str:
    """A run of blank lines becomes one blank line."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _drop_doubled_of(text: str) -> str:
    return re.sub(r"\bof of\b", "of", text)


def _join_hold_spec(text: str) -> str:
    """A period printed between ``holding pattern`` and the parenthesized
    hold that describes it (``holding pattern. (Hold W, ...)``) is dropped."""
    return re.sub(
        r"\b(holding pattern)\. (\(hold\b)", r"\1 \2", text, flags=re.IGNORECASE
    )


_SUBSTITUTIONS: tuple[Callable[[str], str], ...] = (
    _straighten_quotes,
    _expand_ellipsis,
    _spell_radials,
    _join_navaid_slash,
    _space_headings,
    _drop_heading_decimals,
    _hyphenate_climb_in_hold,
    _spell_at_or_above,
    _drop_thousands_separators,
    _spell_speed_limits,
    _single_periods,
    _spell_out_min_climb,
    _spell_vcoa,
    _climb_runway_heading_to,
    _expand_fractions,
    _drop_foot_marks,
    _collapse_horizontal_whitespace,
    _strip_lines,
    _spell_proceeding,
    _climb_on_course,
    _collapse_blank_lines,
    _drop_doubled_of,
    _join_hold_spec,
    str.strip,
)


def normalize(text: str) -> str:
    """Return ``text`` with every canonical-spelling substitution applied in order."""
    for substitute in _SUBSTITUTIONS:
        text = substitute(text)
    return text
