"""The command line: cycle printing, the test-grid pack and the ODP build."""

import json
import zipfile

import requests

from odp_kml.cli import main
from odp_kml.cycle import Cycle
from tests.test_pipeline import build_on_fixtures


def test_print_cycles_reports_whether_the_next_cycle_is_posted(capsys):
    probed = []

    def is_available(cycle):
        probed.append(cycle)
        return True

    status = main(
        ["--cycle", "2026-09-03", "--print-cycles"], is_available=is_available
    )

    assert status == 0
    assert probed == [Cycle.from_iso("2026-10-01")]
    assert capsys.readouterr().out == (
        "current=2026-09-03 next=2026-10-01 next_available=true\n"
    )


def test_test_grid_pack_has_a_manifest_and_one_layer(tmp_path):
    out = tmp_path / "grid.zip"

    assert main(["--test-grid", "--cycle", "2026-09-03", "--out", str(out)]) == 0

    names = zipfile.ZipFile(out).namelist()
    assert "ODP Test Grid/manifest.json" in names
    assert [name for name in names if "/layers/" in name] == [
        "ODP Test Grid/layers/ODP Test Grid.kml"
    ]


def test_kml_only_build_of_one_airport(tmp_path, capsys):
    kml = tmp_path / "odp.kml"
    report = tmp_path / "out" / "report.json"
    dump = tmp_path / "sections.json"

    status = main(
        [
            "--cycle",
            "2026-09-03",
            "--kml-only",
            "--airports",
            "TPH",
            "--out",
            str(kml),
            "--report",
            str(report),
            "--dump-sections",
            str(dump),
        ],
        build=build_on_fixtures,
    )

    assert status == 0
    assert "<name>TPH – TONOPAH</name>" in kml.read_text()
    assert json.loads(report.read_text())["drawn"] == 1
    assert report.with_suffix(".md").exists()
    assert [entry["lid"] for entry in json.loads(dump.read_text())] == ["TPH"]
    out = capsys.readouterr().out
    assert "Drew 1 of 1 ODPs (100%)" in out
    assert str(kml) in out


def test_pack_build_wraps_the_kml_and_reports_beside_it(tmp_path):
    out = tmp_path / "pack.zip"
    args = ["--cycle", "2026-09-03", "--airports", "TPH", "--out", str(out)]

    assert main(args, build=build_on_fixtures) == 0

    names = zipfile.ZipFile(out).namelist()
    assert "Textual ODPs/layers/Textual ODPs.kml" in names
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.md").exists()


def test_download_failure_exits_1(tmp_path):
    def unreachable(options):
        raise requests.ConnectionError("aeronav.faa.gov unreachable")

    args = ["--cycle", "2026-09-03", "--out", str(tmp_path / "pack.zip")]

    assert main(args, build=unreachable) == 1
