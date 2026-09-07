"""A ruled box around the header, and money labelled by a parent row.

Reproduces the mechanisms behind AIG's zero extraction (F05, F04). Synthetic:
the structure is copied, the content is not (spec section 9).

Two things happen at once in that layout, and either alone is enough to lose
the document:

* **The header block is boxed and the claim rows are not.** A ruled-table
  detector returns the box as a table — a header, one row, and none of the
  data, which sits below the ruling. Nothing checked that the table it chose
  actually contained anything.
* **Three columns are labelled "Total".** What separates them is the row
  beneath: Reserves, Recoveries, Incurred. Position alone would guess; the
  parent alone is ambiguous; only parent and child together say what each
  column is.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.pipeline import run_pipeline

FONT = "helv"
SIZE = 7.0

#: (x, parent label, child label). The parent alone cannot name the last three.
COLUMNS = (
    (30, "Claim #", ""),
    (150, "Loss", "Date"),
    (235, "", "Status"),
    (300, "Ind/BI", "Paid"),
    (380, "Med/PD", "Paid"),
    (460, "Alloc Exp", "Paid"),
    (545, "Total", "Reserves"),
    (630, "Total", "Recoveries"),
    (710, "Total", "Incurred"),
)

CLAIMS = (
    ("801-114552-001", "3/13/2023", "Closed", "1200.00", "300.00", "150.00",
     "0.00", "0.00", "1650.00"),
    ("801-114552-002", "5/2/2023", "Open", "400.00", "0.00", "75.00",
     "900.00", "0.00", "1375.00"),
    ("802-330941-001", "9/18/2023", "Closed", "0.00", "0.00", "0.00",
     "0.00", "0.00", "0.00"),
)


def _write(page, text, x, y):
    page.insert_text((x, y), text, fontname=FONT, fontsize=SIZE)


def _build(path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=792, height=540)
    _write(page, "Assurance Indemnity Group", 40, 26)
    _write(page, "Valuation Date: 12/31/2023", 545, 26)

    # The header sits inside a drawn box; the claim rows below it do not.
    page.draw_rect(pymupdf.Rect(25, 52, 770, 82), color=(0, 0, 0), width=0.7)
    for x, parent, child in COLUMNS:
        if parent:
            _write(page, parent, x, 64)
        if child:
            _write(page, child, x, 76)

    y = 100
    for row in CLAIMS:
        for (x, _, _), value in zip(COLUMNS, row):
            _write(page, value, x, y)
        y += 14

    _write(page, "Claim Count = 3", 40, y + 10)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    path = tmp_path_factory.mktemp("boxed") / "boxed_header.pdf"
    _build(path)
    return run_pipeline(path, use_vision=False)


def test_rows_outside_the_ruled_header_are_still_found(result):
    """A grid holding a header and no data has not found the table."""
    assert [c.claim_number for c in result.document.claims] == [
        claim[0] for claim in CLAIMS
    ]


def test_the_child_row_disambiguates_three_columns_named_total(result):
    """Reserves, recoveries and incurred are told apart by the row beneath."""
    first = next(c for c in result.document.claims if c.claim_number.endswith("-001"))
    assert first.reserve_total == Decimal("0.00")
    assert first.recovery_total == Decimal("0.00")
    assert first.incurred_total == Decimal("1650.00")


def test_the_trade_abbreviations_map_to_their_components(result):
    """Ind/BI, Med/PD and Alloc Exp are indemnity, medical and ALAE."""
    first = next(c for c in result.document.claims if c.claim_number.endswith("-001"))
    assert first.paid_indemnity == Decimal("1200.00")
    assert first.paid_medical == Decimal("300.00")
    assert first.paid_expense == Decimal("150.00")


def test_the_arithmetic_holds_once_the_columns_mean_what_they_say(result):
    """Mapping the three Totals by position would break this identity."""
    second = next(c for c in result.document.claims if c.claim_number.endswith("-002"))
    assert second.reserve_total == Decimal("900.00")
    assert second.incurred_total == Decimal("1375.00")


def test_hash_is_read_as_the_word_number(result):
    """"Claim #" identifies the claim column; dropping # left it unmapped."""
    assert "claim_number" in result.mapping.mapped_fields
