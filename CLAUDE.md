# Textual ODP planview layer

ForeFlight content pack that draws FAA textual obstacle departure procedures
(ODPs) as planview KML, rebuilt every 28-day cycle by GitHub Actions.

## Rules

- A procedure is drawn only when the pipeline is certain of it. Any parse
  failure, unresolved reference, or degenerate geometry becomes a `Finding`
  and is reported (and filed as a GitHub issue by CI); it is never drawn.
- The grammar has no "skip unknown words" rule.
- No machine learning in the runtime pipeline. `tools/draft_golden.py` is an
  offline aid for drafting test fixtures that a human reviews.

## Code

- Python 3.14 via pyenv-virtualenv (`odp`); deps in `requirements*.txt`.
- `ruff check --fix && ruff format` before finishing; `pytest` must pass.
- Hand-written KML 2.2 limited to the subset ForeFlight renders.
