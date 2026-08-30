"""Claims printed across several physical lines (F18).

Some carriers spend three printed lines on one claim: the claimant on the
first, the identifier and status on the second, the dates and money on the
third, under a header block with one line of labels per record line. Read line
by line, such a document yields claims carrying nothing but a number, and every
date and amount comes back null.

Reconstructing them is worth doing and dangerous to do. If the line beneath one
claim is attached to the claim above when it actually opens the next, the
result is a complete-looking record whose money belongs to somebody else, and
nothing downstream can catch it — the totals still add up. So the fixtures here
are mostly about refusal. Six of the ten present a document where membership
cannot be established and assert that the claim comes back *incomplete*, its
fields null and the document held for review, rather than plausible and wrong.

Every fixture is synthetic. Where one reproduces a real carrier's layout it
copies the structure and never the content (spec section 9).
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.extract_digital import extract_pdf
from core.pipeline import run_pipeline
from core.schema import DocumentStatus

FONT = "helv"
SIZE = 7.0

#: Lines inside one record; lines between two records. Records sit exactly
#: twice as far apart as their own lines do, which is the spacing evidence a
#: reader uses and the only spacing evidence the code is allowed to use.
INTRA = 11.0
INTER = 22.0

#: (x, label on each of the three header lines). Blank where that record line
#: has no column at this position.
COLUMNS = (
    (30, "Claimant Name", "Claim Number", "Loss Date"),
    (150, "Loss Type", "State", "Report Date"),
    (235, "Accident / Loss Description", "Status", "Cause of Loss"),
    (400, "", "", "Paid Indemnity"),
    (490, "", "", "Paid Medical"),
    (580, "", "", "Paid ALAE"),
    (650, "", "", "Total Reserves"),
    (730, "", "", "Total Incurred"),
)

#: Money columns print right-aligned under a left-aligned label, as they do on
#: every real report, so the column-naming has to survive the offset.
RIGHT_ALIGNED = frozenset({3, 4, 5, 6, 7})


def _width(text: str) -> float:
    return pymupdf.get_text_length(text, fontname=FONT, fontsize=SIZE)


def _write(page, text: str, x: float, y: float, *, right: bool = False) -> None:
    if not text:
        return
    origin = x + 55 - _width(text) if right else x
    page.insert_text((origin, y), text, fontname=FONT, fontsize=SIZE)


def _header(page, y: float, lines: int = 3) -> float:
    """The block of labels: one printed line per line of a record."""
    for offset in range(lines):
        for column, entry in enumerate(COLUMNS):
            _write(page, entry[1 + offset], entry[0], y)
        y += INTRA
    return y


class Record:
    """One claim's three printed lines, any of which may be withheld."""

    def __init__(self, name, kind, description, number, state, status,
                 loss_date, report_date, cause, indemnity, medical, alae,
                 reserves, incurred):
        self.lines = [
            (name, kind, description, "", "", "", "", ""),
            (number, state, status, "", "", "", "", ""),
            (loss_date, report_date, cause, indemnity, medical, alae,
             reserves, incurred),
        ]
        self.number = number


def _record(page, record: Record, y: float, *, skip: int | None = None,
            stretch_before: int | None = None) -> float:
    """Print one record, optionally omitting a line or spacing one oddly."""
    for index, values in enumerate(record.lines):
        if index == skip:
            continue
        if index == stretch_before:
            y += INTER - INTRA
        for column, value in enumerate(values):
            _write(page, value, COLUMNS[column][0], y, right=column in RIGHT_ALIGNED)
        y += INTRA
    return y


def _page(document) -> "pymupdf.Page":
    page = document.new_page(width=792, height=560)
    _write(page, "Meridian Assurance Group", 30, 26)
    _write(page, "Valuation Date: 12/31/2024", 620, 26)
    return page


CLAIMS = (
    Record("ROSA VENN", "AUTO", "Rear-ended at the depot gate",
           "701-448120-001", "TX", "Closed",
           "3/14/2024", "3/18/2024", "Collision",
           "1,200.00", "300.00", "150.00", "0.00", "1,650.00"),
    Record("DALE HOOKER", "AUTO", "Struck a bollard reversing",
           "701-448120-002", "TX", "Open",
           "5/02/2024", "5/06/2024", "Collision",
           "400.00", "0.00", "75.00", "900.00", "1,375.00"),
    Record("PRIYA NAIR", "GL", "Slip on a wet loading bay",
           "702-330941-001", "AZ", "Open",
           "9/18/2024", "9/20/2024", "Slip/Fall",
           "0.00", "250.00", "0.00", "2,000.00", "2,250.00"),
)


def _run(tmp_path, name: str, build) -> "object":
    path = tmp_path / f"{name}.pdf"
    document = pymupdf.open()
    build(document)
    document.save(str(path))
    document.close()
    return path


