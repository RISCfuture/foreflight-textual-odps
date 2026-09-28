"""The whole build: FAA sources in, planview drawings and a findings report out.

Each airport block runs parse, resolve and draw on its own; a failure at any
stage becomes that airport's `Finding` and the build moves on. Only the
exception types those stages raise for uncertain input are caught, so an
unexpected exception, which means a bug, still stops the build.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Mapping
from pathlib import Path

from . import dtpp, nasr
from .cycle import Cycle
from .extract import AirportBlock, check_against_metafile, extract_blocks
from .findings import Finding, Kind, Report
from .geometry import DEFAULT_PARAMS, Degenerate, DisplayParams, draw
from .grammar import ParseError, parse_procedure
from .minimums import parse_takeoff_minimums
from .normalize import normalize
from .palette import assign_palettes
from .procedure import Procedure
from .resolve import ResolveError, resolve
from .sections import Sections, split_sections
from .shapes import AirportDrawing, Label

LOG = logging.getLogger(__name__)

_TEXT_FIELDS = (
    "takeoff_minimums",
    "departure_procedure",
    "vcoa",
    "obstacle_notes",
    "dva",
)
_NO_BLOCK = re.compile(r"\S+: metafile airport (?P<airport>\S+) has no block")
_NO_METAFILE_AIRPORT = re.compile(
    r"\S+: block (?P<airport>\S+) has no metafile airport"
)
_ICAO_LENGTH = 4

type SectionsEntry = dict[str, str | None]


@dataclasses.dataclass(frozen=True)
class BuildOptions:
    """What to build: the cycle, where downloads are cached, an optional
    airport subset (by printed id, LID or ICAO id), and display assumptions."""

    cycle: Cycle
    cache_dir: Path
    airports: frozenset[str] | None = None
    params: DisplayParams = DEFAULT_PARAMS


@dataclasses.dataclass
class BuildResult:
    """Drawings sorted by LID, the build report, and each block's sections."""

    drawings: list[AirportDrawing]
    report: Report
    sections_dump: list[SectionsEntry]


@dataclasses.dataclass(frozen=True)
class BlockOutcome:
    """One airport block's sections entry and either its drawing, its finding,
    or `graphic_only` when every runway flies a charted DP instead."""

    sections: SectionsEntry
    drawing: AirportDrawing | None = None
    finding: Finding | None = None
    graphic_only: bool = False


def build(options: BuildOptions) -> BuildResult:
    """Download (or reuse cached) d-TPP and NASR data for the cycle and build."""
    metafile = dtpp.fetch_metafile(options.cycle, options.cache_dir)
    pdfs = dtpp.fetch_to_pdfs(options.cycle, metafile, options.cache_dir)
    nasr_data = nasr.fetch(options.cycle, options.cache_dir)
    return build_from_sources(metafile, pdfs, nasr_data, options)


def build_from_sources(
    metafile: dtpp.Metafile,
    pdfs_by_volume: Mapping[str, Path],
    nasr_data: nasr.NasrData,
    options: BuildOptions,
) -> BuildResult:
    """Build from already-fetched sources: one Takeoff-Minimums PDF per volume."""
    outcomes: list[BlockOutcome] = []
    findings: list[Finding] = []
    for volume, pdf in sorted(pdfs_by_volume.items()):
        blocks = _volume_blocks(pdf, volume)
        findings += _metafile_findings(blocks, metafile, volume, options)
        outcomes += _process_blocks(blocks, nasr_data, options)
    drawings = assign_palettes(
        outcome.drawing for outcome in outcomes if outcome.drawing
    )
    findings += [outcome.finding for outcome in outcomes if outcome.finding]
    report = Report(
        cycle=options.cycle.iso,
        airports_with_text=len(outcomes),
        drawn=len(drawings),
        findings=findings,
        label_count=_label_count(drawings),
        graphic_only=sum(outcome.graphic_only for outcome in outcomes),
    )
    return BuildResult(drawings, report, [outcome.sections for outcome in outcomes])


def process_block(
    block: AirportBlock, nasr_data: nasr.NasrData, options: BuildOptions
) -> BlockOutcome | None:
    """Parse, resolve and draw one block; ``None`` when it has no procedure text.

    A parse, resolve or geometry failure becomes the outcome's finding. A
    procedure whose every runway flies a charted DP is neither drawn nor a
    finding.
    """
    raw = split_sections(block.text)
    if raw.departure_procedure is None and raw.vcoa is None:
        return None
    sections = _normalized(raw)
    entry = _sections_entry(block.lid, sections)
    try:
        procedure = parse_procedure(sections, airport=block.lid)
        if procedure.graphic_only:
            return BlockOutcome(entry, graphic_only=True)
        drawing = _draw_procedure(
            block.lid, procedure, sections, nasr_data, options.params
        )
    except (ParseError, ResolveError, Degenerate) as error:
        return BlockOutcome(entry, finding=_finding(error, block.lid, raw, options))
    return BlockOutcome(entry, drawing=drawing)


