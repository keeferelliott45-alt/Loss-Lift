"""Printed metadata must not become the previous claim's accident description.

A carrier that prints one claim over several lines also prints contract
metadata above the table:

    Contract Effective Date: 10/13/20
    Contract Number: WCCZ9147233202 - Impact Landscaping & Irrigation, LLC

Those lines belong to no claim. The record reconstruction correctly declines
to group them -- they end a record rather than continue one -- but it then
emitted them as ordinary ``data`` rows, and the claim builder folds a row it
cannot identify into the description of the claim above it. The claim above
the first row of page 48 is the last claim of page 45, so a Liberty claim
finished with the loader/head-injury narrative acquired a contract number and
the insured's company name.

Two things make that the worst kind of wrong. It is silent -- no rule fires,
because a longer description breaks no arithmetic -- and it is exported: the
Claim Detail sheet then attributes a policy identifier to an accident.

The extractor already knows: ``_ends_a_record`` recognises the ``Label:``
shape and is what kept these lines out of a record in the first place. The
fix carries that judgement onto the row, so the claim builder is told what
the extractor already worked out rather than having to re-derive it from
cells whose column boundaries have since moved.

Geometry follows Liberty's real record pages; claim numbers, names and
narrative are synthetic (spec section 9).
"""

from __future__ import annotations

import pytest

from core.extract_digital import (
    COLUMN_GUTTER_FACTOR,
    Line,
    Word,
    _extract_record_table,
    find_header_line,
    header_block,
    split_cells,
)
from core.pipeline import ColumnMapping, build_claims
from core.records import detect_layout
from core.schema import RawRow, RawTable

CHAR_WIDTH = 3.3619

_HEADER = [
    [("Claim Number", 41.2, 83.0), ("Claimant Name", 95.0, 190.0),
     ("Loss Date", 243.3, 290.0), ("Total Incurred", 704.3, 780.0)],
    [("Location", 41.2, 83.0), ("Paid Indemnity", 449.0, 500.0),
     ("Paid Medical", 534.0, 585.0), ("Total Paid", 704.3, 760.0)],
    [("Cause", 38.3, 60.0), ("Status", 243.3, 268.0),
     ("Indemnity O/R", 449.0, 500.0), ("Medical O/R", 534.0, 585.0),
     ("Expense O/R", 619.2, 670.0), ("Outstanding Reserve", 704.3, 790.0)],
    [("Applied Recovery", 449.0, 520.0)],
    [("Date of Hire", 41.2, 90.0), ("Accident State", 243.3, 300.0)],
    [("Nature of Injury", 41.2, 100.0)],
    [("Accident Description", 41.2, 120.0)],
]

#: The contract block a carrier prints above the table on a continuation page.
_METADATA = [
    [("Contract", 41.2, 78.0), ("Effective", 80.0, 118.0), ("Date:", 120.0, 141.0),
     ("10/13/20", 143.0, 180.0)],
    [("Contract", 41.2, 78.0), ("Number:", 80.0, 114.0),
     ("WCCZ9147233202", 116.0, 190.0), ("-", 192.0, 195.0),
     ("Syntheticorp", 197.0, 260.0), ("Landscaping,", 262.0, 320.0),
     ("LLC", 322.0, 340.0)],
]

_RECORD_GAPS = [10.0, 10.0, 10.0, 14.0, 10.0, 10.0]
_INTER_RECORD = 22.0
_HEADER_PITCH = 8.0


def _w(text, x0, x1, top):
    return Word(text=text, x0=x0, x1=x1, top=top, bottom=top + 6.0)


def _line(cells, index, top):
    return Line(words=tuple(_w(t, a, b, top) for t, a, b in cells), index=index)


