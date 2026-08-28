"""A loss run grouped into policy-period sections.

Real carrier reports routinely group claims by policy period, print a subtotal
under each group and one grand total at the end, wrap column labels over two
lines, and share the letterhead line with a "Printed:" stamp. None of those
shapes appear in the flat single-table fixtures, and each one of them broke a
different stage of the pipeline. The document below is synthetic — it carries
the structure without carrying anyone's claim data.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pymupdf
import pytest

from core.pipeline import run_pipeline
from core.schema import DocumentStatus, Severity

FONT = "helv"
SIZE = 7.5

#: x offset of each column, and whether its label wraps over two lines.
COLUMNS = (
    (40, None, "Claim Number"),
    (130, None, "Claimant"),
    (215, "Date of", "Loss"),
    (270, None, "Status"),
    (330, "Total", "Paid"),
    (405, "Outstanding", "Reserves"),
    (485, "Third Party", "Recovery"),
    (565, "Total", "Incurred"),
)

CLAIMS = (
    ("2023", "C-1001", "Alpha, A", "3/4/2023", "Closed", 1000, 0, 0, 1000),
    ("2023", "C-1002", "Bravo, B", "7/15/2023", "Open", 2000, 500, 100, 2400),
    ("2024", "C-2001", "Charlie, C", "2/20/2024", "Open", 1500, 2500, 0, 4000),
    ("2024", "C-2002", "Delta, D", "9/9/2024", "Closed", 750, 0, 250, 500),
)

GRAND = (5250, 3000, 350, 7900)


def _money(value: int) -> str:
    return f"${value:,}.00"


def _write(page, text: str, x: float, y: float) -> None:
    page.insert_text((x, y), text, fontname=FONT, fontsize=SIZE)


def _section(page, year: str, y: float) -> float:
    rows = [claim for claim in CLAIMS if claim[0] == year]
    _write(page, f"Policy Period: 01/01/{year} - 12/31/{year}", 40, y)
    y += 14

    for _, number, claimant, loss, status, paid, reserve, recovery, incurred in rows:
        values = (number, claimant, loss, status, *(_money(v) for v in
                  (paid, reserve, recovery, incurred)))
        for (x, _, _), value in zip(COLUMNS, values):
            _write(page, value, x, y)
        y += 12

    # The subtotal label sits on its own line; its amounts print underneath.
    _write(page, f"01/01/{year} - 12/31/{year} Totals:", 40, y)
    y += 11
    _write(page, f"# Claims: {len(rows)}", 130, y)
    for (x, _, _), index in zip(COLUMNS[4:], range(5, 9)):
        _write(page, _money(sum(row[index] for row in rows)), x, y)
    return y + 18


def _build(path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=792, height=612)

    # The carrier name shares its line with a metadata label, as real
    # letterheads do — the name is what precedes the label, not the whole line.
    _write(page, "ACME PUBLIC RISK POOL", 40, 30)
    _write(page, "Printed: 3/1/2025 10:00:00 AM", 300, 30)
    _write(page, "Loss Run Summary Report", 40, 44)
    _write(page, "Numbers As of 12/31/2024 11:59 PM", 40, 58)

    for x, upper, lower in COLUMNS:
        if upper:
            _write(page, upper, x, 86)
        _write(page, lower, x, 98)

    y = 120
    for year in ("2023", "2024"):
        y = _section(page, year, y)

    _write(page, "Report Totals:", 40, y)
    y += 11
    _write(page, f"# Claims: {len(CLAIMS)}", 130, y)
    for (x, _, _), value in zip(COLUMNS[4:], GRAND):
        _write(page, _money(value), x, y)

    _write(page, "Report: LossRunSummary", 40, y + 30)
    _write(page, "Page 1 of 1", 300, y + 30)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    path = tmp_path_factory.mktemp("sections") / "section_grouped.pdf"
    _build(path)
    return run_pipeline(path, use_vision=False)


def test_section_headings_are_not_claims(result):
    """"Policy Period:" and "Report:" rows must not become empty claims."""
    assert len(result.document.claims) == len(CLAIMS)
    numbers = {claim.claim_number for claim in result.document.claims}
    assert numbers == {claim[1] for claim in CLAIMS}


def test_grand_total_outranks_section_subtotals(result):
    """R-04 has to check against the total covering every claim."""
    printed = result.document.printed_totals
    assert printed["paid_total"] == Decimal("5250.00")
    assert printed["reserve_total"] == Decimal("3000.00")
    assert printed["recovery_total"] == Decimal("350.00")
    assert printed["incurred_total"] == Decimal("7900.00")
    assert result.document.printed_claim_count == len(CLAIMS)


def test_wrapped_header_maps_the_date_column(result):
    """"Date of" above "Loss" is one label, so every claim has a loss date."""
    assert all(claim.date_of_loss is not None for claim in result.document.claims)
    by_number = {claim.claim_number: claim for claim in result.document.claims}
    assert by_number["C-1001"].date_of_loss == date(2023, 3, 4)
    assert by_number["C-2002"].date_of_loss == date(2024, 9, 9)


def test_valuation_date_and_carrier_come_off_the_letterhead(result):
    assert result.document.valuation_date == date(2024, 12, 31)
    assert result.document.carrier == "ACME PUBLIC RISK POOL"


def test_period_spans_every_section(result):
    """One term per section, so the document covers all of them."""
    assert result.document.policy_period_start == date(2023, 1, 1)
    assert result.document.policy_period_end == date(2024, 12, 31)


WRAPPED = (
    ("W-01", "Ecks, E", "1/9/2024", "Closed", 400, "rear-ended while stopped, neck and back soreness"),
    ("W-02", "Wye, W", "4/2/2024", "Open", 600, "slipped on ice, contusion back of head, right hip"),
    ("W-03", "Zed, Z", "8/8/2024", "Closed", 300, "MOVING SEAT WITH TWO WHEEL DOLLY, LEFT SHOULDER"),
    ("W-04", "Que, Q", "11/5/2024", "Open", 700, "lifted wheelchair into position, left shoulder"),
)

#: The tagline every page of a real report carries under the claims.
STRAPLINE = "delivering what matters most."


def _build_wrapped(path) -> None:
    """Two pages, a repeated footer, and descriptions that wrap across columns."""
    doc = pymupdf.open()
    for page_number, rows in ((1, WRAPPED[:2]), (2, WRAPPED[2:])):
        page = doc.new_page(width=792, height=612)
        _write(page, "ACME PUBLIC RISK POOL", 40, 30)
        _write(page, "Printed: 3/1/2025 10:00:00 AM", 300, 30)
        _write(page, "Numbers As of 12/31/2024", 40, 44)
        for x, upper, lower in COLUMNS:
            if upper:
                _write(page, upper, x, 70)
            _write(page, lower, x, 82)

        y = 105
        for number, claimant, loss, status, paid, description in rows:
            for (x, _, _), value in zip(
                COLUMNS,
                (number, claimant, loss, status, _money(paid), _money(0),
                 _money(0), _money(paid)),
            ):
                _write(page, value, x, y)
            y += 11
            # The description starts under the claimant column and runs right,
            # so its words cross the date and money column boundaries.
            words = description.split()
            third = max(1, len(words) // 3)
            for x, chunk in zip(
                (130, 260, 400),
                (words[:third], words[third:third * 2], words[third * 2:]),
            ):
                if chunk:
                    _write(page, " ".join(chunk), x, y)
            y += 14

        if page_number == 2:
            _write(page, "Report Totals:", 40, y)
            _write(page, f"# Claims: {len(WRAPPED)}", 130, y + 11)
            paid_total = sum(row[4] for row in WRAPPED)
            for (x, _, _), value in zip(
                COLUMNS[4:], (paid_total, 0, 0, paid_total)
            ):
                _write(page, _money(value), x, y + 11)
            y += 30

        _write(page, STRAPLINE, 40, 560)
        _write(page, f"Page {page_number} of 2", 300, 560)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def wrapped(tmp_path_factory):
    path = tmp_path_factory.mktemp("wrapped") / "wrapped.pdf"
    _build_wrapped(path)
    return run_pipeline(path, use_vision=False)


def test_repeated_footer_is_not_reported_as_skipped_data(wrapped):
    """A strapline on every page is furniture, not a claim the app lost."""
    assert [claim.claim_number for claim in wrapped.document.claims] == [
        row[0] for row in WRAPPED
    ]
    assert wrapped.warnings == []


def test_wrapped_description_is_kept_whole(wrapped):
    """A description spilling across columns must not stop at the first one."""
    descriptions = {
        claim.claim_number: (claim.loss_description or "")
        for claim in wrapped.document.claims
    }
    for number, _, _, _, _, expected in WRAPPED:
        assert descriptions[number] == expected


def test_document_reconciles_clean(result):
    """Nothing above is wrong, so nothing should be reported as wrong."""
    errors = [
        finding
        for finding in result.reconciliation.findings
        if finding.severity is Severity.ERROR
    ]
    assert errors == []
    assert result.reconciliation.status is DocumentStatus.CLEAN
