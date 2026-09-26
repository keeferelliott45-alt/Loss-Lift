"""A packet binding loss runs that number their claims differently.

Board and RFP packets bind several carriers' loss runs into one PDF. Each run
issues claim numbers from its own system, so the shapes differ: eight digits
in one run, a prefix and five digits in another.

The claim-number filter learns what an identifier looks like from the document
itself -- a shape has to recur to be trusted, which is what keeps a cause code
on a continuation line from becoming a claim. It took that vote across every
table in the document at once, so the larger run outvoted the smaller. A
one-claim run bound beside an eight-claim run had its claim refused as a stray
code, and the claim's figures were reported as money nothing could be attached
to. Any run printing fewer claims than a quarter of the largest run's went the
same way, all of its claims together.

Where one run ends and the next begins is the question, and the table header
cannot answer it. Two carriers print the same generic labels, so equal headers
do not make one run; and one run's header comes out differently on a page
where a label wraps, so different headers do not make two. What a run does
print is its own page numbering: a report numbers its pages from 1, and a page
calling itself "Page 1 of N" is where one begins. That is the boundary used
here, and only that. A packet whose reports print no page numbers gives no
boundary at all, and is read as one run -- its minority claims are then
refused as before, and their figures reported rather than guessed at.

Within a run, the document's vote stands wherever it accepts any of the run's
identifiers. Only a run the vote reads nothing in is judged by its own claims,
counted from rows that read as claims in their own right.

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

#: Generic labels, the kind two carriers print identically.
LARGE_HEADERS = (
    "Claim Number", "Loss Date", "Status", "Paid Total", "Reserve Total", "Incurred Total",
)
#: Another carrier's wording for the same six fields.
SMALL_HEADERS = (
    "Claim #", "Date of Loss", "Claim Status", "Total Paid", "Outstanding", "Total Incurred",
)
#: A qualifier wrapped above one label on one page only. The page's header then
#: reads "Claim Status" where the run's other pages read "Status": a different
#: header, the same six fields, the same run.
WRAPPED_STATUS = ("", "", "Claim", "", "", "")

#: Cause codes printed on the continuation lines of a detail block: one shape,
#: three different codes, so no two lines read as repeated page furniture.
CAUSE_CODES = ("0HA-MATERIAL", "7UX-STRUCKBY", "4QE-CUTPUNCT")

#: The smaller run: a prefix and five digits. Every claim ties.
SMALL_RUN = (
    ("CR-40117", "02/14/2022", "OPEN", "250.00", "750.00", "1,000.00"),
    ("CR-40152", "06/21/2022", "CLOSED", "1,800.00", "0.00", "1,800.00"),
    ("CR-40188", "09/30/2022", "OPEN", "0.00", "5,000.00", "5,000.00"),
)


def _large_run(count: int, first: int = 71004410) -> list[tuple[str, ...]]:
    """The larger run: eight digits, issued in sequence. Every claim ties."""
    return [
        (f"{first + n}", f"{1 + n % 9:02d}/{10 + n % 10}/2022", "CLOSED",
         "1,000.00", "0.00", "1,000.00")
        for n in range(count)
    ]


def _page(document, carrier, headers, rows, *, number=None, of=None, qualifiers=()):
    """One printed page of a loss run, numbered the way the run numbers it."""
    page = document.new_page(width=612, height=792)
    y = 36.0
    if number is not None:
        page.insert_text((LEFT, y), f"Page {number} of {of}", fontsize=8)
    y += LINE
    for line in (carrier, "LOSS RUN REPORT", "Valuation Date: 12/31/2022"):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, qualifiers):
        if label:
            page.insert_text((LEFT + offset, y - 9), label, fontsize=8.5)
    for offset, label in zip(COLUMNS, headers):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in rows:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE


def _write(path, pages):
    document = pymupdf.open()
    for page in pages:
        _page(document, *page[:3], **(page[3] if len(page) > 3 else {}))
    document.save(path)
    document.close()
    return path


def _read(tmp_path, pages):
    return run_pipeline(
        _write(tmp_path / "packet.pdf", pages),
        use_vision=False,
        profiles_dir=tmp_path / "profiles",
    )


SMALL_CARRIER = "HARBOR CREST SPECIALTY INSURANCE COMPANY"
LARGE_CARRIER = "NORTHFIELD AUTO INSURANCE COMPANY"


def _packet_pages(small, large, *, small_headers, small_first=True,
                  small_pages=1, numbered=True):
    """Two runs bound together, each numbering its own pages from 1."""
    def numbering(index, total):
        return {"number": index, "of": total} if numbered else {}

    per_page = -(-len(small) // small_pages)
    small_run = [
        (SMALL_CARRIER, small_headers, small[i * per_page:(i + 1) * per_page],
         numbering(i + 1, small_pages))
        for i in range(small_pages)
    ]
    large_run = [(LARGE_CARRIER, LARGE_HEADERS, large, numbering(1, 1))]
    return small_run + large_run if small_first else large_run + small_run


def _assert_every_claim_read(result, expected):
    document = result.document
    assert [claim.claim_number for claim in document.claims] == expected
    # The figures belong to claims now, so nothing is left unattached.
    assert document.unplaced_rows == []
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert result.reconciliation.status is DocumentStatus.CLEAN


@pytest.mark.parametrize(
    "small_count, large_count",
    [
        (1, 8),    # a single claim beside a run of eight
        (3, 16),   # a run of three, under a quarter of the run beside it
    ],
)
def test_every_run_in_the_packet_keeps_its_claims(tmp_path, small_count, large_count):
    """The packet as it arrived: two carriers, two headers, two paginations."""
    small, large = SMALL_RUN[:small_count], _large_run(large_count)
    result = _read(tmp_path, _packet_pages(small, large, small_headers=SMALL_HEADERS))

    _assert_every_claim_read(result, [row[0] for row in (*small, *large)])
    first = result.document.claims[0]
    assert first.claim_status is ClaimStatus.OPEN
    assert (first.paid_total, first.reserve_total, first.incurred_total) == (
        Decimal("250.00"), Decimal("750.00"), Decimal("1000.00"),
    )


@pytest.mark.parametrize(
    "small_count, large_count, small_first, small_pages",
    [
        (1, 8, True, 1),
        (1, 8, False, 1),   # the order the runs are bound in does not matter
        (2, 16, True, 2),   # a smaller run of two pages, one claim on each
    ],
)
def test_runs_sharing_generic_headers_keep_their_own_numbering(
    tmp_path, small_count, large_count, small_first, small_pages
):
    """Equal headers do not make one run.

    Both carriers print the same six generic labels. Grouping tables by their
    header text put the two runs in one group, where the larger run's shape
    was the vote and the smaller run's claims were refused.
    """
    small, large = SMALL_RUN[:small_count], _large_run(large_count)
    result = _read(tmp_path, _packet_pages(
        small, large, small_headers=LARGE_HEADERS,
        small_first=small_first, small_pages=small_pages,
    ))
    expected = [row[0] for row in small] + [row[0] for row in large]
    if not small_first:
        expected = [row[0] for row in large] + [row[0] for row in small]
    _assert_every_claim_read(result, expected)


def test_a_page_read_under_a_wrapped_header_stays_in_its_run(tmp_path):
    """Different headers do not make two runs.

    One carrier's report on two pages. On the second a qualifier wraps above
    "Status", so that page's header reads differently, and grouped by header
    text the page took a vote of its own. It holds a claim whose number was
    printed run into the text beside it, and a continuation line carrying a
    cause code and an unambiguous date. Neither shape recurs anywhere, so a
    vote among the page's own rows accepted both, and the cause code became a
    claim of its own.

    The page is page 2 of 2 of the same report, so it is read by that report's
    vote. The damaged number is refused exactly as it would be on page 1, and
    its figures are reported for a reviewer; the cause code is not a claim.
    """
    large = _large_run(8)
    second_page = [
        ("71004418CL", "04/14/2022", "CLOSED", "1,000.00", "0.00", "1,000.00"),
        ("0HA-MATERIAL", "11/23/2022", "", "", "", ""),
    ]
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"number": 1, "of": 2}),
        (LARGE_CARRIER, LARGE_HEADERS, second_page,
         {"number": 2, "of": 2, "qualifiers": WRAPPED_STATUS}),
    ])
    document = result.document

    assert [claim.claim_number for claim in document.claims] == [row[0] for row in large]
    unplaced = [row for row in document.unplaced_rows if row.page == 2]
    assert len(unplaced) == 1, "the damaged claim's figures must be reported"
    assert unplaced[0].parsed_amounts.get("incurred_total") == Decimal("1000.00")
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# What the vote still refuses
#
# The vote exists to keep codes printed on continuation lines out of the
# claims. Judging a run by its own claims must not reopen that door.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("small_headers", [LARGE_HEADERS, SMALL_HEADERS])
def test_without_printed_page_numbers_the_packet_is_one_run(tmp_path, small_headers):
    """No boundary is printed, so none is drawn.

    Neither report numbers its pages. Whether their headers match or not, a
    different header is also what a single run's wrapped label looks like, so
    it is not evidence of a second run. The smaller run's claim is refused as
    it always was -- and its figures are reported, not lost.
    """
    small, large = SMALL_RUN[:1], _large_run(8)
    result = _read(tmp_path, _packet_pages(
        small, large, small_headers=small_headers, numbered=False,
    ))
    document = result.document

    assert [claim.claim_number for claim in document.claims] == [row[0] for row in large]
    assert len(document.unplaced_rows) == 1
    assert document.unplaced_rows[0].page == 1
    assert [f.rule_id for f in result.reconciliation.findings].count("R-23") == 1
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_report_numbering_each_section_from_one_keeps_the_documents_vote(tmp_path):
    """A boundary drawn inside one carrier's numbering changes nothing.

    Some reports number every section from page 1. The second section here is
    then its own run, but its claim carries the document's shape, so the run
    is read by the document's vote -- and the one-off damaged number beside it
    is refused, as it would be anywhere else.
    """
    large = _large_run(8)
    section = [
        ("71004420", "03/14/2022", "CLOSED", "1,000.00", "0.00", "1,000.00"),
        ("7I0O4421-X", "03/15/2022", "OPEN", "500.00", "0.00", "500.00"),
    ]
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"number": 1, "of": 1}),
        (LARGE_CARRIER, LARGE_HEADERS, section, {"number": 1, "of": 1}),
    ])
    document = result.document

    assert [claim.claim_number for claim in document.claims] == [
        *(row[0] for row in large), "71004420",
    ]
    assert [row.page for row in document.unplaced_rows] == [2]


def test_a_run_judged_alone_is_voted_on_by_its_claim_rows(tmp_path):
    """Within a run judged on its own, only rows that read as claims vote.

    The smaller carrier prints its one claim as a detail block: the claim
    line, then three continuation lines that each open with a cause code. The
    codes share one shape and outnumber the claim, so a vote among every line
    of the run would take them for its numbering and refuse the claim. Only
    the claim line has a date and a status, so only it votes -- and the codes
    stay part of its description.
    """
    large = _large_run(16)
    block = [
        SMALL_RUN[0],
        *((code, "", "STRUCK BY", "FALLING", "STOCK IN", "AISLE") for code in CAUSE_CODES),
    ]
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"number": 1, "of": 1}),
        (SMALL_CARRIER, SMALL_HEADERS, block, {"number": 1, "of": 1}),
    ])
    claims = result.document.claims
    assert [claim.claim_number for claim in claims] == [
        *(row[0] for row in large), SMALL_RUN[0][0],
    ]
    assert all(code in (claims[-1].loss_description or "") for code in CAUSE_CODES)


def test_printed_page_numbers_mark_where_each_report_begins():
    """The boundary evidence itself, read from each page's printed text."""
    from core.pipeline import printed_runs

    pages = {
        1: "Renewal submission\nCover letter",                     # before any report
        2: "Loss Run Report\nPage 1 of 2",
        3: "Page 2 of 2\nsee page 1 for the policy terms",         # prose is not a page number
        4: "Account summary",                                      # an unnumbered cover page
        5: "PAGE 1 OF 4   Printed 03/31/2023",
        6: "Page 2 of 4",
        7: "Packet page 7 of 40\nPage 1 of 1",                     # a packet stamp beside the report's own
    }
    assert printed_runs(pages) == {1: 0, 2: 1, 3: 1, 4: 1, 5: 2, 6: 2, 7: 3}


def test_one_report_numbered_across_its_pages_is_one_run():
    """Page 1 opens the report and every later page continues it."""
    from core.pipeline import printed_runs

    pages = {n: f"Page {n} of 3" for n in (1, 2, 3)}
    assert printed_runs(pages) == {1: 1, 2: 1, 3: 1}


# --------------------------------------------------------------------------
# Reading claims without any run evidence: one vote, as before
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


def _claims_from(*tables: RawTable):
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
    """A code on a continuation line shares its run's vote."""
    table = _large_table(12)
    table.rows.append(_continuation_row(1, 40, "0HA-MATERIAL"))
    assert _claims_from(table) == [f"{71004410 + n}" for n in range(12)]


def test_tables_given_no_run_evidence_share_one_vote():
    """A second header is not a second run.

    Tables handed over with nothing saying where a run begins are one run,
    whatever their headers say: the codes on a differently headed page are
    refused by the document's vote, not promoted by their own.
    """
    continuation = RawTable(
        page=2,
        headers=list(SMALL_HEADERS),
        rows=[_continuation_row(2, 10 + n, code) for n, code in enumerate(CAUSE_CODES)],
    )
    assert _claims_from(_large_table(16), continuation) == [
        f"{71004410 + n}" for n in range(16)
    ]
