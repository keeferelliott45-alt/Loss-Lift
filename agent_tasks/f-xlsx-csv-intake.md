# Task: Stage F — CSV/XLSX intake with carrier-agnostic header mapping

## Objective
Accept CSV and XLSX uploads through the same intake path as PDFs, import each
visible worksheet as a separate source (never auto-combined), preserve row/cell
provenance, never execute formulas (use a cached result only, else flag for
review), and never silently turn an ambiguous/missing/unreadable value into zero
or a clean result. Synthetic fixtures only in git.

## Why it matters
Two real owner-authorized XLSX samples fail today with `IngestError` at
`core/ingest.py:194-197`; the uploader is PDF-only (`app.py:721-727`). Real
workbooks also carry numeric-looking text (4,660 / 166 cells) that must be parsed
deterministically, and empty sheets that must be recorded, not treated as
"no claims → clean".

## Allowed scope
- `core/ingest.py` — kind sniffing (PDF magic, ZIP containing `[Content_Types].xml`
  = xlsx, delimited UTF-8 text = csv), stage with the right extension, keep the
  size cap and change detection.
- New `core/extract_sheet.py` — openpyxl `read_only=True, data_only=True`; per
  visible non-empty sheet produce a header row + cell grid feeding the existing
  `build_mapping`/`build_claims` path; `source_method` marked for sheets; formula
  cell with no cached value → null + finding (never evaluated, never zero).
- `core/pipeline.py` — minimal routing so a sheet source binds as its own logical
  run (`run-N`); do not change PDF extraction/reconciliation semantics.
- `app.py` — uploader accepts pdf/xlsx/csv; rejection path reused.
- Tests: synthetic openpyxl-built fixtures + in-memory CSV bytes.

## Likely files
- `core/ingest.py`, new `core/extract_sheet.py`, `core/pipeline.py` (routing only),
  `app.py`, `core/profiles.py` (reuse mapping), `core/schema.py` (provenance fields).

## Invariants that must hold
§1 fail closed (unreadable/ambiguous cell → null + reason + NEEDS_REVIEW, never
zero); §1 no silent row disappearance (every claim-like row read or recorded as
refused/unplaced); §2 each visible sheet is a separate source/run, facts from its
own cells; §4 one status policy (canonical status only); §7 stable identities;
§10 never execute formulas; provenance per row/cell. Hidden sheets ignored but
recorded.

## Required tests (synthetic, openpyxl/in-memory only)
- ingest accepts xlsx (zip magic) and csv; rejects extension/magic mismatch and
  unknown magic (e.g. legacy .xls BIFF) fail-closed.
- two visible sheets → two sources/runs; empty visible sheet recorded not zero;
  hidden sheet ignored but recorded.
- formula with cached value uses the cache; formula without a cached value is
  null + finding + NEEDS_REVIEW.
- numeric-looking text parsed by the deterministic normalize parsers.
- duplicate/missing/malformed headers flagged, not guessed.
- claim-like row becomes a claim or a refused record (accountability).
- provenance + deterministic re-read (replay) reproduce the claims.

## Corpus expectations
Additive input kind; PDF extraction/reconciliation semantics unchanged. Private
corpus gate NOT required for the intake feature itself (the gate measures PDFs) —
state the reasoning. Do not claim a gate pass. Synthetic fixtures only in git.

## Definition of done
- [ ] CSV + XLSX intake works on synthetic fixtures; per-sheet sources; provenance
- [ ] Formula cells never executed; no silent zero; fail-closed on ambiguity
- [ ] New tests red-first then green; full suite + golden green
- [ ] Gate not required (reason recorded); no manifest/allowlist/golden edits
- [ ] `docs/agent/CURRENT_STATE.md` updated
- [ ] Handoff written

## Prohibited scope expansion
- No rule-id/field/enum renames; no PDF extraction/reconciliation behaviour change;
  do not touch `local_test_corpus/` or other worktrees; no `.xls`/`.xlsm` support
  (fail closed); no real workbook bytes committed.

## Environment
- Python: the local Python 3.14 interpreter with every dependency installed (`py -3`)
- pytest basetemp: `--basetemp=%TEMP%\kilo\pytest-f` (fresh per run)
- Golden: `python -m tests.golden.baseline`

## Handoff
Use `docs/agent/HANDOFF_TEMPLATE.md`.
