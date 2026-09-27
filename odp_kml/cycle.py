"""28-day AIRAC cycle arithmetic shared by the d-TPP and NASR publications.

Every FAA aeronautical product used here turns over on the same 28-day
schedule. Cycles are computed from a fixed datum rather than scraped, so the
pipeline can name a cycle's files before the FAA posts them and can tell the
currently effective cycle from the upcoming one.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

DATUM = dt.date(2020, 12, 3)
PERIOD = dt.timedelta(days=28)


@dataclasses.dataclass(frozen=True, order=True)
class Cycle:
    """One 28-day publication cycle, identified by its effective date."""

    effective: dt.date

    def __post_init__(self) -> None:
        if (self.effective - DATUM).days % PERIOD.days:
            raise ValueError(f"{self.effective} is not a cycle effective date")

    @classmethod
    def containing(cls, day: dt.date) -> Cycle:
        """The cycle in effect on ``day``."""
        elapsed = (day - DATUM).days // PERIOD.days
        return cls(DATUM + elapsed * PERIOD)

    @classmethod
    def from_iso(cls, text: str) -> Cycle:
        return cls(dt.date.fromisoformat(text))

    @property
    def next(self) -> Cycle:
        return Cycle(self.effective + PERIOD)

    @property
    def iso(self) -> str:
        return self.effective.isoformat()

    @property
    def nasr_tag(self) -> str:
        """The ``DD_Mon_YYYY`` token in NASR CSV zip filenames."""
        return self.effective.strftime("%d_%b_%Y")

    @property
    def dtpp_code(self) -> str:
        """The d-TPP ``YYCC`` code: two-digit year and 1-based index within it."""
        index = (self.effective - self._first_of_year().effective).days // PERIOD.days
        return f"{self.effective.year % 100:02d}{index + 1:02d}"

    def _first_of_year(self) -> Cycle:
        candidate = Cycle.containing(dt.date(self.effective.year, 1, 1))
        return (
            candidate
            if candidate.effective.year == self.effective.year
            else candidate.next
        )
