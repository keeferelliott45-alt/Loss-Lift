"""Money printed on row-labelled lines beneath the claim it belongs to.

A widely used carrier "Detail Loss Report" prints each claim on one line --
claimant, office codes, claim number, dates, open/closed -- and its money on
the lines beneath, one line per kind, each labelled in the status column:
``Inc:``, ``Pd:``, ``O/S:``, with the amounts under Total / Claim / Medical /
Expense. Subtotal and grand-total blocks repeat the same labelled lines.

Read line by line, every claim came back with no money and every labelled line
was reported as unplaced. The labelled lines now attach to the claim above them
-- each label once, consecutively, and never across a subtotal -- and the
column under each amount, read with the line's label, names its field. A
labelled line that cannot be attached stays reported, never absorbed.

Synthetic PDFs only.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf

from core.pipeline import run_pipeline
from core.review import canonical_status
from core.schema import ClaimStatus, DocumentStatus

X = {"claimant": 30, "off": 150, "fp": 185, "number": 215, "acc": 285, "notice": 345,
     "close": 405, "oc": 468, "total": 505, "claim": 565, "medical": 625, "expense": 690}
HEADER = (("claimant", "Claimant"), ("off", "Adj Off"), ("fp", "FP"),
          ("number", "Claim Number"), ("acc", "Accident Date"), ("notice", "Notice Date"),
          ("close", "Close Date"), ("oc", "O/C"), ("total", "Total"), ("claim", "Claim"),
          ("medical", "Medical"), ("expense", "Expense"))
LINE = 11.0

# number, claimant, accident, notice, close, o/c, description, inc, pd, os
# where each money tuple is (total, claim, medical, expense).
CLAIMS = (
    ("E0W6370", "HARBOR TEST LLC", "02/19/2015", "08/14/2015", "11/01/2016", "C",
     "VEHICLE STRUCK A POST IN THE YARD.",
     ("5,173.00", "5,173.00", "0.00", "0.00"), ("5,173.00", "5,173.00", "0.00", "0.00"),
     ("0.00", "0.00", "0.00", "0.00")),
    ("E1J8764", "QUARRY LANE", "10/14/2014", "10/16/2014", "", "O",
     "EMPLOYEE REAR ENDED A VEHICLE AT A STOP.",
     ("26,177.00", "20,000.00", "5,167.00", "1,010.00"),
     ("19,425.00", "15,000.00", "3,415.00", "1,010.00"),
     ("6,752.00", "5,000.00", "1,752.00", "0.00")),
    ("E2K7270", "ASHGROVE PARK", "09/19/2018", "12/26/2018", "12/29/2018", "C",
     "CLAIMANT FELL ON THE SIDEWALK.",
     ("0.00", "0.00", "0.00", "0.00"), ("0.00", "0.00", "0.00", "0.00"),
     ("0.00", "0.00", "0.00", "0.00")),
)


def _money_line(page, y, label, values, text=""):
    if text:
        page.insert_text((X["claimant"], y), text, fontsize=7)
    page.insert_text((X["oc"], y), label, fontsize=7)
    for key, value in zip(("total", "claim", "medical", "expense"), values):
        page.insert_text((X[key], y), f"${value}", fontsize=7)


def _sum(rows, index, column):
    return f"{sum(Decimal(r[index][column].replace(',', '')) for r in rows):,.2f}"


def _pdf(tmp_path, claims=CLAIMS, *, subtotal=True, grand=True, orphan=False, repeat=False):
    document = pymupdf.open()
    page = document.new_page(width=792, height=612)
    y = 30.0
    for line in ("RIDGEWAY TEST COUNTY", "Policy Number(s): 4357M3083",
                 "Detail Loss Report", "Losses From: 10/01/2014 To 06/10/2019"):
        page.insert_text((X["claimant"], y), line, fontsize=8)
        y += LINE
    y += 4
    for key, label in HEADER:
        page.insert_text((X[key], y), label, fontsize=7)
    y += LINE
    page.insert_text((X["claimant"], y), "Policy Year: 2014", fontsize=7)
    y += LINE
    if orphan:
        _money_line(page, y, "Inc:", ("999.00", "999.00", "0.00", "0.00"))
        y += LINE
    for number, who, acc, notice, close, oc, text, inc, pd, os_ in claims:
        for key, value in (("claimant", who), ("off", "234"), ("fp", "AD"), ("number", number),
                           ("acc", acc), ("notice", notice), ("close", close), ("oc", oc)):
            if value:
                page.insert_text((X[key], y), value, fontsize=7)
        y += LINE
        _money_line(page, y, "Inc:", inc, text)
        y += LINE
        _money_line(page, y, "Pd:", pd)
        y += LINE
        if repeat and number == claims[0][0]:
            _money_line(page, y, "Pd:", ("1.00", "1.00", "0.00", "0.00"))
            y += LINE
        _money_line(page, y, "O/S:", os_)
        y += LINE
    blocks = []
    if subtotal:
        blocks.append("Subtotals for Policy Year : 2014")
    if grand:
        blocks.append("Report Grand Totals")
    # As the carrier prints them: a subtotal heading on its own line, then
    # Inc; the grand total's heading shares its line with Inc. The count
    # shares its line with Pd.
    for heading in blocks:
        page.insert_text((X["claimant"], y), heading, fontsize=7)
        if heading.startswith("Subtotals"):
            y += LINE
        _money_line(page, y, "Inc:", tuple(_sum(claims, 7, c) for c in range(4)))
        y += LINE
        page.insert_text((X["claimant"], y), f"Total Claim Count: {len(claims)}", fontsize=7)
        _money_line(page, y, "Pd:", tuple(_sum(claims, 8, c) for c in range(4)))
        y += LINE
        _money_line(page, y, "O/S:", tuple(_sum(claims, 9, c) for c in range(4)))
        y += LINE
    page.insert_text((X["claimant"], 590), "Losses as of: 06/08/2019", fontsize=7)
    page.insert_text((X["oc"], 590), "Page 1", fontsize=7)
    path = tmp_path / "detail.pdf"
    document.save(path)
    document.close()
    return path


def _read(tmp_path, **kwargs):
    return run_pipeline(_pdf(tmp_path, **kwargs), use_vision=False,
                        profiles_dir=tmp_path / "profiles")


def _dec(text):
    return Decimal(text.replace(",", ""))


def test_every_claim_reads_its_labelled_money(tmp_path):
    result = _read(tmp_path)
    by_number = {c.claim_number: c for c in result.document.claims}
    assert sorted(by_number) == sorted(c[0] for c in CLAIMS)
    for number, *_rest, inc, pd, os_ in CLAIMS:
        claim = by_number[number]
        assert claim.incurred_total == _dec(inc[0])
        assert claim.paid_total == _dec(pd[0])
        assert claim.reserve_total == _dec(os_[0])
        assert (claim.paid_indemnity, claim.paid_medical, claim.paid_expense) == tuple(
            _dec(v) for v in pd[1:])
        assert (claim.reserve_indemnity, claim.reserve_medical, claim.reserve_expense) == tuple(
            _dec(v) for v in os_[1:])


def test_the_open_closed_column_is_the_status(tmp_path):
    by_number = {c.claim_number: c for c in _read(tmp_path).document.claims}
    assert by_number["E0W6370"].claim_status is ClaimStatus.CLOSED
    assert by_number["E1J8764"].claim_status is ClaimStatus.OPEN


def test_no_amount_is_ever_a_claim_number(tmp_path):
    numbers = [c.claim_number for c in _read(tmp_path).document.claims]
    assert not any("$" in n or n.replace(",", "").replace(".", "").isdigit() for n in numbers)


def test_subtotal_and_grand_total_lines_never_join_a_claim(tmp_path):
    result = _read(tmp_path)
    last = next(c for c in result.document.claims if c.claim_number == CLAIMS[-1][0])
    assert last.incurred_total == Decimal("0.00") and last.paid_total == Decimal("0.00")
    assert len(result.document.claims) == len(CLAIMS)


def test_the_grand_total_is_the_documents_printed_total(tmp_path):
    result = _read(tmp_path)
    assert result.document.printed_claim_count == len(CLAIMS)
    rules = result.reconciliation.rule_ids()
    assert "R-04" not in rules and "R-05" not in rules
    assert result.document.printed_totals["paid_total"] == Decimal("24598.00")
    assert result.document.printed_totals["reserve_total"] == Decimal("6752.00")
    assert result.document.printed_totals["incurred_total"] == Decimal("31350.00")


def test_a_clean_detail_report_reports_nothing_unplaced(tmp_path):
    assert "R-23" not in _read(tmp_path).reconciliation.rule_ids()


def test_a_labelled_line_with_no_claim_above_it_stays_reported(tmp_path):
    result = _read(tmp_path, orphan=True)
    assert "R-23" in result.reconciliation.rule_ids()
    assert all(c.incurred_total != Decimal("999.00") for c in result.document.claims)


def test_a_repeated_label_is_never_attached_twice(tmp_path):
    result = _read(tmp_path, repeat=True)
    first = next(c for c in result.document.claims if c.claim_number == CLAIMS[0][0])
    assert first.paid_total == _dec(CLAIMS[0][8][0])
    assert "R-23" in result.reconciliation.rule_ids()
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def test_an_amount_is_never_a_claim_number_candidate():
    from core.records import is_identifier_candidate

    for amount in ("$6,750.00", "6,750.00", "($1,000)", "€1.234,56", "1,234"):
        assert not is_identifier_candidate(amount), amount
    for number in ("E0W6370", "AL201102574-1", "550-105946-001", "2019.01", "040512146091"):
        assert is_identifier_candidate(number), number


def test_a_clean_detail_report_reads_clean(tmp_path):
    """Its valuation is "Losses as of:", and its "Claim" column is money."""
    result = _read(tmp_path)
    assert str(result.document.valuation_date) == "2019-06-08"
    assert "R-21" not in result.reconciliation.rule_ids()
    assert canonical_status(result.reconciliation) is DocumentStatus.CLEAN


def test_a_contested_column_holding_text_is_still_reported():
    from core.labelled_rows import read_line

    headers = ("Claim Number", "O/C", "Total", "Claim")
    assert read_line(("", "Pd:", "$5.00", "$5.00"), headers) is not None
    assert read_line(("", "Pd:", "$5.00", "AB1234"), headers) is None


def test_a_description_crossing_the_claim_number_column_is_not_a_claim_row(tmp_path):
    long = tuple(
        (*claim[:6], "PATIENT STATED THAT WHILE WORKING AS A SUPERINTENDANT HE FELL", *claim[7:])
        for claim in CLAIMS
    )
    result = _read(tmp_path, claims=long)
    assert len(result.document.claims) == len(CLAIMS)
    assert "R-19" not in result.reconciliation.rule_ids()
