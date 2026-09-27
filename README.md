# Textual ODPs — ForeFlight content pack

A ForeFlight content pack that draws FAA textual obstacle departure
procedures (ODPs) as a planview KML layer, nationwide. ForeFlight shows a
textual ODP only as text in the FAA's Takeoff Minimums PDF under Airport →
Procedures; this pack extracts that text from the born-digital PDF, parses
it against NASR data, then draws the routing — climb, turns, and navaid
tracking — directly on the map. Rebuilt every 28-day NASR cycle by
GitHub Actions.

## Install in ForeFlight

On the iOS device that has ForeFlight installed, open this link in Safari:

**[Install the ODP layer][install]**

When the preview appears, long-press or pull the page down and tap **Open
in ForeFlight**. The pack downloads into **More → Downloads** and the
layer turns on under **Maps → Layers**.

The link goes through GitHub's `releases/latest` redirect, so bookmarking
it once keeps pulling the current NASR cycle — when ForeFlight flags the
pack as expired (every 28 days), just tap it again. A daily CI job polls
the FAA and publishes a new release within a day of each cycle's effective
date.

[install]: https://foreflight.com/content?downloadURL=https%3A%2F%2Fgithub.com%2FRISCfuture%2Fodp%2Freleases%2Flatest%2Fdownload%2FTextual.ODPs.zip

### Test Grid

A separate pack, `ODP Test Grid`, draws one synthetic procedure per
construction (turns, radial intercepts, holds, VCOA, and so on) so a
change to the drawing code can be checked visually in ForeFlight without a
real airport. It is not part of the ODP data pack.

**[Install the construction test grid][install-test-grid]**

[install-test-grid]: https://foreflight.com/content?downloadURL=https%3A%2F%2Fgithub.com%2FRISCfuture%2Fodp%2Freleases%2Flatest%2Fdownload%2FODP.Test.Grid.zip

## What is drawn

For each airport with a DEPARTURE PROCEDURE or VCOA section that resolves
with certainty:

- Initial climb, climbing turns, and turns at a fix or altitude.
- Direct-to-navaid and heading legs flown to an altitude.
- Radial intercepts and tracking to an altitude, fix, DME distance, or
  the navaid.
- "Proceed on course" after a runway's legs as a short dashed stub, bent
  toward any published turn restriction. A VCOA that only proceeds on
  course draws just its circle.
- Climb-in-hold racetracks.
- VCOA (visual climb over airport) circles, including airports whose only
  procedure is a VCOA.
- Altitude labels spelled out in plain text (ForeFlight renders neither
  rich text nor combining marks): `7000'` for "climb to," `at or above
  7000'`, `at or below 7000'`, and `at 7000'` for a mandatory altitude;
  holds read `Hold (at or above 9300')`. With `--label-style fms` they take
  the FMS form: `A` and `B` suffixes, and plain digits for "climb to" and
  "at." Heading legs are labelled with the magnetic heading; leg lengths
  are not labelled.
- Each airport's lines take one of six colors, chosen so that no two
  airports within 40 NM share a color.

**Not drawn:** takeoff minimums (ceiling/visibility and climb gradient
tables), obstacle notes, diverse vector areas (DVAs), procedures that
reference a graphic ODP instead of text, and airway routing after the ODP
ends.

## How it decides what to draw

A procedure is drawn only when the pipeline is certain of it: the text
parses completely, every navaid and fix reference resolves to exactly one
match in NASR, and the geometry hits no degenerate case (a radial parallel
to a course, a turn with no defined radius, and so on). Anything less
certain is never drawn; it becomes a `Finding` instead, listed in
`report.md` on the release for that cycle and filed as a GitHub issue,
grouped by failure signature so one recurring problem across airports and
cycles produces one issue. Coverage improves cycle by cycle as those
findings get fixed.

This pack is an educational aid, not a tool for navigation. The published
FAA text and charts govern; always fly from those, not from this layer.

## Depiction assumptions

Every drawn shape follows the same aircraft and rendering assumptions,
used whenever the text does not otherwise constrain the geometry:

| Assumption | Value |
| --- | --- |
| Display true airspeed | 150 kt |
| Turn radius | 25° bank or standard rate, whichever is larger |
| Turn start | 400 ft above the DER (departure end of runway) |
| Climb gradient for altitude legs | Published minimum, else 200 ft/NM |
| Hold leg length | 1 minute (1.5 minutes above 14,000 ft) |
| VCOA circle | 2 NM schematic radius |
| Magnetic headings | Airport's variation of record (WMM if unpublished) |
| Magnetic radials | Referenced navaid's own station declination |

## Data sources

- FAA d-TPP Takeoff Minimums PDFs and metafile
  ([aeronav.faa.gov/d-tpp](https://aeronav.faa.gov/d-tpp/)) for the
  procedure text.
- FAA NASR 28-day subscription CSV — APT (airports), NAV (navaids), FIX
  (fixes), and HPF (holding patterns) — for identifiers, positions, and
  magnetic variation.
- FAA Orders 8260.3G (TERPS) and 8260.46K, and Instrument Flight
  Procedures Information Bulletin (IFP IB / IAC) 7, for the construction
  rules a textual ODP follows.

FAA aeronautical data is in the public domain.

## Building locally

```sh
pyenv virtualenv 3.14.7 odp
pyenv activate odp
pip install -r requirements-dev.txt
brew install poppler
```

```sh
# Resolve and print the current and next NASR cycle
python generate_odp_kml.py --print-cycles

# Build a KML for a couple of airports only, skipping the pack wrapper
python generate_odp_kml.py --airports TPH,ALB --kml-only

# Full nationwide build (writes "Textual ODPs.zip" and report.json/.md)
python generate_odp_kml.py -v

# The construction test-grid pack
python generate_odp_kml.py --test-grid

# Also dump each airport's normalized sections (input to draft_golden.py)
python generate_odp_kml.py --dump-sections sections.json
```

Run the test suite and linter before committing:

```sh
pytest
ruff check --fix . && ruff format .
```

## Release automation

`.github/workflows/build-pack.yml` polls the FAA daily. The currently
effective NASR cycle is published as the `latest` GitHub release; the
upcoming cycle, once its data is available, is published as a prerelease
and promoted to `latest` on its effective date. The workflow keeps the six
most recent cycle releases and prunes older ones, and syncs findings from
the current cycle's build into GitHub issues.

GitHub disables a scheduled workflow after 60 days with no repository
activity — push a commit (or run it manually) if releases stop appearing.

## Golden set drafting

`tools/draft_golden.py` is an offline aid for drafting golden-fixture ASTs
from real ODP text, using the Claude API (`ANTHROPIC_API_KEY` required;
`pip install -r requirements-tools.txt`). It drafts a parse for each
airport's section and checks it mechanically, but every draft is reviewed
by a human before it becomes a fixture in `tests/fixtures/golden/`. The
test suite then requires the grammar to parse each fixture's text to exactly
its reviewed AST, or to refuse it. The tool is never imported by, or run as
part of, the build.

## License

[Unlicense](UNLICENSE) — public domain. Provided as-is, with no warranty;
verify any procedure against current FAA publications before using it for
navigation.
