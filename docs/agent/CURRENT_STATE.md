# CURRENT_STATE — LossLift as of 2026-09-27

Rewrite this file when a milestone changes; do not append history (that goes
in `Remember.md`).

## Where the code is

- Branch `claude/packet-series-and-signed-totals` (PR #9, **draft, unmerged**),
  product code at `470b883`; agent docs/tooling committed on top. `main` is
  `7f86de8` (cloud corpus gate, PR #10).
- Tests: 2,594 collected — 2,581 pass, 7 skipped, 6 strict xfail (known gaps).
  Golden ratchet: 108/108 rows, money and text 100%.

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
   settled), recorded and never folded; R-19 blocks trust.
2. Per-run R-05 (printed count per run) works but no test pins it; one
   changed line in `run_view` would disable it silently.
3. Vision: a clean scan always reads NEEDS_REVIEW (R-27; the model's count
   is not adopted), and nothing real-corpus protects the scanned path.
4. `detect_carrier` can take a column-label line as a carrier; account
   identity then treats a repeated claim as two claims without flagging it.
5. ~~A single report whose own numbering stopped short (e.g. `Page 1 of 3`
   with pages 2-3 absent) carried no run, so nothing recorded that it was
   incomplete and it read CLEAN with part of its table missing.~~ Closed on
   this branch: `LossRunDocument.incomplete_report` from the run planner and
   R-28 (ERROR) name the missing pages (synthetic regression in
   `test_packet_runs.py`).

## Known architectural gaps

- Number format, date order, recovery sign, saved profile and column mapping
  are per document; per run they are only *checked* (borrowed-convention R-15).
- Packet document header, Source Info and Loss Summary show page 1's facts.
- Refused rows on a bounded single report are recorded but named by no rule.
- A claim-like row whose identifier cell carries a worded label before a colon
  (e.g. `Claim No: ...`) is taken for printed furniture by
  `pipeline.is_structural_row` and dropped before it is parsed, so it is
  neither a claim nor in `rows_seen_per_page`; with no printed total to
  disagree with, the document can read CLEAN. Found by this campaign's
  synthetic probe; not fixed on this branch (see the handoff).
- R-19 (row-count gap) is categorised underwriting (spec WARN) but blocks
  trust through `UNACCOUNTED_RULES`.

## Intentional limitations

- Reconcile's cross-run R-11 flags repeats even across carriers; account
  rollup keeps them apart (different questions, fail closed on each).
- A restart under a heading that names nothing is ambiguous (NEEDS_REVIEW),
  including a single report exported page by page.
- A single report read partly by text and partly by vision keeps separate
  per-reader votes (strict xfail).

## Highest-value next tasks

1. Pin per-run R-05 and per-run printed facts with tests; mutation-check
   `run_view`.
2. Make the real corpus measurable (aggregate-only gate output; merge so the
   run-aware collector runs in the cloud).
3. Scanned path: adopt the model's claim count safely (R-27); carry vision
   recordings into the cloud gate.
4. Stop column labels being read as carrier names (run facts, account
   identity).

## Explicitly deferred

Per-run profiles/conventions; per-run recovery sign; analytics UI; any
spec §13 item; Codex review of `470b883` (usage-limited; a retry is
scheduled).
