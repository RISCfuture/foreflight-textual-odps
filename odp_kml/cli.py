"""Command-line entry point: pick the cycle, build, and write the pack or KML."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

from . import availability, config, pipeline, testgrid
from .cycle import Cycle
from .findings import write_report
from .geometry import DisplayParams
from .kml import write_kml
from .pack import build_foreflight_pack
from .shapes import AirportDrawing

LOG = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class _Layer:
    """User-visible names of one content pack and its single KML layer."""

    name: str
    abbrev: str
    kml_filename: str


ODP_LAYER = _Layer(config.PACK_NAME, config.PACK_ABBREV, config.KML_FILENAME)
TEST_GRID_LAYER = _Layer(
    config.TEST_GRID_PACK_NAME,
    config.TEST_GRID_PACK_ABBREV,
    config.TEST_GRID_KML_FILENAME,
)


def main(
    argv: list[str],
    *,
    build: Callable[[pipeline.BuildOptions], pipeline.BuildResult] = pipeline.build,
    is_available: Callable[[Cycle], bool] = availability.is_available,
) -> int:
    """Run the CLI; returns the process exit status.

    ``build`` and ``is_available`` are the network-facing steps, replaceable
    so the CLI can run on fixture data.
    """
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cycle = args.cycle or Cycle.containing(dt.datetime.now(dt.UTC).date())

    if args.print_cycles:
        print(_cycles_line(cycle, is_available(cycle.next)))
        return 0
    if args.test_grid:
        out = _output_path(args, TEST_GRID_LAYER)
        _write_layer(testgrid.draw_grid(), out, TEST_GRID_LAYER, cycle, args.kml_only)
        print(f"wrote {out}")
        return 0

    try:
        _build_odp_layer(args, cycle, build)
    except (OSError, subprocess.SubprocessError) as error:
        LOG.error("build failed: %s", error)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        help=f'Output ForeFlight content pack (default "{config.PACK_NAME}.zip"). '
        "If --kml-only is set, this is the KML path instead.",
    )
    ap.add_argument(
        "--kml-only",
        action="store_true",
        help="Write just the KML, not a ForeFlight content pack.",
    )
    ap.add_argument("--cache", type=Path, default=Path("data_cache"))
    ap.add_argument(
        "--cycle",
        type=Cycle.from_iso,
        help="Cycle effective date YYYY-MM-DD (default: the cycle in effect today, UTC).",
    )
    ap.add_argument(
        "--print-cycles",
        action="store_true",
        help="Print 'current=… next=… next_available=true|false' and exit. "
        "Used by CI to decide which cycles to build.",
    )
    ap.add_argument(
        "--airports",
        type=_airport_set,
        help="Comma-separated airport ids to build (default: every airport).",
    )
    ap.add_argument(
        "--tas",
        type=float,
        default=DisplayParams.tas_kt,
        help="True airspeed in knots for turn radii (default %(default)s).",
    )
    ap.add_argument(
        "--gradient",
        type=float,
        default=DisplayParams.default_gradient_ft_nm,
        help="Climb gradient in ft/NM where none is published (default %(default)s).",
    )
    ap.add_argument(
        "--label-style",
        choices=("chart", "fms"),
        default=DisplayParams.label_style,
    )
    ap.add_argument(
        "--report",
        type=Path,
        help="Findings report JSON path (default: report.json beside --out); "
        "the Markdown report goes next to it as .md.",
    )
    ap.add_argument(
        "--dump-sections",
        type=Path,
        help="Also write each airport's normalized sections as JSON "
        "(the input of tools/draft_golden.py).",
    )
    ap.add_argument(
        "--test-grid",
        action="store_true",
        help="Build the synthetic construction test-grid pack instead.",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def _airport_set(text: str) -> frozenset[str]:
    return frozenset(
        ident.strip().upper() for ident in text.split(",") if ident.strip()
    )


def _cycles_line(cycle: Cycle, next_available: bool) -> str:
    return (
        f"current={cycle.iso} next={cycle.next.iso} "
        f"next_available={str(next_available).lower()}"
    )


def _output_path(args: argparse.Namespace, layer: _Layer) -> Path:
    if args.out is not None:
        return args.out
    return Path(layer.kml_filename if args.kml_only else f"{layer.name}.zip")


def _build_odp_layer(
    args: argparse.Namespace,
    cycle: Cycle,
    build: Callable[[pipeline.BuildOptions], pipeline.BuildResult],
) -> None:
    result = build(_build_options(args, cycle))
    out = _output_path(args, ODP_LAYER)
    _write_layer(result.drawings, out, ODP_LAYER, cycle, args.kml_only)
    _write_report(result, args.report or out.with_name("report.json"))
    if args.dump_sections is not None:
        _write_json(result.sections_dump, args.dump_sections)
    print(result.report.summary_line())
    print(f"wrote {out}")


def _build_options(args: argparse.Namespace, cycle: Cycle) -> pipeline.BuildOptions:
    return pipeline.BuildOptions(
        cycle=cycle,
        cache_dir=args.cache,
        airports=args.airports,
        params=DisplayParams(
            tas_kt=args.tas,
            default_gradient_ft_nm=args.gradient,
            label_style=args.label_style,
        ),
    )


def _write_layer(
    drawings: Iterable[AirportDrawing],
    out: Path,
    layer: _Layer,
    cycle: Cycle,
    kml_only: bool,
) -> None:
    """Write ``drawings`` as a KML at ``out``, or as a content pack wrapping it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if kml_only:
        write_kml(drawings, out, document_name=layer.name)
        return
    with tempfile.TemporaryDirectory() as tmp:
        kml_path = Path(tmp) / layer.kml_filename
        write_kml(drawings, kml_path, document_name=layer.name)
        build_foreflight_pack(
            out,
            kml_path,
            cycle=cycle,
            pack_name=layer.name,
            pack_abbrev=layer.abbrev,
            organization=config.ORGANIZATION,
        )


def _write_report(result: pipeline.BuildResult, json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(result.report, json_path, json_path.with_suffix(".md"))


def _write_json(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
