"""Sync a build `Report`'s findings to GitHub issues via the `gh` CLI.

One issue per signature, never per airport: `Report.grouped()` already
collapses findings that share a `signature_key` across airports and cycles,
so this module only has to reconcile that grouping against the currently
open `odp-finding` issues. Every managed issue's body carries a hidden
marker comment holding its signature key, which is how an issue is matched
back to a group on the next sync regardless of title or body edits.

All `gh` calls go through the injectable `run` callable so tests never shell
out to the real `gh` binary.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from .findings import Finding, Kind, Report, normalize_signature

MAX_TABLE_ROWS = 50

_MARKER = re.compile(r"<!-- odp-signature: (\S+) -->")
_ISSUE_NUMBER = re.compile(r"/issues/(\d+)")


def _gh(args: list[str]) -> str:
    """Run `gh` with `args`, returning stdout. The default `run` for `sync_issues`."""
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


@dataclasses.dataclass
class SyncSummary:
    """Issue numbers touched by one `sync_issues` call, grouped by outcome."""

    created: list[int | None]
    updated: list[int]
    unchanged: list[int]
    closed: list[int]


def render_title(kind: Kind, signature: str) -> str:
    """The issue title for a signature group: kind plus its normalized signature."""
    return f"[odp] {kind}: {normalize_signature(signature)}"


def render_body(cycle: str, key: str, findings: list[Finding]) -> str:
    """The issue body for a signature group: marker, summary, table, verbatim blocks.

    The table (and the fenced verbatim block following it) is capped at
    `MAX_TABLE_ROWS` airports, with a note about how many more were omitted.
    """
    listed = findings[:MAX_TABLE_ROWS]
    omitted = len(findings) - len(listed)
    lines = [
        f"<!-- odp-signature: {key} -->",
        "",
        f"Cycle {cycle}, {len(findings)} airports.",
        "",
        "| Airport | Amendment | Detail |",
        "| --- | --- | --- |",
        *(f"| {f.airport} | {f.amendment} | {f.detail} |" for f in listed),
    ]
    if omitted > 0:
        lines.append(f"… and {omitted} more, see report.json in the release")
    lines.append("")
    for finding in listed:
        lines += [f"### {finding.airport}", "```", finding.verbatim_text, "```", ""]
    return "\n".join(lines).rstrip() + "\n"


@contextmanager
def _body_file(body: str):
    """Write `body` to a temp file and yield its path, since `gh` bodies contain
    newlines and quotes that don't survive as a shell argument."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
        handle.write(body)
        path = handle.name
    try:
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


def _list_existing(run: Callable[[list[str]], str], repo: str) -> dict[str, dict]:
    """Open `odp-finding` issues, keyed by the signature marker in their body."""
    raw = run(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--label",
            "odp-finding",
            "--state",
            "open",
            "--limit",
            "500",
            "--json",
            "number,title,body",
        ]
    )
    existing = {}
    for issue in json.loads(raw):
        match = _MARKER.search(issue.get("body") or "")
        if match:
            existing[match.group(1)] = issue
    return existing


def _ensure_labels(
    run: Callable[[list[str]], str], repo: str, kinds: set[Kind]
) -> None:
    """Create `odp-finding` and one `odp-<kind>` label per kind, idempotently."""
    for name in ("odp-finding", *(f"odp-{kind}" for kind in sorted(kinds))):
        run(["label", "create", name, "--repo", repo, "--force"])


def _parse_issue_number(output: str) -> int | None:
    """Extract the issue number from `gh issue create`'s URL output."""
    match = _ISSUE_NUMBER.search(output)
    return int(match.group(1)) if match else None


def _create_issue(
    run: Callable[[list[str]], str], repo: str, kind: Kind, title: str, body: str
) -> int | None:
    with _body_file(body) as path:
        output = run(
            [
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                title,
                "--label",
                "odp-finding",
                "--label",
                f"odp-{kind}",
                "--body-file",
                path,
            ]
        )
    return _parse_issue_number(output)


def _update_issue(
    run: Callable[[list[str]], str],
    repo: str,
    number: int,
    body: str,
    cycle: str,
    count: int,
) -> None:
    with _body_file(body) as path:
        run(["issue", "edit", str(number), "--repo", repo, "--body-file", path])
    run(
        [
            "issue",
            "comment",
            str(number),
            "--repo",
            repo,
            "--body",
            f"Still present in cycle {cycle} ({count} airports).",
        ]
    )


def _close_issue(
    run: Callable[[list[str]], str], repo: str, number: int, cycle: str
) -> None:
    run(
        [
            "issue",
            "comment",
            str(number),
            "--repo",
            repo,
            "--body",
            f"No longer occurs as of cycle {cycle}.",
        ]
    )
    run(["issue", "close", str(number), "--repo", repo])


