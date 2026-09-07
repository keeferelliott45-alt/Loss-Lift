"""A loss run exported from a spreadsheet, and what that shape does to reading.

Carriers outside the US mainframe tradition often send the run as a wide
spreadsheet printed to PDF. Nothing about it is exotic — one claim per line, a
heading above each column — and it broke four separate things at once:

* the heading wraps onto a **second line below** the one that scores as the
  header, so half the labels were never read and the line itself was left in
  the body, where its words close the very gutters between the columns it is
  naming;
* one claimant name long enough to touch the currency beside it **closes a
  column boundary for the whole page**, because a gutter is space every row
  leaves blank;
* the amounts are **right-aligned under left-aligned labels**, so a label's
  first word lands in the column before it;
* the facts that belong to the document — the company, the insured, the policy
  and its term — are printed as **columns repeated on every claim** rather than
  in a letterhead, so the letterhead scan read the table's own headings and
  reported the carrier as "Status Currency Indemnity".

The first fixture reproduces all four with synthetic content. The rest ask what
else the fixes could break: they are the documents the new rules would get
wrong if the rules were any looser.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pymupdf
import pytest

from core.extract_digital import extract_pdf
from core.pipeline import run_pipeline
from core.schema import DocumentStatus, LineOfBusiness

FONT = "helv"
SIZE = 7.0
WIDTH, HEIGHT = 1240, 400


def _width(text: str) -> float:
    return pymupdf.get_text_length(text, fontname=FONT, fontsize=SIZE)


def _write(page, text: str, x: float, y: float, *, right: float | None = None) -> None:
    if not text:
        return
    page.insert_text(
        (right - _width(text) if right else x, y), text, fontname=FONT, fontsize=SIZE
    )


#: (x, upper label, lower label, right edge for right-aligned values)
COLUMNS = (
    (30, "Company Name", "", None),
    (180, "Insured Name", "", None),
    (330, "Policy Number", "", None),
    (430, "Inception Date", "", None),
    (510, "Expiry Date", "", None),
    (590, "Claim Number", "", None),
    (670, "Claim Description", "", None),
    (810, "Trigger Date", "", None),
    (880, "Claim", "Status", None),
    (930, "Claimant Name", "", None),
    (1010, "Reporting", "Currency", None),
    (1050, "Gross Reserve", "Indemnity", 1120),
    (1130, "Payment Indemnity", "", 1210),
)

CLAIMS = (
    ("Meridian Assurance SE", "COMUNE DI VALDERRA", "IT00099001LI12",
     "01-Nov-12", "01-Nov-13", "0004112801", "Hole in the road surface",
     "28-Jan-13", "Closed", "DE ANGELIS", "EUR", "0,00", "199,65"),
    ("Meridian Assurance SE", "COMUNE DI VALDERRA", "IT00099001LI12",
     "01-Nov-12", "01-Nov-13", "0004112802", "Fall on the sidewalk",
     "06-Dec-12", "Closed", "MINCUZZI DE ROVERE", "EUR", "0,00", "33.701,30"),
    ("Meridian Assurance SE", "COMUNE DI VALDERRA", "IT00099001LI12",
     "01-Nov-12", "01-Nov-13", "0004112803", "Power line construction damage",
     "15-Jan-13", "Open", "CICCOTTI", "EUR", "5.500,00", "600,00"),
    ("Meridian Assurance SE", "COMUNE DI VALDERRA", "IT00104477LI14",
     "01-Nov-14", "01-Nov-15", "0004330912", "Uneven road, bodily injury",
     "20-Aug-15", "Reopened", "SCHIBONI", "EUR", "1.250,50", "8.054,63"),
)


def _build(path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _write(page, "Loss Run as per September 13, 2016", 30, 30)

    for x, upper, lower, _ in COLUMNS:
        _write(page, upper, x, 60)
        _write(page, lower, x, 72)

    y = 100
    for row in CLAIMS:
        for (x, _, _, right), value in zip(COLUMNS, row):
            _write(page, value, x, y, right=right)
        y += 14

    # A footer at its own positions, on its own line, well below the table.
    # Its words run together across the gutter between the first two columns.
    for x, word in ((30, "sinistrosita_sopra"), (110, "SIR_Loss"), (150, "Run"),
                    (172, "per"), (196, "20160913")):
        _write(page, word, x, y + 60)
    _write(page, "Page 1 of 1", 600, y + 60)
    document.save(str(path))
    document.close()


@pytest.fixture(scope="module")
def export(tmp_path_factory):
    path = tmp_path_factory.mktemp("xls") / "spreadsheet_export.pdf"
    _build(path)
    return path


@pytest.fixture(scope="module")
def result(export):
    return run_pipeline(export, use_vision=False)


# --------------------------------------------------------------------------
# The columns, read as printed
# --------------------------------------------------------------------------


def test_every_column_is_found_and_named(export):
    """Thirteen columns, including the three whose labels wrap downwards."""
    headers = extract_pdf(export).tables[0].headers
    assert "Trigger Date" in headers
    assert "Claim Status" in headers
    assert "Reporting Currency" in headers
    assert "Gross Reserve Indemnity" in headers
    assert "Payment Indemnity" in headers


def test_the_second_line_of_the_heading_is_not_read_as_a_claim(export):
    """Left in the body it becomes a row, and closes the gutters it names."""
    rows = extract_pdf(export).tables[0].rows
    assert all("Currency" not in row.text() for row in rows)


def test_a_claimant_name_touching_the_currency_keeps_them_apart(result):
    """One overlong cell must not merge two columns for the whole page."""
    claims = {claim.claim_number: claim for claim in result.document.claims}
    assert claims["0004112802"].claimant_name == "MINCUZZI DE ROVERE"
    # The currency beside it kept its own column, so the document could read it.
    assert result.document.currency == "EUR"


def test_right_aligned_amounts_land_under_their_own_labels(result):
    """Off by one column and every reserve would be read as a payment."""
    claims = {claim.claim_number: claim for claim in result.document.claims}
    assert claims["0004112801"].reserve_indemnity == Decimal("0.00")
    assert claims["0004112801"].paid_indemnity == Decimal("199.65")
    assert claims["0004112803"].reserve_indemnity == Decimal("5500.00")
    assert claims["0004112803"].paid_indemnity == Decimal("600.00")


def test_european_amounts_and_month_name_dates_are_read(result):
    claims = {claim.claim_number: claim for claim in result.document.claims}
    assert claims["0004112802"].paid_indemnity == Decimal("33701.30")
    assert claims["0004112802"].date_of_loss == date(2012, 12, 6)
    assert result.document.locale_hint == "eu"


def test_an_incurred_total_the_carrier_never_printed_is_not_invented(result):
    """Reserve plus payment is not an incurred figure this document stated."""
    assert all(claim.incurred_total is None for claim in result.document.claims)
    assert "R-07" in {f.rule_id for f in result.reconciliation.findings}
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# Facts printed as columns instead of a letterhead
# --------------------------------------------------------------------------


def test_the_carrier_comes_from_the_column_that_names_it(result):
    """The letterhead here *is* the table's heading, and named no carrier."""
    assert result.document.carrier == "Meridian Assurance SE"
    assert result.document.named_insured == "COMUNE DI VALDERRA"
    assert result.document.currency == "EUR"
    assert result.document.line_of_business is None or isinstance(
        result.document.line_of_business, LineOfBusiness
    )


