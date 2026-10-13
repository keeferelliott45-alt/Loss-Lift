"""A claim number printed with its own label in the cell is still a claim.

Some carriers print "Claim No: CN-1004" in the identifier column of every
claim, or of the odd one. ``pipeline.is_structural_row`` took any worded label
before a colon for page furniture ("Policy Period: ...", "Report: ...") and
dropped the row before it was parsed. The claim left the claim list and the
per-page row count together, so nothing disagreed with anything and a
document with no printed total read CLEAN with a claim missing.

The label is removed only when it names the claim number itself, from a
closed vocabulary. Every other colon label stays furniture: reading a section
heading or a subtotal as a claim is the worse error.

Fixtures are synthetic: constructed rows called through ``build_claims``, and
generated PDFs driven through ``run_pipeline``.
"""

from __future__ import annotations

import pymupdf
import pytest

from core.pipeline import ColumnMapping, build_claims, is_structural_row
from core.records import leading_identifier, strip_identifier_label
from core.schema import RawRow, RawTable

HEADERS = ("Claim No", "Date of Loss", "Status", "Loss Description", "Paid Total",
           "Total Incurred")
MAPPING = ColumnMapping(
    headers=list(HEADERS),
    fields={0: "claim_number", 1: "date_of_loss", 2: "claim_status",
            3: "loss_description", 4: "paid_total", 5: "incurred_total"},
)
CLAIMS = (
    ("CN-1001", "03/12/2024", "OPEN", "Ladder fall", "1,200.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "Rear-end collision", "2,450.00", "2,450.00"),
    ("CN-1003", "09/21/2024", "OPEN", "Water ingress", "775.50", "2,000.00"),
)

LEFT = 40.0
LINE = 14.0
COLUMNS = (0.0, 100.0, 175.0, 250.0, 350.0, 450.0)


def _row(cells, line=0):
    return RawRow(cells=list(cells), page=1, line_index=line, kind="data")


def _claims(rows):
    table = RawTable(page=1, headers=list(HEADERS),
                     rows=[_row(cells, i) for i, cells in enumerate(rows, 1)],
                     total_rows=[], strategy="words")
    claims, _warnings, unplaced = build_claims([table], MAPPING, "us", "mdy")
    return claims, unplaced


def _write(path, rows):
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    y = 50.0
    for line in (
        "MERIDIAN MUTUAL ASSURANCE",
        "LOSS RUN REPORT",
        "Named Insured: Northwind Fabrication Ltd",
        "Policy Number: GL-4417-2024",
        "Policy Period: 01/01/2024 to 12/31/2024",
        "Valuation Date: 12/31/2024",
    ):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in rows:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    document.save(path)
    document.close()
    return path


# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cell, expected", [
    ("Claim No: CN-1004", "CN-1004"),
    ("Claim No.: CN-1004", "CN-1004"),
    ("claim #: CN-1004", "CN-1004"),
    ("Claim Number:CN-1004", "CN-1004"),
    ("CLAIM ID: CN-1004", "CN-1004"),
    ("Claim: CN-1004", "CN-1004"),
    ("Clm Nbr: CN-1004", "CN-1004"),
    ("File No: F-20931", "F-20931"),
    ("Occurrence: 2024-0001", "2024-0001"),
    ("Incident No: IN-5521", "IN-5521"),
])
def test_a_label_naming_the_claim_number_is_removed(cell, expected):
    assert strip_identifier_label(cell) == expected


@pytest.mark.parametrize("cell", [
    "Policy Period: 01/01/2024 - 12/31/2024",
    "Policy: GL-4417-2024",
    "Policy Total: 3 claims",
    "Report: LossRunSummary",
    "Claimant: Jane Doe",
    "Claim Count: 3",
    "Claims: 5",
    "Claim No:",
    "Location: 0042 Main St",
    "CN-1004",
])
def test_any_other_cell_is_left_as_printed(cell):
    assert strip_identifier_label(cell) == cell.strip()


def test_leading_identifier_reads_through_the_label():
    shapes = {"AA-9999"}
    assert leading_identifier("Claim No: CN-1004", shapes) == "CN-1004"
    assert leading_identifier("Policy: GL-4417", shapes) is None


# --------------------------------------------------------------------------
# is_structural_row keeps furniture and releases labelled claims
# --------------------------------------------------------------------------


def test_a_labelled_claim_is_not_structural():
    row = _row(("Claim No: CN-1004", "10/02/2024", "OPEN", "Slip", "10.00", "10.00"))
    assert not is_structural_row(row, MAPPING)


@pytest.mark.parametrize("cell", [
    "Policy Period: 01/01/2024 - 12/31/2024",
    "Report: LossRunSummary",
    "Claimant: Jane Doe",
    "Claim Count: 3",
])
def test_furniture_stays_structural(cell):
    assert is_structural_row(_row((cell, "", "", "", "", "")), MAPPING)


# --------------------------------------------------------------------------
# build_claims
# --------------------------------------------------------------------------


def test_one_labelled_row_among_plain_ones_is_read():
    labelled = ("Claim No: CN-1004", "10/02/2024", "OPEN", "Slip on ice",
                "310.00", "1,310.00")
    claims, unplaced = _claims(CLAIMS + (labelled,))
    assert [c.claim_number for c in claims] == [
        "CN-1001", "CN-1002", "CN-1003", "CN-1004",
    ]
    assert str(claims[-1].incurred_total) == "1310.00"
    assert not unplaced


