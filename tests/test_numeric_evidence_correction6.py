"""Correction-6 regressions for row-local numeric-evidence trust.

The synthetic report used here is arithmetically sound and has no printed
count or totals.  Its control is CLEAN, so an added unresolved value is the
only reason the status may change.  The assertions stay at the product
boundary: evidence survives verbatim, every occurrence keeps its source
page/row, proven prose does not become R-23, and uncertainty is never folded
into a neighbouring claim.  No fixture contains real claimant information.
"""

from __future__ import annotations

import pymupdf
import pytest

from core.pipeline import (
    ColumnMapping,
    build_claims,
    run_pipeline,
    unplaced_evidence,
)
from core.schema import DocumentStatus, RawRow, RawTable


LEFT = 25.0
LINE = 14.0
COLUMNS = (0.0, 80.0, 155.0, 230.0, 355.0, 430.0, 505.0)
HEADERS = (
    "Claim No",
    "Date of Loss",
    "Status",
    "Loss Description",
    "Paid Total",
    "Reserve Total",
    "Total Incurred",
)
MAPPING = ColumnMapping(
    headers=list(HEADERS),
    fields={
        0: "claim_number",
        1: "date_of_loss",
        2: "claim_status",
        3: "loss_description",
        4: "paid_total",
        5: "reserve_total",
        6: "incurred_total",
    },
)
CLAIMS = (
    (
        "CN-1001",
        "03/12/2024",
        "OPEN",
        "Ladder fall",
        "1,200.00",
        "3,800.00",
        "5,000.00",
    ),
    (
        "CN-1002",
        "05/04/2024",
        "CLOSED",
        "Rear-end collision",
        "2,450.00",
        "0.00",
        "2,450.00",
    ),
    (
        "CN-1003",
        "09/21/2024",
        "OPEN",
        "Water ingress",
        "775.50",
        "1,224.50",
        "2,000.00",
    ),
)
MERGED = "4 / 30,000.00"


def _raw(cells, *, page=1, line=0, kind="data"):
    return RawRow(cells=list(cells), page=page, line_index=line, kind=kind)


def _claim_rows(*, page=1, start=1):
    return [
        _raw(cells, page=page, line=line)
        for line, cells in enumerate(CLAIMS, start=start)
    ]


def _table(rows, *, page=1):
    return RawTable(
        page=page,
        headers=list(HEADERS),
        rows=list(rows),
        strategy="words",
    )


def _write(path, rows):
    """Write one privacy-safe, anchorless loss run."""
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


def _texts(rows):
    return {
        text
        for row in rows
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }


def _document_texts(document):
    return _texts(document.unplaced_rows)


def _descriptions(claims):
    return " ".join(claim.loss_description or "" for claim in claims)


def _r23(result):
    return [finding for finding in result.reconciliation.findings
            if finding.rule_id == "R-23"]


def _build(extra_rows):
    claims, warnings, unplaced = build_claims(
        [_table(_claim_rows() + list(extra_rows))], MAPPING, "us", "mdy"
    )
    return claims, warnings, unplaced


def _assert_retained(unplaced, value, *, page=1, row=None):
    matches = [
        item for item in unplaced
        if value in list(item.amounts.values()) + list(item.ambiguous_values.values())
    ]
    assert matches, f"{value!r} disappeared: {unplaced}"
    assert all(item.page == page for item in matches), matches
    if row is not None:
        assert any(item.row == row for item in matches), matches
    return matches


def test_the_correction6_document_is_clean_before_a_counterexample(tmp_path):
    result = run_pipeline(_write(tmp_path / "control.pdf", CLAIMS), use_vision=False)
    assert len(result.document.claims) == len(CLAIMS)
    assert result.document.unplaced_rows == []
    assert not _r23(result)
    assert result.reconciliation.status is DocumentStatus.CLEAN


