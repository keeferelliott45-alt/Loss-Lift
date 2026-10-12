> **Draft for counsel/broker review — not legal advice.**

# System description

A plain-language description of LossLift for a customer's vendor review. It
is arranged around the questions an insurer's AI program asks of a third-party
system under the NAIC Model Bulletin on the Use of AI Systems by Insurers
(guideline 3.2, documentation of AI systems, and 4.1, due diligence on
third-party vendors). It describes the code on `main`. It is not a statement of
compliance with the bulletin, which applies to insurers, not to vendors.

## 1. What the system does

LossLift reads commercial insurance **loss runs** (carrier claim histories,
PDF or spreadsheet) and produces a spreadsheet of the claims they contain.
Every figure is **reconciled**: checked against the totals and claim counts
the carrier printed on the same document. Every mismatch, and everything the
system could not determine, is reported to a person rather than resolved by
a guess.

It does not make, recommend or score an underwriting, pricing, claims or
eligibility decision. It does not rate risk or predict losses. A person
reviews its output and decides what to use.

## 2. Where AI is used, and where it is not

| Step | Method | AI? |
|---|---|---|
| Reading numbers, dates and text off a digital PDF | Deterministic text-layer parsing (`pdfplumber`, PyMuPDF) | **No.** No language model reads a number off a digital PDF (design principle 1, `CLAUDE.md`). |
| Deciding which printed column carries which field (column mapping) | A fixed synonym table and heuristics (`core/profiles.py`). A saved carrier profile is reused when the format was confirmed before. | **Optional, off by default.** With `LOSSLIFT_ENABLE_LLM=1` and a key, an unrecognised header row and up to three sample rows are sent to a language model, which proposes a mapping. A person confirms it on the mapping screen before it is used or saved. |
| Reading a **scanned** page (an image with no text layer) | Vision model (Google Gemini via `google-genai`, default `gemini-2.0-flash`), page rendered at 300 DPI (`core/extract_vision.py`) | **Yes, when a key is configured.** Without a key, scanned pages are not read and a rule (R-22) says so; nothing is lost silently. Every value read this way is marked `vision` and its confidence is capped at 0.85. |
| Reconciliation, status, export | Deterministic rule engine (`core/reconcile.py`, rules R-01 to R-29) and one status policy (`core/review.py`) | **No.** |

The vision path is the only one in which a model reads values, and only off
scanned images. It is treated as the least trustworthy path: its results are
labelled, capped and replayable (below), and the document cannot be marked
reconciled on the model's say-so alone.

## 3. Data flow

```
upload (PDF / XLSX / CSV / saved email)
  -> intake checks: file kind from bytes, size and page limits      core/ingest.py
  -> private temporary copy, hashed                                  core/ingest.py
  -> per page: digital text layer, or scanned image                  core/classify.py
  -> digital tables read deterministically                           core/extract_digital.py
     scanned pages -> vision model (only if configured)              core/extract_vision.py
  -> logical reports inside the file (a PDF can bind several)        core/runs.py
  -> claims built, every claim-like row accounted for                core/pipeline.py
  -> reconciliation against printed totals and counts                core/reconcile.py
  -> one status: CLEAN or NEEDS_REVIEW                               core/review.py
  -> human review and edits (originals kept beside corrections)      app.py, core/pipeline.py
  -> Excel / JSON export                                             core/export.py
  -> temporary copy deleted and the deletion verified                core/ingest.py
```

Data stays in the running process's memory and one temporary directory. There
is no database. What is kept after a session, and for how long, is in
`docs/security/data-handling.md` and `law25-pia-support-pack.md`.

## 4. Data lineage and provenance (bulletin §4 "data source, provenance, lineage")

- Every claim records the **page**, the **printed line(s)** it was read from,
  and the **method**: `digital`, `vision`, `sheet` or `manual`.
- Provenance is kept **per field**. A person correcting one amount does not
  erase where the other fields came from; the original value and the reason
  it was unreadable are kept beside the correction (`Claim.original_values`,
  `Claim.original_issues`).
- The source page can be shown with the row marked. A marked region is read
  back before it is shown and withdrawn if the claim is not inside it
  (`core/evidence.py`).
- Three kinds of fact are never mixed: what the carrier printed, what LossLift
  made of it, and what a person decided afterwards. Reviewer decisions are
  recorded beside findings and never change the reconciliation result
  (`core/review.py`, `docs/review-invariants.md`).
- The export carries the source file's SHA-256, the extraction method and the
  reconciliation status (Source Info sheet), and a Review History sheet.

## 5. Human oversight

- The status is **CLEAN** only when every check ran and passed; anything
  else is **NEEDS_REVIEW** with the reason (`core/review.canonical_status`).
  Missing data is never shown as zero.
- A document that produced no claims, a page that was not read, a claim-like
  row that could not be placed, a printed total that does not tie, or a
  boundary between two reports that cannot be settled each blocks CLEAN.
- Every cell is editable before export. Edits re-run reconciliation; a
  reviewer's approval of a finding never turns a failing check green.

## 6. Change control and validation

See `validation-summary.md`. In short: an automated test suite and a
synthetic accuracy ratchet run before changes are accepted, and changes to
extraction or reconciliation are compared document by document against a
private set of real loss runs (the corpus gate) before they merge. Recorded
vision answers can be replayed, so a scanned-page result can be reproduced
without calling the model again.

## 7. What the system does not do

No user accounts or access control; no persistence of claims beyond the
session; no automated retrieval of loss runs from carriers; no integration
with agency or policy systems; no ACORD form filling; no training of any model
on customer data.

## 8. Known limitations

- Real-document accuracy has **not yet been measured** against adjudicated
  truth. See `validation-summary.md`.
- Scanned pages depend on the vision model, which is not yet in production
  use with customer documents.
- Formats a carrier has never been seen to use may need a person to map
  columns on first sight.
