# LossLift

Turns insurance loss run PDFs into clean, reconciled spreadsheets.

Small commercial brokers and MGAs re-key claims tables out of carrier PDFs by hand — 30–60 minutes per document — or pay an offshore VA to do it. Errors are expensive: a $50k misread on total incurred moves the loss ratio ~2.5 points.

LossLift extracts the claims table, ties every number back to the totals printed on the document, and flags every mismatch. The reconciliation is the point, not the extraction.

## Status

Pre-alpha. Nothing works yet.

## Stack

Python 3.11+ · Streamlit · pdfplumber · PyMuPDF · Pydantic v2 · Gemini (vision fallback only)

## Spec

See `CLAUDE.md` for the data model, extraction pipeline, and reconciliation rules.
