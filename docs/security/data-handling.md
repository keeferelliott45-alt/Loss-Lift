# Data handling and security — what LossLift does today

The factual basis for a pilot security questionnaire, a privacy impact
assessment or a data processing agreement. Each statement describes the code
on `main` (2026-10-12) and names where it lives. This is not a legal
document and has not been reviewed by counsel. **Pilot blockers** are listed
first.

## Pilot blockers (resolve before any customer's real documents)

1. **The external model's terms.** Two features send document content to
   Google's Gemini API (`GEMINI_API_KEY`):
   - Column mapping for an unknown format sends the header row and up to
     three sample data rows, which can carry claim numbers, amounts and
     names (`core/profiles.py`, `LLM_SAMPLE_ROWS`). It is off unless
     `LOSSLIFT_ENABLE_LLM=1`.
   - Vision reads scanned pages by sending the whole page image, rendered at
     300 DPI (`core/extract_vision.py`).

   The spec's "free tiers only" stack (§8) means the free Gemini tier, where
   Google's terms may allow submitted content to be used to improve its
   products. That contradicts design principle 6 (never train on or retain
   customer data) and any DPA a pilot will ask for. Check Google's current
   terms. Before real data, either use a paid tier whose terms exclude
   training and retention, or run with the LLM and vision off. With vision
   off, a scanned page is not read and R-22 flags it, so nothing is lost
   silently.
2. **Hosting and residency.** The planned host is Streamlit Community Cloud
   (§8). It offers no choice of region, no DPA LossLift can sign, and no
   residency guarantee. That blocks Quebec Law 25 and most MGA procurement.
   A pilot should run on infrastructure the operator controls, or locally on
   the customer's machine, until a region-pinned deployment exists.
3. **Redaction defaults differ by export.** The submission workbook is
   redacted by default (`app.py`, *Redact identifying data*, on). The
   per-document, batch and account workbooks are **not**: their *Remove
   claimant names* toggles default to off. Decide one default before a
   pilot, and say it in the DPA.

## What is stored, where, and for how long

| Data | Where | Retention |
|---|---|---|
| Uploaded file bytes | A private temporary directory per upload, marked as LossLift's own (`core/ingest.py`) | Deleted, and the deletion verified, on export, on *Delete*, or on removal from the queue. Orphans from an interrupted session are swept at the next startup after 24 hours. A closed tab alone does not delete them. |
| Extracted claims, findings, review decisions | The Streamlit session's memory | Until the session ends. No database (spec §13). |
| Carrier profiles | `data/profiles/` (git-ignored) | Kept. **Structure only**: column labels, formats and carrier name, enforced by an explicit whitelist in `core/profiles.py`. No claim data. |
| Telemetry | `data/telemetry/events.jsonl` (git-ignored) | Kept locally and never sent. Every event is schema-checked: counts, flags and fixed tokens only, with no names, numbers, amounts, dates, file names or hashes. Turn it off with `LOSSLIFT_TELEMETRY=off` (`core/telemetry.py`). |
| Exported workbooks | The user's own download | Outside LossLift. |

## Processing safeguards already in code

- **Intake.** File kind is decided from the bytes, not the name. Legacy and
  macro Excel formats are refused. Spreadsheet formulas are never evaluated.
  Uploads have a size cap (`core/ingest.py`, `core/extract_sheet.py`).
  Email intake accepts PDF attachments only and names every other
  attachment (`core/eml_intake.py`).
- **Exports.** Every cell goes through one text policy. Formula-like text is
  stored as quoted text, and control characters are replaced and counted
  (`core/xlsx_safety.py`).
- **Numbers.** Money is never read by a language model on a digital PDF
  (design principle 1). Vision-read values are capped at 0.85 confidence and
  marked.
- **Real documents in development.** The real corpus lives outside the
  repository, as releases of a private repository. The corpus gate runs
  candidate code without network access or credentials. Its output carries
  ids, counts and keyed digests, never document text
  (`docs/cloud-corpus-gate.md`).

## Not yet in place (for questionnaires; answer "no" honestly)

User accounts and authentication; access control between users; encryption
at rest beyond the host's own; audit logging of who viewed what; SOC 2 or
ISO 27001; a signed DPA template; Tech E&O and cyber insurance; region-pinned
hosting; incident response and breach notification procedures.
