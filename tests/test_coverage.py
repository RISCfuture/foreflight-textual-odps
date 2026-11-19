"""Flagging a coverage drop against the previous release."""

import json
from pathlib import Path

import pytest

from odp_kml.coverage import check, coverage, drop_body, main, previous_cycle
from odp_kml.findings import Report

REPO = "example/repo"


def report(cycle, drawn, airports_with_text=1000, graphic_only=0):
    return Report(
        cycle=cycle,
        airports_with_text=airports_with_text,
        drawn=drawn,
        findings=[],
        label_count=0,
        graphic_only=graphic_only,
    )


class FakeGh:
    """Fake `run`: serves releases and a previous report, records every call."""

    def __init__(self, previous, *, tags=None, open_issues=()):
        self.previous = previous
        self.tags = tags if tags is not None else [previous.cycle]
        self.open_issues = list(open_issues)
        self.calls: list[list[str]] = []
        self.bodies: list[str] = []

    def __call__(self, args):
        self.calls.append(args)
        if "--body-file" in args:
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text())
        if args[:2] == ["release", "list"]:
            return json.dumps([{"tagName": tag} for tag in self.tags])
        if args[:2] == ["release", "download"]:
            assert args[2] == self.previous.cycle
            output = args[args.index("--output") + 1]
            Path(output).write_text(self.previous.to_json())
            return ""
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": n} for n in self.open_issues])
        return ""

    def commands(self):
        return [call[:2] for call in self.calls]


class TestCoverage:
    def test_leaves_out_airports_with_only_charted_dps(self):
        assert coverage(report("2026-10-01", 1228, 1677, 205)) == pytest.approx(
            100 * 1228 / 1472
        )

    def test_nothing_to_draw_is_zero(self):
        assert coverage(report("2026-10-01", 0, 0)) == 0.0


class TestDropBody:
    def test_drop_beyond_threshold_names_both_cycles_and_numbers(self):
        body = drop_body(
            report("2026-10-01", 1228, 1677, 205),
            report("2026-10-29", 1158, 1675, 186),
        )
        assert body is not None
        assert "83.4% in cycle 2026-10-01 (1,228 of 1,472 drawn)" in body
        assert "77.8% in cycle 2026-10-29 (1,158 of 1,489 drawn)" in body
        assert "a drop of 5.7 points" in body

    @pytest.mark.parametrize("drawn", [800, 780, 900], ids=["same", "2.0", "rise"])
    def test_drop_within_threshold_or_rise_is_none(self, drawn):
        assert drop_body(report("2026-10-01", 800), report("2026-10-29", drawn)) is None

    def test_drop_just_over_threshold(self):
        assert drop_body(report("2026-10-01", 800), report("2026-10-29", 779))


class TestPreviousCycle:
    def test_newest_tag_before_the_cycle(self):
        tags = ["2026-11-26", "2026-09-03", "2026-10-29", "2026-10-01"]
        assert previous_cycle(tags, "2026-10-29") == "2026-10-01"

    def test_none_when_no_earlier_release(self):
        assert previous_cycle(["2026-10-29"], "2026-10-29") is None


class TestCheck:
    def test_drop_creates_labelled_issue(self):
        gh = FakeGh(report("2026-10-01", 850), tags=["2026-10-01", "2026-10-29"])
        result = check(report("2026-10-29", 780), repo=REPO, run=gh)
        assert result.endswith("filed issue")
        assert gh.commands()[-2:] == [["label", "create"], ["issue", "create"]]
        create = gh.calls[-1]
        assert create[create.index("--label") + 1] == "odp-coverage"
        assert create[create.index("--title") + 1] == (
            "[odp] Coverage fell from 2026-10-01 to 2026-10-29"
        )
        assert "85.0% in cycle 2026-10-01" in gh.bodies[0]
        assert "78.0% in cycle 2026-10-29" in gh.bodies[0]

    def test_drop_edits_the_open_issue(self):
        gh = FakeGh(report("2026-10-01", 850), open_issues=[42])
        check(report("2026-10-29", 780), repo=REPO, run=gh)
        assert gh.calls[-1][:3] == ["issue", "edit", "42"]
        assert ["issue", "create"] not in gh.commands()

    def test_no_drop_files_nothing(self):
        gh = FakeGh(report("2026-10-01", 850))
        result = check(report("2026-10-29", 845), repo=REPO, run=gh)
        assert result == "coverage 85.0% in 2026-10-01, 84.5% in 2026-10-29"
        assert ["issue", "list"] not in gh.commands()

    def test_no_previous_release_downloads_nothing(self):
        gh = FakeGh(report("2026-10-01", 850), tags=["2026-10-29"])
        result = check(report("2026-10-29", 100), repo=REPO, run=gh)
        assert result == "no release before 2026-10-29 to compare with"
        assert gh.commands() == [["release", "list"]]


def test_main_reads_the_report(tmp_path, capsys):
    path = tmp_path / "report.json"
    path.write_text(report("2026-10-29", 780).to_json())
    gh = FakeGh(report("2026-10-01", 850))
    assert main(["--report", str(path), "--repo", REPO], run=gh) == 0
    assert capsys.readouterr().out.strip().endswith("filed issue")
