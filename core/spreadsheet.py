"""Stage F intake entry point — bytes and a filename in, a parsed result out.

`read_workbook` is the spreadsheet counterpart of the PDF upload path: it
stages the bytes through `core.ingest` (kind sniffed from the bytes, never
the name), runs the sheet pipeline, and returns the extraction result with
the document view (claims, refused rows, unplaced rows), the reconciliation
(findings, status) and per-sheet provenance attached.

The staged copy is removed before returning: the result carries everything
a reviewer needs, and no private document outlives the call.
"""

from __future__ import annotations

from core.ingest import discard, ingest
from core.pipeline import ExtractionResult, run_pipeline


def read_workbook(data: bytes, filename: str) -> ExtractionResult:
    """Parse an uploaded XLSX or CSV loss run from bytes.

    The bytes are staged (sniffed, hashed, size-capped), extracted, and the
    staged copy is deleted. Raises `IngestError` for anything the app cannot
    read -- empty bytes, oversize, a legacy .xls container, a filename that
    contradicts the bytes -- and never guesses its way past a refusal.
    """
    staged = ingest(data, filename)
    try:
        return run_pipeline(staged, use_vision=False)
    finally:
        discard(staged)


def read_xlsx(data: bytes, filename: str) -> ExtractionResult:
    """Read XLSX bytes; refuses anything the bytes say is not a workbook."""
    return read_workbook(data, filename)


def read_csv(data: bytes, filename: str) -> ExtractionResult:
    """Read CSV bytes; refuses anything the bytes say is not delimited text."""
    return read_workbook(data, filename)
