"""ForeFlight content pack assembly: manifest fields and zip layout."""

import datetime as dt
import json
import zipfile

from odp_kml.cycle import Cycle
from odp_kml.pack import build_foreflight_pack


def test_pack_contains_manifest_and_layer_under_the_pack_root(tmp_path):
    kml = tmp_path / "Layer.kml"
    kml.write_text("<kml/>")
    out = tmp_path / "Pack.zip"

    build_foreflight_pack(
        out,
        kml,
        cycle=Cycle(dt.date(2026, 9, 3)),
        pack_name="Textual ODPs",
        pack_abbrev="ODP",
        organization="Tim Morgan",
        built=dt.datetime(2026, 9, 29, 6, 42, tzinfo=dt.UTC),
    )

    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == [
            "Textual ODPs/layers/Layer.kml",
            "Textual ODPs/manifest.json",
        ]
        manifest = json.loads(z.read("Textual ODPs/manifest.json"))
    assert manifest == {
        "name": "Textual ODPs",
        "abbreviation": "ODP",
        "version": 1790664120,
        "effectiveDate": "20260903T00:00:00Z",
        "expirationDate": "20261001T00:00:00Z",
        "organizationName": "Tim Morgan",
    }
