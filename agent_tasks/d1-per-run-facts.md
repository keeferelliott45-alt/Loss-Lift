# Task: D1 — per-run Source Info and Loss Summary for packet documents

## Objective
When one PDF binds several logical runs, the exported Source Info and Loss
Summary must present each run's own facts (carrier, named insured, policy,
term, valuation, claim count, printed totals) beside the document-level block,
instead of showing only page 1's facts. Each run's figures must come from the
run whose pages print them and must never be borrowed from another run.

## Why it matters
`docs/agent/CURRENT_STATE.md` records: "Packet document header, Source Info and
Loss Summary show page 1's facts." On authorized real packets this is visible —
multi-run documents (e.g. a 6-run Liberty Mutual report) collapse to one set of
header facts. The per-run facts already exist on `LogicalRun`; only the
display/export layer ignores them.

## Allowed scope
- `core/export.py` — Source Info sheet (`_write_source_sheet`) and Loss Summary
  (`_write_summary_sheet`) to add a per-run block; Runs sheet already per-run.
- `core/summary.py` — group the period/claims summarisation per run (keep the
  existing document-level roll-up for single-run docs unchanged).
- `app.py` — the reconciliation/header card: show per-run facts (or "N runs")
  without borrowing facts across runs.
- Tests: `tests/test_run_metadata.py`, `tests/test_export.py`,
  `tests/test_status_policy.py`, `tests/test_packet_runs.py`.

## Likely files
- `core/export.py` (`_write_source_sheet`, `_write_summary_sheet`, sheet order)
- `core/summary.py` (`summarise_periods` / `summarise_by_period`)
- `app.py` (`_reconciliation_card` header area)
- `core/schema.py` (read only — `LogicalRun`, `printed_claim_count`)

## Invariants that must hold
INVARIANTS §2 (facts belong to the run whose pages print them; never borrow) and
§4 (trust/status only via `core.review.canonical_status` /
`canonical_run_status` — do not introduce a second status definition). §7 stable
identities: do not rename sheets, columns, rule ids, enums. A single-run document
must keep the SAME output as today (byte-identical where practical).

## Required tests
- New failing-first test(s) proving a multi-run packet's Source Info and Loss
  Summary carry each run's own facts (red on base, green after).
- Regression: single-run documents unchanged.
- Targeted: `python -m pytest -q tests/test_run_metadata.py tests/test_export.py tests/test_status_policy.py tests/test_packet_runs.py`
- Standard: `scripts/agent_validate.sh --tests "<those paths>"`
- Full suite + `python -m tests.golden.baseline`.

## Corpus expectations
Display/export only, no extraction/reconciliation behaviour change: corpus gate
NOT required (state the reasoning). If the change would alter any measured gate
field, stop and re-scope.

## Definition of done
- [ ] Per-run facts shown; no cross-run borrowing; single-run output unchanged
- [ ] New test red on base, green after; full suite + golden green
- [ ] Corpus gate explicitly not required (reason recorded)
- [ ] `docs/agent/CURRENT_STATE.md` updated for the closed gap
- [ ] Handoff written

## Prohibited scope expansion
- No changes to `core/pipeline.py`, `core/runs.py`, `core/reconcile.py`
  (another task owns that surface) unless impossible — if so, stop and report.
- No rule-id/field/sheet renames; no new product features; no refactors.

## Environment
- Python: the local Python 3.14 interpreter with every dependency installed (`py -3`)
- pytest basetemp: `--basetemp=%TEMP%\kilo\pytest-d1` (fresh per run)
- Git Bash: `C:\Program Files\Git\bin\bash.exe`

## Handoff
Use `docs/agent/HANDOFF_TEMPLATE.md`.
