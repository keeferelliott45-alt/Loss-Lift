# CURRENT_STATE — LossLift as of 2026-09-30

Rewrite this file when a milestone changes; do not append history (that goes
in `Remember.md`).

## Where the code is

- `main`: PR #9 (logical runs, `ff76bfc`) and PR #12 (bridged foreign page,
  `c7de782`) are merged.
- `agent/lead/reliability-phase-1` is the phase-1 integration branch
  (draft, not merged to `main`). It holds:
  - B0 `0f13c5c`: the submission workspace plus PRs #12, #13, #17 and #16;
  - the six phase-1 tasks E1–E6 (below), integrated in order.
  Its head is F1.
- PRs #13, #16, #17 and #18 are still open against `main`. Their code is in
  B0.
- Tests on F1:
  - the full suite passes (see the F1 handoff for the counts);
  - golden ratchet 108/108 rows, money and text 100%;
  - 5 strict xfails remain (known gaps).

## Reliability phase 1 (integrated on F1)

| Task | What changed |
|---|---|
| E1 Q1 | `tools/qualification`: truth v1, `score_result`, the `validate`/`run` CLI (committed checkout only, offline, sealed public report), the frozen `build_cases` interface. Docs: `docs/qualification.md`. |
| E2 L1 | A claim printed as `Claim No: X` is read, not dropped as furniture (closed label vocabulary). A column header or claim row is never taken for the carrier. |
| E4 M1 | Within an established report (a settled run, or a single report whose every page prints its own numbering), the digital and vision readers share one claim-number vote. The bounded P1-3 xfail is fixed. |
| E3 | One PDF preflight on every intake path (direct, path, email): empty, over 64 MB, not a PDF, damaged, no pages, opening password, over 1000 pages. It uses fixed reason codes. Owner-only encryption is allowed. |
| E5 / E6 | Two synthetic packs of 12 cases each, with truth authored from construction. Both are registered in `tests/qualification/registry.py` and pinned by `pack_ledger.json`. |

## Qualification status

**Packs on F1: 19 of 24 cases met.** The five that are not met are
ledgered, and they are the top of Phase 2:

1. **False CLEAN** (`policy-change-boundary`). Two unnumbered reports under
   one generic heading, told apart only by their printed policy numbers, are
   merged into one report and auto-accepted. One policy's claims are then
   filed under the other.
2. **Over-review of real-shaped packets** (`two-series-digital-packet`,
   `mixed-series-packet`, `run-counts-packet`).
   - Each carrier's page prints the same named insured, so the headings
     "partially overlap" and run 2 is left unsettled (R-28).
   - Printed totals and counts then stay at document level and raise false
     R-04, R-25 and R-27 findings.
   - The result is safe, but it will be the common case on real packets.
3. **Foreign page inside a report** (`foreign-page-bridged`). The page is
   split off, but its claim is named (R-23/R-28) rather than read.

**Real documents:** no adjudicated truth exists yet. The 10-document corpus is
unlabelled, so no real accuracy figure exists, and the charging thresholds in
CLAUDE.md §10 are unmeasured.

## Corpus gate

- corpus-v1 has 10 real documents: 2 packets and 8 single reports.
- `main` 7f86de8 → B0 lineage: 179 approved corpus-gate differences, all from
  PR #9 (R-28 naming, run-tagged findings, claim-free text as warnings). Claim
  counts, statuses, money and printed figures are unchanged.
- E3 was measured separately: B0 → `bc7bf58`, PASS 10/10 unchanged.
- E2 and E4, and B0 → F1 as a whole, need the owner's local runs. Approvals
  never transfer to new commits.
- The cloud workflow runs `main`'s collector. Cloud runs have vision off.

## Known architectural gaps

- **Per-document conventions.** Number format, date order, recovery sign,
  profile and column mapping are per document; per run they are only checked
  (the borrowed-convention R-15).
- **Page-1 facts in packets.** The packet document header, Source Info and
  Loss Summary show page 1's facts.
- **Refused rows on a single report.** On a bounded single report they are
  recorded but named by no rule.
- **R-19.** It is categorised as underwriting (the spec's WARN) but blocks
  trust through `UNACCOUNTED_RULES`.
- **Scanned reports.** A clean scan that prints several counts reads
  NEEDS_REVIEW (R-27; the model's count is not adopted). Nothing on the real
  corpus protects the scanned path until vision recordings exist.
- **Truth v1.** It scores claims, critical fields, runs, printed facts and
  status. It does not score document facts (carrier, insured, valuation).

## Intentional limitations

- Cross-run R-11 flags repeated claim numbers even across carriers; the
  account rollup keeps them apart.
- A restart under a heading that names nothing is ambiguous (NEEDS_REVIEW).
- The unnumbered mixed-reader report keeps per-reader votes
  (`test_packet_adversarial.py::test_d1`, strict xfail).

## Highest-value next tasks (the approved plan)

1. **Phase 0 exit.**
   - The owner runs the corpus gates: E2 (Q1 → L1), E4 (L1 → M1) and
     B0 → F1.
   - Fix the policy-change false CLEAN. Phase 0 cannot exit with a false
     CLEAN.
2. **Phase 1.**
   - Build a blind labelling helper, then label the 10 documents (packets
     first) and run the first real qualification report.
   - Grow the corpus to 30–50 documents.
   - Produce aggregate-only gate output.
3. **Phase 2:**
   - fix the shared-insured run boundary;
   - read the claim on a foreign page;
   - add per-run packet facts;
   - make R-19's category consistent;
   - fix whatever the real qualification report finds.
4. **Then:**
   - Phase 3: scanned path;
   - Phase 4: per-run conventions;
   - Phase 5: XLSX/CSV with cell provenance.

   UI, connectors and billing wait for the release gate.

## Explicitly deferred

These wait until the release gate:
- any spec §13 item;
- analytics UI;
- live inbox connectors;
- XLS and standalone images.