# A typed field containing unparseable text does not make the row narrative.
@pytest.mark.parametrize(
    "text_column",
    [0, 1, 2, 3],
    ids=["claim-number", "date", "status", "description"],
)
def test_ambiguous_amount_survives_every_non_money_column_boundary(
    tmp_path, text_column
):
    cells = [""] * len(HEADERS)
    cells[text_column] = "New loss"
    cells[4] = MERGED
    result = run_pipeline(
        _write(tmp_path / f"boundary-{text_column}.pdf", CLAIMS + (tuple(cells),)),
        use_vision=False,
    )

    _assert_retained(result.document.unplaced_rows, MERGED)
    assert len(result.document.claims) == len(CLAIMS), "claim ownership was invented"
    assert MERGED not in _descriptions(result.document.claims)
    findings = _r23(result)
    assert findings and all(finding.page == 1 for finding in findings)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_prose_row_cannot_decide_that_the_next_amount_is_prose(tmp_path):
    rows = CLAIMS + (
        ("", "", "", "This loss narrative", "continued across", "several columns", ""),
        ("", "", "", "", MERGED, "", ""),
    )
    result = run_pipeline(_write(tmp_path / "cross-row.pdf", rows), use_vision=False)

    _assert_retained(result.document.unplaced_rows, MERGED)
    assert MERGED not in _descriptions(result.document.claims)
    assert _r23(result)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "kind, preceding",
    [
        ("header", HEADERS),
        ("data", HEADERS),
        ("data", ("CONFIDENTIAL", "LOSS RUN", "REPORT", "", "", "", "")),
    ],
    ids=["typed-header", "data-kind-header", "pooled-page-furniture"],
)
def test_repeated_headers_never_erase_the_amount_after_them(kind, preceding):
    tables = []
    for page in (1, 2, 3):
        rows = _claim_rows(page=page) if page == 1 else []
        rows.extend(
            [
                _raw(preceding, page=page, line=40, kind=kind),
                _raw(("", "", "", "", MERGED, "", ""), page=page, line=41),
            ]
        )
        tables.append(_table(rows, page=page))

    _claims, _warnings, unplaced = build_claims(tables, MAPPING, "us", "mdy")
    occurrences = [
        (row.page, row.row)
        for row in unplaced
        if MERGED in list(row.amounts.values()) + list(row.ambiguous_values.values())
    ]
    assert occurrences == [(1, 41), (2, 41), (3, 41)], (
        f"repeated evidence lost or provenance collapsed: {occurrences}; "
        f"unplaced={unplaced}"
    )


@pytest.mark.parametrize(
    "label, gap, value, value_field",
    [
        ("Software version", False, "$500.00", 4),
        ("Version", True, "$500.00", 5),
        ("Software version", True, "$500.00", 5),
        ("Policy year", True, "$500.00", 5),
        ("Policy year 2024", False, "$2,024.00", 4),
        ("Policy year 2024", False, "$2,024-", 4),
        ("Policy year 2024", False, "$2,024 CR", 4),
        ("Claim ID 500", False, "$500-", 4),
        ("Version unknown", False, "$500.00", 4),
        ("Version N.A.", False, "$500.00", 4),
        ("Version not applicable", False, "$500.00", 4),
        ("Version not known", False, "$500.00", 4),
        ("Policy year", False, MERGED, 4),
        ("Software version", False, "500 30", 4),
    ],
    ids=[
        "adjacent-label-has-no-value",
        "version-cannot-skip-blank",
        "software-version-cannot-skip-blank",
        "policy-year-cannot-skip-blank",
        "currency-contradicts-year",
        "sign-contradicts-year",
        "credit-marker-contradicts-year",
        "sign-contradicts-identifier",
        "unknown",
        "dotted-na",
        "not-applicable",
        "not-known",
        "label-cannot-explain-merged-amount",
        "label-cannot-explain-two-fragments",
    ],
)
def test_a_non_money_label_cannot_consume_an_unestablished_financial_value(
    label, gap, value, value_field
):
    cells = [""] * len(HEADERS)
    cells[3] = label
    if gap:
        assert value_field > 4  # The fixture intentionally leaves column 4 blank.
    cells[value_field] = value
    claims, _warnings, unplaced = _build([_raw(cells, line=70)])

    _assert_retained(unplaced, value, row=70)
    assert len(claims) == len(CLAIMS)
    assert value not in _descriptions(claims)


@pytest.mark.parametrize("marker", ["N/A", "unknown", "pending"])
def test_digit_free_money_cell_cannot_hide_unreadable_neighbour(marker):
    cells = ["", "", "", "", marker, MERGED, ""]
    claims, _warnings, unplaced = _build([_raw(cells, line=75)])

    _assert_retained(unplaced, MERGED, row=75)
    assert MERGED not in _descriptions(claims)


def test_split_parseable_prose_does_not_manufacture_r23(tmp_path):
    rows = CLAIMS + (
        ("", "", "", "reported within", "30.00", "days", ""),
    )
    result = run_pipeline(_write(tmp_path / "numeric-prose.pdf", rows), use_vision=False)

    assert "30.00" not in _document_texts(result.document), (
        result.document.unplaced_rows
    )
    assert not _r23(result)
    assert result.reconciliation.status is DocumentStatus.CLEAN