def _claims(path):
    return {claim.claim_number: claim for claim in
            run_pipeline(path, use_vision=False).document.claims}


# --------------------------------------------------------------------------
# 1. The layout read correctly: three lines become one claim
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def three_line(tmp_path_factory):
    def build(document):
        page = _page(document)
        y = _header(page, 60)
        y += INTER
        for record in CLAIMS:
            y = _record(page, record, y) + (INTER - INTRA)
    return _run(tmp_path_factory.mktemp("f18"), "three_line", build)


def test_a_three_line_record_arrives_as_one_complete_claim(three_line):
    claims = _claims(three_line)
    assert set(claims) == {record.number for record in CLAIMS}
    first = claims["701-448120-001"]
    assert first.claimant_name == "ROSA VENN"
    assert first.date_of_loss.isoformat() == "2024-03-14"
    assert first.date_reported.isoformat() == "2024-03-18"
    assert first.paid_indemnity == Decimal("1200.00")
    assert first.paid_medical == Decimal("300.00")
    assert first.paid_expense == Decimal("150.00")
    assert first.reserve_total == Decimal("0.00")
    assert first.incurred_total == Decimal("1650.00")


def test_the_record_keeps_every_line_it_was_read_from(three_line):
    """Provenance survives reconstruction: a merged row says which lines."""
    table = extract_pdf(three_line).tables[0]
    assert table.strategy == "records"
    assert [len(row.source_lines) for row in table.rows] == [3, 3, 3]
    assert all(
        row.source_lines == list(range(row.source_lines[0], row.source_lines[0] + 3))
        for row in table.rows
    )


def test_the_money_belongs_to_the_claim_it_was_printed_under(three_line):
    """The whole risk of the mechanism, stated as an assertion."""
    claims = _claims(three_line)
    assert claims["701-448120-002"].reserve_total == Decimal("900.00")
    assert claims["702-330941-001"].reserve_total == Decimal("2000.00")
    assert claims["702-330941-001"].paid_medical == Decimal("250.00")


# --------------------------------------------------------------------------
# 2. Two lines, not three: the height comes from the document
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def two_line(tmp_path_factory):
    """The same problem with a shorter record, so "three" cannot be assumed."""
    def build(document):
        page = _page(document)
        y = 60
        for offset in (1, 2):
            for entry in COLUMNS:
                _write(page, entry[1 + offset], entry[0], y)
            y += INTRA
        y += INTER
        for record in CLAIMS:
            for values in record.lines[1:]:
                for column, value in enumerate(values):
                    _write(page, value, COLUMNS[column][0], y,
                           right=column in RIGHT_ALIGNED)
                y += INTRA
            y += INTER - INTRA
    return _run(tmp_path_factory.mktemp("f18"), "two_line", build)


def test_a_two_line_record_is_read_as_two_lines(two_line):
    claims = _claims(two_line)
    assert set(claims) == {record.number for record in CLAIMS}
    assert claims["701-448120-001"].incurred_total == Decimal("1650.00")
    assert claims["701-448120-002"].date_of_loss.isoformat() == "2024-05-02"
    assert extract_pdf(two_line).tables[0].strategy == "records"


# --------------------------------------------------------------------------
# 3. A section total printed hard against the last claim
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def with_section_total(tmp_path_factory):
    """The dangerous adjacency: a money line one record-line below the last claim."""
    def build(document):
        page = _page(document)
        y = _header(page, 60)
        y += INTER
        for record in CLAIMS:
            y = _record(page, record, y) + (INTER - INTRA)
        y -= INTER - INTRA  # flush against the claim above it
        for column, value in enumerate(
            ("Section Totals:", "", "Claim Count = 3",
             "1,600.00", "550.00", "225.00", "2,900.00", "5,275.00")
        ):
            _write(page, value, COLUMNS[column][0], y, right=column in RIGHT_ALIGNED)
    return _run(tmp_path_factory.mktemp("f18"), "section_total", build)


def test_a_section_total_is_never_absorbed_into_the_claim_above_it(with_section_total):
    """Its money would look perfectly plausible on the last claim."""
    claims = _claims(with_section_total)
    assert set(claims) == {record.number for record in CLAIMS}
    last = claims["702-330941-001"]
    assert last.incurred_total == Decimal("2250.00")
    assert last.paid_indemnity == Decimal("0.00")


def test_the_section_total_is_kept_as_a_total_not_as_a_claim(with_section_total):
    table = extract_pdf(with_section_total).tables[0]
    assert len(table.rows) == 3
    assert any("5,275.00" in row.text() for row in table.total_rows)


