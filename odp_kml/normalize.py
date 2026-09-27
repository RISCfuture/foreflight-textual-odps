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
    return text.replace("…", "...")


def _spell_out_min_climb(text: str) -> str:
    """``w/min.``, ``w/ min.``, ``w/min`` and ``with a min.`` all read ``with a min.``."""
    return re.sub(r"\b(?:w/ ?|with a )min\b\.?", "with a min.", text)


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


def _collapse_blank_lines(text: str) -> str:
    """A run of blank lines becomes one blank line."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _drop_doubled_of(text: str) -> str:
    return re.sub(r"\bof of\b", "of", text)


_SUBSTITUTIONS: tuple[Callable[[str], str], ...] = (
    _straighten_quotes,
    _expand_ellipsis,
    _spell_out_min_climb,
    _expand_fractions,
    _drop_foot_marks,
    _collapse_horizontal_whitespace,
    _strip_lines,
    _collapse_blank_lines,
    _drop_doubled_of,
    str.strip,
)


def normalize(text: str) -> str:
    """Return ``text`` with every canonical-spelling substitution applied in order."""
    for substitute in _SUBSTITUTIONS:
        text = substitute(text)
    return text
