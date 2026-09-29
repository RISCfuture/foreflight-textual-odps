"""Published minimum climb gradients from TAKEOFF MINIMUMS text, and the
speed limits it sets.

The section is advisory for gradients only, so unlike the procedure grammar
this extractor skips what it does not recognize. The pipeline does not read
a speed limit, so one there withholds every route it may bind.
"""

from __future__ import annotations

import dataclasses
import re

_TAKEOFF_MINIMUMS_ENTRY = re.compile(
    r"\bRwys? (?P<runways>\d{1,2}[LRC]?(?:/[LRC])?"
    r"(?:\s*,\s*\d{1,2}[LRC]?(?:/[LRC])?)*)\s*,(?P<body>.*?)(?=\bRwys? \d|$)",
    re.MULTILINE,
)
_STANDARD_MIN_CLIMB = re.compile(
    r"\bstd\. with a min\. climb of (\d+) ft per NM(?: (?:to|until passing) (\d+)\b)?"
)
_RUNWAY_ID = re.compile(r"(\d{1,2})([LRC]?)((?:/[LRC])*)")
_SPEED = re.compile(r"\b\d{2,3} ?(?:K|KIAS|KTS?|knots)\b", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class ClimbGradient:
    """A published minimum climb gradient and the altitude it holds to
    ("... 490 ft per NM to 6300"), ``None`` when printed without one."""

    ft_per_nm: float
    to_ft: int | None = None


def parse_takeoff_minimums(text: str) -> dict[str, ClimbGradient]:
    """Each runway's published minimum climb gradient with standard minimums,
    e.g. ``{"15": ClimbGradient(320.0, 9100)}``; runways without one are
    skipped."""
    return {
        runway: ClimbGradient(float(climb[1]), int(climb[2]) if climb[2] else None)
        for entry in _TAKEOFF_MINIMUMS_ENTRY.finditer(text)
        if (climb := _STANDARD_MIN_CLIMB.search(entry["body"]))
        for runway in _runway_ids(entry["runways"])
    }


def speed_limit_for(text: str, runways: tuple[str, ...]) -> str | None:
    """The first line of `text` setting a speed limit that may bind `runways`
    ("Rwy 10, ... do not exceed 210 KIAS until intercepting the ENI R-073"),
    or ``None``. A line naming no runway may bind any, and every line may
    bind a route for all runways (empty `runways`)."""
    return next(
        (
            line
            for line in text.splitlines()
            if _SPEED.search(line) and _may_bind(_named_runways(line), runways)
        ),
        None,
    )


def _named_runways(line: str) -> set[str]:
    return {
        runway
        for entry in _TAKEOFF_MINIMUMS_ENTRY.finditer(line)
        for runway in _runway_ids(entry["runways"])
    }


def _may_bind(named: set[str], runways: tuple[str, ...]) -> bool:
    return not named or not runways or not named.isdisjoint(runways)


def _runway_ids(runway_list: str) -> list[str]:
    """``"4, 35"`` → ``["4", "35"]``; ``"2L/R"`` → ``["2L", "2R"]``."""
    return [
        runway
        for match in _RUNWAY_ID.finditer(runway_list)
        for runway in _expand_runway(match[1], match[2], match[3])
    ]


def _expand_runway(number: str, side: str, siblings: str) -> list[str]:
    return [number + side, *(number + sibling for sibling in siblings.split("/")[1:])]
