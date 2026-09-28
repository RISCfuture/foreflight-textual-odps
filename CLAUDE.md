# Textual ODP planview layer

ForeFlight content pack that draws FAA textual obstacle departure procedures
(ODPs) as planview KML, rebuilt every 28-day cycle by GitHub Actions.

## Rules

- A procedure is drawn only when the pipeline is certain of it, one runway
  route (with any shared tail it flies) or VCOA at a time. Any parse
  failure, unresolved reference, or degenerate geometry in a route becomes a
  `Finding` and is reported (and filed as a GitHub issue by CI); that route
  is never drawn, and the airport's other routes are drawn beside a
  "Not drawn: RWY …" label. A route is never drawn truncated.
- The grammar has no "skip unknown words" rule.
- No machine learning in the runtime pipeline. `tools/draft_golden.py` is an
  offline aid for drafting test fixtures that a human reviews.

## Code

- Python 3.14 via pyenv-virtualenv (`odp`); deps in `requirements*.txt`.
- `ruff check --fix && ruff format` before finishing; `pytest` must pass.
- Hand-written KML 2.2 limited to the subset ForeFlight renders.
