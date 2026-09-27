---
description: Run an adversarial review of a LossLift change, hunting for fail-open regressions
---
Adversarially review: $ARGUMENTS

Attack the change. Assume it is wrong and try to prove it. Report findings with
evidence (file:line, command output); do not fix anything unless asked.

Hunt specifically for:

- **False clean.** A document or row that would be reported `CLEAN` /
  AUTO-SAFE while materially wrong or incomplete. Check R-20 (empty
  extraction), R-21–R-27 and the account/packet logic.
- **Silent loss, fabrication, merge or reassignment.** A claim or amount that
  is dropped, invented, combined with another row, or attached to the wrong
  claim. Incorrect association is worse than incomplete association.
- **Provenance and evidence.** A value shown against the wrong row/page, or a
  vision value approximated into a region it did not produce
  (`core/evidence.py`).
- **Null as zero.** Any path where an unparseable value becomes `0` instead of
  `null` with a `NullReason`.
- **Reconcile vs review.** A reviewer decision that changes a reconciliation
  result without a changed value and a full rule re-run; findings mutated in
  place.
- **Weakened checks.** A lowered tolerance, a disabled rule, an edited golden
  expected output or accuracy baseline, or a gate widened to pass.
- **Vacuous tests.** Tests that pass because fixtures are empty or assertions
  are weak; a regression test that does not fail against the pre-change code.
- **Privacy/secret exposure.** Any document text, claimant data, key or token
  in code, tests, logs, commits or PR text.

For each finding, state the mechanism, the smallest reproducing case, and the
severity. If the change survives, say so and name what you tried.
