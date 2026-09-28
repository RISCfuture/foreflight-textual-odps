"""Uncertainty records the pipeline emits instead of drawing, and the build report.

A procedure is drawn only when the pipeline is certain of it. Anything less
certain becomes a `Finding`. The build report lists every finding for a
cycle and groups them by `signature_key`, so CI can file one GitHub issue
per distinct underlying problem no matter which airport or cycle it recurs
in.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections import Counter
from enum import StrEnum
from pathlib import Path


class Kind(StrEnum):
    """Why a procedure was not drawn."""

    PARSE_FAILED = "parse_failed"
    UNRESOLVED_REF = "unresolved_ref"
    AMBIGUOUS_REF = "ambiguous_ref"
    HOLD_AMBIGUOUS = "hold_ambiguous"
    GEOMETRY_DEGENERATE = "geometry_degenerate"
    RUNWAY_MISSING = "runway_missing"


@dataclasses.dataclass(frozen=True)
class Finding:
    """One reason a single airport's procedure could not be drawn."""

    kind: Kind
    signature: str
    airport: str
    cycle: str
    amendment: str | None
    verbatim_text: str
    detail: str


_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")
_IDENTIFIER = re.compile(r"\b[A-Z0-9]{2,5}\b")

# Aviation abbreviations that share the identifier token shape (2-5
# uppercase letters/digits) but name a *kind* of thing rather than one
# airport's own navaid, fix or facility, so they must survive blurring.
_IDENTIFIER_ALLOWLIST = frozenset(
    {
        "AGL",
        "AMDT",
        "ATC",
        "CCW",
        "CW",
        "DER",
        "DME",
        "DP",
        "DVA",
        "E",
        "FAA",
        "FT",
        "ICA",
        "IFR",
        "ILS",
        "KIAS",
        "KT",
        "LOC",
        "LT",
        "MCA",
        "MEA",
        "MOCA",
        "MSL",
        "N",
        "NA",
        "NDB",
        "NE",
        "NM",
        "NW",
        "ODP",
        "ORIG",
        "RNAV",
        "RT",
        "S",
        "SE",
        "SID",
        "SM",
        "STAR",
        "SW",
        "TACAN",
        "TODA",
        "TORA",
        "VCA",
        "VCOA",
        "VFR",
        "VOR",
        "VORTAC",
        "W",
    }
)


def _collapse_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _blur_digits(text: str) -> str:
    """Replace every digit run with a same-length run of ``n`` (``210`` -> ``nnn``)."""
    return _DIGITS.sub(lambda match: "n" * len(match.group()), text)


def _blur_identifier_token(match: re.Match[str]) -> str:
    token = match.group()
    if token.isdigit() or token in _IDENTIFIER_ALLOWLIST:
        return token
    return "ID"


def _blur_identifiers(text: str, airport: str | None) -> str:
    """Replace airport, navaid and fix identifiers with ``ID``.

    A bare token of 2-5 uppercase letters and/or digits (``TPH``, ``KTNX``,
    ``3U3``, ``MECCA``) names one specific airport, navaid or fix rather
    than the underlying issue, so it is blurred unless it is a fixed
    aviation abbreviation in `_IDENTIFIER_ALLOWLIST` or is made entirely of
    digits (a bare number is blurred separately, by digit run, so distinct
    numeric magnitudes stay distinguishable). ``airport`` - the finding's
    own airport LID - is blurred wherever it appears, case insensitively,
    even when it does not fit that shape.
    """
    if airport:
        text = re.sub(rf"\b{re.escape(airport)}\b", "ID", text, flags=re.IGNORECASE)
    return _IDENTIFIER.sub(_blur_identifier_token, text)


def normalize_signature(text: str, airport: str | None = None) -> str:
    """Fold ``text`` to a form that is identical across airports and cycles.

    The rules, applied in order:

    1. Blur the finding's own airport LID (``airport``) wherever it
       appears, case insensitively.
    2. Blur every other bare identifier token - an airport LID, navaid or
       fix name of 2-5 uppercase letters/digits - to ``ID``, except a
       fixed allowlist of aviation abbreviations (``VOR``, ``NDB``,
       ``DME``, ...) which are kept verbatim. This applies inside quoted
       spans too, since a quoted verbatim excerpt is exactly where such
       identifiers appear.
    3. Lowercase and collapse whitespace.
    4. Replace every remaining run of digits with a same-length run of
       ``n``, since runway, radial and altitude numbers vary per airport
       but do not change the underlying issue (``R-210`` -> ``r-nnn``,
       ``9300`` -> ``nnnn``).
    """
    text = _blur_identifiers(text, airport)
    text = _collapse_whitespace(text.lower())
    return _blur_digits(text)


