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

from core.pipeline import ColumnMapping, build_claims, run_pipeline, unplaced_money
from core.reconcile import reconcile
from core.schema import DocumentStatus, RawRow, RawTable, Severity, UnplacedRow

LINE = 14.0
LEFT = 40.0
#: Wide, even columns so the gutters are unambiguous and column detection is
#: not what the test is measuring.
COLUMNS = (0.0, 95.0, 175.0, 245.0, 330.0, 415.0)
HEADERS = ("Claim No", "Date of Loss", "Status", "Paid Total", "Reserve Total", "Total Incurred")
MAPPING = ColumnMapping(
    headers=list(HEADERS),
    fields={
        0: "claim_number",
        1: "date_of_loss",
        2: "claim_status",
        3: "paid_total",
        4: "reserve_total",
        5: "incurred_total",
    },
)

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


def test_an_r04_match_does_not_suppress_unplaced_money(anchorless):
    """A document-wide tie says nothing about a separately discarded row."""
    document = run_pipeline(anchorless, use_vision=False).document
    document.printed_totals = {"paid_total": document.column_total("paid_total")}
    document.unplaced_rows = [
        UnplacedRow(page=1, row=12, amounts={"paid_total": "100.00"})
    ]
    result = reconcile(document)

    assert not [f for f in result.findings if f.rule_id == "R-04"]
    assert [f for f in result.findings if f.rule_id == "R-23"]
    assert result.status is DocumentStatus.NEEDS_REVIEW


def test_offsetting_unplaced_rows_cannot_hide_behind_an_r04_match(anchorless):
    """Equal-and-opposite discarded rows preserve the total while losing data."""
    document = run_pipeline(anchorless, use_vision=False).document
    document.printed_totals = {
        "paid_total": document.column_total("paid_total"),
        "incurred_total": document.column_total("incurred_total"),
    }
    document.unplaced_rows = [
        UnplacedRow(
            page=1,
            row=12,
            amounts={"paid_total": "100.00", "incurred_total": "100.00"},
        ),
        UnplacedRow(
            page=1,
            row=13,
            amounts={"paid_total": "(100.00)", "incurred_total": "(100.00)"},
        ),
    ]
    result = reconcile(document)

    raised = [f for f in result.findings if f.rule_id == "R-23"]
    assert len(raised) == 2
    assert len({f.condition for f in raised}) == 2
    assert result.status is DocumentStatus.NEEDS_REVIEW


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


@pytest.mark.parametrize("separator", [" ", "\u00a0"])
def test_space_grouped_money_is_not_mistaken_for_smeared_cells(separator):
    row = RawRow(
        page=1,
        line_index=4,
        cells=["", "12.03.2024", "OPEN", f"1{separator}234,56", "", ""],
    )

    assert unplaced_money(row, MAPPING, "eu") == {
        "paid_total": f"1{separator}234,56"
    }


def test_a_claim_like_row_preserves_whole_unit_money():
    row = RawRow(
        page=1,
        line_index=4,
        cells=["", "03/12/2024", "OPEN", "9400", "", ""],
    )

    assert unplaced_money(row, MAPPING, "us") == {"paid_total": "9400"}


def test_a_claim_like_zero_row_is_not_automatically_irrelevant():
    row = RawRow(
        page=1,
        line_index=4,
        cells=["", "03/12/2024", "CLOSED", "0", "0", "0"],
    )

    assert unplaced_money(row, MAPPING, "us") == {
        "paid_total": "0",
        "reserve_total": "0",
        "incurred_total": "0",
    }


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


def test_an_unlabelled_bare_integer_is_preserved_as_ambiguous(tmp_path):
    """Without a label, seven dollars and an index of seven are indistinguishable."""
    path = _write(tmp_path / "bare.pdf", CLAIMS + (("", "", "", "7", "", ""),))
    result = run_pipeline(path, use_vision=False)
    assert len(result.document.unplaced_rows) == 1
    assert result.document.unplaced_rows[0].amounts == {}
    assert result.document.unplaced_rows[0].ambiguous_values == {"paid_total": "7"}
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "fragment",
    [
        "12/31/2024",  # printed date
        "12.31.2024",  # punctuated date
        "45292",       # plausible Excel serial date
        "7",           # count or index
        "GL-4417-2024",  # identifier
        "(2024)",      # parenthesised year, not accounting evidence by itself
        "2024-",       # punctuated year, not a trailing-negative amount by itself
        "1.2",         # version or split identifier fragment
        "4 .00",       # two cells smeared together
    ],
)
def test_an_isolated_numeric_fragment_in_a_money_column_is_not_money(fragment):
    row = RawRow(
        page=1,
        line_index=4,
        cells=["", "", "", fragment, "", ""],
    )

    assert unplaced_money(row, MAPPING, "us") == {}


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


