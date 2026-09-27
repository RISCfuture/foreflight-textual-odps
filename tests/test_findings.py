"""Findings: uncertainty records grouped into a per-cycle build report."""

import json

from odp_kml.findings import (
    Finding,
    Kind,
    Report,
    normalize_signature,
    signature_key,
    write_report,
)


def make_finding(**overrides):
    fields = {
        "kind": Kind.UNRESOLVED_REF,
        "signature": 'unresolved fix "ABCDE" via TPH VOR on radial R-210',
        "airport": "TPH",
        "cycle": "2026-09-03",
        "amendment": "5",
        "verbatim_text": 'Climb via TPH VOR R-210 to "ABCDE" then as filed.',
        "detail": "no such fix in NASR",
    }
    fields.update(overrides)
    return Finding(**fields)


class TestSignatureKey:
    def test_identical_across_airport_numbers_and_whitespace(self):
        first = make_finding(
            airport="TPH",
            signature='unresolved fix "ABCDE" on radial R-210',
        )
        second = make_finding(
            airport="ABQ",
            signature='unresolved   fix "ABCDE"  on radial R-045',
        )
        assert signature_key(first.kind, first.signature) == signature_key(
            second.kind, second.signature
        )

    def test_identical_across_airport_and_navaid_identifiers(self):
        first = make_finding(
            airport="TPH",
            signature='unmatched phrase "direct TPH VORTAC on radial R-210 thence to KTNX"',
        )
        second = make_finding(
            airport="ABQ",
            signature='unmatched   phrase "direct VNY VORTAC on radial  R-045 thence to 3U3"',
        )
        assert signature_key(
            first.kind, first.signature, first.airport
        ) == signature_key(second.kind, second.signature, second.airport)

    def test_allowlisted_aviation_abbreviations_survive_blurring(self):
        text = 'unmatched phrase "direct TPH VORTAC via NDB thence"'
        normalized = normalize_signature(text, airport="TPH")
        assert "vortac" in normalized
        assert "ndb" in normalized
        assert "tph" not in normalized

    def test_own_airport_is_blurred_even_outside_the_identifier_shape(self):
        text = 'unmatched phrase "direct K9L2XY VORTAC thence"'
        blurred = normalize_signature(text, airport="K9L2XY")
        unblurred = normalize_signature(text)
        assert blurred != unblurred
        assert "k9l2xy" not in blurred

    def test_differs_when_the_phrase_differs(self):
        first = make_finding(
            signature="unresolved reference on radial R-210, proceed direct"
        )
        second = make_finding(
            signature="unresolved reference on radial R-210, hold as published"
        )
        assert signature_key(first.kind, first.signature) != signature_key(
            second.kind, second.signature
        )

    def test_differs_when_kind_differs(self):
        signature = 'unresolved fix "ABCDE" on radial R-210'
        assert signature_key(Kind.UNRESOLVED_REF, signature) != signature_key(
            Kind.AMBIGUOUS_REF, signature
        )


class TestReportCounts:
    def test_not_drawn_by_kind_counts_each_kind(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=10,
            drawn=8,
            findings=[
                make_finding(kind=Kind.UNRESOLVED_REF),
                make_finding(kind=Kind.UNRESOLVED_REF),
                make_finding(kind=Kind.PARSE_FAILED),
            ],
            label_count=20,
        )
        assert report.not_drawn_by_kind() == {
            Kind.UNRESOLVED_REF: 2,
            Kind.PARSE_FAILED: 1,
        }

    def test_grouped_collapses_matching_signatures_and_sorts_by_airport(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=10,
            drawn=8,
            findings=[
                make_finding(
                    airport="TPH", signature='unresolved fix "ABCDE" on radial R-210'
                ),
                make_finding(
                    airport="ABQ", signature='unresolved fix "ABCDE" on radial R-045'
                ),
                make_finding(
                    airport="BOS",
                    signature='unresolved fix "FGHIJ" on radial R-210',
                    kind=Kind.AMBIGUOUS_REF,
                ),
            ],
            label_count=20,
        )
        groups = report.grouped()
        assert len(groups) == 2
        matched = next(g for g in groups.values() if len(g) == 2)
        assert [finding.airport for finding in matched] == ["ABQ", "TPH"]

    def test_summary_line_formats_thousands_and_percentage(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=2512,
            drawn=2341,
            findings=[make_finding() for _ in range(171)],
            label_count=0,
        )
        assert report.summary_line() == (
            "Drew 2,341 of 2,512 ODPs (93%); 171 findings in 1 signatures"
        )

    def test_summary_line_handles_zero_airports_without_dividing_by_zero(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=0,
            drawn=0,
            findings=[],
            label_count=0,
        )
        assert (
            report.summary_line() == "Drew 0 of 0 ODPs (0%); 0 findings in 0 signatures"
        )


class TestReportSerialization:
    def test_json_round_trip(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=3,
            drawn=2,
            findings=[make_finding(), make_finding(airport="ABQ", amendment=None)],
            label_count=5,
        )
        restored = Report.from_json(report.to_json())
        assert restored == report

    def test_to_json_is_valid_json(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=1,
            drawn=1,
            findings=[make_finding()],
            label_count=1,
        )
        json.loads(report.to_json())


class TestReportMarkdown:
    def test_contains_summary_heading_signatures_and_verbatim_text(self):
        report = Report(
            cycle="2026-09-03",
            airports_with_text=2,
            drawn=1,
            findings=[
                make_finding(
                    airport="TPH",
                    signature='unresolved fix "ABCDE" on radial R-210',
                    verbatim_text='Climb via R-210 to "ABCDE" then as filed.',
                )
            ],
            label_count=1,
        )
        markdown = report.to_markdown()
        assert report.summary_line() in markdown
        assert "unresolved_ref" in markdown
        assert 'unresolved fix "id" on radial r-nnn' in markdown
        assert 'Climb via R-210 to "ABCDE" then as filed.' in markdown
        assert "TPH" in markdown


def test_write_report_writes_json_and_markdown(tmp_path):
    report = Report(
        cycle="2026-09-03",
        airports_with_text=1,
        drawn=1,
        findings=[make_finding()],
        label_count=1,
    )
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"

    write_report(report, json_path, md_path)

    assert Report.from_json(json_path.read_text()) == report
    assert report.summary_line() in md_path.read_text()
