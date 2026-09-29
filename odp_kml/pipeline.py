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
from .geo import destination
from .geometry import DEFAULT_PARAMS, Degenerate, DisplayParams, draw
from .grammar import ParseError, Unparsed, parse_procedure_in_part
from .minimums import parse_takeoff_minimums, speed_limit_for
from .normalize import normalize
from .palette import assign_palettes
from .procedure import Procedure, RunwayGroup, Thence
from .resolve import ResolveError, resolve
from .sections import Sections, split_sections
from .shapes import AirportDrawing, Label, Polyline

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
VCOA_PART = "VCOA"
UNNAMED_PART = "other runways"
NOT_DRAWN_LABEL_OFFSET_NM = 1.0
NOT_SHOWN_LABEL = "ODP NOT SHOWN"

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
    """Drawings sorted by LID, the build report, each block's sections, and
    the "ODP NOT SHOWN" markers for airports with text none of which drew."""

    drawings: list[AirportDrawing]
    report: Report
    sections_dump: list[SectionsEntry]
    markers: list[AirportDrawing] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class BlockOutcome:
    """One airport block's sections entry, its drawing (of all or part of the
    procedure) and its findings, or `graphic_only` when every runway flies a
    charted DP instead. `marker` labels an airport whose procedure could not
    be drawn at all."""

    sections: SectionsEntry
    drawing: AirportDrawing | None = None
    findings: tuple[Finding, ...] = ()
    graphic_only: bool = False
    marker: AirportDrawing | None = None


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
    markers = sorted(
        (outcome.marker for outcome in outcomes if outcome.marker),
        key=lambda marker: marker.lid,
    )
    findings += [finding for outcome in outcomes for finding in outcome.findings]
    report = Report(
        cycle=options.cycle.iso,
        airports_with_text=len(outcomes),
        drawn=len(drawings),
        findings=findings,
        label_count=_label_count([*drawings, *markers]),
        graphic_only=sum(outcome.graphic_only for outcome in outcomes),
        partial=sum(bool(outcome.drawing and outcome.findings) for outcome in outcomes),
    )
    return BuildResult(
        drawings, report, [outcome.sections for outcome in outcomes], markers
    )


def process_block(
    block: AirportBlock, nasr_data: nasr.NasrData, options: BuildOptions
) -> BlockOutcome | None:
    """Parse, resolve and draw one block; ``None`` when it has no procedure text.

    Each runway route and VCOA is drawn when it is certain on its own, so a
    parse, resolve or geometry failure in one of them becomes a finding for
    that part while the rest are drawn, beside a label naming what was
    not. When nothing can be drawn the airport has a single finding, as
    does a failure that no part escapes (the airport missing from NASR). A
    procedure whose every runway flies a charted DP is neither drawn nor a
    finding.
    """
    raw = split_sections(block.text)
    if raw.departure_procedure is None and raw.vcoa is None:
        return None
    sections = _normalized(raw)
    entry = _sections_entry(block.lid, sections)
    try:
        procedure, unparsed = parse_procedure_in_part(sections, airport=block.lid)
    except ParseError as error:
        return _not_drawn(entry, _finding(error, block.lid, raw, options), nasr_data)
    if procedure.graphic_only and not unparsed:
        return BlockOutcome(entry, graphic_only=True)
    unread = [(_unparsed_part(part), part.error) for part in unparsed]
    procedure, withheld = _without_speed_limited(procedure, sections)
    try:
        drawing, failed = _draw_in_part(
            block.lid, procedure, sections, nasr_data, options.params
        )
    except (ResolveError, Degenerate) as error:
        drawing, failed = None, [("", error)]
    failed = [*unread, *withheld, *failed]
    if drawing is None and not failed:
        return BlockOutcome(entry)
    if drawing is None:
        _, error = failed[0]
        return _not_drawn(entry, _finding(error, block.lid, raw, options), nasr_data)
    findings = tuple(
        _finding(error, block.lid, raw, options, part) for part, error in failed
    )
    if failed:
        drawing = _with_not_shown_label(drawing, [part for part, _ in failed])
    return BlockOutcome(entry, drawing=drawing, findings=findings)


def _not_drawn(
    entry: SectionsEntry, finding: Finding, nasr_data: nasr.NasrData
) -> BlockOutcome:
    """The outcome of a procedure none of which could be drawn: its finding,
    and an "ODP NOT SHOWN" label at the airport so its empty map is not read
    as an airport without an ODP. An airport missing from NASR has nowhere to
    put the label."""
    try:
        airport = find_airport(nasr_data, finding.airport)
    except ResolveError:
        return BlockOutcome(entry, findings=(finding,))
    at = destination(airport.position, 180.0, NOT_DRAWN_LABEL_OFFSET_NM)
    marker = AirportDrawing(
        airport.lid,
        airport.name,
        (Label(NOT_SHOWN_LABEL, at),),
        position=airport.position,
    )
    return BlockOutcome(entry, findings=(finding,), marker=marker)


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


type Failure = tuple[str, ParseError | ResolveError | Degenerate]