def test_the_valuation_date_comes_off_the_title(result):
    assert result.document.valuation_date == date(2016, 9, 13)


def test_two_policies_are_not_collapsed_into_one(result):
    """Three claims on one policy and one on another is not one policy."""
    assert result.document.policy_number is None
    assert (date(2012, 11, 1), date(2013, 11, 1)) in result.document.policy_periods
    assert (date(2014, 11, 1), date(2015, 11, 1)) in result.document.policy_periods


def test_the_footer_neither_becomes_a_claim_nor_names_the_carrier(result):
    numbers = {claim.claim_number for claim in result.document.claims}
    assert numbers == {row[5] for row in CLAIMS}
    assert "SIR_Loss" not in (result.document.carrier or "")


# --------------------------------------------------------------------------
# What else could these rules break?
# --------------------------------------------------------------------------


def _simple(page, columns, rows, *, gap: int = 14) -> None:
    for x, upper, lower in columns:
        _write(page, upper, x, 60)
        _write(page, lower, x, 72)
    y = 100
    for row in rows:
        for (x, _, _), value in zip(columns, row):
            _write(page, value, x, y)
        y += gap


@pytest.fixture(scope="module")
def wide_description(tmp_path_factory):
    """A centred two-word heading over a column of running prose.

    The header shows a wide gap in the middle of the description column, which
    is exactly the evidence used to cut a column in two. Cutting here would
    slice every description in half, so the rows have to agree — and prose
    leaves different space blank on every line, so they never will.
    """
    path = tmp_path_factory.mktemp("xls") / "wide_description.pdf"
    document = pymupdf.open()
    page = document.new_page(width=900, height=300)
    _write(page, "Northgate Fire & Marine", 30, 30)
    # "Accident" and "Description" head one column and are set a hundred points
    # apart, which is the very evidence used to cut a column in two.
    for x, label in ((30, "Claim Number"), (140, "Date of Loss"),
                     (230, "Accident"), (330, "Description"),
                     (600, "Total Incurred")):
        _write(page, label, x, 60)
    y = 90
    for row in (
        ("CL-9001", "3/4/2024", "struck a parked vehicle while reversing", "1,200.00"),
        ("CL-9002", "7/15/2024", "slipped on a wet floor near the entrance", "800.00"),
        ("CL-9003", "9/18/2024", "load shifted and fell from the tail lift", "450.00"),
    ):
        for x, value in zip((30, 140, 230, 600), row):
            _write(page, value, x, y)
        y += 14
    document.save(str(path))
    document.close()
    return path


