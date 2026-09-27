# Fail-closed extraction and reconciliation

CLAUDE.md §2–§6 and `docs/review-invariants.md` are authoritative. These are
the guardrails that must not be broken.

- **Never silently lose, fabricate, merge or reassign a claim or an amount.**
  Incorrect association is worse than incomplete association. When a reading
  cannot be settled, refuse it and report why; do not guess.
- **Fail loud, never silently.** A field that cannot be parsed is `null` with a
  `NullReason`, never `0`. `0.00` and "no data" are different facts.
- **Every number carries provenance**, per field: page, region/row, and
  extraction method (`digital` | `vision` | `manual`). A vision-read value is
  never approximated into a region it did not produce.
- **Reconcile against what the carrier printed.** R-04 (money column sums to the
  printed footer total) and R-05 (row count equals the printed claim count) are
  the rules that sell the product. Never relax a check to raise the automation
  rate; abstention beats unsupported certainty.
- **ERROR blocks a clean export; WARN/INFO never do.** A document is `CLEAN`
  only when no ERROR finding remains. Never report a document healthier than
  the checks actually run against it.
- **Review is not reconciling.** A reviewer's decision is recorded beside a
  finding, never in place of it. Only a changed value moves a reconciliation
  result, and only after every rule ran again. Findings are immutable
  (`core/review.py`, `docs/review-invariants.md`).
- **Never train on or retain customer data.** Process in memory; delete after
  export unless the user opts in. `data/profiles/` stores structure only, never
  claim data.