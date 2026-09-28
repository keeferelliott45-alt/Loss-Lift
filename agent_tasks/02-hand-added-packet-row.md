# Task: a hand-added claim must not default into run-1 of a packet

- **Title:** Hand-added packet row (held-out benchmark H1)
- **Owner:** lead (`agent/lead/p0-2-hand-row`)
- **Base:** current PR #9 head `230c0fcd404385fc3ed2e6f9ac6c614ea9429886`
  (stacked on `claude/packet-series-and-signed-totals`; never committed to it)
- **Branch:** `agent/lead/p0-2-hand-row`

## Goal

A claim a reviewer adds on the review screen, in a packet, without saying which
page/run it belongs to, must not be counted into run-1 and must not leave the
packet reading CLEAN. It belongs to no run; R-11 names it and the packet is
NEEDS_REVIEW until the reviewer places it.

## Why now

Held-out benchmark H1; `docs/agent/INVARIANTS.md` "each claim belongs to exactly
one run". Reproduced at base: adding a money-free claim to a CLEAN two-run
packet left `status=CLEAN` and placed the claim in run-1 (`source_page=1`).

## Invariants at risk

- **Claim belongs to exactly one run:** the added row belonged to run-1 by a
  page it never had.
- **Fail closed:** an unplaceable claim is a review, not a silent default.

## Scope

- `core/schema.py`: `Claim.source_page` becomes `int | None` (default still 1);
  `run_claims` unchanged (already excludes a `None` page via `holds`).
- `core/pipeline.py`: `_added_page` decides a hand-added row's page — the page
  the reviewer gives, `None` in a packet when none is given, 1 in a single
  report.
- `core/reconcile.py`: `_claims_in_no_run` raises R-11 for a packet claim no run
  holds (the same rule whose expected value is "each claim in one run").
- `tests/test_packet_runs.py`: red-first regression.
- Out of scope: no new rule id, no gate field, no other module's behaviour. A
  claim read off the page keeps its run whatever the page cell says (existing
  contract `test_a_claim_added_by_hand_joins_the_run_of_its_page`).

## Verification

- Red-first: `test_a_claim_added_with_no_page_belongs_to_no_run_and_is_named`
  fails at base (`source_page == 1`, status CLEAN), passes on the candidate.
- Full suite: 27 failed, **all** in `test_cloud_gate*` and identical on the
  unmodified base; 0 non-cloud, 0 errors.
- Ratchet: exit 0, ALL 108/108. `compileall` 0. `git diff --check` 0.
- Corpus gate: required (packet/run behaviour). Not run — no local corpus; PR #9
  awaits owner verification. Expected: no change on real corpus (no review-added
  claims in extraction output).

## Definition of done

- Red-first test exists and passes; explicit-page path unchanged.
- Full suite and ratchet green modulo pre-existing cloud-gate failures.
- Draft PR stacked on PR #9; campaign log updated.