def test_a_two_word_heading_over_prose_does_not_split_the_column(wide_description):
    """The header asserts a gap; the rows never agree on one. No cut."""
    claims = run_pipeline(wide_description, use_vision=False).document.claims
    descriptions = {claim.claim_number: claim.loss_description for claim in claims}
    assert descriptions["CL-9001"] == "struck a parked vehicle while reversing"
    assert descriptions["CL-9003"] == "load shifted and fell from the tail lift"


@pytest.fixture(scope="module")
def text_subheading(tmp_path_factory):
    """A section heading of pure text printed directly under the header.

    The block walks downwards while the lines carry no figures, which is what
    finds a wrapped label. A section heading carries no figures either, and
    swallowing it would put "GENERAL LIABILITY" into the name of a column.
    """
    path = tmp_path_factory.mktemp("xls") / "subheading.pdf"
    document = pymupdf.open()
    page = document.new_page(width=900, height=300)
    _write(page, "Keystone Regional Insurance", 30, 30)
    for x, label in ((30, "Claim Number"), (140, "Date of Loss"),
                     (240, "Status"), (330, "Total Paid"),
                     (430, "Total Reserves"), (540, "Total Incurred")):
        _write(page, label, x, 60)
    _write(page, "GENERAL LIABILITY", 30, 74)
    y = 96
    for row in (("KR-3001", "2/2/2024", "Open", "500.00", "250.00", "750.00"),
                ("KR-3002", "5/9/2024", "Closed", "300.00", "0.00", "300.00")):
        for x, value in zip((30, 140, 240, 330, 430, 540), row):
            _write(page, value, x, y)
        y += 14
    document.save(str(path))
    document.close()
    return path


def test_a_text_section_heading_is_not_swallowed_into_the_column_labels(text_subheading):
    """Every column keeps its own name, and every claim its own money."""
    result = run_pipeline(text_subheading, use_vision=False)
    claims = {claim.claim_number: claim for claim in result.document.claims}
    assert set(claims) == {"KR-3001", "KR-3002"}
    assert claims["KR-3001"].paid_total == Decimal("500.00")
    assert claims["KR-3001"].reserve_total == Decimal("250.00")
    assert claims["KR-3001"].incurred_total == Decimal("750.00")


@pytest.fixture(scope="module")
def two_carriers(tmp_path_factory):
    """One sheet, two carriers. Neither of them is *the* carrier."""
    path = tmp_path_factory.mktemp("xls") / "two_carriers.pdf"
    document = pymupdf.open()
    page = document.new_page(width=900, height=300)
    _simple(
        page,
        ((30, "Company Name", ""), (200, "Claim Number", ""),
         (300, "Date of Loss", ""), (400, "Total Incurred", "")),
        (("Ardent Mutual Insurance", "AM-01", "3/4/2024", "1,000.00"),
         ("Ardent Mutual Insurance", "AM-02", "5/1/2024", "2,000.00"),
         ("Colworth Indemnity Company", "CI-01", "8/8/2024", "3,000.00")),
    )
    document.save(str(path))
    document.close()
    return path


def test_a_column_naming_two_carriers_names_neither(two_carriers):
    """Picking the commoner one would file a third of the book wrongly."""
    document = run_pipeline(two_carriers, use_vision=False).document
    assert document.carrier is None
    assert len(document.claims) == 3


@pytest.fixture(scope="module")
def real_letterhead(tmp_path_factory):
    """A carrier printed above the table, the ordinary way."""
    path = tmp_path_factory.mktemp("xls") / "letterhead.pdf"
    document = pymupdf.open()
    page = document.new_page(width=900, height=300)
    _write(page, "Statewide Mutual Insurance Company", 30, 30)
    _write(page, "Valuation Date: 12/31/2024", 500, 30)
    _simple(
        page,
        ((30, "Claim Number", ""), (160, "Date of Loss", ""),
         (280, "Status", ""), (380, "Total Paid", ""),
         (480, "Total Reserves", ""), (600, "Total Incurred", "")),
        (("SM-01", "3/4/2024", "Open", "500.00", "250.00", "750.00"),
         ("SM-02", "6/6/2024", "Closed", "100.00", "0.00", "100.00")),
    )
    document.save(str(path))
    document.close()
    return path


def test_a_letterhead_carrier_is_still_read(real_letterhead):
    """The new column rule must not cost the ordinary case anything."""
    document = run_pipeline(real_letterhead, use_vision=False).document
    assert document.carrier == "Statewide Mutual Insurance Company"
    assert len(document.claims) == 2
