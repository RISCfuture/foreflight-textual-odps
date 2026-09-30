"""Assemble a ForeFlight content pack (.zip) from a KML and metadata.

Layout inside the zip:

    <pack_root>/
        manifest.json
        layers/
            <kml_filename>

See https://foreflight.com/support/content-packs/ for the spec.

The pack is valid for one 28-day cycle. Its version is the build time, so
ForeFlight replaces an installed pack with any rebuild, even of the same
cycle.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import zipfile
from pathlib import Path

from .cycle import Cycle

LOG = logging.getLogger(__name__)


def build_foreflight_pack(
    out_zip: Path,
    kml_path: Path,
    *,
    cycle: Cycle,
    pack_name: str,
    pack_abbrev: str,
    organization: str,
    built: dt.datetime,
) -> None:
    effective = cycle.effective
    expires = cycle.next.effective

    manifest = {
        "name": pack_name,
        "abbreviation": pack_abbrev,
        "version": int(built.timestamp()),
        "effectiveDate": effective.strftime("%Y%m%dT00:00:00Z"),
        "expirationDate": expires.strftime("%Y%m%dT00:00:00Z"),
        "organizationName": organization,
    }
    pack_root = pack_name  # readable folder name inside the zip
    LOG.info("building ForeFlight pack %s (root=%s)", out_zip, pack_root)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            f"{pack_root}/manifest.json",
            json.dumps(manifest, indent=2) + "\n",
        )
        z.write(kml_path, f"{pack_root}/layers/{kml_path.name}")
    LOG.info(
        "pack written: %s (%.1f MB)", out_zip, out_zip.stat().st_size / 1024 / 1024
    )
