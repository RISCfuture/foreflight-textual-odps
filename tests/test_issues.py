"""Syncing build findings to GitHub issues via `gh`."""

import json

from odp_kml.findings import Kind, Report
from odp_kml.issues import SyncSummary, render_body, render_title, sync_issues
from tests.test_findings import make_finding

REPO = "example/repo"


class RecordingRun:
    """Fake `run`: returns canned `gh` output and records every invocation."""

    def __init__(self, *, issue_list="[]", create_number=900):
        self.issue_list = issue_list
        self.create_number = create_number
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            return self.issue_list
        if args[:2] == ["issue", "create"]:
            return f"https://github.com/{REPO}/issues/{self.create_number}\n"
        return ""


def gh_issue(number, body, title="[odp] unresolved_ref: whatever"):
    return {"number": number, "title": title, "body": body}


class TestSyncIssuesDecisions:
    def test_creates_updates_leaves_unchanged_and_closes(self):
        new_finding = make_finding(
            kind=Kind.UNRESOLVED_REF,
            signature='unresolved fix "NEWFX" on radial R-090',
            airport="ABQ",
        )
        identical_finding = make_finding(
            kind=Kind.PARSE_FAILED,
            signature="garbled text near line 12",
            airport="TPH",
        )
        changed_finding = make_finding(
            kind=Kind.AMBIGUOUS_REF,
            signature='ambiguous fix "DUPFX"',
            airport="BOS",
        )
        report = Report(
            cycle="2026-09-03",
            airports_with_text=10,
            drawn=7,
            findings=[new_finding, identical_finding, changed_finding],
            label_count=1,
        )
        groups = report.grouped()
        identical_key = next(
            k for k, fs in groups.items() if fs[0].kind == Kind.PARSE_FAILED
        )
        changed_key = next(
            k for k, fs in groups.items() if fs[0].kind == Kind.AMBIGUOUS_REF
        )
        stale_key = "stale-signature-not-in-report"

        identical_body = render_body(report.cycle, identical_key, groups[identical_key])
        changed_body = render_body(
            "2026-08-06", changed_key, groups[changed_key]
        ).replace("BOS", "BOS-OLD-STALE-CONTENT")

        existing = [
            gh_issue(10, identical_body),
            gh_issue(11, changed_body),
            gh_issue(
                12, f"<!-- odp-signature: {stale_key} -->\nold stale finding body"
            ),
        ]
        run = RecordingRun(issue_list=json.dumps(existing), create_number=42)

        summary = sync_issues(report, repo=REPO, run=run)

        assert summary == SyncSummary(
            created=[42], updated=[11], unchanged=[10], closed=[12]
        )

    def test_labels_are_created_before_the_first_issue_create(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=1,
            drawn=0,
            findings=[make_finding(kind=Kind.RUNWAY_MISSING)],
            label_count=0,
        )
        run = RecordingRun(issue_list="[]", create_number=7)

        sync_issues(report, repo=REPO, run=run)

        create_index = next(
            i for i, call in enumerate(run.calls) if call[:2] == ["issue", "create"]
        )
        label_indices = [
            i for i, call in enumerate(run.calls) if call[:2] == ["label", "create"]
        ]
        assert label_indices, "expected at least one label create call"
        assert all(i < create_index for i in label_indices)
        label_names = {call[2] for call in run.calls if call[:2] == ["label", "create"]}
        assert label_names == {"odp-finding", "odp-runway_missing"}

    def test_update_posts_still_present_comment(self):
        finding = make_finding(
            kind=Kind.HOLD_AMBIGUOUS, signature="ambiguous hold at FIXNAME"
        )
        report = Report(
            cycle="2026-09-03",
            airports_with_text=1,
            drawn=0,
            findings=[finding],
            label_count=0,
        )
        key = next(iter(report.grouped()))
        stale_body = f"<!-- odp-signature: {key} -->\nsomething different"
        run = RecordingRun(issue_list=json.dumps([gh_issue(5, stale_body)]))

        sync_issues(report, repo=REPO, run=run)

        comment_calls = [call for call in run.calls if call[:2] == ["issue", "comment"]]
        assert len(comment_calls) == 1
        assert comment_calls[0][:3] == ["issue", "comment", "5"]
        assert "Still present in cycle 2026-09-03" in comment_calls[0][-1]

    def test_close_posts_no_longer_occurs_comment_then_closes(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=0,
            drawn=0,
            findings=[],
            label_count=0,
        )
        stale_body = "<!-- odp-signature: gone -->\nold body"
        run = RecordingRun(issue_list=json.dumps([gh_issue(9, stale_body)]))

        sync_issues(report, repo=REPO, run=run)

        kinds = [call[:2] for call in run.calls]
        assert kinds == [["issue", "list"], ["issue", "comment"], ["issue", "close"]]
        assert "No longer occurs as of cycle 2026-09-03" in run.calls[1][-1]
        assert run.calls[2] == ["issue", "close", "9", "--repo", REPO]


class TestRenderTitle:
    def test_format_uses_kind_and_normalized_signature(self):
        title = render_title(
            Kind.UNRESOLVED_REF, 'unresolved fix "ABCDE" on radial R-210'
        )
        assert title == '[odp] unresolved_ref: unresolved fix "id" on radial r-nnn'


class TestRenderBody:
    def test_contains_marker_cycle_count_and_table(self):
        finding = make_finding(airport="TPH", amendment="5", detail="no such fix")
        body = render_body("2026-09-03", "abc123", [finding])

        assert "<!-- odp-signature: abc123 -->" in body
        assert "2026-09-03" in body
        assert "| TPH | 5 | no such fix |" in body
        assert "```" in body
        assert finding.verbatim_text in body

    def test_caps_table_and_fenced_blocks_at_fifty_with_overflow_note(self):
        findings = [
            make_finding(airport=f"A{i:03d}", verbatim_text=f"text {i}")
            for i in range(52)
        ]
        body = render_body("2026-09-03", "key", findings)

        table_rows = [line for line in body.splitlines() if line.startswith("| A0")]
        assert len(table_rows) == 50
        assert "… and 2 more, see report.json in the release" in body
        assert body.count("```") == 100
        assert "A051" not in body
