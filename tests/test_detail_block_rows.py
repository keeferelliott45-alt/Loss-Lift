"""Claims printed as stacked detail blocks rather than table rows.

Reproduces the mechanism behind F06/F02, found in a Liberty Mutual RISKTRAC
run embedded in a municipal board packet. Synthetic: the shape is copied, the
content is not (spec section 9).

Some carriers print each claim as a block of stacked lines — the claim number
and its money on the first line, then a cause code, a class code, a date and
an injury description on lines beneath. Every one of those lines lands in the
claim-number column, so the column is never empty and the continuation-folding
that keys on emptiness never fires. Each line becomes a claim, and the count,
the sums and the duplicate checks are all wrong by the block height. On the
real document that turned 28 claims into 162 rows.

What separates a claim from its continuation lines is that the claim carries
an identifier: it has digits, it is not a date, and its shape recurs once per
claim across the document. None of that names a carrier, which is the point —
any detail-block layout fails the same way.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.pipeline import (
    accepted_identifier_shapes,
    identifier_shape,
    run_pipeline,
)

FONT = "helv"
SIZE = 7.5
COLUMNS = ((40, "Claim Number"), (150, "Loss Date"), (250, "Status"),
           (330, "Total Incurred"), (430, "Total Paid"))

#: Each claim, then the continuation lines printed beneath it. The
#: continuations are what the real report puts there: a cause code, a class
#: code, a date with no column of its own, and the injury description.
BLOCKS = (
    ("WC550C44573", "5/3/2021", "OPEN", "2487.87", "1200.00",
     ["-UNKNOV\\N", "OYA-MISCELLANEOUS-NOC", "7/24/13", "000 -UNDEFINED",
      "HEART IS RACING"]),
    ("WC550C46497", "8/31/2021", "CLOSED", "168.60", "168.60",
     ["-UNKNOV\\N", "OLA -MATERIAL", "11/11/19", "000 -UNDEFINED",
      "CUT LEFT KNEE"]),
    ("WC550C47293", "10/8/2021", "OPEN", "1498.35", "500.00",
     ["-UNKNOV\\N", "ORA-STRUCK", "7/12/21", "0HA-MATERIAL",
      "IW CLEANING TRUCK"]),
)


def _write(page, text, x, y):
    page.insert_text((x, y), text, fontname=FONT, fontsize=SIZE)


def _build(path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    _write(page, "Assurance Mutual Group", 40, 28)
    _write(page, "Numbers As of 12/31/2021", 40, 42)
    for x, title in COLUMNS:
        _write(page, title, x, 70)

    y = 90
    for number, loss, status, incurred, paid, continuations in BLOCKS:
        for (x, _), value in zip(COLUMNS, (number, loss, status, incurred, paid)):
            _write(page, value, x, y)
        y += 11
        for line in continuations:
            _write(page, line, 40, y)   # every continuation starts in column 1
            y += 11
        y += 4

    _write(page, "Report Totals", 40, y + 8)
    _write(page, "Claim Count : 3", 150, y + 8)
    _write(page, f"{sum(Decimal(b[3]) for b in BLOCKS)}", 330, y + 8)
    _write(page, f"{sum(Decimal(b[4]) for b in BLOCKS)}", 430, y + 8)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    path = tmp_path_factory.mktemp("blocks") / "detail_blocks.pdf"
    _build(path)
    return run_pipeline(path, use_vision=False)


def test_one_claim_per_block_not_one_per_line(result):
    """The count is the thing this breaks: 3 claims, 18 printed lines."""
    assert [c.claim_number for c in result.document.claims] == [
        block[0] for block in BLOCKS
    ]


def test_the_extracted_count_matches_the_printed_count(result):
    assert len(result.document.claims) == result.document.printed_claim_count == 3


def test_continuation_text_is_kept_not_discarded(result):
    """Folding beats dropping: the injury description is real information."""
    first = result.document.claims[0]
    assert "HEART IS RACING" in (first.loss_description or "")


def test_money_is_not_multiplied_across_the_block(result):
    """Every continuation line inheriting the block's money inflates the sums."""
    first = result.document.claims[0]
    assert first.incurred_total == Decimal("2487.87")
    assert result.document.column_total("incurred_total") == sum(
        Decimal(block[3]) for block in BLOCKS
    )


def test_a_shape_must_recur_to_count_as_an_identifier():
    """A mangled one-off must not define its own identifier shape.

    "0HA-MATERIAL" is a cause code an OCR pass turned into something with a
    digit in it. It survives the digit and date tests, so only the requirement
    that a shape recur once per claim keeps it out.
    """
    from core.schema import RawRow, RawTable

    real = [f"WC550C4{n:04d}" for n in range(12)]
    rows = [RawRow(cells=[value]) for value in real + ["0HA-MATERIAL"]]
    table = RawTable(page=1, headers=["Claim Number"], rows=rows)
    mapping = type("M", (), {"headers": ["Claim Number"], "fields": {0: "claim_number"},
                             "index_of": lambda self, f: 0 if f == "claim_number" else None})()
    shapes = accepted_identifier_shapes([table], mapping)
    assert identifier_shape("WC550C40000") in shapes
    assert identifier_shape("0HA-MATERIAL") not in shapes


def test_a_row_with_money_but_no_identifier_is_reported_not_absorbed(result):
    """Prose folds into the claim above; figures must never be buried in it."""
    assert result.warnings == []  # this document has no such row
