"""Published minimum climb gradients from TAKEOFF MINIMUMS text.

The section is advisory for gradients only, so unlike the procedure grammar
this extractor skips what it does not recognize.
"""

from __future__ import annotations

import re

_TAKEOFF_MINIMUMS_ENTRY = re.compile(
    r"\bRwys? (?P<runways>\d{1,2}[LRC]?(?:/[LRC])?"
    r"(?:\s*,\s*\d{1,2}[LRC]?(?:/[LRC])?)*)\s*,(?P<body>.*?)(?=\bRwys? \d|\Z)"
)
_STANDARD_MIN_CLIMB = re.compile(r"\bstd\. with a min\. climb of (\d+) ft per NM")
_RUNWAY_ID = re.compile(r"(\d{1,2})([LRC]?)((?:/[LRC])*)")


def parse_takeoff_minimums(text: str) -> dict[str, float]:
    """Each runway's published minimum climb gradient (ft/NM) with standard
    minimums, e.g. ``{"15": 320.0}``; runways without one are skipped."""
    return {
        runway: float(climb[1])
        for entry in _TAKEOFF_MINIMUMS_ENTRY.finditer(text)
        if (climb := _STANDARD_MIN_CLIMB.search(entry["body"]))
        for runway in _runway_ids(entry["runways"])
    }


def _runway_ids(runway_list: str) -> list[str]:
    """``"4, 35"`` → ``["4", "35"]``; ``"2L/R"`` → ``["2L", "2R"]``."""
    return [
        runway
        for match in _RUNWAY_ID.finditer(runway_list)
        for runway in _expand_runway(match[1], match[2], match[3])
    ]


def _expand_runway(number: str, side: str, siblings: str) -> list[str]:
    return [number + side, *(number + sibling for sibling in siblings.split("/")[1:])]
