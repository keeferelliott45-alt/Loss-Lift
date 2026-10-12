# CURRENT_STATE — LossLift as of 2026-10-12

Rewrite this file when a milestone changes; do not append history (that goes
in `Remember.md`).

## Where the code is

- `main` is `15cf2b4`. Merged since 2026-09-27: #9 and #12 (packet series,
  signed totals, bridged page; #19 merged into #9's branch first), #37
  (packet export privacy: #18's content including #20's submission
  workspace), #38 (D1 per-run facts) and #39 (F: CSV/XLSX intake).
- Tests on `main` (Linux): 3070 passed, 21 skipped, 6 xfailed, 0 failed.
  The golden ratchet is 108/108 rows, with money and text at 100%. On
  Windows, 27 `tests/test_cloud_gate*` tests fail for environment reasons
  (docker, symlink, mkfifo). They fail identically on a clean base and are
  not a regression signal.
- **Reliability lineage, not on `main`.** Branch
  `agent/kilo/b1-foreign-page-bridged` carries all of
  `agent/lead/reliability-phase-1`: #13, #16, #17, then E1–E14 and R1
  (#22–#36). On top of that it adds B1, which reads a bridged foreign page
  as its own run and refuses rows on a blind page that names another
  report. The branch has `main` merged in (head `5303b4e`). Its draft
  carrier PR, #40, is **blocked on the owner-run corpus gate**: E8 fixes a false CLEAN, B1
  reclassifies bridged pages, and E10a changed the no-losses document. On
  that head, qualification is 23 of 24 met, and fuzz smoke and golden pass.

## What `main` does now

Logical runs (`core/runs.py`) are first-class. Reconciliation runs per run,
under one canonical status policy (`core/review.py`). A refused row is
recorded and never folded into another claim. R-19 blocks trust. R-28 names
an incomplete report's missing pages, and a bridged unnumbered page is named
(#12). Exports go through one text policy (`core/xlsx_safety.py`), which
stores formula payloads as quoted text, with whole-package redaction.
Staged uploads are owner-marked and verifiably deleted (#37). Saved `.eml`
submissions are read with PDF attachments only, through the submission
workspace. Packets show each run's own facts in Source Info and Loss
Summary (#38). XLSX and CSV uploads are read sheet by sheet: each visible
worksheet is its own `STRUCTURAL` run, and formulas are never evaluated
(#39).

## Known gaps (re-audited 2026-10-12)

Closed since the last audit:
- XLSX/CSV ingest: #39 (on `main`).
- Column labels taken as the carrier, and `Claim No:`-labelled rows dropped
  as furniture: E2 (on the carrier).
- A foreign page bridged into a report: named by #12 (on `main`), read as
  its own run by B1 (on the carrier).
- A refused row on a bounded single report named by no rule: #17 (on the
  carrier).
- An incomplete single report reading CLEAN: R-28 (#9, on `main`).

Still open:
- Per-run R-05 (printed count per run) works but is not pinned by a test.
  One changed line in `run_view` would disable it silently.
- Scanned path: a clean scan always reads NEEDS_REVIEW, because the model's
  claim count is not adopted (R-27). Nothing protects the vision path on the
  real corpus.
- Recovery sign, number format, date order and profile are per document.
  Per run they are only checked (borrowed-convention R-15).
- The packet's document-level header rows still show page 1's facts. The
  per-run blocks from #38 sit beside them.
- R-19 is categorised underwriting but blocks trust (#15 proposes
  `extraction`).
- Digital pages that are laid out as tables but yield no table are not
  accounted for. That fix is open as #4 (conflicting, needs a gate run).
- Qualification `mixed-series-packet`: a vision-read second run keeps its
  printed totals at document level. It fails safe (over-flagged).

## Intentional limitations

- Reconcile's cross-run R-11 flags repeats even across carriers, while the
  account rollup keeps them apart. These are different questions, and each
  fails closed.
- A restart under a heading that names nothing is ambiguous (NEEDS_REVIEW).
- Email attachments are accepted only as PDFs. An emailed XLSX is rejected
  and named.

## Highest-value next tasks

1. Owner: run the cloud corpus gate on #40's head, explain every changed
   document, and then merge the carrier.
2. Pin per-run R-05 and per-run printed facts with tests; mutation-check
   `run_view`.
3. Adjudicated real truth via the R1 labelling helper (needs the owner's
   corpus). Without it, no real-corpus accuracy figure exists.
4. Scanned path: adopt the model's claim count safely (R-27), and carry
   vision recordings into the cloud gate.
5. After the carrier lands: rebase #4 (unread table pages) and decide what
   remains of #15.

## Explicitly deferred

Per-run profiles and conventions; per-run recovery sign; analytics UI; any
spec §13 item.