def _build_page(number: str, *, metadata_first: bool):
    """One record page, optionally opening with the contract block."""
    lines, top, index = [], 100.0, 0
    for row in _HEADER:
        lines.append(_line(row, index, top))
        index += 1
        top += _HEADER_PITCH

    if metadata_first:
        for row in _METADATA:
            top += 12.0
            lines.append(_line(row, index, top))
            index += 1

    top += _INTER_RECORD
    rows = [
        [(number, 41.2, 83.0), ("SYNTHETIC,NAME", 95.0, 190.0),
         ("01/05/2023", 243.3, 290.0), ("$141.00", 704.3, 740.0)],
        [("-UNKNOWN", 41.2, 83.0), ("$0.00", 449.0, 470.0),
         ("$126.00", 534.0, 560.0), ("$141.00", 704.3, 740.0)],
        [("0RA-", 38.3, 55.0), ("MISCELLANEOUS", 57.0, 130.0),
         ("Closed", 243.3, 263.8), ("$0.00", 449.0, 470.0),
         ("$0.00", 534.0, 560.0), ("$0.00", 619.2, 645.0),
         ("$0.00", 704.3, 730.0)],
        [("$0.00", 449.0, 470.0)],
        [("1/14/16", 41.2, 80.0), ("FL", 243.3, 255.0)],
        [("000 -UNDEFINED", 41.2, 100.0)],
        [("LIFTED MULCH BAGS", 41.2, 120.0)],
    ]
    for offset, row in enumerate(rows):
        lines.append(_line(row, index, top))
        index += 1
        if offset < len(_RECORD_GAPS):
            top += _RECORD_GAPS[offset]
    return lines


def _table(page_number: int, lines) -> RawTable:
    header_index, _ = find_header_line(lines, CHAR_WIDTH)
    block = header_block(lines, header_index, CHAR_WIDTH)
    block_end = max(line.index for line in block)
    layout = detect_layout(
        [split_cells(line, CHAR_WIDTH, COLUMN_GUTTER_FACTOR) for line in block]
    )
    assert layout.is_multi_line, "fixture must exercise the record path"
    table = _extract_record_table(
        page_number, lines, header_index, block_end, layout, CHAR_WIDTH
    )
    assert table is not None
    return table


def _metadata_rows(table: RawTable) -> list[RawRow]:
    return [row for row in table.rows if "Contract" in row.text()]


def test_the_fixture_reproduces_an_ungrouped_metadata_row():
    """Without this the test below would prove nothing: the contract block has
    to survive record grouping as its own row, which is what puts it in front
    of the claim builder."""
    table = _table(48, _build_page("SYN0000902", metadata_first=True))
    assert _metadata_rows(table), (
        f"metadata was grouped away; rows: {[r.text()[:50] for r in table.rows]}"
    )


def test_metadata_rows_are_not_claim_data():
    """The extractor already decided these lines end a record. That judgement
    has to reach the row, or the claim builder has to guess again."""
    table = _table(48, _build_page("SYN0000902", metadata_first=True))
    for row in _metadata_rows(table):
        assert row.kind != "data", (
            f"contract metadata is still presented as claim data: {row.text()[:70]}"
        )


def test_page_two_metadata_does_not_reach_page_one_s_claim():
    """The regression, end to end and across the page boundary.

    Page 45's last claim is complete when page 48 begins. Nothing printed on
    page 48 above its first claim may be appended to it.
    """
    first = _table(45, _build_page("SYN0000901", metadata_first=False))
    second = _table(48, _build_page("SYN0000902", metadata_first=True))
    mapping = ColumnMapping(
        headers=first.headers,
        fields={
            first.headers.index("Claim Number"): "claim_number",
            first.headers.index("Claimant Name"): "claimant_name",
            first.headers.index("Loss Date"): "date_of_loss",
            first.headers.index("Accident Description"): "loss_description",
        },
    )
    claims, _warnings, _unplaced = build_claims(
        [first, second], mapping, locale="us", date_order="mdy"
    )
    by_number = {claim.claim_number: claim for claim in claims}
    assert set(by_number) == {"SYN0000901", "SYN0000902"}, sorted(by_number)

    earlier = by_number["SYN0000901"].loss_description or ""
    assert "Contract Number" not in earlier, (
        f"page 48's contract line reached page 45's claim: {earlier!r}"
    )
    assert "WCCZ9147233202" not in earlier, earlier
    assert "Syntheticorp" not in earlier, (
        f"the insured's company name became an accident description: {earlier!r}"
    )


@pytest.mark.parametrize("number", ["SYN0000901", "SYN0000902"])
def test_the_claims_themselves_are_unaffected(number):
    """Nothing here may cost a claim its own description."""
    first = _table(45, _build_page("SYN0000901", metadata_first=False))
    second = _table(48, _build_page("SYN0000902", metadata_first=True))
    mapping = ColumnMapping(
        headers=first.headers,
        fields={
            first.headers.index("Claim Number"): "claim_number",
            first.headers.index("Accident Description"): "loss_description",
        },
    )
    claims, _w, _u = build_claims([first, second], mapping, locale="us", date_order="mdy")
    claim = next(c for c in claims if c.claim_number == number)
    assert "LIFTED MULCH BAGS" in (claim.loss_description or ""), claim.loss_description