@pytest.mark.parametrize("value", ["30.00", "$30.00"])
def test_isolated_parseable_value_in_same_column_remains_unresolved(tmp_path, value):
    rows = CLAIMS + (("", "", "", "", value, "", ""),)
    result = run_pipeline(
        _write(tmp_path / f"isolated-{value.replace('$', 'usd')}.pdf", rows),
        use_vision=False,
    )

    _assert_retained(result.document.unplaced_rows, value)
    assert _r23(result)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_parseable_value_after_prose_row_remains_unresolved(tmp_path):
    rows = CLAIMS + (
        ("", "", "", "This loss narrative", "continued across", "several columns", ""),
        ("", "", "", "", "30.00", "", ""),
    )
    result = run_pipeline(
        _write(tmp_path / "parseable-cross-row.pdf", rows), use_vision=False
    )

    _assert_retained(result.document.unplaced_rows, "30.00")
    assert "30.00" not in _descriptions(result.document.claims)
    assert _r23(result)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# Adversarial neighbours found after the first implementation.  Words on both
# sides of a number are grammar, not proof that the number is non-financial.
@pytest.mark.parametrize(
    "before, value, after",
    [
        ("Entry", "500.00", "pending"),
        ("Loss entry", "500.00", "pending"),
        ("deductible is", "500", "per claim"),
        ("policy limit is", "500", "per occurrence"),
        ("Settlement", "500.00", "approved"),
        ("Paid amount", "500.00", "claims pending"),
        ("Loss reserve", "500", "days outstanding"),
        ("Policy amount", "500.00", "records"),
        ("Entry", MERGED, "days"),
        ("Entry", "500 30", "days"),
    ],
)
def test_words_around_a_number_do_not_prove_it_is_prose(
    tmp_path, before, value, after
):
    rows = CLAIMS + (("", "", "", before, value, after, ""),)
    result = run_pipeline(
        _write(tmp_path / f"ambiguous-{len(value)}-{len(before)}.pdf", rows),
        use_vision=False,
    )

    _assert_retained(result.document.unplaced_rows, value)
    assert value not in _descriptions(result.document.claims)
    assert _r23(result)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "before, value, after",
    [
        ("claims are paid within", "30.00", "days"),
        ("reserve records kept", "7", "years"),
        ("payment due within", "30", "days"),
        ("recovery records kept", "7", "years"),
    ],
)
def test_a_duration_remains_prose_despite_financial_words(
    tmp_path, before, value, after
):
    rows = CLAIMS + (("", "", "", before, value, after, ""),)
    result = run_pipeline(
        _write(tmp_path / f"duration-{value}-{len(before)}.pdf", rows),
        use_vision=False,
    )

    assert value not in _document_texts(result.document)
    assert not _r23(result)
    assert result.reconciliation.status is DocumentStatus.CLEAN


def test_duration_prose_does_not_hide_a_different_amount_on_the_same_row():
    mapping = ColumnMapping(
        headers=["Paid", "Note", "Reserve", "Note"],
        fields={
            0: "paid_total",
            1: "loss_description",
            2: "reserve_total",
            3: "cause_of_loss",
        },
    )
    row = _raw(["$500.00", "reported within", "30.00", "days"], line=82)
    evidence = unplaced_evidence(row, mapping, "us", context="unknown")

    assert evidence.ambiguous == {"paid_total": ("$500.00", 500)}
    assert "reserve_total" not in evidence.ambiguous
    assert evidence.unreadable == {}


@pytest.mark.parametrize(
    "first, second_label, value",
    [
        ("Version unknown", "Print Date:", "5/23/2023"),
        ("Version N.A.", "Policy year", "2024.00"),
        ("Version not applicable", "Claim identifier", "12,345"),
    ],
)
def test_one_no_value_label_cannot_block_a_later_explanation(
    first, second_label, value
):
    mapping = ColumnMapping(
        headers=["Note", "Spacer", "Label", "Paid"],
        fields={0: "loss_description", 1: None, 2: None, 3: "paid_total"},
    )
    row = _raw([first, "", second_label, value], line=84)

    assert not unplaced_evidence(row, mapping, "us", context="claims")


def test_a_typed_header_carrying_numeric_evidence_is_not_discarded():
    rows = _claim_rows() + [
        _raw(("", "", "", "", MERGED, "", ""), line=90, kind="header")
    ]
    _claims, _warnings, unplaced = build_claims(
        [_table(rows)], MAPPING, "us", "mdy"
    )

    _assert_retained(unplaced, MERGED, row=90)


def test_an_explicit_page_marker_under_a_money_column_remains_furniture():
    tables = [
        _table(
            [_raw(("", "", "", "", f"Page {page} of 3", "", ""),
                  page=page, line=95)],
            page=page,
        )
        for page in (1, 2, 3)
    ]
    _claims, _warnings, unplaced = build_claims(tables, MAPPING, "us", "mdy")

    assert unplaced == []
