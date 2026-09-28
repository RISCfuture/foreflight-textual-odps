"""Compare two builds of one cycle airport by airport (offline only).

A grammar or geometry change can draw airports the fixtures never exercise,
or quietly change ones already drawn. This snapshots every airport's outcome
in a cycle (drawn whole, drawn in part, charted DPs only, or not drawn), its
findings, and a digest of every shape it draws; ``diff`` then names each
airport gained, lost or redrawn between two snapshots.

Usage::

    python tools/compare_builds.py snapshot --cycle 2026-09-03 --out before.json
    # ... change the code ...
    python tools/compare_builds.py snapshot --cycle 2026-09-03 --out after.json
    python tools/compare_builds.py diff before.json after.json [--state CO]

Snapshots reuse the build's download cache (``--cache``, default
``data_cache``). Nothing here is imported or used by the build.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odp_kml import dtpp, nasr, pipeline
from odp_kml.cycle import Cycle
from odp_kml.extract import extract_blocks
from odp_kml.shapes import AirportDrawing, Polyline

WHOLE = "whole"
PART = "part"
CHARTED = "charted"
NOT_DRAWN = "not drawn"
_DIGEST_PLACES = 6
_TOP_SIGNATURES = 20


@dataclasses.dataclass(frozen=True)
class Outcome:
    """One airport's result in a snapshot."""

    state: str
    outcome: str
    digest: str | None
    findings: tuple[str, ...]

    @property
    def drawn(self) -> bool:
        return self.outcome in (WHOLE, PART)


type Snapshot = dict[str, Outcome]


def snapshot(cycle: Cycle, cache_dir: Path) -> Snapshot:
    """Build `cycle` from the cache and record every airport with procedure text."""
    metafile = dtpp.fetch_metafile(cycle, cache_dir)
    pdfs = dtpp.fetch_to_pdfs(cycle, metafile, cache_dir)
    nasr_data = nasr.fetch(cycle, cache_dir)
    options = pipeline.BuildOptions(cycle=cycle, cache_dir=cache_dir)
    states = {lid: airport.state for lid, airport in metafile.airports.items()}
    result: Snapshot = {}
    for volume, pdf in sorted(pdfs.items()):
        blocks, _ = extract_blocks(pdf, volume)
        for block in blocks:
            outcome = pipeline.process_block(block, nasr_data, options)
            if outcome is not None:
                state = states.get(block.lid) or states.get(block.lid[1:], "")
                result[block.lid] = record(outcome, state)
    return result


def record(outcome: pipeline.BlockOutcome, state: str = "") -> Outcome:
    """The snapshot entry for one block's outcome."""
    if outcome.drawing is not None:
        kind = PART if outcome.findings else WHOLE
    else:
        kind = CHARTED if outcome.graphic_only else NOT_DRAWN
    return Outcome(
        state=state,
        outcome=kind,
        digest=digest(outcome.drawing) if outcome.drawing else None,
        findings=tuple(
            f"{finding.kind}: {finding.signature}" for finding in outcome.findings
        ),
    )


def digest(drawing: AirportDrawing) -> str:
    """A short hash of every shape, in order, with positions rounded to about
    10 cm so float noise does not read as a change."""
    shapes = [
        (
            type(shape).__name__,
            shape.name if isinstance(shape, Polyline) else shape.text,
            [
                (round(point.lat, _DIGEST_PLACES), round(point.lon, _DIGEST_PLACES))
                for point in (
                    shape.points if isinstance(shape, Polyline) else [shape.at]
                )
            ],
        )
        for shape in drawing.shapes
    ]
    return hashlib.sha1(repr(shapes).encode()).hexdigest()[:12]


@dataclasses.dataclass(frozen=True)
class Difference:
    """What changed between two snapshots."""

    gained: list[str]
    lost: list[str]
    redrawn: list[str]
    findings_changed: list[str]


def diff(before: Snapshot, after: Snapshot) -> Difference:
    """Airports newly drawn, no longer drawn, drawn differently, and whose
    findings changed, each sorted by LID."""
    both = sorted(before.keys() & after.keys())
    return Difference(
        gained=sorted(
            lid for lid in after if after[lid].drawn and not _drawn(before, lid)
        ),
        lost=sorted(
            lid for lid in before if before[lid].drawn and not _drawn(after, lid)
        ),
        redrawn=[
            lid
            for lid in both
            if before[lid].drawn
            and after[lid].drawn
            and before[lid].digest != after[lid].digest
        ],
        findings_changed=[
            lid for lid in both if before[lid].findings != after[lid].findings
        ],
    )


def _drawn(snap: Snapshot, lid: str) -> bool:
    return lid in snap and snap[lid].drawn


def summary(snap: Snapshot) -> str:
    """e.g. "1,154 of 1,479 drawn (78%; 1,010 whole, 144 in part); 199 charted"."""
    counts = collections.Counter(outcome.outcome for outcome in snap.values())
    textual = len(snap) - counts[CHARTED]
    drawn = counts[WHOLE] + counts[PART]
    percentage = round(100 * drawn / textual) if textual else 0
    return (
        f"{drawn:,} of {textual:,} drawn ({percentage}%; {counts[WHOLE]:,} whole, "
        f"{counts[PART]:,} in part); {counts[CHARTED]:,} charted"
    )


def only_state(snap: Snapshot, state: str | None) -> Snapshot:
    if state is None:
        return snap
    return {lid: outcome for lid, outcome in snap.items() if outcome.state == state}


def save(snap: Snapshot, path: Path) -> None:
    payload = {lid: dataclasses.asdict(outcome) for lid, outcome in snap.items()}
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")


def load(path: Path) -> Snapshot:
    return {
        lid: Outcome(
            state=entry["state"],
            outcome=entry["outcome"],
            digest=entry["digest"],
            findings=tuple(entry["findings"]),
        )
        for lid, entry in json.loads(path.read_text()).items()
    }


def report(before: Snapshot, after: Snapshot) -> str:
    """The text `diff` prints."""
    change = diff(before, after)
    signatures = collections.Counter(
        finding for outcome in after.values() for finding in outcome.findings
    )
    lines = [
        f"before: {summary(before)}",
        f"after:  {summary(after)}",
        f"gained {len(change.gained)}: {' '.join(change.gained)}",
        f"lost {len(change.lost)}: {' '.join(change.lost)}",
        f"redrawn {len(change.redrawn)}: {' '.join(change.redrawn)}",
        f"findings changed at {len(change.findings_changed)} airports",
        "top findings after:",
        *(
            f"  {count:5d}  {signature}"
            for signature, count in signatures.most_common(_TOP_SIGNATURES)
        ),
    ]
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="Record every airport in a cycle.")
    snap.add_argument("--cycle", type=Cycle.from_iso, required=True)
    snap.add_argument("--out", type=Path, required=True)
    snap.add_argument("--cache", type=Path, default=Path("data_cache"))
    compare = commands.add_parser("diff", help="Compare two snapshots.")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    compare.add_argument("--state", help="Only airports in this state, e.g. CO.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args.command == "snapshot":
        snap = snapshot(args.cycle, args.cache)
        save(snap, args.out)
        print(f"{args.out}: {summary(snap)}")
        return 0
    before = only_state(load(args.before), args.state)
    after = only_state(load(args.after), args.state)
    print(report(before, after))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