def _without_speed_limited(
    procedure: Procedure, sections: Sections
) -> tuple[Procedure, list[Failure]]:
    """`procedure` without the runway routes and VCOAs that a speed limit in
    the takeoff minimums may bind, each a failure: the pipeline does not
    read those limits, and a route is never drawn without one it has."""
    minimums = sections.takeoff_minimums or ""
    routes = [
        (group, speed_limit_for(minimums, group.runways) if _flies(group) else None)
        for group in procedure.runway_groups
    ]
    vcoas = [(vcoa, speed_limit_for(minimums, vcoa.runways)) for vcoa in procedure.vcoa]
    kept = dataclasses.replace(
        procedure,
        runway_groups=tuple(group for group, line in routes if line is None),
        vcoa=tuple(vcoa for vcoa, line in vcoas if line is None),
    )
    withheld = [
        *((_runways_part(group.runways), line) for group, line in routes if line),
        *((VCOA_PART, line) for _, line in vcoas if line),
    ]
    return kept, [(part, _unread_speed_limit(line)) for part, line in withheld]


def _unread_speed_limit(line: str) -> ParseError:
    return ParseError("unread speed limit in takeoff minimums", line)


def _flies(group: RunwayGroup) -> bool:
    return bool(group.legs) and not group.graphic


def _draw_in_part(
    lid: str,
    procedure: Procedure,
    sections: Sections,
    nasr_data: nasr.NasrData,
    params: DisplayParams,
) -> tuple[AirportDrawing | None, list[Failure]]:
    """The whole procedure drawn; failing that, each part drawn on its own
    and the drawable ones merged, with the parts that failed.

    Raises the whole procedure's error when it has at most one part or no
    part draws. ``None`` when there is no part to draw.
    """
    airport = find_airport(nasr_data, lid)
    min_climb = parse_takeoff_minimums(sections.takeoff_minimums or "")

    def drawn(part: Procedure) -> AirportDrawing:
        return draw(resolve(part, airport, nasr_data, min_climb=min_climb), params)

    parts = _parts(procedure)
    if not parts:
        return None, []
    try:
        return drawn(procedure), []
    except (ResolveError, Degenerate) as error:
        if len(parts) == 1:
            raise
        whole_error = error
    drawings, failed = [], []
    for name, part in parts:
        try:
            drawings.append(drawn(part))
        except (ResolveError, Degenerate) as error:
            failed.append((name, error))
    if not drawings:
        raise whole_error
    return _merged(drawings), failed


def _parts(procedure: Procedure) -> list[tuple[str, Procedure]]:
    """Each runway group that flies a route, with the shared tail when it
    continues into it, and each VCOA group, as a procedure of its own."""
    parts = []
    for group in procedure.runway_groups:
        if not group.legs or group.graphic:
            continue
        tail = procedure.shared_tail if isinstance(group.legs[-1], Thence) else None
        part = dataclasses.replace(
            procedure, runway_groups=(group,), shared_tail=tail, vcoa=()
        )
        parts.append((_runways_part(group.runways), part))
    for vcoa in procedure.vcoa:
        part = dataclasses.replace(
            procedure, runway_groups=(), shared_tail=None, vcoa=(vcoa,)
        )
        parts.append((VCOA_PART, part))
    return parts


def _merged(drawings: list[AirportDrawing]) -> AirportDrawing:
    """One drawing of every part's shapes, keeping one of any that coincide:
    parts sharing a tail each draw it."""
    seen: set = set()
    shapes: list[Polyline | Label] = []
    for drawing in drawings:
        for shape in drawing.shapes:
            key = (shape.style, shape.points) if isinstance(shape, Polyline) else shape
            if key not in seen:
                seen.add(key)
                shapes.append(shape)
    return dataclasses.replace(drawings[0], shapes=tuple(shapes))


def _with_not_shown_label(drawing: AirportDrawing, parts: list[str]) -> AirportDrawing:
    """Name the parts left undrawn just south of the airport, so a missing
    line is not read as a runway without an ODP."""
    text = f"{NOT_SHOWN_LABEL}: {', '.join(dict.fromkeys(parts))}"
    at = destination(drawing.position, 180.0, NOT_DRAWN_LABEL_OFFSET_NM)
    return dataclasses.replace(drawing, shapes=(*drawing.shapes, Label(text, at)))


def _runways_part(runways: tuple[str, ...]) -> str:
    """``RWY 11`` or ``RWY 16L/16R``; ``other runways`` when none was read."""
    return f"RWY {'/'.join(runways)}" if runways else UNNAMED_PART


def _unparsed_part(unparsed: Unparsed) -> str:
    return VCOA_PART if unparsed.vcoa else _runways_part(unparsed.runways)


def _finding(
    error: ParseError | ResolveError | Degenerate,
    lid: str,
    raw: Sections,
    options: BuildOptions,
    part: str | None = None,
) -> Finding:
    """The finding for `error`; `part` names the runways or VCOA it kept
    from being drawn when the rest of the procedure was."""
    kind, signature = _kind_and_signature(error)
    return Finding(
        kind=kind,
        signature=signature,
        airport=lid,
        cycle=options.cycle.iso,
        amendment=raw.amendment,
        verbatim_text="\n".join(filter(None, (raw.departure_procedure, raw.vcoa))),
        detail=f"{part}: {error.detail}" if part else error.detail,
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
