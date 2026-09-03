"""Money read off a row that could not be attached to a claim.

The extractor already knows the difference between a wrapped description and a
row of figures: a continuation line is prose, and a line whose cells parse under
mapped money columns is data whose claim number could not be identified. Having
made that distinction it discarded the second kind to a warning, and warnings
are not findings -- they do not block a clean badge, do not reach the Exceptions
sheet, and do not survive into the workbook.

So a document could read as reconciled while an amount it had itself parsed sat
nowhere. The rules that would normally catch a missing figure cannot: R-04 and
R-05 verify against a printed total or claim count, and a document that prints
neither has no anchor at all. That is the shape reproduced here.

The fixture is wholly invented -- made-up identifiers, no names, no addresses,
no descriptions. It reproduces the structure of the failure and none of its
content.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.pipeline import run_pipeline
from core.schema import DocumentStatus, Severity

LINE = 14.0
LEFT = 40.0
#: Wide, even columns so the gutters are unambiguous and column detection is
#: not what the test is measuring.
COLUMNS = (0.0, 95.0, 175.0, 245.0, 330.0, 415.0)
HEADERS = ("Claim No", "Date of Loss", "Status", "Paid Total", "Reserve Total", "Total Incurred")

#: Three claims whose money ties: paid + reserve == incurred, nothing null,
#: numbers unique, dates inside the stated term. Everything the engine checks
#: about a claim passes, so nothing but the discarded row can raise an error.
CLAIMS = (
    ("CN-1001", "03/12/2024", "OPEN", "1,200.00", "3,800.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "2,450.00", "0.00", "2,450.00"),
    ("CN-1003", "09/21/2024", "OPEN", "775.50", "1,224.50", "2,000.00"),
)


def _write(path, rows, *, headers=HEADERS, footer_lines=()):
    """A single-page loss run with no printed totals and no claim count.

    Deliberately anchorless: with no totals row and no printed count, R-04 and
    R-05 have nothing to verify against, which is what makes a dropped amount
    invisible to every other rule.
    """
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
        "Currency: USD",
    ):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE

    for offset, label in zip(COLUMNS, headers):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE

    for row in rows:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE

    for line in footer_lines:
        page.insert_text((LEFT, y), line, fontsize=8.5)
        y += LINE

    document.save(path)
    document.close()
    return path


@pytest.fixture()
def anchorless(tmp_path):
    """Three sound claims, no printed totals, nothing dropped."""
    return _write(tmp_path / "anchorless.pdf", CLAIMS)


def test_the_fixture_is_clean_without_the_dropped_row(anchorless):
    """The control. Everything else about this document reconciles."""
    result = run_pipeline(anchorless, use_vision=False)
    assert len(result.document.claims) == 3
    assert result.document.printed_claim_count is None
    assert not result.document.printed_totals, "no anchor to verify against"
    assert not [f for f in result.reconciliation.findings if f.rule_id in {"R-04", "R-05"}]
    assert result.reconciliation.status is DocumentStatus.CLEAN


def test_a_dropped_money_row_stops_the_document_reading_as_clean(tmp_path):
    """The same document with one unattachable row carrying figures."""
    path = _write(
        tmp_path / "dropped.pdf",
        CLAIMS + (("", "", "", "9,400.00", "600.00", "10,000.00"),),
    )
    result = run_pipeline(path, use_vision=False)

    assert len(result.document.claims) == 3, "the row is not turned into a claim"
    assert not [f for f in result.reconciliation.findings if f.rule_id in {"R-04", "R-05"}]
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    raised = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert len(raised) == 1
    assert raised[0].severity is Severity.ERROR


# --------------------------------------------------------------------------
# The Austin shape, without joining anything
# --------------------------------------------------------------------------


def test_a_column_split_export_reports_the_money_it_could_not_place(tmp_path):
    """A spreadsheet paginated by column, not by row.

    Page one carries the identifiers, page two the amounts for the same sheet
    rows. This unit does not join them -- guessing which claim an amount on
    another page belongs to is exactly the invention the product exists to
    avoid. It reports that the money was read and not placed, which is the
    difference between a thin document and a wrong one.
    """
    document = pymupdf.open()
    first = document.new_page(width=612, height=792)
    y = 50.0
    for line in ("MERIDIAN MUTUAL ASSURANCE", "LOSS RUN REPORT",
                 "Valuation Date: 12/31/2024",
                 "Policy Period: 01/01/2024 to 12/31/2024"):
        first.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, HEADERS):
        first.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in CLAIMS:
        for offset, cell in zip(COLUMNS, row):
            first.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE

    # The continuation page: the same sheet rows, the right-hand columns only.
    second = document.new_page(width=612, height=792)
    y = 50.0
    for offset, label in zip(COLUMNS[3:], HEADERS[3:]):
        second.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for amounts in (("4,100.00", "900.00", "5,000.00"),
                    ("2,450.00", "0.00", "2,450.00"),
                    ("1,000.00", "1,000.00", "2,000.00")):
        for offset, cell in zip(COLUMNS[3:], amounts):
            second.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE

    path = tmp_path / "column-split.pdf"
    document.save(path)
    document.close()

    result = run_pipeline(path, use_vision=False)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    raised = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert raised, "money on the continuation page must not vanish"
    assert all(f.page == 2 for f in raised), "reported where it was printed"
    assert len({f.condition for f in raised}) == len(raised), "one identity per row"


def test_several_dropped_rows_keep_distinct_provenance_and_identity(tmp_path):
    path = _write(
        tmp_path / "several.pdf",
        CLAIMS
        + (("", "", "", "9,400.00", "600.00", "10,000.00"),
           ("", "", "", "1,250.00", "250.00", "1,500.00")),
    )
    result = run_pipeline(path, use_vision=False)
    dropped = result.document.unplaced_rows
    assert len(dropped) == 2
    assert len({(d.page, d.row) for d in dropped}) == 2, "distinct rows"
    raised = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert len(raised) == 2
    assert len({f.condition for f in raised}) == 2, "resolving one leaves the other"


# --------------------------------------------------------------------------
# What counts as money, and what does not
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount",
    ["$9,400.00", "(1,234.56)", "1,234.56-", "9,400.00 CR", "-2,500.00"],
)
def test_printed_money_formats_are_recognised(tmp_path, amount):
    """Currency symbols, accounting parentheses, trailing and leading minus,
    and credit markers -- every convention the parser already understands."""
    path = _write(tmp_path / "money.pdf", CLAIMS + (("", "", "", amount, "", ""),))
    result = run_pipeline(path, use_vision=False)
    assert result.document.unplaced_rows, f"{amount!r} is money"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


#: The same three claims written the other way round, so the document's own
#: tokens establish the convention rather than a guess about the amount.
EU_CLAIMS = (
    ("CN-1001", "12.03.2024", "OPEN", "1.200,00", "3.800,00", "5.000,00"),
    ("CN-1002", "04.05.2024", "CLOSED", "2.450,00", "0,00", "2.450,00"),
    ("CN-1003", "21.09.2024", "OPEN", "775,50", "1.224,50", "2.000,00"),
)


def test_eu_formatted_money_is_recognised_where_the_document_supports_it(tmp_path):
    """Locale evidence decides, and it comes from the document's own numbers.

    The same text in a US-locale run is a different amount, so nothing here is
    read from the dropped row alone.
    """
    path = _write(
        tmp_path / "eu.pdf", EU_CLAIMS + (("", "", "", "9.400,00", "", ""),)
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.unplaced_rows
    assert result.document.unplaced_rows[0].amounts == {"paid_total": "9.400,00"}
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "row",
    [
        ("", "12/31/2024", "", "", "", ""),          # a date
        ("", "", "2024", "", "", ""),                # a year
        ("", "", "", "", "", ""),                    # blank separator
        ("", "", "Page 2 of 3", "", "", ""),         # a page marker
        ("", "", "GL-4417-2024", "", "", ""),        # a policy number
        ("", "", "continued from the previous page", "", "", ""),  # prose
    ],
)
def test_ordinary_numbers_and_text_do_not_become_money(tmp_path, row):
    """Nothing outside a mapped money column is money, whatever it looks like."""
    path = _write(tmp_path / "ordinary.pdf", CLAIMS + (row,))
    result = run_pipeline(path, use_vision=False)
    assert result.document.unplaced_rows == []
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert result.reconciliation.status is DocumentStatus.CLEAN


def test_a_bare_integer_under_a_money_column_is_not_reported(tmp_path):
    """A count or an index sharing a money column carries no separator."""
    path = _write(tmp_path / "bare.pdf", CLAIMS + (("", "", "", "7", "", ""),))
    result = run_pipeline(path, use_vision=False)
    assert result.document.unplaced_rows == []
    assert result.reconciliation.status is DocumentStatus.CLEAN


def test_a_recognised_totals_row_does_not_become_a_finding(tmp_path):
    """Footer totals are consumed as structure; they are not unplaced money."""
    path = _write(
        tmp_path / "totals.pdf", CLAIMS,
        footer_lines=("TOTALS: 4,425.50 5,024.50 9,450.00", "Total Claims: 3"),
    )
    result = run_pipeline(path, use_vision=False)
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert result.document.unplaced_rows == []


def test_text_only_dropped_rows_remain_ordinary_warnings(tmp_path):
    """Nothing measurable went with them, so they stay out of the rules."""
    path = _write(
        tmp_path / "prose.pdf",
        CLAIMS + (("", "", "", "", "", ""), ("see adjuster file for detail", "", "", "", "", "")),
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.unplaced_rows == []
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-23"]


# --------------------------------------------------------------------------
# A tie is not proof: neither the whole column's, nor one claim's
# --------------------------------------------------------------------------
#
# Two suppressions were tried while this rule was built and both were proven
# unsafe on real documents before either shipped: excusing a row because its
# *field* summed to the printed total, and excusing it because its *value*
# matched some claim's own figure on the same page. Both times the excused
# row turned out to be a real, previously-uncaptured policy-section total
# that only looked accounted for by coincidence -- once because two other
# unplaced rows on the same field happened to cancel, once because every
# other claim on the page happened to be zero in that field, leaving the
# section sum equal to the one claim that was not. Real corpus, not a
# constructed case; the fixtures below reproduce the shape of both.


def _write_with_footer(path, extra_rows, footer_row_cells):
    """A single-page loss run whose footer prints real, column-mapped totals.

    Unlike ``_write``, the footer numbers sit under the same column offsets
    as the data, so they are read as a genuine per-column totals row -- the
    only way a figure actually reaches ``printed_totals`` -- rather than one
    run-on string a total-row detector cannot parse into fields.
    """
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    y = 50.0
    for line in (
        "MERIDIAN MUTUAL ASSURANCE", "LOSS RUN REPORT",
        "Named Insured: Northwind Fabrication Ltd",
        "Policy Number: GL-4417-2024",
        "Policy Period: 01/01/2024 to 12/31/2024",
        "Valuation Date: 12/31/2024", "Currency: USD",
    ):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in CLAIMS + extra_rows:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    page.insert_text((LEFT, y), "TOTALS", fontsize=8.5)
    for offset, cell in zip(COLUMNS, footer_row_cells):
        if cell:
            page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
    y += LINE
    document.save(path)
    document.close()
    return path


#: What the three CLAIMS actually sum to. The footer in the fixtures below
#: prints exactly this -- the claims alone tie perfectly, with nothing
#: missing -- so any finding that appears must come from a row nobody has
#: explained, not from an arithmetic shortfall the footer would also catch.
_CLAIMS_FOOTER = ("", "", "", "4,425.50", "5,024.50", "9,450.00")


def test_a_footer_tie_does_not_excuse_offsetting_unplaced_rows(tmp_path):
    """A credit and its reversal, dropped, cancel in the aggregate but not in
    fact. Both must still be visible; neither is safe to infer from the other.
    """
    path = _write_with_footer(
        tmp_path / "offsetting.pdf",
        extra_rows=(
            ("", "", "", "500.00", "", ""),
            ("", "", "", "-500.00", "", ""),
        ),
        footer_row_cells=_CLAIMS_FOOTER,
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.printed_totals.get("paid_total") == Decimal("4425.50")
    assert result.document.column_total("paid_total") == Decimal("4425.50")
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-04"]

    r23 = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert len(r23) == 2, "both offsetting rows must be reported, not just one"
    assert {f.actual for f in r23} == {"paid total 500.00", "paid total -500.00"}
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_footer_tie_does_not_excuse_a_single_unplaced_row(tmp_path):
    """The general case behind the offsetting one: a field tying in aggregate
    says nothing about a specific dropped row, cancelling partner or not.
    """
    path = _write_with_footer(
        tmp_path / "single-stray.pdf",
        extra_rows=(("", "", "", "500.00", "", ""),),
        footer_row_cells=_CLAIMS_FOOTER,
    )
    result = run_pipeline(path, use_vision=False)
    r23 = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert len(r23) == 1
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_correctly_extracted_money_with_a_tying_footer_raises_nothing(tmp_path):
    """The control: nothing dropped, a real footer, a real tie -- silence."""
    path = _write_with_footer(
        tmp_path / "clean-with-footer.pdf", extra_rows=(), footer_row_cells=_CLAIMS_FOOTER
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.unplaced_rows == []
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert result.reconciliation.status is DocumentStatus.CLEAN


def test_a_same_page_value_match_does_not_excuse_a_section_total(tmp_path):
    """A policy-section subtotal that only one claim contributed to non-zero.

    Reproduces the real Liberty and AIG shape: a page carries several claims,
    all but one are zero in some field, and the page's own printed subtotal
    for that field is only ever a coincidental echo of that one claim's own
    value -- not evidence the subtotal is already represented anywhere. A
    rule that excused a row for matching one same-page claim's figure would
    have hidden this on the real documents; it hid an AIG policy total of
    $30,692.75 and a Liberty section total of $43,562.74 before this test
    was written to hold the line.
    """
    two_claims = (
        ("CN-3001", "02/01/2024", "OPEN", "600.00", "0.00", "600.00"),
        ("CN-3002", "03/01/2024", "CLOSED", "0.00", "0.00", "0.00"),
    )
    path = _write(
        tmp_path / "section-total.pdf",
        two_claims + (("", "", "", "600.00", "", ""),),
    )
    result = run_pipeline(path, use_vision=False)
    r23 = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert len(r23) == 1, (
        "the dropped row's $600.00 coincidentally equals CN-3001's own paid "
        "total, but that is not evidence it is the same figure"
    )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_an_other_insured_section_is_not_excused_by_a_shared_page(tmp_path):
    """A different account bundled in the same packet, on its own page.

    A same-page match would have nothing to match against here -- there are
    no claims at all on the second page -- so this proves the general case:
    money belonging to an account LossLift never extracted a single claim
    for is still reported, not silently absorbed into the first account's
    reconciliation.
    """
    document = pymupdf.open()
    first = document.new_page(width=612, height=792)
    y = 50.0
    for line in ("MERIDIAN MUTUAL ASSURANCE", "LOSS RUN REPORT",
                 "Valuation Date: 12/31/2024",
                 "Policy Period: 01/01/2024 to 12/31/2024"):
        first.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, HEADERS):
        first.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in CLAIMS:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                first.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE

    second = document.new_page(width=612, height=792)
    y = 50.0
    second.insert_text((LEFT, y), "A DIFFERENT INSURED — SEPARATE ACCOUNT", fontsize=9)
    y += LINE * 2
    for offset, label in zip(COLUMNS, HEADERS):
        second.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    # No claim number column on this page's one row -- a totals-style line
    # for an account nothing here was ever extracted as a claim for.
    for offset, cell in zip(COLUMNS, ("", "", "", "12,000.00", "3,000.00", "15,000.00")):
        if cell:
            second.insert_text((LEFT + offset, y), cell, fontsize=8.5)

    path = tmp_path / "other-insured.pdf"
    document.save(path)
    document.close()

    result = run_pipeline(path, use_vision=False)
    r23 = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert r23, "a second account's money must not vanish for lack of a match"
    assert all(f.page == 2 for f in r23)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# Formats that must not be mistaken for smears, and figures that must
# --------------------------------------------------------------------------


#: The same three claims written with space-grouped thousands and a comma
#: decimal -- the French / non-breaking-space convention section 4 lists --
#: large enough that a real thousands grouping appears, so the document's
#: own numbers are what establish the convention, not the dropped row.
EU_SPACE_CLAIMS = (
    ("CN-4001", "12.03.2024", "OPEN", "1 200,00", "3 800,00", "5 000,00"),
    ("CN-4002", "04.05.2024", "CLOSED", "12 450,00", "0,00", "12 450,00"),
    ("CN-4003", "21.09.2024", "OPEN", "775,50", "1 224,50", "2 000,00"),
)


def test_eu_space_grouped_thousands_are_recognised_not_rejected_as_a_smear(
    tmp_path,
):
    """Space-as-thousands-separator is a real convention, not a smear.

    ``"9 400,00"`` and ``"36571 44694"`` look alike at a glance -- both are
    digits split by whitespace -- but one groups a single number in threes
    from the right and the other glues two unrelated fragments together. The
    document's own claims establish which convention is in play here.
    """
    path = _write(
        tmp_path / "eu-space.pdf",
        EU_SPACE_CLAIMS + (("", "", "", "9 400,00", "", ""),),
        headers=HEADERS,
    )
    result = run_pipeline(path, use_vision=False)
    assert result.locale.locale == "eu"
    assert result.locale.confident
    assert [c.paid_total for c in result.document.claims] == [
        Decimal("1200.00"), Decimal("12450.00"), Decimal("775.50"),
    ]
    assert result.document.unplaced_rows
    assert result.document.unplaced_rows[0].amounts == {"paid_total": "9 400,00"}
    assert result.document.unplaced_rows[0].parsed_amounts == {
        "paid_total": Decimal("9400.00")
    }
    r23 = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert len(r23) == 1
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "smeared",
    [
        "4 .00",           # a section marker beside a decimal fragment
        "36571 44694",     # two unrelated five-digit runs, not a 3-digit group
        "4 30,000.00",     # a marker glued to a complete number by a column gap
        "1 2",              # neither fragment is a three-digit continuation
    ],
)
def test_genuine_column_smears_are_still_rejected(tmp_path, smeared):
    """The corrected check still catches what it was built to catch.

    Every one of these is a real AIG/Liberty shape: a stray digit or code
    fused to an adjacent cell's figure by a column boundary the extractor
    misjudged. None of them is a number a carrier printed, and accepting one
    would invent a figure nobody wrote down.
    """
    path = _write(tmp_path / "smear.pdf", CLAIMS + (("", "", "", smeared, "", ""),))
    result = run_pipeline(path, use_vision=False)
    assert result.document.unplaced_rows == []
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert result.reconciliation.status is DocumentStatus.CLEAN


def test_excel_date_serials_under_a_money_column_are_never_reported_as_money(
    tmp_path,
):
    """The Austin shape: a bare integer that is a date, not an amount.

    A column-split spreadsheet export can leave a date serial sitting under a
    header the mapper reads as a money column on a page nothing else claims.
    ``44806`` is 2022-09-12, not $44,806 -- and printing it as unplaced money
    would be inventing a figure nobody meant as one. This is exactly the
    safeguard that keeps the real Austin document out of scope for this
    rule: without a currency mark, a sign, or a separator, a bare integer
    never qualifies, however plausible its magnitude.
    """
    path = _write(tmp_path / "date-serial.pdf", CLAIMS + (("", "", "", "44806", "", ""),))
    result = run_pipeline(path, use_vision=False)
    assert result.document.unplaced_rows == []
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert result.reconciliation.status is DocumentStatus.CLEAN
