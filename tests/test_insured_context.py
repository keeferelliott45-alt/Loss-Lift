"""The insured every page shares does not decide whose report a page is.

A submission packet binds several carriers' loss runs for one insured, and
every page prints that insured. Its words counted as naming words, so two
carriers' headings always partially overlapped -- which settles nothing -- and
run 2 was left ambiguous (R-28): printed totals and counts stayed at document
level and raised findings nobody made (R-04/R-25/R-27). Real packets for one
insured almost always look like this.

Where two pages print the same insured, its words are set aside and the rest
of the heading -- the carrier -- decides. Where the insureds differ, or a page
prints none, headings are compared exactly as before, and where nothing is
left once the insured is set aside, the insured itself still confirms.

Synthetic PDFs only.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf

from core.pipeline import run_pipeline
from core.review import canonical_status
from core.runs import same_heading
from core.schema import DocumentStatus

INSURED = "Named Insured: Ridgeway Test Freight LLC"
OTHER_INSURED = "Named Insured: Quarry Lane Test Bakery Inc"
LEFT, LINE = 36.0, 14.0
COLUMNS = (0, 95, 170, 240, 325, 410)
HEADERS = ("Claim Number", "Loss Date", "Status", "Paid Total", "Reserve Total",
           "Incurred Total")


def _rows(prefix, start, count, digits=False):
    rows = []
    for n in range(count):
        number = f"{prefix}{start + n}" if digits else f"{prefix}-{start + n}"
        paid = Decimal(1000 + 250 * n)
        rows.append((number, f"{1 + n:02d}/{14 + n:02d}/2024", "CLOSED", f"{paid:,.2f}",
                     "0.00", f"{paid:,.2f}"))
    return rows


def _total(rows):
    paid = sum(Decimal(r[3].replace(",", "")) for r in rows)
    return ("TOTAL", "", "", f"{paid:,.2f}", "0.00", f"{paid:,.2f}")


def _pdf(tmp_path, pages):
    document = pymupdf.open()
    for top, rows, total, footer in pages:
        page = document.new_page(width=612, height=792)
        y = 40.0
        for line in top:
            page.insert_text((LEFT, y), line, fontsize=9)
            y += LINE
        y += LINE
        for x, label in zip(COLUMNS, HEADERS):
            page.insert_text((LEFT + x, y), label, fontsize=8)
        y += LINE
        for row in [*rows, *([total] if total else [])]:
            for x, cell in zip(COLUMNS, row):
                if cell:
                    page.insert_text((LEFT + x, y), cell, fontsize=8)
            y += LINE
        page.insert_text((LEFT, 792 - 24), footer, fontsize=8)
    path = tmp_path / "packet.pdf"
    document.save(path)
    document.close()
    return path


def _read(tmp_path, pages):
    return run_pipeline(_pdf(tmp_path, pages), use_vision=False,
                        profiles_dir=tmp_path / "profiles")


def _top(carrier, insured=INSURED):
    return tuple(line for line in (carrier, "LOSS RUN REPORT", insured,
                                   "Valuation Date: 12/31/2024") if line)


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------


def test_a_shared_insured_is_set_aside_and_the_carriers_decide():
    one = "ashgrove mutual ridgeway test freight"
    other = "blue ledger casualty ridgeway test freight"
    insured = "Ridgeway Test Freight LLC"
    assert same_heading(one, other) is None  # the defect: partial overlap
    assert same_heading(one, other, insured=(insured, insured)) is False
    assert same_heading(one, one, insured=(insured, insured)) is True


def test_with_nothing_left_the_insured_still_confirms():
    insured = "Ridgeway Test Freight LLC"
    heading = "loss run report ridgeway test freight"
    assert same_heading(heading, heading, insured=(insured, insured)) is True


def test_different_insureds_are_compared_as_before():
    one = "ashgrove mutual ridgeway test freight"
    other = "ashgrove mutual quarry lane bakery"
    as_before = same_heading(one, other)
    assert same_heading(one, other, insured=("Ridgeway Test Freight LLC",
                                              "Quarry Lane Test Bakery Inc")) is as_before
    assert same_heading(one, other, insured=("Ridgeway Test Freight LLC", None)) is as_before


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


def test_two_carriers_for_one_insured_are_two_settled_runs(tmp_path):
    first, second = _rows("AG", 24101, 5), _rows("7310", 450, 3, digits=True)
    result = _read(tmp_path, [
        (_top("ASHGROVE MUTUAL INSURANCE COMPANY"), first, _total(first), "Page 1 of 1"),
        (_top("BLUE LEDGER CASUALTY COMPANY"), second, _total(second), "Page 1 of 1"),
    ])
    runs = result.document.runs
    assert [run.pages for run in runs] == [[1], [2]]
    assert not any(run.ambiguous for run in runs)
    rules = result.reconciliation.rule_ids()
    assert "R-28" not in rules and "R-04" not in rules and "R-25" not in rules
    assert [c.claim_number for c in result.document.claims] == [r[0] for r in first + second]
    assert canonical_status(result.reconciliation) is DocumentStatus.CLEAN


def test_a_report_without_a_carrier_line_keeps_its_pages(tmp_path):
    rows = _rows("AG", 24301, 8)
    result = _read(tmp_path, [
        (_top(None), rows[:4], None, "Page 1 of 2"),
        (_top(None), rows[4:], _total(rows), "Page 2 of 2"),
    ])
    assert result.document.runs == []
    assert len(result.document.claims) == 8
    assert canonical_status(result.reconciliation) is DocumentStatus.CLEAN


def test_two_insureds_under_one_carrier_are_not_merged_silently(tmp_path):
    first, second = _rows("AG", 24501, 4), _rows("AG", 24601, 3)
    result = _read(tmp_path, [
        (_top("ASHGROVE MUTUAL INSURANCE COMPANY"), first, _total(first), "Page 1 of 1"),
        (_top("ASHGROVE MUTUAL INSURANCE COMPANY", OTHER_INSURED), second, _total(second),
         "Page 1 of 1"),
    ])
    merged_clean = (len(result.document.runs) < 2
                    and canonical_status(result.reconciliation) is DocumentStatus.CLEAN)
    assert not merged_clean
