# Product invariants

Derived from `CLAUDE.md` (authoritative) and `docs/review-invariants.md`. Any
change must preserve every one of these. If a change cannot, stop and ask.

1. **One spec.** `CLAUDE.md` is authoritative. These invariants summarize it;
   they never override it.
2. **Accountability first.** LossLift is a buyer-neutral trusted loss-history
   engine. Every logical run, claim and financial amount must be explicitly
   accountable and source-verifiable.
3. **Fail closed.** Never silently lose, fabricate, merge or reassign a claim or
   an amount. Incorrect association is worse than incomplete association. When a
   reading cannot be settled, refuse it and report why.
4. **A null is never a zero.** An unparseable value is `null` with a
   `NullReason`, never `0`. `0.00` and "no data" are different facts.
5. **Provenance per field.** Every number carries page, region/row and method
   (`digital` | `vision` | `manual`), answered per field so editing one cell does
   not cost another its evidence. A vision value is never approximated into a
   region it did not produce (`core/evidence.py`).
6. **Reconcile against what the carrier printed.** R-04 (money columns tie to the
   printed footer total) and R-05 (row count equals printed claim count) are the
   product. The printed identity keeps recoveries in it (R-01), and a total is
   only used for the claims it is a total of.
7. **Severity decides the badge.** ERROR blocks a clean export; WARN and INFO
   never do. A document is `CLEAN` only with no ERROR finding. Never report a
   document healthier than the checks run against it — false-clean is the defect
   that matters.
8. **Review is not reconciling.** Findings are immutable; a reviewer's decision
   is recorded beside a finding, never in place of it. Only a changed value moves
   a reconciliation result, and only after every rule ran again. Findings carry
   `scope`, `category` and an explainable `subject`.
9. **Numbers are never read by an LLM on a digital PDF.** For text-layer PDFs the
   money comes from deterministic parsing; the model maps structure. Vision is a
   flagged fallback for scanned pages only, capped at 0.85 confidence.
10. **Learn per carrier; store structure only.** Profiles are saved format
    structure (labels, formats, carrier), never claim data, enforced by an
    explicit whitelist that raises rather than dropping extra fields.
11. **No retention by default.** Process in memory; delete uploads after export
    unless the user opts in. Never train on customer data.
12. **Scope discipline.** Do not build from `CLAUDE.md` §13, and do not add
    ACORD/SOV ingestion, carrier portals, BMS integrations or underwriting rules
    without customer evidence.
13. **Measurement objective.** Track the AUTO-SAFE (`CLEAN`) vs NEEDS_REVIEW vs
    unresolved rate by document class. Priorities come from corpus frequency and
    severity.

Verification: `python -m pytest`, `python -m tests.golden.baseline`,
`python -m tests.golden.report`, and (for extraction/reconciliation changes) the
corpus gate. See `AGENTS.md`.
