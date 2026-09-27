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


_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")


def _collapse_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _blur_digits(text: str) -> str:
    """Replace every digit run with a same-length run of ``n`` (``210`` -> ``nnn``)."""
    return _DIGITS.sub(lambda match: "n" * len(match.group()), text)


def normalize_signature(text: str) -> str:
    """Fold ``text`` to a form that is identical across airports and cycles.

    The rules, applied in order:

    1. Lowercase and collapse whitespace.
    2. Replace every run of digits with a same-length run of ``n``, since
       runway, radial and altitude numbers vary per airport but do not
       change the underlying issue (``R-210`` -> ``r-nnn``, ``9300`` ->
       ``nnnn``).
    3. Leave quoted phrases (fix names, verbatim excerpts) untouched by
       rule 2, so two findings that genuinely differ only inside a quote
       still produce different signatures.
    """
    text = _collapse_whitespace(text.lower())
    pieces = []
    position = 0
    for match in _QUOTED.finditer(text):
        pieces.append(_blur_digits(text[position : match.start()]))
        pieces.append(match.group())
        position = match.end()
    pieces.append(_blur_digits(text[position:]))
    return "".join(pieces)


def signature_key(kind: Kind, signature: str) -> str:
    """A stable id for ``kind`` and ``signature``, identical across airports and cycles."""
    normalized = normalize_signature(signature)
    return hashlib.sha1(f"{kind}:{normalized}".encode()).hexdigest()


@dataclasses.dataclass
class Report:
    """Everything about one cycle's build: what was drawn and what was not."""

    cycle: str
    airports_with_text: int
    drawn: int
    findings: list[Finding]
    label_count: int

    def not_drawn_by_kind(self) -> dict[Kind, int]:
        """Count of findings for each `Kind` that actually occurred."""
        return dict(Counter(finding.kind for finding in self.findings))

    def grouped(self) -> dict[str, list[Finding]]:
        """Findings grouped by `signature_key`, each group sorted by airport."""
        groups: dict[str, list[Finding]] = {}
        for finding in self.findings:
            key = signature_key(finding.kind, finding.signature)
            groups.setdefault(key, []).append(finding)
        for group in groups.values():
            group.sort(key=lambda finding: finding.airport)
        return groups

    def summary_line(self) -> str:
        """e.g. "Drew 2,341 of 2,512 ODPs (93%); 171 findings in 23 signatures"."""
        percentage = (
            round(100 * self.drawn / self.airports_with_text)
            if self.airports_with_text
            else 0
        )
        return (
            f"Drew {self.drawn:,} of {self.airports_with_text:,} ODPs ({percentage}%); "
            f"{len(self.findings):,} findings in {len(self.grouped()):,} signatures"
        )

    def to_json(self) -> str:
        """Serialize to JSON preserving every field, for `from_json` to restore."""
        payload = {
            "cycle": self.cycle,
            "airports_with_text": self.airports_with_text,
            "drawn": self.drawn,
            "label_count": self.label_count,
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
        heading = f"### {first.kind}: {normalize_signature(first.signature)} ({len(findings)} airports)"
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