def _diff(
    report: Report, existing: dict[str, dict]
) -> tuple[
    dict[str, list[Finding]],
    dict[str, tuple[dict, list[Finding]]],
    dict[str, dict],
    dict[str, dict],
]:
    """Classify `report`'s signature groups against `existing` open issues.

    Returns `(to_create, to_update, unchanged, to_close)`: groups with no
    matching issue, groups whose matching issue's body has drifted (paired
    with that issue), groups whose matching issue is already up to date, and
    open issues whose signature no longer occurs in `report`.
    """
    groups = report.grouped()
    to_create: dict[str, list[Finding]] = {}
    to_update: dict[str, tuple[dict, list[Finding]]] = {}
    unchanged: dict[str, dict] = {}
    for key, findings in groups.items():
        issue = existing.get(key)
        if issue is None:
            to_create[key] = findings
        elif (issue.get("body") or "").strip() != render_body(
            report.cycle, key, findings
        ).strip():
            to_update[key] = (issue, findings)
        else:
            unchanged[key] = issue
    to_close = {key: issue for key, issue in existing.items() if key not in groups}
    return to_create, to_update, unchanged, to_close


def sync_issues(
    report: Report, *, repo: str, run: Callable[[list[str]], str] = _gh
) -> SyncSummary:
    """Reconcile `report`'s findings against open `odp-finding` issues in `repo`.

    One issue per signature group. A group with no matching open issue is
    created; a matching issue whose body has drifted is edited and commented
    on; a matching issue with an identical body is left alone; an open issue
    whose signature no longer occurs in the report is commented on and
    closed.
    """
    existing = _list_existing(run, repo)
    to_create, to_update, unchanged, to_close = _diff(report, existing)

    if to_create:
        _ensure_labels(run, repo, {findings[0].kind for findings in to_create.values()})

    summary = SyncSummary(created=[], updated=[], unchanged=[], closed=[])
    for key, findings in to_create.items():
        kind = findings[0].kind
        title = render_title(kind, findings[0].signature)
        body = render_body(report.cycle, key, findings)
        summary.created.append(_create_issue(run, repo, kind, title, body))

    for key, (issue, findings) in to_update.items():
        body = render_body(report.cycle, key, findings)
        _update_issue(run, repo, issue["number"], body, report.cycle, len(findings))
        summary.updated.append(issue["number"])

    summary.unchanged = [issue["number"] for issue in unchanged.values()]

    for issue in to_close.values():
        _close_issue(run, repo, issue["number"], report.cycle)
        summary.closed.append(issue["number"])

    return summary


def describe_plan(
    report: Report, *, repo: str, run: Callable[[list[str]], str]
) -> list[str]:
    """Preview what `sync_issues` would do, without creating, editing,
    commenting on, or closing any issue (it still lists open issues to
    compare against, via `run`)."""
    existing = _list_existing(run, repo)
    to_create, to_update, unchanged, to_close = _diff(report, existing)

    lines = []
    for findings in to_create.values():
        title = render_title(findings[0].kind, findings[0].signature)
        lines.append(f"CREATE: {title} ({len(findings)} airports)")
    for issue, findings in to_update.values():
        title = render_title(findings[0].kind, findings[0].signature)
        lines.append(f"UPDATE #{issue['number']}: {title} ({len(findings)} airports)")
    for issue in unchanged.values():
        lines.append(f"UNCHANGED #{issue['number']}")
    for issue in to_close.values():
        lines.append(f"CLOSE #{issue['number']}: {issue.get('title', '')}")
    return lines


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync a build report's findings to GitHub issues via `gh`."
    )
    parser.add_argument(
        "--report", required=True, help="Path to a report.json written by write_report."
    )
    parser.add_argument("--repo", required=True, help="Target repo, as OWNER/REPO.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the would-be create/update/close decisions without "
        "creating, editing, commenting on, or closing any issue.",
    )
    return parser.parse_args(argv)


def main(argv: list[str], *, run: Callable[[list[str]], str] = _gh) -> int:
    """CLI entry point: `python -m odp_kml.issues --report report.json --repo OWNER/REPO`."""
    args = _parse_args(argv)
    report = Report.from_json(Path(args.report).read_text())

    if args.dry_run:
        for line in describe_plan(report, repo=args.repo, run=run):
            print(line)
        return 0

    summary = sync_issues(report, repo=args.repo, run=run)
    print(
        f"created {len(summary.created)}, updated {len(summary.updated)}, "
        f"unchanged {len(summary.unchanged)}, closed {len(summary.closed)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
