"""NASR CSV loaders: airports, runways, navaids, fixes, and holding patterns.

A later stage resolves parsed procedure text ("direct TONOPAH (TPH)
VORTAC", "Rwy 15") against the records loaded here, so every blank NASR
field becomes ``None`` rather than a false zero.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from .download import download
from .geo import LatLon

if TYPE_CHECKING:
    from .cycle import Cycle

NASR_ZIP_URL = "https://nfdc.faa.gov/webContent/28DaySub/extra/{tag}_{group}_CSV.zip"

_AIRPORT_SITE_TYPE = "A"
_OPERATIONAL_STATUS = "O"


@dataclasses.dataclass(frozen=True)
class RunwayEnd:
    id: str
    position: LatLon | None
    elevation_ft: float | None
    true_alignment: int | None
    displaced_threshold: LatLon | None
    tora_ft: int | None


@dataclasses.dataclass(frozen=True)
class Runway:
    id: str
    length_ft: int | None
    ends: tuple[RunwayEnd, RunwayEnd]


@dataclasses.dataclass(frozen=True)
class Airport:
    lid: str
    icao: str | None
    name: str
    city: str
    state: str
    position: LatLon
    elevation_ft: float
    mag_var_east: float | None
    runways: tuple[Runway, ...]

    def runway_end(self, end_id: str) -> RunwayEnd | None:
        """The runway end named ``end_id`` (e.g. "15"), or None if absent."""
        for runway in self.runways:
            for end in runway.ends:
                if end.id == end_id:
                    return end
        return None

    def reciprocal_end(self, end_id: str) -> RunwayEnd | None:
        """The other end of the runway that ``end_id`` belongs to."""
        for runway in self.runways:
            ids = [end.id for end in runway.ends]
            if end_id in ids:
                return runway.ends[1 - ids.index(end_id)]
        return None


@dataclasses.dataclass(frozen=True)
class Navaid:
    ident: str
    type: str
    name: str
    position: LatLon
    elevation_ft: float | None
    mag_var_east: float | None


@dataclasses.dataclass(frozen=True)
class Fix:
    ident: str
    state: str
    position: LatLon


@dataclasses.dataclass(frozen=True)
class Hold:
    name: str
    fix_ident: str
    navaid_ident: str | None
    inbound_course: int
    turn: str
    leg_length_nm: float | None
    direction: str | None


@dataclasses.dataclass(frozen=True)
class NasrData:
    airports: dict[str, Airport]
    navaids: dict[str, tuple[Navaid, ...]]
    fixes: dict[str, tuple[Fix, ...]]
    holds: dict[str, tuple[Hold, ...]]


def group_url(cycle: Cycle, group: str) -> str:
    """The FAA NASR subject zip URL for ``group`` (APT/NAV/FIX/HPF) in ``cycle``."""
    return NASR_ZIP_URL.format(tag=cycle.nasr_tag, group=group)


def fetch(cycle: Cycle, cache_dir: Path) -> NasrData:
    """Download the four NASR subject zips for ``cycle`` and load them."""
    zips = {
        group: download(group_url(cycle, group), cache_dir)
        for group in ("APT", "NAV", "FIX", "HPF")
    }
    return load(zips["APT"], zips["NAV"], zips["FIX"], zips["HPF"])


def load(apt_zip: Path, nav_zip: Path, fix_zip: Path, hpf_zip: Path) -> NasrData:
    """Parse the four NASR subject zips into a NasrData snapshot."""
    return NasrData(
        airports=_load_airports(apt_zip),
        navaids=_load_navaids(nav_zip),
        fixes=_load_fixes(fix_zip),
        holds=_load_holds(hpf_zip),
    )


# --- Field parsing --------------------------------------------------------
#
# NASR renders every unpublished value as an empty string, never a zero, so
# each parser below preserves that distinction as None.


def _blank(raw: str) -> bool:
    return not raw.strip()


def parse_optional_float(raw: str) -> float | None:
    """``raw`` as a float, or None if blank."""
    return None if _blank(raw) else float(raw)


def parse_optional_int(raw: str) -> int | None:
    """``raw`` as an int, or None if blank."""
    return None if _blank(raw) else int(float(raw))


def parse_optional_latlon(lat_raw: str, lon_raw: str) -> LatLon | None:
    """A LatLon from decimal-degree fields, or None if either is blank."""
    if _blank(lat_raw) or _blank(lon_raw):
        return None
    return LatLon(lat=float(lat_raw), lon=float(lon_raw))


def parse_mag_var(raw: str, hemisphere: str) -> float | None:
    """Signed magnetic variation in degrees, east positive; blank -> None."""
    magnitude = parse_optional_float(raw)
    if magnitude is None:
        return None
    return magnitude if hemisphere.strip().upper() == "E" else -magnitude


# --- CSV access ------------------------------------------------------------


def _read_csv(zip_path: Path, member: str) -> list[dict[str, str]]:
    """Rows of a CSV member inside a NASR zip, as string dicts."""
    with zipfile.ZipFile(zip_path) as zf, zf.open(member) as raw:
        return list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")))


def _group_by_ids(
    rows: list[dict[str, str]], id_fields: tuple[str, str]
) -> dict[tuple[str, str], list[dict[str, str]]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row[id_fields[0]], row[id_fields[1]])
        groups.setdefault(key, []).append(row)
    return groups


# --- Airports, runways, and runway ends ------------------------------------


def _load_airports(apt_zip: Path) -> dict[str, Airport]:
    base_rows = _read_csv(apt_zip, "APT_BASE.csv")
    runway_rows = _read_csv(apt_zip, "APT_RWY.csv")
    ends_by_runway = _group_by_ids(
        _read_csv(apt_zip, "APT_RWY_END.csv"), ("ARPT_ID", "RWY_ID")
    )

    airports = {}
    for row in base_rows:
        if not _is_operational_airport(row):
            continue
        lid = row["ARPT_ID"]
        runways = tuple(
            runway
            for runway in (
                _load_runway(
                    runway_row, ends_by_runway.get((lid, runway_row["RWY_ID"]), [])
                )
                for runway_row in runway_rows
                if runway_row["ARPT_ID"] == lid
            )
            if runway is not None
        )
        airports[lid] = _load_airport(row, runways)
    return airports


def _is_operational_airport(row: dict[str, str]) -> bool:
    return (
        row["SITE_TYPE_CODE"] == _AIRPORT_SITE_TYPE
        and row["ARPT_STATUS"] == _OPERATIONAL_STATUS
    )


def _load_airport(row: dict[str, str], runways: tuple[Runway, ...]) -> Airport:
    return Airport(
        lid=row["ARPT_ID"],
        icao=row["ICAO_ID"] or None,
        name=row["ARPT_NAME"],
        city=row["CITY"],
        state=row["STATE_CODE"],
        position=LatLon(lat=float(row["LAT_DECIMAL"]), lon=float(row["LONG_DECIMAL"])),
        elevation_ft=float(row["ELEV"]),
        mag_var_east=parse_mag_var(row["MAG_VARN"], row["MAG_HEMIS"]),
        runways=runways,
    )


def _load_runway(
    runway_row: dict[str, str], end_rows: list[dict[str, str]]
) -> Runway | None:
    """A Runway from its two ends, or None for a pseudo-runway (e.g. a helipad
    pad listed in APT_RWY.csv) that lacks a second end in APT_RWY_END.csv."""
    end_ids = runway_row["RWY_ID"].split("/")
    if len(end_rows) != 2 or {r["RWY_END_ID"] for r in end_rows} != set(end_ids):
        return None
    ordered = sorted(end_rows, key=lambda r: end_ids.index(r["RWY_END_ID"]))
    return Runway(
        id=runway_row["RWY_ID"],
        length_ft=parse_optional_int(runway_row["RWY_LEN"]),
        ends=(_load_runway_end(ordered[0]), _load_runway_end(ordered[1])),
    )


def _load_runway_end(row: dict[str, str]) -> RunwayEnd:
    return RunwayEnd(
        id=row["RWY_END_ID"],
        position=parse_optional_latlon(row["LAT_DECIMAL"], row["LONG_DECIMAL"]),
        elevation_ft=parse_optional_float(row["RWY_END_ELEV"]),
        true_alignment=parse_optional_int(row["TRUE_ALIGNMENT"]),
        displaced_threshold=parse_optional_latlon(
            row["LAT_DISPLACED_THR_DECIMAL"], row["LONG_DISPLACED_THR_DECIMAL"]
        ),
        tora_ft=parse_optional_int(row["TKOF_RUN_AVBL"]),
    )


# --- Navaids, fixes, and holds ----------------------------------------------


def _load_navaids(nav_zip: Path) -> dict[str, tuple[Navaid, ...]]:
    navaids: dict[str, list[Navaid]] = {}
    for row in _read_csv(nav_zip, "NAV_BASE.csv"):
        navaid = Navaid(
            ident=row["NAV_ID"],
            type=row["NAV_TYPE"],
            name=row["NAME"],
            position=LatLon(
                lat=float(row["LAT_DECIMAL"]), lon=float(row["LONG_DECIMAL"])
            ),
            elevation_ft=parse_optional_float(row["ELEV"]),
            mag_var_east=parse_mag_var(row["MAG_VARN"], row["MAG_VARN_HEMIS"]),
        )
        navaids.setdefault(navaid.ident, []).append(navaid)
    return {ident: tuple(group) for ident, group in navaids.items()}


def _load_fixes(fix_zip: Path) -> dict[str, tuple[Fix, ...]]:
    fixes: dict[str, list[Fix]] = {}
    for row in _read_csv(fix_zip, "FIX_BASE.csv"):
        ident = row["FIX_ID"].strip()
        fix = Fix(
            ident=ident,
            state=row["STATE_CODE"],
            position=LatLon(
                lat=float(row["LAT_DECIMAL"]), lon=float(row["LONG_DECIMAL"])
            ),
        )
        fixes.setdefault(ident, []).append(fix)
    return {ident: tuple(group) for ident, group in fixes.items()}


def _load_holds(hpf_zip: Path) -> dict[str, tuple[Hold, ...]]:
    holds: dict[str, list[Hold]] = {}
    for row in _read_csv(hpf_zip, "HPF_BASE.csv"):
        hold, key = _load_hold(row)
        holds.setdefault(key, []).append(hold)
    return {ident: tuple(group) for ident, group in holds.items()}


def _load_hold(row: dict[str, str]) -> tuple[Hold, str]:
    """A Hold and the identifier it is filed under.

    A hold at a named fix (e.g. "TOBEY INT") is keyed by that fix; a hold
    flown directly at a navaid, with no separate fix, falls back to the
    navaid's identifier for both the key and ``fix_ident``.
    """
    fix_id = row["FIX_ID"].strip()
    navaid_id = row["NAV_ID"].strip() or None
    key = fix_id or navaid_id
    hold = Hold(
        name=row["HP_NAME"],
        fix_ident=key,
        navaid_ident=navaid_id,
        inbound_course=int(row["COURSE_INBOUND_DEG"]),
        turn=row["TURN_DIRECTION"],
        leg_length_nm=parse_optional_float(row["LEG_LENGTH_DIST"]),
        direction=row["HOLD_DIRECTION"].strip() or None,
    )
    return hold, key
