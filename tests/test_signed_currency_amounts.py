"""A negative amount whose sign is printed before its currency marker.

Carriers print a recovery as "-$4,815" or, in accounting style, "($4,815.00)":
the sign or the parenthesis first, then the marker, then the digits. That is
one amount. The check that refuses two numbers run together by a column
boundary took the marker out before looking and left "- 4,815" behind -- a
sign it read as a first number, and "4,815" as a second that is not a group of
three digits. So the amount was refused as a smear.

A claim row does not go through that check, so the damage fell on the rows
where the carrier states its own figures. A totals row printing its recovery
column as "-$4,815" had that total thrown away: R-04 stopped checking the
column, and R-26 told the reviewer two values had been run together where one
was printed. Whether it happened turned on digit count alone -- "-$815"
survived, because "815" happens to be a group of three.

A sign or a parenthesis printed against the marker belongs to the amount the
marker opens. A dash standing apart from the marker is left refused: a dash is
also how carriers print zero, and "- $4,815" can be a zero column and the
amount beside it run together.

Synthetic cells and an invented one-page loss run (spec section 9).
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.pipeline import ColumnMapping, _is_smeared, run_pipeline, unplaced_evidence
from core.schema import DocumentStatus, RawRow

MAPPING = ColumnMapping(
    headers=["Claim Number", "Recovery"],
    fields={0: "claim_number", 1: "recovery_total"},
)


def _amount(text: str, locale: str = "us"):
    """What the reader makes of this cell under an established money column."""
    row = RawRow(cells=["", text], page=1, line_index=10)
    return unplaced_evidence(
        row, MAPPING, locale, context="monetary-only"
    ).amounts.get("recovery_total")


@pytest.mark.parametrize(
    "text, locale, value",
    [
        ("-$4,815", "us", Decimal("-4815")),
        ("-$4,815.00", "us", Decimal("-4815.00")),
        ("($4,815.00)", "us", Decimal("-4815.00")),
        ("-$ 4,815.00", "us", Decimal("-4815.00")),
        ("-€4.815,00", "eu", Decimal("-4815.00")),
        ("-$0", "us", Decimal("0")),
    ],
)
def test_a_sign_before_the_marker_belongs_to_the_amount(text, locale, value):
    assert _is_smeared(text) is False, text
    assert _amount(text, locale) == (text, value)


@pytest.mark.parametrize(
    "text",
    [
        "- $4,815.00",        # a dash apart from the marker: zero, then an amount
        "-$4,815 1,000.00",   # a signed amount run into an unmarked one
        "-$4,815 -$1,000",    # two signed amounts
    ],
)
def test_signed_smears_are_still_refused(text):
    assert _is_smeared(text) is True, text
    assert _amount(text) is None


# --------------------------------------------------------------------------
# Where it did the damage: the carrier's own printed total
# --------------------------------------------------------------------------

LINE = 14.0
LEFT = 40.0
COLUMNS = (0.0, 80.0, 150.0, 215.0, 295.0, 375.0, 455.0)
HEADERS = (
    "Claim Number", "Loss Date", "Status", "Paid Total", "Recovery",
    "Reserve Total", "Incurred Total",
)


def _write(path, notation: str, printed_recovery: str = "1,250.00"):
    """A loss run with one recovered claim, its recovery printed negative.

    Every claim ties with the recovery credited -- paid + reserve - recovery
    == incurred -- and the totals row ties with the claims, so the printed
    recovery total is the only thing that can raise anything.
    """
    signed = notation.format
    rows = (
        ("CN-2001", "03/14/2022", "CLOSED", "$5,000.00", signed("1,250.00"), "$0.00", "$3,750.00"),
        ("CN-2002", "05/20/2022", "OPEN", "$1,500.00", "$0.00", "$2,500.00", "$4,000.00"),
        ("CN-2003", "08/02/2022", "CLOSED", "$700.00", "$0.00", "$0.00", "$700.00"),
        ("Totals", "", "", "$7,200.00", signed(printed_recovery), "$2,500.00", "$8,450.00"),
    )
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    y = 50.0
    for line in (
        "MERIDIAN MUTUAL ASSURANCE",
        "LOSS RUN REPORT",
        "Policy Period: 01/01/2022 to 12/31/2022",
        "Valuation Date: 12/31/2022",
    ):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + offset, y), label, fontsize=8)
    y += LINE
    for row in rows:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8)
        y += LINE
    document.save(path)
    document.close()
    return path


NOTATIONS = pytest.mark.parametrize("notation", ["-${}", "(${})"])


@NOTATIONS
def test_a_signed_printed_total_is_read_and_checked(tmp_path, notation):
    """The regression: the carrier's recovery total was thrown away."""
    result = run_pipeline(
        _write(tmp_path / "run.pdf", notation),
        use_vision=False,
        profiles_dir=tmp_path / "profiles",
    )
    document = result.document
    assert document.unreadable_totals == {}
    # Read as printed, then credited like the claims' own recoveries.
    assert document.printed_totals["recovery_total"] == Decimal("1250.00")
    raised = {f.rule_id for f in result.reconciliation.findings}
    assert not raised & {"R-04", "R-26"}, sorted(raised)
    assert result.reconciliation.status is DocumentStatus.CLEAN


@NOTATIONS
def test_a_wrong_signed_total_is_caught(tmp_path, notation):
    """Being read is only worth something if a wrong figure is then caught."""
    result = run_pipeline(
        _write(tmp_path / "run.pdf", notation, printed_recovery="1,300.00"),
        use_vision=False,
        profiles_dir=tmp_path / "profiles",
    )
    mismatches = [
        f for f in result.reconciliation.findings
        if f.rule_id == "R-04" and f.field == "recovery_total"
    ]
    assert len(mismatches) == 1, [f.message for f in result.reconciliation.findings]
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-26"]
