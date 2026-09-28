"""The offline build comparison tool: outcomes, digests, diffs and snapshots."""

from __future__ import annotations

from odp_kml.findings import Finding, Kind
from odp_kml.geo import LatLon
from odp_kml.pipeline import BlockOutcome
from odp_kml.shapes import AirportDrawing, Label, Polyline, Style
from tools import compare_builds
from tools.compare_builds import CHARTED, NOT_DRAWN, PART, WHOLE, Outcome


def drawing(*points: tuple[float, float]) -> AirportDrawing:
    line = Polyline("RWY 36", Style.ROUTE, tuple(LatLon(*p) for p in points))
    return AirportDrawing("TST", "TEST", (line, Label("7000'", LatLon(*points[-1]))))


FINDING = Finding(Kind.PARSE_FAILED, 'unmatched phrase "x"', "TST", "c", None, "", "")


def outcome(kind: str, digest: str | None = "d", findings=()) -> Outcome:
    return Outcome(state="CO", outcome=kind, digest=digest, findings=findings)


def test_record_classifies_each_kind_of_outcome():
    shapes = drawing((38.0, -105.0), (38.1, -105.0))

    assert compare_builds.record(BlockOutcome({}, drawing=shapes)).outcome == WHOLE
    part = BlockOutcome({}, drawing=shapes, findings=(FINDING,))
    assert compare_builds.record(part).outcome == PART
    assert compare_builds.record(BlockOutcome({}, graphic_only=True)).outcome == CHARTED
    assert compare_builds.record(BlockOutcome({}, findings=(FINDING,))).findings == (
        'parse_failed: unmatched phrase "x"',
    )
    assert compare_builds.record(BlockOutcome({})).outcome == NOT_DRAWN


def test_digest_ignores_float_noise_but_not_a_move():
    base = compare_builds.digest(drawing((38.0, -105.0), (38.1, -105.0)))

    assert compare_builds.digest(drawing((38.0 + 1e-9, -105.0), (38.1, -105.0))) == base
    assert compare_builds.digest(drawing((38.0, -105.0), (38.2, -105.0))) != base


def test_diff_names_gained_lost_redrawn_and_changed_findings():
    before = {
        "AAA": outcome(NOT_DRAWN, None, ("f",)),
        "BBB": outcome(WHOLE),
        "CCC": outcome(WHOLE, "old"),
        "DDD": outcome(PART, "d", ("f",)),
    }
    after = {
        "AAA": outcome(PART, "d", ("g",)),
        "BBB": outcome(NOT_DRAWN, None, ("f",)),
        "CCC": outcome(WHOLE, "new"),
        "DDD": outcome(PART, "d", ("f",)),
    }

    change = compare_builds.diff(before, after)

    assert (change.gained, change.lost, change.redrawn) == (["AAA"], ["BBB"], ["CCC"])
    assert change.findings_changed == ["AAA", "BBB"]


def test_summary_leaves_charted_airports_out_of_the_percentage():
    snap = {
        "A": outcome(WHOLE),
        "B": outcome(PART),
        "C": outcome(NOT_DRAWN),
        "D": outcome(NOT_DRAWN),
        "E": outcome(CHARTED),
    }

    assert compare_builds.summary(snap) == (
        "2 of 4 drawn (50%; 1 whole, 1 in part); 1 charted"
    )


def test_snapshot_round_trips_through_json(tmp_path):
    snap = {"TST": outcome(PART, "abc", ("f", "g"))}
    path = tmp_path / "snap.json"

    compare_builds.save(snap, path)

    assert compare_builds.load(path) == snap
    assert compare_builds.only_state(snap, "NV") == {}