# --------------------------------------------------------------------------
# 4. The header block repeated part-way down the page
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def repeated_header(tmp_path_factory):
    def build(document):
        page = _page(document)
        y = _header(page, 60) + INTER
        y = _record(page, CLAIMS[0], y) + (INTER - INTRA)
        y = _header(page, y + INTER) + INTER
        for record in CLAIMS[1:]:
            y = _record(page, record, y) + (INTER - INTRA)
    return _run(tmp_path_factory.mktemp("f18"), "repeated_header", build)


def test_a_repeated_header_block_is_not_read_as_part_of_a_claim(repeated_header):
    claims = _claims(repeated_header)
    assert set(claims) == {record.number for record in CLAIMS}
    assert claims["701-448120-002"].claimant_name == "DALE HOOKER"
    assert claims["701-448120-002"].incurred_total == Decimal("1375.00")


# --------------------------------------------------------------------------
# 5. Policy metadata printed between two claims
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def with_metadata(tmp_path_factory):
    def build(document):
        page = _page(document)
        y = _header(page, 60) + INTER
        y = _record(page, CLAIMS[0], y) + (INTER - INTRA)
        _write(page, "Policy Number: PN-4471  Term 01/01/2024 - 01/01/2025", 30, y)
        y += INTER
        for record in CLAIMS[1:]:
            y = _record(page, record, y) + (INTER - INTRA)
    return _run(tmp_path_factory.mktemp("f18"), "metadata", build)


def test_a_metadata_line_between_claims_joins_neither_of_them(with_metadata):
    claims = _claims(with_metadata)
    assert set(claims) == {record.number for record in CLAIMS}
    assert claims["701-448120-001"].incurred_total == Decimal("1650.00")
    assert claims["701-448120-002"].incurred_total == Decimal("1375.00")


# --------------------------------------------------------------------------
# 6. A record cut in half by the end of the page
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def split_across_pages(tmp_path_factory):
    """The last claim's money is overleaf. Nothing on page two may supply it."""
    def build(document):
        page = _page(document)
        y = _header(page, 60) + INTER
        for record in CLAIMS[:2]:
            y = _record(page, record, y) + (INTER - INTRA)
        for values in CLAIMS[2].lines[:2]:  # claimant and identifier only
            for column, value in enumerate(values):
                _write(page, value, COLUMNS[column][0], y)
            y += INTRA

        second = _page(document)
        y = _header(second, 60) + INTER
        for column, value in enumerate(CLAIMS[2].lines[2]):
            _write(second, value, COLUMNS[column][0], y,
                   right=column in RIGHT_ALIGNED)
    return _run(tmp_path_factory.mktemp("f18"), "split_pages", build)


def test_a_record_cut_by_the_page_edge_stays_incomplete(split_across_pages):
    """Its dates and money are on the next page and are not guessed at."""
    result = run_pipeline(split_across_pages, use_vision=False)
    claims = {claim.claim_number: claim for claim in result.document.claims}
    assert "702-330941-001" in claims
    truncated = claims["702-330941-001"]
    assert truncated.date_of_loss is None
    assert truncated.incurred_total is None
    assert truncated.reserve_total is None
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_the_complete_records_on_that_page_are_unaffected(split_across_pages):
    claims = _claims(split_across_pages)
    assert claims["701-448120-001"].incurred_total == Decimal("1650.00")
    assert claims["701-448120-002"].incurred_total == Decimal("1375.00")


# --------------------------------------------------------------------------
# 7. A record with one of its lines missing
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def missing_line(tmp_path_factory):
    """The second claim prints no claimant line, so the shape no longer fits."""
    def build(document):
        page = _page(document)
        y = _header(page, 60) + INTER
        y = _record(page, CLAIMS[0], y) + (INTER - INTRA)
        y = _record(page, CLAIMS[1], y, skip=0) + (INTER - INTRA)
        _record(page, CLAIMS[2], y)
    return _run(tmp_path_factory.mktemp("f18"), "missing_line", build)


def test_a_short_record_refuses_the_page_rather_than_reach_for_a_line(missing_line):
    """One claim that does not fit the shape means the shape is not the page's.

    The line above the short record belongs to the first claim, and pairing it
    with the second is available, cheap and wrong. Rather than take the records
    that happen to fit and reach for the one that does not, the page is read a
    line at a time and every claim on it comes back incomplete.
    """
    claims = _claims(missing_line)
    assert set(claims) == {record.number for record in CLAIMS}
    assert all(claim.incurred_total is None for claim in claims.values())
    assert all(claim.date_of_loss is None for claim in claims.values())


def test_no_claim_on_a_refused_page_carries_another_claim_s_money(missing_line):
    """Refusing costs three complete claims. This is what it buys."""
    printed = {Decimal("1650.00"), Decimal("1375.00"), Decimal("2250.00")}
    for claim in _claims(missing_line).values():
        assert claim.incurred_total not in printed
        assert claim.paid_indemnity is None


