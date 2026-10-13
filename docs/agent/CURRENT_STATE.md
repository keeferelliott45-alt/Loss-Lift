# CURRENT_STATE — LossLift as of 2026-10-13

Rewrite this file when a milestone changes; do not append history (that goes
in `Remember.md`).

## Where the code is

`main` is `1077728`. Merged since the last rewrite, in merge order, each
through its PR under the watchers protocol:

| PR | Merge | What |
|---|---|---|
| #40 | `ffc01c5` | Reliability lineage carrier (E1–E14, R1, B1). Merged under a **written owner exception** to gate run 38308067802 (exit 1: 6 changed, 0 failed, no critical measure moved; recorded on #40). |
| #53 | `5db9c9b` | Underwriter triage layer `core/intelligence.py` (T-01…T-06, triage-local), fail-closed duplicate-flag merge `c5a20d7`. |
| #50 | `db1f178` | R1 labelling helper (`tools/corpus_gate/labeller/`): owner-side real-document truth. |
| #54 | `8a3e443` | Golden + fuzz expansion: ratchet 108 → **121** rows (16 formats); account/triage hazard pack. |
| #55 | `db9132b` | Per-run R-05 / `run_view` pinning (test-only). |
| #57 | `84e9605` | Fix-forward: triage smoke fixture (test-only). |
| #58 | `98ed282` | Account `dropped`: a later run printing the same claim number counts as present. |
| #59 | `1077728` | Labeller hardening (non-finite money, unknown fields, `ensure_outside`). |

### Verification on `1077728`

- **Linux** (Python 3.11): full suite **3446 passed, 0 failed**, 21 skipped,
  6 xfailed; golden **121/121** (money and text 100%), exit 0; `compileall` 0.
- **Windows**: 31 failures, all in the known set below; golden 121/121;
  `compileall` 0.
- **Fuzz** (Linux, `main`): see the 2026-10-13 cross-check PR. 0 SILENT in the PDF,
  account and triage families. The account pack on #51 shows two SILENT
  classes in `dropped` reporting (gaps below).

### Known failure set (name-stable)

A run is clean when its failures are exactly these and nothing else. Any
extra failure must **reproduce at the base commit** before it may be called
environmental. A delta of zero is the criterion, not a count.

| Platform | Tests | Why |
|---|---|---|
| Windows | `tests/test_cloud_gate*.py`: 27 | POSIX symlink privilege, `os.mkfifo`, container tooling |
| Windows | `tests/test_pdf_preflight.py` `[damaged]`: 3 | file lock on the malformed fixture |
| Windows (flaky) | `tests/test_attack_lifecycle.py::test_discard_refuses_path_outside_tempdir` | cwd-dependent: passes only when the cwd is outside the temp directory |
| Linux | none | — |

Linux note: pytest's `--basetemp` must sit inside `tempfile.gettempdir()`.
A basetemp elsewhere makes the staged-file tests fail by design, because
`discard` refuses paths outside the temp directory.

## What `main` does now

Logical runs are first-class (`core/runs.py`), reconciled per run under one
canonical status policy (`core/review.py`). Refused rows are recorded, never
folded. R-19 blocks trust, and R-28 names incomplete reports and bridged pages.
A bridged foreign page is read as its own run (B1). Exports go through one
text policy. Uploads are owner-marked and verifiably deleted. `.eml`
submissions take PDF attachments only. Packets show each run's own facts.
XLSX/CSV are read sheet by sheet as `STRUCTURAL` runs. The account rollup
feeds an optional, deterministic triage view that flags against user-set
thresholds and names what it could not evaluate. The R1 labeller can produce
the first adjudicated real-document accuracy number. Qualification: 23 of 24
cases met.

## Parked, gate-required (do not merge without the gate)

| PR | Expected corpus change | Prerequisite |
|---|---|---|
| #49 R-27 scanned count adoption | clean scanned pages NEEDS_REVIEW → CLEAN, plus an R-27 INFO record | **re-record vision answers** (the prompt hash is in `recording_key`); #46 (gate `vision` group) merged first |
| #56 pooled grand-wording count guard | pooled grand-wording rows lose a false R-05 LOUD; vision unaffected | none |
| #52 records shape ceiling | documents whose claim-number vote spans more than 4 series families → 0 claims, rows refused (R-29) | none |

Owner brief: `docs/agent/GATE_QUEUE.md`. Allowlist entries are owner-only.
Exit 0 is the only pass.

## Known gaps (2026-10-13)

Open, with pins:
- **Figureless claim row across a page break** (`pipeline.page_furniture`):
  a claim row with a number and status but no date or amount, printed last
  on one page and repeated first on the next, is dropped as furniture. With
  no printed total or count, the document reads CLEAN with the claim missing.
  Strict xfails and a verified sketch are on #51.
- **Account `dropped` misses true omissions** (`account._later_run_omits`).
  There are two classes:
  - a later run of the same policy that prints no term;
  - since #58, another carrier's claim with the same number counts as
    presence.

  Both read CLEAN, and triage T-05 stays silent. They are pinned on #51. A
  sketch is on #58 (shared class 132 → 0 SILENT, over-flags 2 → 14).
- **mixed-series-packet** (qualification 23/24): one model-read boundary
  unsettles the whole packet (`split_is_settled` is all-or-nothing), giving a
  false R-04 delta. It fails closed. Pinned on #47 with a sketch that moves
  nothing else.
- **Pooled grand-wording count** adopted as a run's count (false R-05),
  pinned as a strict xfail in `test_run_view_pinning.py`; the fix is #56.
- **Scanned path**: a clean scan reads NEEDS_REVIEW until #49. The cloud gate
  still carries no recordings.

Consistency notes, no risk:
- `core/export.py::_section_header` writes static literal headers outside
  the TextPolicy. They are literals, never document text; this is a
  consistency gap, not an injection path.
- The labeller's `_KNOWN_FIELDS` is derived from `worksheet.CLAIM_FIELDS`,
  so the two move in lockstep by construction. Keep it derived.
- The account presence scan is O(runs × claims), which is fine at loss-run scale.
- Triage `_finish` joins not-evaluated reasons in first-seen order over a
  sorted input. The output is deterministic, but it is not a stable set
  ordering across code changes.
- Per-run number format, date order, recovery sign and profile are only
  checked (borrowed-convention R-15), not applied per run.
- `tests/fuzz/run.py` keeps every rendered PDF of a run in one temp
  directory, so long `--vision` runs need chunking.

## Highest-value next tasks

1. Owner: dispatch the gate for #56 and #52. Re-record vision answers, then
   gate #49 after #46 merges.
2. Owner: run the R1 labeller over the corpus to get the first real accuracy
   number.
3. Fix the two SILENT classes pinned on #51 (furniture and account
   `dropped`), using the sketches posted there and on #58. Both are
   gate-required.
4. Apply the #47 sketch (per-run settledness) → qualification 24/24.
5. Merge the Claude-lane drafts whose Linux matrix is green once Kilo has
   reviewed them: #43 privacy scrub, #46 gate vision group, #48 compliance
   pack, #44 demo pack.

## Explicitly deferred

Per-run profiles and conventions; per-run recovery sign; analytics UI; any
spec §13 item.
