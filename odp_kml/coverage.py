"""Flag a drop in coverage against the previous cycle's release.

Coverage is the share of airports with a text procedure that the build drew
(`Report.drawn` over `Report.textual`, the figure in the summary line). A
change in the FAA's PDFs or in the PDF tools can withhold many airports at
once without any one finding standing out, as cycle 2611's new page banner
did. After a build, this compares its report with the report attached to the
release of the cycle before it and, when coverage fell by more than
`THRESHOLD` percentage points, opens one `odp-coverage` issue (or updates the
open one). It never fails the build: whether to hold a release is the
maintainer's call.

All `gh` calls go through the injectable `run` callable so tests never shell
out to the real `gh` binary.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .findings import Report
from .issues import _body_file, _gh

THRESHOLD = 2.0
LABEL = "odp-coverage"


def coverage(report: Report) -> float:
    """Percentage of airports with a text procedure that the build drew."""
    return 100 * report.drawn / report.textual if report.textual else 0.0


def drop_body(previous: Report, current: Report) -> str | None:
    """The issue body when coverage fell more than `THRESHOLD` points, else None."""
    before, after = coverage(previous), coverage(current)
    if before - after <= THRESHOLD:
        return None
    return (
        f"Coverage fell from {before:.1f}% in cycle {previous.cycle} "
        f"({previous.drawn:,} of {previous.textual:,} drawn) to {after:.1f}% "
        f"in cycle {current.cycle} ({current.drawn:,} of {current.textual:,} "
        f"drawn), a drop of {before - after:.1f} points.\n\n"
        "Compare the two releases' report.json, or snapshot both cycles with "
        "`tools/compare_builds.py`, to find the airports lost.\n"
    )


def previous_cycle(tags: list[str], cycle: str) -> str | None:
    """The newest release tag before `cycle`; tags are ISO dates, so they sort."""
    return max((tag for tag in tags if tag < cycle), default=None)


def check(report: Report, *, repo: str, run: Callable[[list[str]], str] = _gh) -> str:
    """Compare `report` with the previous release's and file an issue on a drop.

    Returns a one-line description of what it did.
    """
    releases = json.loads(
        run(["release", "list", "--repo", repo, "--limit", "200", "--json", "tagName"])
    )
    previous_tag = previous_cycle([r["tagName"] for r in releases], report.cycle)
    if previous_tag is None:
        return f"no release before {report.cycle} to compare with"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "report.json"
        run(
            [
                "release",
                "download",
                previous_tag,
                "--repo",
                repo,
                "--pattern",
                "report.json",
                "--output",
                str(path),
            ]
        )
        previous = Report.from_json(path.read_text())

    body = drop_body(previous, report)
    summary = (
        f"coverage {coverage(previous):.1f}% in {previous.cycle}, "
        f"{coverage(report):.1f}% in {report.cycle}"
    )
    if body is None:
        return summary
    title = f"[odp] Coverage fell from {previous.cycle} to {report.cycle}"
    _file_issue(run, repo, title, body)
    return f"{summary}; filed issue"


def _file_issue(
    run: Callable[[list[str]], str], repo: str, title: str, body: str
) -> None:
    """Edit the open `odp-coverage` issue, or create one when none is open."""
    open_issues = json.loads(
        run(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--label",
                LABEL,
                "--state",
                "open",
                "--json",
                "number",
            ]
        )
    )
    with _body_file(body) as path:
        if open_issues:
            number = str(open_issues[0]["number"])
            run(
                [
                    "issue",
                    "edit",
                    number,
                    "--repo",
                    repo,
                    "--title",
                    title,
                    "--body-file",
                    path,
                ]
            )
        else:
            run(["label", "create", LABEL, "--repo", repo, "--force"])
            run(
                [
                    "issue",
                    "create",
                    "--repo",
                    repo,
                    "--title",
                    title,
                    "--label",
                    LABEL,
                    "--body-file",
                    path,
                ]
            )


def main(argv: list[str], *, run: Callable[[list[str]], str] = _gh) -> int:
    """CLI entry point: `python -m odp_kml.coverage --report report.json --repo OWNER/REPO`."""
    parser = argparse.ArgumentParser(
        description="File an issue when coverage fell against the previous release."
    )
    parser.add_argument(
        "--report", required=True, help="Path to a report.json written by the build."
    )
    parser.add_argument("--repo", required=True, help="Target repo, as OWNER/REPO.")
    args = parser.parse_args(argv)
    report = Report.from_json(Path(args.report).read_text())
    print(check(report, repo=args.repo, run=run))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
