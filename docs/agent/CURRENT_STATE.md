# CURRENT_STATE — LossLift as of 2026-09-27

Rewrite this file when a milestone changes; do not append history (that goes
in `Remember.md`).

## Where the code is

- Branch `claude/beautiful-cray-np1b28` (draft PR, builds on PR #9
  `claude/packet-series-and-signed-totals` @ `4ca0d41`). `main` is `7f86de8`
  (cloud corpus gate, PR #10).
- Tests: 2,727 collected. Golden ratchet: 108/108 rows, money and text 100%.
  (The cloud-gate suite needs Docker and POSIX file types and does not run
  under Windows.)

## Recently completed (logical-run propagation cycle)

Logical runs (`core/runs.py`) with furniture-only boundaries; per-run
reconciliation; canonical status policy (`core/review.py`) used everywhere;
run-level metadata in `run_view` and exports; run-aware corpus gate
measurements (runs, refused, review_status; `run_id` in finding identity);
vision record/replay; account rollup identity + trust; local telemetry and
trust class; claim-accounting view; JSON export; corpus labels; per-run
"borrowed convention" R-15.

## Corpus gate

- 10 real documents (corpus-v1). Inferred: 2 packets, 8 single reports.
- main → `470b883`: 2 changed (both packets: per-run printed facts and
  findings; one also a folded description line), 8 unchanged, no claim-count
  or engine-status change anywhere. Owner has not yet confirmed the two
  packets' run splits locally.
- `51789cc` → `470b883` (the run-propagation cycle): **no change** on any
  document.
- The cloud workflow runs main's collector: run-aware measurements, the
  canonical review status and vision replay take effect there only after
  merge. Cloud runs have vision off. No real-corpus rates exist yet (the gate
  publishes changes, not values).

## Test coverage audit (2026-09-27, read-only)

Grades: A strong · B reasonable · C weak · D effectively unprotected.

| Invariant | Grade |
|---|---|
| Claim belongs to exactly one run | B |
| No silent claim disappearance | **D** (see blind spot 1) |
| Packet/run separation | B |
| Unsettled boundaries (R-28) | A |
| Refused rows | C |
| Unplaced rows (R-23) | A |
| Run-specific metadata | B |
| Printed count reconciliation (R-05) | B single / **C per run** |
| Printed totals reconciliation (R-04) | A |
| Signed financial semantics | B |
| Duplicates without wrong merging | B |
| Canonical status consistency | A |
| One unsafe run → packet unsafe | A |
| Unsafe data kept out of trusted account | B |
| Digital extraction | A |
| Vision / replayed vision | C |
| Provenance | B |
| Edits / review resolution | A |
| Single-run backward compatibility | A |

Catastrophic blind spots found:
1. ~~A claim row can vanish into another claim's description and the
   document reads CLEAN.~~ Closed after `2e62e69` (Codex P1s): a refused row
   is judged under the settled date order (either order when none is
   settled), recorded and never folded; R-19 blocks trust. R-19 is now
   categorised `extraction` (spec WARN severity unchanged), so it blocks under
   the one policy for reading problems instead of through a category carve-out.
2. ~~Per-run R-05 (printed count per run) works but no test pins it; one
   changed line in `run_view` would disable it silently.~~ Closed on this
   branch: `tests/test_run_view_pinned.py` pins R-04/05/06/09/19/29 per run
   and every field `run_view` hands a run; deleting any one of its 20 lines
   fails a test (mutation-checked).
3. Vision: a clean scan always reads NEEDS_REVIEW (R-27; the model's count
   is not adopted), and nothing real-corpus protects the scanned path.
4. ~~`detect_carrier` can take a column-label line as a carrier; account
   identity then treats a repeated claim as two claims without flagging it.~~
   Closed on this branch: a line naming three or more column labels, or
   carrying a date or an amount, is never a carrier candidate (no carrier
   beats a wrong one; `test_profiles.py`).
5. ~~A single report whose own numbering stopped short (e.g. `Page 1 of 3`
   with pages 2-3 absent) carried no run, so nothing recorded that it was
   incomplete and it read CLEAN with part of its table missing.~~ Closed on
   this branch: `LossRunDocument.incomplete_report` from the run planner and
   R-28 (ERROR) name the missing pages (synthetic regression in
   `test_packet_runs.py`).
6. ~~A claim whose identifier cell carries its own label (`Claim No: ...`)
   was taken for furniture by `pipeline.is_structural_row` and dropped from
   both the claims and `rows_seen_per_page`, so with no printed total the
   document read CLEAN with the claim missing.~~ Closed on this branch:
   `records.strip_identifier_label` removes a closed vocabulary of
   claim-number labels before every identifier read (shape vote, refusal,
   record anchors, `leading_identifier`); every other colon label stays
   furniture (`tests/test_labelled_identifiers.py`).
7. ~~A claim-like row refused on a bounded single report was recorded but
   named by no rule.~~ Closed on this branch: R-29 (ERROR) names it with
   condition `bounded` (`test_refused_row_fold.py`).

## Known architectural gaps

- Number format, date order, recovery sign, saved profile and column mapping
  are per document; per run they are only *checked* (borrowed-convention R-15).
- Packet document header and the claim rows' denormalised document facts still
  show page 1's. Loss Summary is per run, and Source Info now shows a fact only
  when every run agrees (`_agreed`), otherwise "differs by run — see Runs
  sheet", with the printed claim count summed only when every run printed one
  (`_printed_claim_count`).

## Intentional limitations

- Reconcile's cross-run R-11 flags repeats even across carriers; account
  rollup keeps them apart (different questions, fail closed on each).
- A restart under a heading that names nothing is ambiguous (NEEDS_REVIEW),
  including a single report exported page by page.
- A single report read partly by text and partly by vision keeps separate
  per-reader votes (strict xfail).

## Highest-value next tasks

1. Make the real corpus measurable (aggregate-only gate output; merge so the
   run-aware collector runs in the cloud).
2. Scanned path: adopt the model's claim count safely (R-27); carry vision
   recordings into the cloud gate.
3. Per-run conventions (number format, date order, recovery sign) instead
   of per-document ones checked per run.

## Explicitly deferred

Per-run profiles/conventions; per-run recovery sign; analytics UI; any
spec §13 item; Codex review of `470b883` (usage-limited; a retry is
scheduled).
