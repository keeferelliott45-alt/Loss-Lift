# B05 — Pin per-run printed claim counts with tests (test / gate work)

| Field | Value |
|---|---|
| Starting commit | `b60e93ce5f14f1036888334f6aec6d6c207df02c` |
| Category | test modification |
| Allowed scope | `tests/` only — no production change |
| Max unrelated diff | 0 lines outside `tests/` |
| Corpus expectation | gate must pass (exit 0) — no behaviour change |
| Difficulty | medium |
| Known correct solution | n/a — scored objectively by hidden mutants |
| Acceptance | new tests pass; each hidden mutant must make at least one new test fail |

## Prompt
In a packet, each logical run's printed claim count is read from that run's
own pages and checked against that run's claims (R-05, per run). This
behaviour works today, but no test would notice if it silently stopped
working — whether through where the count is read, what the rules are
given, or which rule runs per run, for digital and scanned pages alike. Write
the smallest set of tests that would catch such regressions. Do not change
production code. Follow AGENTS.md and end with the handoff template.

## Invariants
INVARIANTS.md §2, §3.
