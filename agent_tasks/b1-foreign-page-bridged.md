# Task: close the `foreign-page-bridged` qualification gap

## Objective
Make qualification case `foreign-page-bridged` met: the claim printed on a
foreign carrier's unnumbered page that sits inside a numbered report must be
read into the correct logical run with the correct canonical status, instead of
being only named by R-23/R-28 with its claim missing. `tests/qualification/
test_packs.py` must pass with the case marked met in `pack_ledger.json`, and no
other ledgered case may move.

## Why it matters
The qualification ledger (on base `c765777`) holds 24 cases; `foreign-page-bridged`
is one of two unmet (shortfalls `claims_missing`, `status_differs`). It is real:
on authorized real documents a foreign page bridged into a report is split off,
its figures counted (R-23), its boundary flagged (R-28), and its claim never
read. Silent claim loss is out of the question but a missing claim is still a
missed claim.

## Allowed scope
- Run planning / boundary logic and the attribution of a bridged page to a run:
  `core/runs.py` (`plan_packet`, `_Planner`, `same_heading`, `confirms`,
  `unsettled_runs`), and only the minimal `core/pipeline.py` / `core/reconcile.py`
  touch needed to read the bridged page's row and compute its status.
- The synthetic pack for the case and its ledger entry (only after evidence).
- `docs/agent/CURRENT_STATE.md` if the milestone is reached.

## Likely files
- `core/runs.py` — where the foreign page is split off / left unnumbered.
- `core/pipeline.py` — building claims for the bridged page (`build_claims`,
  `identifier_shapes_by_run`, per-run facts).
- `core/reconcile.py` — R-23/R-28/R-29 behaviour for the bridged page.
- `tests/qualification/packs/**` and `tests/qualification/pack_ledger.json`.
- `tests/test_packet_runs.py`, `tests/test_packet_adversarial.py` for regressions.

## Invariants that must hold
INVARIANTS §1 (claim accountability; no silent disappearance), §2 (logical runs —
a claim belongs to exactly one run; run facts never borrowed), §4 (one status
policy via `core.review`), §7 (stable identities — do NOT rename rule ids,
categories, scopes, conditions, enum values, run-id format). Fail closed: when
the boundary is genuinely ambiguous the answer stays NEEDS_REVIEW, never a guess.

## Required tests
- New synthetic regression(s) that FAIL on base `c765777` for the intended reason
  (the foreign page's claim missing / wrong status), and pass after the fix.
- Targeted: `python -m pytest -q tests/qualification tests/test_packet_runs.py tests/test_packet_adversarial.py`
- Standard: `scripts/agent_validate.sh --tests "tests/qualification tests/test_packet_runs.py tests/test_packet_adversarial.py"`
- Full suite + `python -m tests.golden.baseline` must stay green.

## Corpus expectations
Behavior change to extraction/reconciliation: the private corpus gate WOULD be
required, but the owner CANNOT run it (campaign decision D-006). Report
`CORPUS GATE: NOT RUN (owner cannot run)`. Do not claim a gate pass. Do not edit
any manifest/allowlist.

## Definition of done
- [ ] `foreign-page-bridged` met; ledger updated only with evidence; no other case moves
- [ ] New test fails on base, passes on head
- [ ] `scripts/agent_validate.sh` passes (full suite + golden)
- [ ] Corpus gate explicitly NOT RUN with the reason above
- [ ] Handoff written (`docs/agent/HANDOFF_TEMPLATE.md`)

## Prohibited scope expansion
- No changes outside Allowed scope; no renamed rule ids/categories/fields.
- Do not weaken any check to reach 24/24; do not change pack truth to fit output.
- Do not touch `local_test_corpus/` or other worktrees. Adjacent problems go to
  DEFERRED ITEMS.

## Environment
- Python (all deps): the local Python 3.14 interpreter with every dependency installed (`py -3`)
- pytest basetemp is access-denied by default; pass `--basetemp=%TEMP%\kilo\pytest-b1` (fresh per run).
- `python -m tests.golden.baseline` for the ratchet.

## Handoff
Use `docs/agent/HANDOFF_TEMPLATE.md` exactly.
