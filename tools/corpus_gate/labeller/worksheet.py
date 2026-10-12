"""Adjudication worksheets: the extraction laid out for a person to judge.

One worksheet per document, one row per claim-field. The row carries the
extraction's value, the provenance a reviewer needs to find the cell it came
from, and a blank TRUTH column. Nothing is inferred on the owner's behalf:
a blank TRUTH cell means "not adjudicated", everywhere, always.
"""

from __future__ import annotations

import csv
from pathlib import Path

from core.pipeline import ExtractionResult
from core.review import canonical_status
from core.schema import (
    DATE_FIELDS,
    MONEY_FIELDS,
    Claim,
    SourceMethod,
)

#: Header of every worksheet. The TRUTH column is the only one a person
#: writes; every other column is the extraction's own audit trail.
WORKSHEET_COLUMNS: tuple[str, ...] = (
    "doc_id",
    "claim_index",
    "claim_number",
    "field",
    "kind",
    "extracted_value",
    "TRUTH",
    "page",
    "row_id",
    "source_lines",
    "method",
    "null_reason",
)

#: The claim fields a worksheet adjudicates, in reading order: identity and
#: dates first, then status, then text, then money, then the flag. The same
#: field set the golden harness scores, so the numbers are comparable.
CLAIM_FIELDS: tuple[str, ...] = (
    "claim_number",
    "date_of_loss",
    "date_reported",
    "close_date",
    "claim_status",
    "claimant_name",
    "claimant_ref",
    "loss_state",
    "body_part",
    "nature_of_injury",
    "ncci_class_code",
    "cause_of_loss",
    "loss_description",
    *MONEY_FIELDS,
    "litigation_flag",
)

#: The one document-level row: the extraction's own verdict, which the owner
#: confirms or overturns, feeding the NEEDS_REVIEW cross-tab.
DOCUMENT_STATUS_FIELD = "document_status"

# Field kinds. Money and text are the two reported accuracies; the rest are
# adjudicated with their own vocabularies.
MONEY = "money"
TEXT = "text"
DATE = "date"
STATUS = "status"
BOOL = "bool"
DOC_STATUS = "doc_status"


def field_kind(field_name: str) -> str:
    """Which vocabulary a field's truth is judged against."""
    if field_name in MONEY_FIELDS:
        return MONEY
    if field_name in DATE_FIELDS:
        return DATE
    if field_name == "claim_status":
        return STATUS
    if field_name == "litigation_flag":
        return BOOL
    if field_name == DOCUMENT_STATUS_FIELD:
        return DOC_STATUS
    return TEXT


def _extracted_text(claim: Claim, field_name: str) -> str:
    """A field's extracted value, as the golden harness prints it."""
    value = getattr(claim, field_name, None)
    if value is None:
        return ""
    kind = field_kind(field_name)
    if kind == MONEY:
        return f"{value:.2f}"
    if kind == DATE:
        return value.isoformat()
    if kind == STATUS:
        return value.value
    if kind == BOOL:
        return "Y" if value else "N"
    return str(value)


def _claim_rows(doc_id: str, claim: Claim, index: int) -> list[dict[str, str]]:
    lines = ";".join(str(line + 1) for line in claim.source_lines)
    rows = []
    for field_name in CLAIM_FIELDS:
        rows.append(
            {
                "doc_id": doc_id,
                "claim_index": str(index),
                "claim_number": claim.claim_number,
                "field": field_name,
                "kind": field_kind(field_name),
                "extracted_value": _extracted_text(claim, field_name),
                "TRUTH": "",
                "page": "" if claim.source_page is None else str(claim.source_page),
                "row_id": claim.row_id,
                "source_lines": lines,
                "method": claim.provenance_of(field_name).value,
                "null_reason": claim.field_issues.get(field_name, "").value
                if claim.field_issues.get(field_name) is not None
                else "",
            }
        )
    return rows


def _document_status_row(doc_id: str, result: ExtractionResult) -> dict[str, str]:
    status = canonical_status(result.reconciliation, needs_mapping=result.needs_mapping)
    return {
        "doc_id": doc_id,
        "claim_index": "",
        "claim_number": "",
        "field": DOCUMENT_STATUS_FIELD,
        "kind": DOC_STATUS,
        "extracted_value": status.value,
        "TRUTH": "",
        "page": "",
        "row_id": "",
        "source_lines": "",
        "method": "",
        "null_reason": "",
    }


def build_rows(doc_id: str, result: ExtractionResult) -> list[dict[str, str]]:
    """Every adjudicable row of one document, document status first."""
    rows = [_document_status_row(doc_id, result)]
    for index, claim in enumerate(result.document.claims):
        rows.extend(_claim_rows(doc_id, claim, index))
    return rows


def worksheet_path(out_dir: Path, doc_id: str) -> Path:
    return out_dir / f"{doc_id}.csv"


def write_worksheet(
    out_dir: Path, doc_id: str, result: ExtractionResult, *, force: bool = False
) -> Path:
    """Write one document's worksheet. Existing adjudications are never
    silently overwritten: regeneration destroys the TRUTH column, so it
    must be asked for."""
    path = worksheet_path(out_dir, doc_id)
    if path.exists() and not force:
        from tools.corpus_gate.manifest import SetupError

        raise SetupError(f"worksheet {doc_id} already exists; pass --force to replace it")
    out_dir.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel opens the file as the owner expects.
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=WORKSHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(build_rows(doc_id, result))
    return path


def read_worksheet(path: Path) -> list[dict[str, str]]:
    """Read an adjudicated worksheet back. Any change to the header is a
    stop: the schema is what makes the truth file trustworthy."""
    from tools.corpus_gate.manifest import SetupError

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise SetupError(f"worksheet {path.name} is empty") from None
        if tuple(header) != WORKSHEET_COLUMNS:
            raise SetupError(f"worksheet {path.name} does not have the expected columns")
        rows = []
        for number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(WORKSHEET_COLUMNS):
                raise SetupError(f"worksheet {path.name} line {number} has the wrong width")
            rows.append(dict(zip(WORKSHEET_COLUMNS, row)))
    return rows
