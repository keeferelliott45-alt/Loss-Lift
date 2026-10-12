"""A claim register printed in spreadsheet accounting format.

Public entities that administer their own claims keep them in a spreadsheet
and print it. Accounting format sets the currency mark at the left edge of
each money cell and the amount at the right, prints zero as ``$ -`` and a
negative as ``$ (1,000.00)``. Read naively:

* the space between the mark and the amount looked like a column gutter, so
  every money column split in two -- "Total Paid" became a "Total" column read
  as the incurred figure and a "Paid" column -- and the lone ``$`` was filed
  as a value;
* ``$ -`` and ``$ (…)`` did not parse, so printed zeros came back as nothing.

The mark now joins the amount it introduces, ``$ -`` is zero and ``$ (…)`` a
negative. And where a register prints both a bare "Reserve" and a qualified
"Rem Reserve", the qualified one is the reserve outstanding; the bare one is
left for a person to place (R-21), never guessed.

Synthetic PDFs only.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.normalize import parse_money
from core.pipeline import run_pipeline
from core.profiles import resolve_columns

# Left edge of each column; money columns put "$" here and right-align the
# amount at the next edge minus a small margin.
EDGES = {"number": 20, "loss": 66, "desc": 120, "reserve": 300, "indemnity": 362,
         "legal": 426, "paid": 490, "rem": 554, "status": 618, "end": 660}
MONEY = ("reserve", "indemnity", "legal", "paid", "rem")
HEADER = {"number": "Claim #", "loss": "Date of Loss", "desc": "Loss Description",
          "reserve": "Reserve", "indemnity": "Indemnity", "legal": "Legal Expenses",
          "paid": "Total Paid", "rem": "Rem Reserve", "status": "Status"}
NEXT = {"reserve": "indemnity", "indemnity": "legal", "legal": "paid", "paid": "rem",
        "rem": "status"}

# number, date, description, reserve, indemnity, legal, paid, rem, status
# None prints an empty cell (no mark either); "-" prints the accounting zero.
ROWS = (
    ("L16-0003", "5/26/2015", "Fell at county building", "4,610.28", "4,610.28", "-",
     "4,610.28", "-", "closed"),
    ("L16-0012", "6/9/2014", "Negligence of officer", "9,706.55", "-", "9,706.55",
     "9,706.55", "-", "closed"),
    ("L24-0002", "1/14/2024", "Cot dropped in transport", "50,000.00", "-", "676.00",
     "676.00", "49,324.00", "open"),
    ("L24-0010", "2/20/2024", "Inmate complaint", "100,000.00", None, "61,644.00",
     "61,644.00", "38,356.00", "open"),
)


def _money(page, y, column, value, size):
    if value is None:
        return
    left, right = EDGES[column] + 2, EDGES[NEXT[column]] - 6
    page.insert_text((left, y), "$", fontsize=size)
    width = pymupdf.get_text_length(value, fontsize=size)
    page.insert_text((right - width, y), value, fontsize=size)


def _pdf(tmp_path, rows=ROWS):
    document = pymupdf.open()
    page = document.new_page(width=680, height=500)
    size = 7
    page.insert_text((20, 30), "General Liability Claims - Ridgeway Test County 2015/2016",
                      fontsize=9)
    page.insert_text((20, 42), "Valuation Date: 03/31/2025", fontsize=8)
    y = 62.0
    for column, label in HEADER.items():
        page.insert_text((EDGES[column] + 2, y), label, fontsize=size)
    y += 12
    for number, loss, desc, *money, status in rows:
        page.insert_text((EDGES["number"] + 2, y), number, fontsize=size)
        page.insert_text((EDGES["loss"] + 2, y), loss, fontsize=size)
        page.insert_text((EDGES["desc"] + 2, y), desc, fontsize=size)
        for column, value in zip(MONEY, money):
            _money(page, y, column, value, size)
        page.insert_text((EDGES["status"] + 2, y), status, fontsize=size)
        y += 12
    path = tmp_path / "register.pdf"
    document.save(path)
    document.close()
    return path


def _read(tmp_path, rows=ROWS):
    return run_pipeline(_pdf(tmp_path, rows), use_vision=False,
                        profiles_dir=tmp_path / "profiles")


@pytest.mark.parametrize("text, value", [
    ("$ -", "0"), ("$   -", "0"), ("$ (1,000.00)", "-1000.00"), ("$  4,610.28", "4610.28"),
])
def test_accounting_format_cells_parse(text, value):
    assert parse_money(text, "us").value == Decimal(value)


def test_a_lone_dash_is_still_not_a_zero():
    assert parse_money("-", "us").value is None


def test_a_qualified_reserve_outranks_a_bare_one():
    guesses, _ = resolve_columns(["Claim #", "Reserve", "Total Paid", "Rem Reserve"], [])
    assert guesses[3].field == "reserve_total"
    assert guesses[1].field is None and guesses[1].contested_field == "reserve_total"


def test_the_money_columns_stay_whole(tmp_path):
    headers = [r.source_header_raw for r in _read(tmp_path).document.column_mapping]
    for label in ("Total Paid", "Rem Reserve", "Legal Expenses"):
        assert label in headers


def test_every_amount_lands_in_its_field(tmp_path):
    by_number = {c.claim_number: c for c in _read(tmp_path).document.claims}
    assert sorted(by_number) == sorted(r[0] for r in ROWS)
    for number, _loss, _desc, _reserve, _ind, _legal, paid, rem, _status in ROWS:
        claim = by_number[number]
        assert claim.paid_total == Decimal(paid.replace(",", "") if paid != "-" else "0")
        assert claim.reserve_total == Decimal(rem.replace(",", "") if rem != "-" else "0")


def test_the_accounting_zero_is_zero_and_a_blank_stays_blank(tmp_path):
    result = _read(tmp_path)
    by_number = {c.claim_number: c for c in result.document.claims}
    assert by_number["L16-0003"].reserve_total == Decimal("0")
    assert by_number["L24-0010"].raw_cells.get("paid_indemnity", "") == ""


def test_the_bare_reserve_column_is_left_to_a_person(tmp_path):
    result = _read(tmp_path)
    assert "R-21" in result.reconciliation.rule_ids()
    assert all(c.incurred_total is None for c in result.document.claims)
