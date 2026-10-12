# INVARIANTS — what must stay true

Each invariant says what is **ENFORCED TODAY** (code + tests exist) and what is
**DESIRED / NOT YET FULLY ENFORCED**. Do not describe the second as the first.
Coverage grades and blind spots per invariant: the coverage audit in
`docs/agent/CURRENT_STATE.md` (§ Test coverage audit).

## 1. Claim accountability

**Enforced.**
- Every row with a well-formed identifier and a parsed loss date or status of
  its own is either read as a claim or recorded in
  `LossRunDocument.refused_claim_rows` — never only folded into another claim
  (`pipeline.build_claims`, `_note_refusal`, which reads dates under the
  settled order or, with none settled, either order; property test
  `test_packet_runs.py::test_every_claim_like_row_becomes_a_claim_or_is_recorded`).
- A row carrying money under mapped money columns that no claim took is an
  `UnplacedRow` and raises R-23 (`test_unplaced_money.py`).
- A continuation line is folded only into a claim of its own run (`_same_run`).
- Unread source pages (failed / unresolved / partly recognised pictures) raise
  R-22; no claims at all raises R-20 (ERROR).
- A null is never a zero (`NullReason`, R-15).

**Desired / not yet.**
- Refused rows on a single report whose claim-number vote was bounded are
  recorded, shown in claim accounting, measured by the gate and exported —
  but no rule names them, so they alone do not block trust.
- Text-only rows with no money and no claim data become warnings, not
  findings.

## 2. Logical runs

**Enforced.**
- Boundaries come only from page furniture (top/bottom 15% of the crop box,
  table rows excluded) and vision labels placed in header/footer whose text
  restates the numbers (`extract_digital.page_evidence`,
  `extract_vision._page_label`, `runs.vision_evidence`).
- For a packet, each claim belongs to exactly one run by its source page
  (`LogicalRun.holds`, `LossRunDocument.run_claims`;
  `test_every_claim_and_total_belongs_to_exactly_one_run`,
  `test_binding_reports_into_a_packet_loses_no_claim`).
- An unsettled boundary is an ambiguous run with page range and evidence;
  R-28 (ERROR) names it and the refused rows on it. A numbering restart is a
  section only when the naming words are equal; a heading that settles
  nothing is ambiguous, never a silent section.
- A single report has `runs == []` and is reconciled as one loss run. If its
  own numbering stops before its declared last page, the pages it says it has
  and the PDF lacks are recorded on the document (`incomplete_report`) and
  R-28 (ERROR) names them, so it no longer reads clean with part of its table
  absent (`test_packet_runs.py::test_a_lone_report_that_stops_before_its_last_page_is_not_clean`).
- Run facts (carrier, insured, policy, term, line, valuation, printed totals
  and count) come from the run's own pages; `run_view` never falls back to
  another run's letterhead (`test_run_metadata.py`).

**Desired / not yet.**
- Number format, date order, recovery sign, saved profile and column mapping
  are still settled once per document. A run that relies on another report's
  convention is only *detected* (`LogicalRun.borrowed_conventions` → run-scoped
  R-15 WARN), not re-inferred per run.
- `detect_carrier` can take a column-label line as a carrier name when no
  letterhead prints one.
- The packet-level document fields (carrier, valuation, policy) are page 1's
  and still shown as the document's in the app header and Source Info.

## 3. Reconciliation against what was printed

**Enforced.**
- R-04 compares column sums to printed totals, per run for packets, and only
  against a total of *these* claims (a totals row naming another claim count
  is not used). R-05 compares claim count to printed count, per run.
- R-01 identity `paid + reserve - recovery == incurred`; component groups
  only when complete. Money is `Decimal`; tolerance per profile.
- Signed printed totals (`-$1,234`, `($1,234.00)`) are read; recoveries'
  sign convention is inferred and applied to printed totals too
  (`test_signed_currency_amounts.py`).
- Unreadable printed totals raise R-26; counts that do not scope the report
  raise R-27.

**Desired / not yet.**
- Recovery sign is inferred per document, not per run.
- A claim count read by the vision model is not adopted as the document's
  printed count, so a clean scan raises R-27 (pre-existing).

## 4. Status and trust

**Enforced.**
- One policy: `core.review.canonical_status` / `canonical_run_status`.
  NEEDS_REVIEW on any financial or extraction finding at any severity, any
  ERROR, any `UNACCOUNTED_RULES` finding, any run not clean, a mapping to
  confirm, or no reconciliation.
  App queue/pill/card, workbook Source Info and Runs sheets, runs overview,
  claim accounting, JSON export, telemetry and the gate all use it
  (`test_status_policy.py`).
- The engine's `ReconciliationResult.status` is ERROR-only (spec §6) and is
  one input to the policy, never a substitute.
- One unsafe run makes the packet NEEDS_REVIEW; a finding with no run applies
  to every run.
- Review is not reconciliation: resolutions never remove findings or change
  status (`test_review_*`, `docs/review-invariants.md`).

## 5. Identity and duplicates

**Enforced.**
- Within a document/run: R-11 (same number, same page) ERROR; R-12 (same
  number across pages) WARN. Across runs of a packet: same number *and* loss
  date in two runs is R-11 ERROR, even across carriers (fail closed).
- Account rollup merges two appearances only when both runs name the same
  carrier and the same policy (number, or the term covering the loss);
  different carriers/policies are distinct claims; unknown carrier or policy
  is kept apart and flagged `uncertain` (`test_account_safety.py`). An
  appearance joins a history only when it is the same claim as *every*
  appearance already in it (`account._joins`): a run naming no policy number
  cannot bridge policy A and policy B.

**Desired / not yet.**
- The two layers deliberately differ (reconcile flags cross-carrier repeats;
  account keeps them apart). A garbage carrier string makes account treat a
  repeat as distinct without flagging it.

## 6. Account rollup

**Enforced.** Every source is a logical run with its own facts and canonical
status; claims from an unclean run stay in the history, marked; the account is
NEEDS_REVIEW with reasons whenever any source is not clean, an identity is
uncertain, or no reconciliation was supplied.

## 7. Provenance and edits

**Enforced.** Claims carry `source_page`, `source_row`, `source_lines`,
`source_bbox`, `source_method`/`read_method`, per-field confidence, raw cells
and original values. Evidence regions are read back before shown
(`core/evidence.py`). Edits go through `pipeline.edit_claims` /
`resolve_finding`; every edit and decision is an append-only `ReviewLog`
entry; every rule re-runs over edited values.

## 8. Digital and vision

**Enforced.** Vision numbers are transcribed, parsed by the same deterministic
parser, confidence capped at 0.85 and marked `vision`. Vision tables keep page
identity and feed the same run planner. Recorded vision answers replay
deterministically; a missing recording is a failed page, not an empty one.

**Desired / not yet.** The cloud corpus gate runs with vision off and has no
recordings, so scanned pages are measured as unread there.

## 9. Corpus regression

**Enforced.** Any change in claim count, engine status, canonical review
status, page accounting, printed evidence, findings (identity includes
`run_id`), claims, metadata, warnings, runs or refused rows fails the gate.
Only an exact allowlist entry approves a change. Absent `runs`, `runs=[]` and
a single report measure alike. Output never carries document text.

**Desired / not yet.** The cloud workflow runs main's collector, so the
run-aware measurements take effect in the cloud only after merge. No
aggregate (rates) output exists for the real corpus.

## 10. Privacy

**Enforced.** Profiles are structure-only (whitelist); telemetry strings must
be in an explicit vocabulary; gate output is shape-checked; manifests, labels
and recordings must live outside the repository (enforced by the tools).