def signature_key(kind: Kind, signature: str, airport: str | None = None) -> str:
    """A stable id for ``kind`` and ``signature``, identical across airports and cycles."""
    normalized = normalize_signature(signature, airport)
    return hashlib.sha1(f"{kind}:{normalized}".encode()).hexdigest()


@dataclasses.dataclass
class Report:
    """Everything about one cycle's build: what was drawn and what was not."""

    cycle: str
    airports_with_text: int
    drawn: int
    findings: list[Finding]
    label_count: int
    graphic_only: int = 0

    @property
    def textual(self) -> int:
        """Airports with a text procedure to draw: those with DEPARTURE
        PROCEDURE or VCOA text, less those that only name charted DPs."""
        return self.airports_with_text - self.graphic_only

    def not_drawn_by_kind(self) -> dict[Kind, int]:
        """Count of findings for each `Kind` that actually occurred."""
        return dict(Counter(finding.kind for finding in self.findings))

    def grouped(self) -> dict[str, list[Finding]]:
        """Findings grouped by `signature_key`, each group sorted by airport."""
        groups: dict[str, list[Finding]] = {}
        for finding in self.findings:
            key = signature_key(finding.kind, finding.signature, finding.airport)
            groups.setdefault(key, []).append(finding)
        for group in groups.values():
            group.sort(key=lambda finding: finding.airport)
        return groups

    def summary_line(self) -> str:
        """e.g. "Drew 2,341 of 2,512 ODPs (93%); 171 findings in 23 signatures",
        with "; 202 airports use only charted DPs" when any do.

        Airports that only name charted DPs have no text procedure to draw, so
        they are left out of the percentage.
        """
        percentage = round(100 * self.drawn / self.textual) if self.textual else 0
        line = f"Drew {self.drawn:,} of {self.textual:,} ODPs ({percentage}%); "
        if self.graphic_only:
            line += f"{self.graphic_only:,} airports use only charted DPs; "
        return (
            line + f"{len(self.findings):,} findings in "
            f"{len(self.grouped()):,} signatures"
        )

    def to_json(self) -> str:
        """Serialize to JSON preserving every field, for `from_json` to restore."""
        payload = {
            "cycle": self.cycle,
            "airports_with_text": self.airports_with_text,
            "drawn": self.drawn,
            "label_count": self.label_count,
            "graphic_only": self.graphic_only,
            "findings": [dataclasses.asdict(finding) for finding in self.findings],
        }
        return json.dumps(payload, indent=2)

    @classmethod
    def from_json(cls, text: str) -> Report:
        """Restore a `Report` written by `to_json`."""
        payload = json.loads(text)
        findings = [
            Finding(**{**finding, "kind": Kind(finding["kind"])})
            for finding in payload["findings"]
        ]
        return cls(
            cycle=payload["cycle"],
            airports_with_text=payload["airports_with_text"],
            drawn=payload["drawn"],
            findings=findings,
            label_count=payload["label_count"],
            graphic_only=payload.get("graphic_only", 0),
        )

    def to_markdown(self) -> str:
        """A human-readable report: summary, counts by kind, then one section per signature."""
        lines = [f"# {self.summary_line()}", "", *self._counts_table()]
        for findings in self.grouped().values():
            lines += self._signature_section(findings)
        return "\n".join(lines) + "\n"

    def _counts_table(self) -> list[str]:
        lines = ["| kind | count |", "| --- | --- |"]
        lines += [
            f"| {kind} | {count} |"
            for kind, count in sorted(self.not_drawn_by_kind().items())
        ]
        return [*lines, ""]

    def _signature_section(self, findings: list[Finding]) -> list[str]:
        first = findings[0]
        signature = normalize_signature(first.signature, first.airport)
        heading = f"### {first.kind}: {signature} ({len(findings)} airports)"
        lines = [heading, ""]
        for finding in findings:
            lines += [
                f"{finding.airport} ({finding.amendment})",
                "```",
                finding.verbatim_text,
                "```",
                "",
            ]
        return lines


def write_report(report: Report, json_path: Path, md_path: Path) -> None:
    """Write ``report`` as JSON (for tooling) and Markdown (for humans)."""
    json_path.write_text(report.to_json() + "\n")
    md_path.write_text(report.to_markdown())
