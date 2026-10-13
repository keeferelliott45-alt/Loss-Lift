# Task: a bridged unnumbered page must not render a packet CLEAN

- **Title:** Bridged unnumbered page (held-out benchmark H2)
- **Owner:** lead (`agent/lead/p0-1-bridged-page`)
- **Base:** current PR #9 head `230c0fcd404385fc3ed2e6f9ac6c614ea9429886`
  (stacked on `claude/packet-series-and-signed-totals`; never committed to it)
- **Branch:** `agent/lead/p0-1-bridged-page`
- **Isolation:** must not touch PR #9's branch or any unrelated open PR.

## Goal

A report printing "Page 1 of 3" and "Page 3 of 3" with another carrier's
unnumbered page bound between them must not read CLEAN. The unnumbered page
fits the report's missing page 2 exactly, so the planner bridges it; today the
bridge is silent, its refused claim is named by no rule, and the packet reads
CLEAN. After the change the page is joined **blind** (nothing on it names the
report), so its refused rows are named by R-29 and the packet is NEEDS_REVIEW.

## Why now

Held-out benchmark task H2 (claim accountability across a bridged page);
`docs/agent/INVARIANTS.md` §1 "no silent claim disappearance". Reproduced at
base: status CLEAN, `runs=[]`, `CR-40117` refused with `bounded=True,
report=False`, rules `['R-18','R-19']`.

## Invariants at risk

- **Invariant 1 (claim accountability):** every claim-like row is read or
  recorded and named by a rule. A bridged page's refused row was recorded but
  named by no rule.
- **Invariant 2 (logical runs):** a page joined without a confirming heading
  must be `blind`. The `exact` branch skipped the blind mark; only `fits` set
  it.
- Must not introduce: false CLEAN (the bug); a false NEEDS_REVIEW on a page
  whose heading **does** confirm the report (unchanged: `confirms` → True).

## Scope

- In scope: `core/runs.py` (`_Planner.unnumbered`, the `exact or fits` branch);
  `tests/test_packet_adversarial.py`, `tests/test_packet_runs.py`.
- Out of scope: any other module, rule id, gate field, schema, tolerance.
- Do not: edit golden expected outputs, baselines, tolerances or gates.

## Plan

1. Red-first: a foreign unnumbered page filling the exact count is not silent.
2. Fix: mark the page blind whenever its heading does not confirm the report,
   regardless of `exact` (was `not exact and not confirms`).
3. Validate.

## Verification

- Targeted: `pytest -q tests/test_packet_adversarial.py::test_r3_9_a_foreign_page_filling_the_exact_count_is_not_silent tests/test_packet_runs.py::test_a_foreign_unnumbered_page_filling_the_exact_count_is_blind`
- Full suite: `python -m pytest -q` — 2673 collected, 27 failed, all in
  `test_cloud_gate*` and identical on the unmodified base (Windows
  symlink/fifo/container environment), 0 non-cloud failures.
- Ratchet: `python -m tests.golden.baseline` — exit 0, ALL 108/108 money and
  text.
- Corpus gate: **required** (extraction/reconciliation). Not run: the local
  corpus is absent on this machine and PR #9 is itself awaiting owner
  verification of two corpus documents. Expected effect: a wrong CLEAN becomes
  NEEDS_REVIEW for a packet with a foreign bridged page; no claim lost.

## Definition of done

- Red-first tests exist and pass; the change is one condition.
- Full suite and golden ratchet green (modulo pre-existing cloud-gate env
  failures).
- Draft PR stacked on PR #9 with this handoff.
- Campaign log updated.
