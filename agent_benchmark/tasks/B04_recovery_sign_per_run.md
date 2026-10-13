# B04 — Mixed recovery conventions in one packet (logical run / reconciliation)

| Field | Value |
|---|---|
| Starting commit | `b60e93ce5f14f1036888334f6aec6d6c207df02c` |
| Category | logical-run / reconciliation |
| Allowed scope | `core/pipeline.py`, `core/normalize.py`, `core/schema.py` (additive fields only), `tests/` |
| Max unrelated diff | 0 lines outside scope |
| Corpus expectation | no change on single reports; packets printing recoveries may change (explain) |
| Difficulty | hard |
| Known correct solution | yes |
| Acceptance | hidden checks + full suite + golden + corpus explanation |

## Prompt
A packet binds two carriers' loss runs. One prints recoveries as negative
credits (`-200.00`), the other as positive amounts. Each report on its own
reconciles cleanly, but bound together one of them fails R-01. Make each
logical run's recovery sign convention its own — for its claims and its
printed totals — without changing how a single report is read. Add tests,
including the single-report guarantees. Follow AGENTS.md and end with the
handoff template.

## Invariants
INVARIANTS.md §2 (run facts never borrowed), §3 (signed semantics, R-01,
R-04), single-run compatibility.
