"""The d-TPP metafile and the per-volume Takeoff-Minimums PDFs it indexes.

Textual obstacle departure procedures are published only inside the 26
``<VOL>TO.PDF`` volumes of each d-TPP cycle. The cycle's metafile lists every
airport that has a TAKEOFF MINIMUMS entry and names the volume PDF holding it.
"""

from __future__ import annotations

import dataclasses
import io
import xml.etree.ElementTree as ET
from pathlib import Path

from .cycle import Cycle
from .download import download

BASE_URL = "https://aeronav.faa.gov/d-tpp"
TO_PDF_SUFFIX = "TO.PDF"


@dataclasses.dataclass(frozen=True)
class AirportMeta:
    """One airport's TAKEOFF MINIMUMS listing in the metafile."""

    lid: str
    icao: str | None
    name: str
    city: str
    state: str
    volume: str
    pdf_name: str


@dataclasses.dataclass(frozen=True)
class Metafile:
    """The cycle header plus every airport with TAKEOFF MINIMUMS, keyed by LID."""

    cycle_code: str
    from_edate: str
    to_edate: str
    airports: dict[str, AirportMeta]


def metafile_url(cycle: Cycle) -> str:
    """URL of the cycle's d-TPP XML metafile."""
    return f"{BASE_URL}/{cycle.dtpp_code}/xml_data/d-tpp_Metafile.xml"


def to_pdf_url(cycle: Cycle, volume: str) -> str:
    """URL of one Takeoff-Minimums volume PDF, e.g. volume ``"SW4"``."""
    return f"{BASE_URL}/{cycle.dtpp_code}/{volume}{TO_PDF_SUFFIX}"


def parse_metafile(source: bytes | Path) -> Metafile:
    """Parse the metafile, keeping only airports with a TAKEOFF MINIMUMS record."""
    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    root_attrs: dict[str, str] = {}
    state = city = ""
    airports: dict[str, AirportMeta] = {}
    for event, elem in ET.iterparse(stream, events=("start", "end")):
        match event, elem.tag:
            case "start", "digital_tpp":
                root_attrs = dict(elem.attrib)
            case "start", "state_code":
                state = elem.get("ID", "")
            case "start", "city_name":
                city = elem.get("ID", "")
            case "end", "airport_name":
                if (pdf_name := _takeoff_minimums_pdf(elem)) is not None:
                    meta = _airport_meta(elem, city, state, pdf_name)
                    airports[meta.lid] = meta
                elem.clear()
    return Metafile(
        cycle_code=root_attrs.get("cycle", ""),
        from_edate=root_attrs.get("from_edate", ""),
        to_edate=root_attrs.get("to_edate", ""),
        airports=airports,
    )


def _takeoff_minimums_pdf(airport: ET.Element) -> str | None:
    """The ``pdf_name`` of the airport's TAKEOFF MINIMUMS record, if it has one."""
    for record in airport.iter("record"):
        if (
            record.findtext("chart_code") == "MIN"
            and record.findtext("chart_name") == "TAKEOFF MINIMUMS"
        ):
            return record.findtext("pdf_name", "")
    return None


def _airport_meta(
    airport: ET.Element, city: str, state: str, pdf_name: str
) -> AirportMeta:
    return AirportMeta(
        lid=airport.get("apt_ident", ""),
        icao=airport.get("icao_ident") or None,
        name=airport.get("ID", ""),
        city=city,
        state=state,
        volume=_volume_of(pdf_name),
        pdf_name=pdf_name,
    )


def _volume_of(pdf_name: str) -> str:
    """``SW4TO.PDF`` → ``SW4``; ``PACTO.PDF`` → ``PAC``."""
    return pdf_name.removesuffix(TO_PDF_SUFFIX)


def fetch_metafile(cycle: Cycle, cache_dir: Path) -> Metafile:
    """Download (or reuse the cached) metafile for ``cycle`` and parse it."""
    return parse_metafile(download(metafile_url(cycle), cache_dir))


def fetch_to_pdfs(cycle: Cycle, metafile: Metafile, cache_dir: Path) -> dict[str, Path]:
    """Download every Takeoff-Minimums volume the metafile references, by volume."""
    volumes = sorted({airport.volume for airport in metafile.airports.values()})
    return {
        volume: download(to_pdf_url(cycle, volume), cache_dir) for volume in volumes
    }