def test_repeated_headers_and_proven_prose_furniture_remain_exempt():
    repeated = RawRow(
        page=1,
        line_index=1,
        cells=["Claim No", "Date of Loss", "Status", "Paid Total", "Reserve Total", "Total Incurred"],
    )
    strapline = RawRow(
        page=1,
        line_index=2,
        cells=["CONFIDENTIAL LOSS RUN", "", "", "", "", ""],
    )
    tables = [
        RawTable(page=page, headers=list(HEADERS), rows=[
            repeated.model_copy(update={"page": page}),
            strapline.model_copy(update={"page": page}),
        ])
        for page in (1, 2)
    ]

    claims, warnings, unplaced = build_claims(tables, MAPPING, "us", "mdy")

    assert claims == []
    assert warnings == []
    assert unplaced == []


def test_identical_repeated_monetary_rows_are_not_page_furniture():
    tables = [
        RawTable(
            page=page,
            headers=list(HEADERS),
            rows=[RawRow(
                page=page,
                line_index=4,
                cells=["", "", "", "9,400.00", "600.00", "10,000.00"],
            )],
        )
        for page in (1, 2)
    ]

    claims, warnings, unplaced = build_claims(tables, MAPPING, "us", "mdy")

    assert claims == []
    assert warnings == []
    assert [(row.page, row.row) for row in unplaced] == [(1, 4), (2, 4)]


# --------------------------------------------------------------------------
# Context decides; token shape does not
# --------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["9400", "9400 CR", "(9400)"])
def test_a_money_only_continuation_table_preserves_whole_units(amount):
    """The header and neighbouring amount establish a financial continuation."""
    table = RawTable(
        page=2,
        headers=["Paid Total"],
        rows=[
            RawRow(page=2, line_index=4, cells=["1,200.00"]),
            RawRow(page=2, line_index=5, cells=[amount]),
        ],
    )

    claims, warnings, unplaced = build_claims([table], MAPPING, "us", "mdy")

    assert claims == []
    assert warnings == []
    assert [record.amounts for record in unplaced] == [
        {"paid_total": "1,200.00"},
        {"paid_total": amount},
    ]


def test_identical_whole_unit_continuation_rows_are_not_furniture():
    tables = [
        RawTable(
            page=page,
            headers=["Paid Total"],
            rows=[RawRow(page=page, line_index=4, cells=["9400"])],
        )
        for page in (2, 3)
    ]

    claims, warnings, unplaced = build_claims(tables, MAPPING, "us", "mdy")

    assert claims == []
    assert warnings == []
    assert [(record.page, record.row, record.amounts) for record in unplaced] == [
        (2, 4, {"paid_total": "9400"}),
        (3, 4, {"paid_total": "9400"}),
    ]


@pytest.mark.parametrize(
    ("label", "fragment"),
    [
        ("Software Version", "1.20"),
        ("Policy Year", "2024.00"),
        ("Excel Serial Date", "45292.00"),
        ("Policy Identifier", "12,345"),
    ],
)
def test_contextually_identified_non_money_is_not_unplaced_money(label, fragment):
    table = RawTable(
        page=1,
        headers=list(HEADERS),
        rows=[RawRow(
            page=1,
            line_index=4,
            cells=[label, "", "", fragment, "", ""],
        )],
    )

    claims, warnings, unplaced = build_claims([table], MAPPING, "us", "mdy")

    assert claims == []
    assert warnings
    assert unplaced == []


def test_multiple_labelled_numeric_fragments_are_not_money():
    table = RawTable(
        page=1,
        headers=list(HEADERS),
        rows=[RawRow(
            page=1,
            line_index=4,
            cells=["", "Page index", "", "45292", "7", ""],
        )],
    )

    claims, warnings, unplaced = build_claims([table], MAPPING, "us", "mdy")

    assert claims == []
    assert warnings
    assert unplaced == []


def test_a_context_free_number_is_preserved_as_ambiguous_not_called_money(anchorless):
    table = RawTable(
        page=2,
        headers=list(HEADERS),
        rows=[RawRow(
            page=2,
            line_index=4,
            cells=["", "", "", "9400", "", ""],
        )],
    )
    claims, warnings, unplaced = build_claims([table], MAPPING, "us", "mdy")

    assert claims == []
    assert warnings == []
    assert len(unplaced) == 1
    assert unplaced[0].amounts == {}
    assert unplaced[0].ambiguous_values == {"paid_total": "9400"}

    document = run_pipeline(anchorless, use_vision=False).document
    document.unplaced_rows = unplaced
    result = reconcile(document)
    finding = next(f for f in result.findings if f.rule_id == "R-23")
    assert result.status is DocumentStatus.NEEDS_REVIEW
    assert "numeric values" in finding.message.lower()
    assert "amounts were printed" not in finding.message.lower()


def test_mixed_confirmed_and_ambiguous_values_both_reach_the_finding(anchorless):
    document = run_pipeline(anchorless, use_vision=False).document
    document.unplaced_rows = [UnplacedRow(
        page=2,
        row=4,
        amounts={"paid_total": "100.00"},
        ambiguous_values={"reserve_total": "45292"},
    )]

    result = reconcile(document)
    finding = next(f for f in result.findings if f.rule_id == "R-23")

    assert "paid total 100.00" in finding.actual.lower()
    assert "reserve total 45292" in finding.actual.lower()