# --------------------------------------------------------------------------
# 8. Blank amounts inside a record stay null
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def blank_amounts(tmp_path_factory):
    """The middle claim prints no medical and no reserve at all."""
    def build(document):
        page = _page(document)
        y = _header(page, 60) + INTER
        y = _record(page, CLAIMS[0], y) + (INTER - INTRA)
        sparse = Record("DALE HOOKER", "AUTO", "Struck a bollard reversing",
                        "701-448120-002", "TX", "Open",
                        "5/02/2024", "5/06/2024", "Collision",
                        "400.00", "", "75.00", "", "475.00")
        y = _record(page, sparse, y) + (INTER - INTRA)
        _record(page, CLAIMS[2], y)
    return _run(tmp_path_factory.mktemp("f18"), "blank_amounts", build)


def test_an_amount_the_carrier_did_not_print_is_null_and_not_zero(blank_amounts):
    """Nothing printed is not the same fact as nothing paid."""
    claims = _claims(blank_amounts)
    sparse = claims["701-448120-002"]
    assert sparse.paid_medical is None
    assert sparse.reserve_total is None
    assert sparse.paid_indemnity == Decimal("400.00")
    assert sparse.incurred_total == Decimal("475.00")


def test_a_printed_zero_is_still_a_zero(blank_amounts):
    assert _claims(blank_amounts)["701-448120-001"].reserve_total == Decimal("0.00")


# --------------------------------------------------------------------------
# 9. An ordinary flat table under a wrapped header is left alone
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def flat_table(tmp_path_factory):
    """Two printed header lines that are two halves of one row of labels."""
    def build(document):
        page = _page(document)
        upper = ("", "Date of", "Total", "Total", "Total")
        lower = ("Claim Number", "Loss", "Paid", "Reserves", "Incurred")
        positions = (30, 150, 260, 370, 480)
        for offset, labels in enumerate((upper, lower)):
            for x, label in zip(positions, labels):
                _write(page, label, x, 60 + offset * INTRA)
        y = 60 + 2 * INTRA + INTER
        for record in CLAIMS:
            loss_date, _, _, indemnity, _, _, reserves, incurred = record.lines[2]
            for x, value in zip(
                positions, (record.number, loss_date, indemnity, reserves, incurred)
            ):
                _write(page, value, x, y)
            y += INTRA
    return _run(tmp_path_factory.mktemp("f18"), "flat_table", build)


def test_a_wrapped_header_over_a_flat_table_is_not_read_as_a_record(flat_table):
    """The mechanism must not fire on the layout it was not built for."""
    assert extract_pdf(flat_table).tables[0].strategy != "records"


def test_the_flat_table_still_reads_every_claim(flat_table):
    claims = _claims(flat_table)
    assert set(claims) == {record.number for record in CLAIMS}
    assert claims["701-448120-001"].incurred_total == Decimal("1650.00")
    assert claims["702-330941-001"].reserve_total == Decimal("2000.00")


# --------------------------------------------------------------------------
# 10. Spacing that cannot settle membership
#
# The fixture the rest of this file exists for. A claim's money sits as far
# from its identifier as the next claim's does, so nothing on the page says
# which of the two it belongs to. There is a plausible answer available and it
# must not be given.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ambiguous_spacing(tmp_path_factory):
    def build(document):
        page = _page(document)
        y = _header(page, 60) + INTER
        y = _record(page, CLAIMS[0], y) + (INTER - INTRA)
        y = _record(page, CLAIMS[1], y, stretch_before=2) + (INTER - INTRA)
        _record(page, CLAIMS[2], y)
    return _run(tmp_path_factory.mktemp("f18"), "ambiguous", build)


def test_an_unsettled_record_is_left_incomplete(ambiguous_spacing):
    """No date, no money, and a document that says it needs a human."""
    result = run_pipeline(ambiguous_spacing, use_vision=False)
    claims = {claim.claim_number: claim for claim in result.document.claims}
    unsettled = claims["701-448120-002"]
    assert unsettled.date_of_loss is None
    assert unsettled.incurred_total is None
    assert unsettled.paid_indemnity is None
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_its_money_is_not_quietly_given_to_a_neighbour(ambiguous_spacing):
    """The failure mode this whole file guards against.

    1,375.00 is printed on the page and belongs to a claim whose membership
    could not be settled. It must reach no claim at all — least of all either
    of the two it sits between, where it would look entirely at home.
    """
    claims = _claims(ambiguous_spacing)
    assert set(claims) == {record.number for record in CLAIMS}
    assert Decimal("1375.00") not in {
        claim.incurred_total for claim in claims.values()
    }
    assert all(claim.incurred_total is None for claim in claims.values())