def test_every_row_labelled_is_read():
    rows = [(f"Claim No: {cells[0]}",) + cells[1:] for cells in CLAIMS]
    claims, unplaced = _claims(rows)
    assert [c.claim_number for c in claims] == ["CN-1001", "CN-1002", "CN-1003"]
    assert not unplaced


def test_a_subtotal_row_is_never_read_as_a_claim():
    subtotal = ("Policy Total: 3", "", "", "", "4,425.50", "9,450.00")
    claims, _unplaced = _claims(CLAIMS + (subtotal,))
    assert [c.claim_number for c in claims] == ["CN-1001", "CN-1002", "CN-1003"]


def test_a_section_heading_between_claims_is_never_read_as_a_claim():
    heading = ("Policy Period: 01/01/2024", "- 12/31/2024", "", "", "", "")
    claims, _unplaced = _claims(CLAIMS[:1] + (heading,) + CLAIMS[1:])
    assert [c.claim_number for c in claims] == ["CN-1001", "CN-1002", "CN-1003"]
    assert "Policy Period" not in (claims[0].loss_description or "")


def test_a_label_with_no_identifier_and_money_is_not_lost():
    """Nothing after the label reads as a claim number: the money is a hole
    in the reading, never silently folded or dropped."""
    row = ("Claim No: pending", "10/02/2024", "OPEN", "Slip", "310.00", "1,310.00")
    claims, unplaced = _claims(CLAIMS + (row,))
    assert [c.claim_number for c in claims] == ["CN-1001", "CN-1002", "CN-1003"]
    assert "Slip" not in " ".join(c.loss_description or "" for c in claims)
    assert unplaced


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


def test_end_to_end_the_labelled_claim_is_counted(tmp_path):
    labelled = ("Claim No: CN-1004", "10/02/2024", "OPEN", "Slip on ice",
                "310.00", "1,310.00")
    path = _write(tmp_path / "labelled.pdf", CLAIMS + (labelled,))
    from core.pipeline import run_pipeline

    result = run_pipeline(path, use_vision=False)
    numbers = [c.claim_number for c in result.document.claims]
    assert numbers == ["CN-1001", "CN-1002", "CN-1003", "CN-1004"], numbers
    assert result.document.rows_seen_per_page.get(1) == 4


def test_end_to_end_every_row_labelled(tmp_path):
    rows = [(f"Claim No: {cells[0]}",) + cells[1:] for cells in CLAIMS]
    path = _write(tmp_path / "all_labelled.pdf", rows)
    from core.pipeline import run_pipeline

    result = run_pipeline(path, use_vision=False)
    numbers = [c.claim_number for c in result.document.claims]
    assert numbers == ["CN-1001", "CN-1002", "CN-1003"], numbers
    assert result.document.rows_seen_per_page.get(1) == 3


# --------------------------------------------------------------------------
# Accountability: the printed cell is kept, nothing labelled disappears
# --------------------------------------------------------------------------


def test_the_printed_cell_is_kept_beside_the_canonical_number():
    labelled = ("Claim No: CN-1004", "10/02/2024", "OPEN", "Slip on ice",
                "310.00", "1,310.00")
    claims, _unplaced = _claims(CLAIMS + (labelled,))
    assert claims[-1].claim_number == "CN-1004"
    assert claims[-1].raw_cells.get("claim_number") == "Claim No: CN-1004"


def test_a_labelled_claim_without_money_is_still_a_claim():
    labelled = ("Claim No: CN-1004", "10/02/2024", "OPEN", "Report only", "", "")
    claims, _unplaced = _claims(CLAIMS + (labelled,))
    assert [c.claim_number for c in claims][-1] == "CN-1004"
    assert claims[-1].incurred_total is None  # a blank stays a blank


def test_an_invalid_labelled_identifier_on_a_page_is_named_not_dropped(tmp_path):
    from core.pipeline import run_pipeline
    from core.review import canonical_status
    from core.schema import DocumentStatus

    row = ("Claim No: pending", "10/02/2024", "OPEN", "Slip", "310.00", "1,310.00")
    path = _write(tmp_path / "pending.pdf", CLAIMS + (row,))
    result = run_pipeline(path, use_vision=False)
    assert [c.claim_number for c in result.document.claims] == [
        "CN-1001", "CN-1002", "CN-1003"]
    document = result.document
    accounted = len(document.unplaced_rows) + len(document.refused_claim_rows)
    assert accounted >= 1 or document.rows_seen_per_page.get(1) == 4
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize("cell", ["Policy Total: 3", "Claim Count: 3",
                                  "Report: Loss Run Summary"])
def test_printed_furniture_never_becomes_a_claim_end_to_end(tmp_path, cell):
    from core.pipeline import run_pipeline

    furniture = (cell, "", "", "", "", "")
    path = _write(tmp_path / "furniture.pdf", CLAIMS + (furniture,))
    result = run_pipeline(path, use_vision=False)
    assert [c.claim_number for c in result.document.claims] == [
        "CN-1001", "CN-1002", "CN-1003"]
