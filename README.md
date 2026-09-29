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

[install]: https://foreflight.com/content?downloadURL=https%3A%2F%2Fgithub.com%2FRISCfuture%2Fforeflight-textual-odps%2Freleases%2Flatest%2Fdownload%2FTextual.ODPs.zip

## What is drawn

For each airport with a DEPARTURE PROCEDURE or VCOA section that resolves
with certainty:

- Initial climb, climbing turns, and turns at a fix or altitude.
- Direct-to-navaid and heading legs flown to an altitude, including
  "climb runway heading", which follows the runway's own course
  (`rwy hdg`), and a climb that names no heading at all ("climb to 1200
  before turning left"), drawn straight out along the runway and labelled
  with its altitude alone.
- Radial intercepts and tracking to an altitude, fix, DME distance, or the
  navaid; an altitude climbed to on the way to a fix ("to 3000 via FSM
  R-064 to FSM VORTAC") is labelled where the leg ends. A heading printed
  with no terminator is held until the radial that follows it is
  intercepted ("heading 170°, thence ... on RZS R-185"). A turn to
  intercept a radial with no heading printed ("climbing left turn to
  intercept PUB R-274") turns the published way onto a schematic,
  unlabelled 45° intercept heading, on the side of the radial the turn
  leaves the aircraft. A turn that would roll out on the radial's course
  within 0.5 NM of it rolls out there and converges on the radial at 20°.
  It is not drawn when no turn direction is printed, when the published
  turn onto the 45° or 20° heading and the one onto the radial's course
  differ by more than 45° (as for a route already converging on the
  radial), nor when that turn exceeds 270°, so a short turn the other way
  would reach the heading.
- "Proceed on course" after a runway's legs as a short dashed stub, bent
  toward any published turn restriction. A published direction ("before
  proceeding northbound", read against true north) turns the stub onto
  it in dashes, the shorter way unless the text names the side; two
  directions ("before turning west or northwest") name no one course, so
  the route ends without a stub. A VCOA that only proceeds on course
  draws just its circle, and the stub leaving it when the text names one
  direction.
- Alternative climbs for one runway that differ only in altitude and turn
  direction ("climb heading 167° to 2700 before turning right. Climb
  heading 167° to 3400 before turning left."), each drawn to its own stub.
- Climb-in-hold racetracks.
- Heading ranges ("climb on a heading between 350° CW to 162° from DER")
  as a fan from the turn-start point, beside the route flown on "all
  other courses": the route splits there into a line along each limiting
  heading, each turning off the arriving course as any other turn does
  (the published way, else the shorter) and ending in an arrowhead, with a
  light dashed arc between them. Each fan is labelled with its runways
  and the range as printed, keeping every heading, CW/CCW, printed turn
  and altitude, on as few lines as ForeFlight shows whole (it elides the
  middle of a label over 22 characters): the runways lead the first line
  (`35L/R hdg 313° CW 172°`), which drops its `hdg` only to make room for
  them or to save a line (`16L/R 213° CCW 353°`, `11 320° CW 220° 3000'`);
  otherwise they stand on a line of their own. Alternative sectors stay
  stacked under the first (`25 hdg 317° CW 083°` over `or 206° CCW 083°`).
  Parallel runways that depart alike share one fan, drawn from between
  their turn-start points. The label stands in or just beyond its own
  fan, clear of other labels, lines and fans where it can.
  A diverse departure limited to a sector ("diverse departures authorized
  300° to 120° CW") is drawn the same way. A minimum climb gradient given
  for the other headings instead ("or min. climb of 415 ft per NM to 1600
  for headings 101° through 314°") names no route, so only the
  fan is drawn.
- VCOA (visual climb over airport) circles, including airports whose only
  procedure is a VCOA and visual climbs written into the departure
  procedure itself ("..., or for climb in visual conditions: cross ...").
  A published crossing direction joins the label where it still fits in
  ForeFlight's 22 characters (`VCOA (≥8200' SE bound)`), and a speed limit
  on the visual climb is labelled below the circle. The label stands at
  the circle's north point; a second altitude around the same circle
  stands at its south point (then east, then west), never on top of it.
- Altitude labels in plain text (ForeFlight renders neither rich text nor
  combining marks): `7000'` for "climb to," `≥7000'` for "at or above,"
  `≤7000'` for "at or below," and `at 7000'` for a mandatory altitude;
  holds read `Hold 246° RT ≥9300'` (inbound course, turns, altitude),
  or `Hold 246° RT ≥MEA/MCA` when the text gives an en-route minimum
  rather than a figure (`≥4000/MEA` for "at or above 4000 or MEA").
  With `--label-style fms` they take the FMS form: `A` and `B` suffixes,
  and plain digits for "climb to" and "at."
- Labels for everything a pilot programs: headings (`hdg 120°`),
  radials with their navaid (`SAU R-035`), DME terminators (`BAM 10 DME`),
  and speed limits (`≤200 KIAS until 9000'`, or just `≤200 KIAS` when the
  limit ends anywhere but at an altitude). Fixes aren't labelled because
  ForeFlight's own chart names them. Leg lengths aren't labelled.
- Each airport's lines take one of six colors, chosen so that no two
  airports within 40 NM share a color. Where an airport draws routes for
  more than one runway group, each group's lines take their own shade of
  that color, and the lines the routes share (a shared tail, a VCOA) keep
  the color itself.

**Not drawn:** takeoff minimums (ceiling/visibility and climb gradient
tables), obstacle notes, diverse vector areas (DVAs), procedures that
reference a graphic ODP instead of text ("use LUNDI DEPARTURE"), and
airway routing after the ODP ends. A runway that flies a graphic ODP is
drawn from none of its text; an airport whose every runway does is left
out of the coverage percentage and counted on its own in the report,
since ForeFlight charts graphic ODPs itself. A speed limit printed in a
runway's takeoff minimums ("do not exceed 210K until intercepting the ENI
R-073") is not read either, so it withholds that runway's route and
visual climb.

## How it decides what to draw

A route is drawn only when the pipeline is certain of it: its text,
and every sentence that may apply to it, parses completely (a sentence
naming its runway applies to its VCOA too), every navaid and fix
reference resolves to exactly one match in NASR, and the geometry hits
no degenerate case (a radial parallel to a course, a turn with no
defined radius, and so on). Anything less
certain is never drawn; it becomes a `Finding` instead, listed in
`report.md` on the release for that cycle and filed as a GitHub issue,
grouped by failure signature so one recurring problem across airports and
cycles produces one issue. Coverage improves cycle by cycle as those
findings get fixed.

Certainty is judged one runway route at a time: each runway's route,
together with the shared tail it continues into, and each VCOA. When some
of an airport's routes are certain and others are not, the certain ones
are drawn and a label just south of the airport names the rest
(`ODP NOT SHOWN: RWY 17L/17R, VCOA`), so a missing line is never read as a
runway without an ODP. An airport whose ODP text draws nothing at all gets
the label alone (`ODP NOT SHOWN`), so its empty map is never read as an
airport without an ODP. A route is never drawn in part, and the report
counts airports drawn in part separately.

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
| Climb gradient for altitude legs | Published minimum up to the altitude it is published to, 200 ft/NM above it or when none is published |
| Hold leg length | 1 minute (1.5 minutes above 14,000 ft; 1 minute to an unstated MEA, MCA or MOCA) |
| VCOA circle | 2 NM schematic radius |
| Heading-range wedge | 1 NM schematic radius from the turn start, or 0.3 NM beyond where the widest turn onto a limiting heading ends |
| Runway-group shades | Same perceived lightness as the airport's color; hue within 15° of it and at most a third of the way to a comparably vivid palette color; chroma down to 60% from four groups on |
| Wedge edges | Turn off the arriving course at the standard turn radius, like every other turn |
| Shared heading-range wedge | Turn starts within 1.5 NM of each other |
| Wedge label clearance | Labels sized for about 20 NM across an iPad screen |
| Course printed as a compass direction ("before proceeding northbound") | The direction's true bearing, turned onto the shorter way unless the side is printed; no stub for two directions ("west or northwest") |
| Climb naming no heading or route | Straight out along the runway's course while every earlier leg has held it; refused anywhere else |
| Intercept heading not printed | 45° to the radial on the side the turn ends; 20° after rolling out within 0.5 NM of it. Not drawn if the turn onto it is over 270° or over 45° off the turn onto the radial's course |
| Magnetic headings | Airport's variation of record (WMM if unpublished) |
| Magnetic radials | Referenced navaid's own station declination |
| Intercept heading rolled out on its radial | Radial joined there, if a runway's route is within 0.5 NM and 3° of it |
| Unprinted radial sense, flown to an altitude | Within 60° of the printed heading, else of a runway route's course or 60°–120° into its stated turn (all routes into a shared tail agreeing) |

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

## Comparing builds

`tools/compare_builds.py` checks a grammar or geometry change against every
airport in a cycle, not just the fixtures. It snapshots each airport's
outcome (drawn whole, drawn in part, charted DPs only, or not drawn), its
findings and a digest of its shapes, then names every airport gained, lost
or redrawn between two snapshots:

```sh
python tools/compare_builds.py snapshot --cycle 2026-09-03 --out before.json
# ... change the code ...
python tools/compare_builds.py snapshot --cycle 2026-09-03 --out after.json
python tools/compare_builds.py diff before.json after.json --state CO
```

It reuses the build's `data_cache` and is never imported by, or run as part
of, the build.

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
