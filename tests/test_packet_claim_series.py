"""A packet binding loss runs that number their claims differently.

Board and RFP packets bind several carriers' loss runs into one PDF. Each run
prints its own header and issues claim numbers from its own system, so the
shapes differ: eight digits in one run, a prefix and five digits in another.

The claim-number filter learns what an identifier looks like from the document
itself -- a shape has to recur to be trusted, which is what keeps a cause code
on a continuation line from becoming a claim. It took that vote across every
table in the document at once, so the larger run outvoted the smaller. A
one-claim run bound beside an eight-claim run had its claim refused as a stray
code, and the claim's figures were reported as money nothing could be attached
to. Any run printing fewer claims than a quarter of the largest run's went the
same way, all of its claims together.

A run printed under its own header is its own numbering system. Where the
document's vote accepts none of a layout's identifiers, that layout is judged
by its own claims -- and only by rows that read as claims in their own right,
so a page of continuation lines under a differently read header still cannot
promote its codes.

Everything here is synthetic (spec section 9): invented carriers, numbers and
amounts. Only the structure of the failure is reproduced.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.pipeline import build_claims, build_mapping, run_pipeline
from core.schema import ClaimStatus, DocumentStatus, RawRow, RawTable

LINE = 14.0
LEFT = 40.0
#: Wide, even columns so the gutters are unambiguous and column detection is
#: not what these tests measure.
COLUMNS = (0.0, 95.0, 180.0, 255.0, 340.0, 425.0)

#: Two carriers' headers for the same six fields, worded as each carrier words
#: them. Different wording is what makes them different layouts.
LARGE_HEADERS = (
    "Claim Number", "Loss Date", "Status", "Paid Total", "Reserve Total", "Incurred Total",
)
SMALL_HEADERS = (
    "Claim #", "Date of Loss", "Claim Status", "Total Paid", "Outstanding", "Total Incurred",
)

#: The smaller run: a prefix and five digits. Every claim ties.
SMALL_RUN = (
    ("CR-40117", "02/14/2022", "OPEN", "250.00", "750.00", "1,000.00"),
    ("CR-40152", "06/21/2022", "CLOSED", "1,800.00", "0.00", "1,800.00"),
    ("CR-40188", "09/30/2022", "OPEN", "0.00", "5,000.00", "5,000.00"),
)


def _large_run(count: int) -> list[tuple[str, ...]]:
    """The larger run: eight digits, issued in sequence. Every claim ties."""
    return [
        (f"{71004410 + n}", f"{1 + n % 9:02d}/{10 + n % 10}/2022", "CLOSED",
         "1,000.00", "0.00", "1,000.00")
        for n in range(count)
    ]


def _page(document, carrier: str, headers, rows) -> None:
    page = document.new_page(width=612, height=792)
    y = 50.0
    for line in (carrier, "LOSS RUN REPORT", "Valuation Date: 12/31/2022"):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, headers):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in rows:
        for offset, cell in zip(COLUMNS, row):
            page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE


def _packet(path, small, large):
    """Two runs bound together, the smaller first, as a packet binds them."""
    document = pymupdf.open()
    _page(document, "HARBOR CREST SPECIALTY INSURANCE COMPANY", SMALL_HEADERS, small)
    _page(document, "NORTHFIELD AUTO INSURANCE COMPANY", LARGE_HEADERS, large)
    document.save(path)
    document.close()
    return path


@pytest.mark.parametrize(
    "small_count, large_count",
    [
        (1, 8),    # a single claim beside a run of eight
        (3, 16),   # a run of three, under a quarter of the run beside it
    ],
)
def test_every_run_in_the_packet_keeps_its_claims(tmp_path, small_count, large_count):
    """The regression: the smaller run's claims were refused, all of them."""
    small = SMALL_RUN[:small_count]
    large = _large_run(large_count)
    result = run_pipeline(
        _packet(tmp_path / "packet.pdf", small, large),
        use_vision=False,
        profiles_dir=tmp_path / "profiles",
    )
    document = result.document

    assert [claim.claim_number for claim in document.claims] == [
        row[0] for row in (*small, *large)
    ]
    first = document.claims[0]
    assert first.claim_status is ClaimStatus.OPEN
    assert (first.paid_total, first.reserve_total, first.incurred_total) == (
        Decimal("250.00"), Decimal("750.00"), Decimal("1000.00"),
    )
    # The figures belong to claims now, so nothing is left unattached.
    assert document.unplaced_rows == []
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert result.reconciliation.status is DocumentStatus.CLEAN


# --------------------------------------------------------------------------
# What the filter still refuses
#
# The vote exists to keep codes printed on continuation lines out of the
# claims. Judging a layout by its own claims must not reopen that door.
# --------------------------------------------------------------------------


def _claim_row(page: int, line: int, number: str, status: str = "CLOSED") -> RawRow:
    return RawRow(
        cells=[number, "03/14/2022", status, "100.00", "0.00", "100.00"],
        page=page,
        line_index=line,
    )


def _continuation_row(page: int, line: int, code: str) -> RawRow:
    """A line printed under a claim, its code landing in the claim column."""
    return RawRow(cells=[code, "", "", "", "", ""], page=page, line_index=line)


def _read(*tables: RawTable):
    mapping = build_mapping(list(tables[0].headers))
    claims, _warnings, _unplaced = build_claims(list(tables), mapping, "us", "mdy")
    return [claim.claim_number for claim in claims]


def _large_table(count: int = 8) -> RawTable:
    return RawTable(
        page=1,
        headers=list(LARGE_HEADERS),
        rows=[_claim_row(1, 10 + n, f"{71004410 + n}") for n in range(count)],
    )


def test_a_one_off_code_inside_a_run_is_still_refused():
    """A code on a continuation line shares its run's layout, and its vote."""
    table = _large_table(12)
    table.rows.append(_continuation_row(1, 40, "0HA-MATERIAL"))
    assert _read(table) == [f"{71004410 + n}" for n in range(12)]


def test_continuation_lines_under_another_header_do_not_become_claims():
    """A layout holding only codes has no claims of its own to vote with.

    A page whose header was read differently from the rest of the document is
    a different layout by its labels. If all it holds is continuation lines,
    their codes recur -- the same cause code under several claims -- and
    would pass a vote taken among themselves, though the document's vote
    refuses them. None of those rows reads as a claim, so none of them votes.
    """
    continuation = RawTable(
        page=2,
        headers=list(SMALL_HEADERS),
        rows=[_continuation_row(2, 10 + n, "0HA-MATERIAL") for n in range(3)],
    )
    assert _read(_large_table(16), continuation) == [
        f"{71004410 + n}" for n in range(16)
    ]


def test_a_layout_sharing_the_document_s_numbering_keeps_its_vote():
    """Only a layout the document's vote reads nothing in is judged alone.

    Where a second layout carries the document's own numbering, it is the same
    system, and a one-off shape inside it is what the vote is there to refuse
    -- even printed on a row that reads as a claim, since a damaged number
    cannot be told from a stray one and is not guessed at. Judged alone, a
    layout of two rows would have accepted both.
    """
    second = RawTable(
        page=2,
        headers=list(SMALL_HEADERS),
        rows=[
            _claim_row(2, 10, "71004420"),
            _claim_row(2, 11, "7I0O4421-X", status="OPEN"),
        ],
    )
    assert _read(_large_table(), second) == [
        *(f"{71004410 + n}" for n in range(8)), "71004420",
    ]
