# Task: a refused row on a bounded single report must be named

- **Title:** Refused rows on a bounded single report (P0-3)
- **Owner:** lead (`agent/lead/p0-3-refused-single`)
- **Base:** PR #9 head `4ca0d414dc8ef94b526014af11b650e56392b2a1` (the branch tip
  after the 2026-09-28 fast-forward; 230c0fc is an ancestor). Stacked on
  `claude/packet-series-and-signed-totals`; never committed to it.

## Goal

A claim-like row refused by the claim-number vote on a lone report whose own
numbering bounds it must be named by a rule, not recorded silently. Today R-23
needs money on the row, R-28 needs an unsettled run, and R-29 reports only
packets and unbounded scans, so a money-free refusal on a bounded single report
is named by no rule and the report reads CLEAN.

## Why now

`CURRENT_STATE.md` known gap "Refused rows on a bounded single report are
recorded but named by no rule"; invariant 2 (no silent claim disappearance).
Reproduced at base `4ca0d41`: a one-page "Page 1 of 1" report with
`DATED_CODE` (`0HA-MATERIAL`, a loss date, no amounts) reads CLEAN with
`refused=[(1, '0HA-MATERIAL', bounded=True, report=False)]`, rules `R-18/R-19`.

## Decision (this item settles the open rule behaviour)

Fail closed: every refused row is named. `row.report` becomes
`not (run is not None and run.ambiguous)` — an unsettled run's refusals stay
with R-28 (which lists them); every other refusal is reported under R-29, which
now covers a bounded single report as well as packets and unbounded scans. R-29's
id is unchanged; its message no longer claims the vote pooled reports.

## Scope

- `core/pipeline.py`: the `row.report` expression (one condition).
- `core/reconcile.py`: R-29 docstring/message generalised; function renamed
  `r29_refused_claims` (rule id unchanged).
- `tests/test_packet_adversarial.py`: red-first regression.
- Out of scope: no new rule id, no gate field, no change to which rows are
  refused or to R-23/R-28.

## Verification

- Red-first: `test_a1b_a_bounded_single_report_refusal_is_named` fails at base
  (`report=False`, CLEAN), passes on the candidate.
- Full suite: 27 failed, all `test_cloud_gate*`, identical on base; 0 non-cloud,
  0 errors.
- Ratchet: exit 0, ALL 108/108. compileall 0. `git diff --check` 0.
- Corpus gate: required (status policy; a bounded single report that refuses a
  row moves CLEAN -> NEEDS_REVIEW). **NOT RUN** — no local corpus; PR #9 awaits
  owner verification. Every affected document needs a per-document explanation
  and the owner's approval before merge.

## Definition of done

- Red-first test exists and passes; packets and ambiguous runs unchanged.
- Full suite and ratchet green modulo pre-existing cloud-gate failures.
- Draft PR stacked on PR #9, marked "awaiting owner approval" for the gate.