def find_airport(data: nasr.NasrData, ident: str) -> nasr.Airport:
    """The NASR airport printed as ``ident``: by LID, by ICAO id, then by the
    id without a leading ``K``. Raises `ResolveError` when none matches."""
    airport = (
        data.airports.get(ident)
        or _airport_by_icao(data, ident)
        or data.airports.get(_without_k(ident))
    )
    if airport is None:
        raise ResolveError(Kind.UNRESOLVED_REF, "airport not in NASR", ident)
    return airport


# --- Volumes -----------------------------------------------------------------


def _volume_blocks(pdf: Path, volume: str) -> list[AirportBlock]:
    blocks, warnings = extract_blocks(pdf, volume)
    for warning in warnings:
        LOG.warning("%s", warning)
    return blocks


def _process_blocks(
    blocks: list[AirportBlock], nasr_data: nasr.NasrData, options: BuildOptions
) -> list[BlockOutcome]:
    return [
        outcome
        for block in blocks
        if _is_selected(block.lid, options.airports)
        and (outcome := process_block(block, nasr_data, options))
    ]


def _is_selected(ident: str, airports: frozenset[str] | None) -> bool:
    return airports is None or bool({ident, _without_k(ident)} & airports)


def _metafile_findings(
    blocks: list[AirportBlock],
    metafile: dtpp.Metafile,
    volume: str,
    options: BuildOptions,
) -> list[Finding]:
    expected = {
        lid: airport
        for lid, airport in metafile.airports.items()
        if airport.volume == volume
    }
    findings = [
        _metafile_finding(mismatch, options.cycle)
        for mismatch in check_against_metafile(blocks, expected)
    ]
    return [
        finding
        for finding in findings
        if _is_selected(finding.airport, options.airports)
    ]


def _metafile_finding(mismatch: str, cycle: Cycle) -> Finding:
    """A finding for one ``check_against_metafile`` message."""
    if match := _NO_BLOCK.fullmatch(mismatch):
        signature = "metafile airport has no block"
    elif match := _NO_METAFILE_AIRPORT.fullmatch(mismatch):
        signature = "airport not in metafile"
    else:
        raise ValueError(f"unrecognized metafile mismatch: {mismatch}")
    return Finding(
        kind=Kind.UNRESOLVED_REF,
        signature=signature,
        airport=match["airport"],
        cycle=cycle.iso,
        amendment=None,
        verbatim_text="",
        detail=mismatch,
    )


# --- One block ---------------------------------------------------------------


def _normalized(sections: Sections) -> Sections:
    return dataclasses.replace(
        sections,
        **{
            field: normalize(text)
            for field in _TEXT_FIELDS
            if (text := getattr(sections, field)) is not None
        },
    )


def _sections_entry(lid: str, sections: Sections) -> SectionsEntry:
    """The block in the input format of ``tools/draft_golden.py``."""
    return {
        "lid": lid,
        "amendment": sections.amendment,
        "departure_procedure": sections.departure_procedure,
        "vcoa": sections.vcoa,
    }


def _draw_procedure(
    lid: str,
    procedure: Procedure,
    sections: Sections,
    nasr_data: nasr.NasrData,
    params: DisplayParams,
) -> AirportDrawing:
    airport = find_airport(nasr_data, lid)
    min_climb = parse_takeoff_minimums(sections.takeoff_minimums or "")
    return draw(resolve(procedure, airport, nasr_data, min_climb=min_climb), params)


def _finding(
    error: ParseError | ResolveError | Degenerate,
    lid: str,
    raw: Sections,
    options: BuildOptions,
) -> Finding:
    kind, signature = _kind_and_signature(error)
    return Finding(
        kind=kind,
        signature=signature,
        airport=lid,
        cycle=options.cycle.iso,
        amendment=raw.amendment,
        verbatim_text="\n".join(filter(None, (raw.departure_procedure, raw.vcoa))),
        detail=error.detail,
    )


def _kind_and_signature(
    error: ParseError | ResolveError | Degenerate,
) -> tuple[Kind, str]:
    match error:
        case ResolveError():
            return error.kind, error.signature
        case ParseError():
            return Kind.PARSE_FAILED, error.signature
        case Degenerate():
            return Kind.GEOMETRY_DEGENERATE, error.signature


# --- Airports ----------------------------------------------------------------


def _airport_by_icao(data: nasr.NasrData, ident: str) -> nasr.Airport | None:
    if len(ident) != _ICAO_LENGTH:
        return None
    return next(
        (airport for airport in data.airports.values() if airport.icao == ident), None
    )


def _without_k(ident: str) -> str:
    """``KW94`` → ``W94``; any other id unchanged."""
    if len(ident) == _ICAO_LENGTH and ident.startswith("K"):
        return ident[1:]
    return ident


def _label_count(drawings: list[AirportDrawing]) -> int:
    return sum(
        isinstance(shape, Label) for drawing in drawings for shape in drawing.shapes
    )
