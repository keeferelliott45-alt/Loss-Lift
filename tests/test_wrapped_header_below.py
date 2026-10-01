"""A column label that wraps downward, below the header line.

A pool's claim report heads one column "Total" on the header line and prints
"Outstanding" and "Reserve" on the two lines beneath it, inside the same
column. Only the header line was read, so the column came through as "Total"
-- taken for the incurred figure -- the reserve was never read, and the real
incurred column lost its label to it.

Lines of labels directly below the header now fold into the label they sit
under, when every word on them lies inside that label's span. A heading set
under the table's left edge does not fold into "Claim Number".

Synthetic PDFs only.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf

from core.pipeline import run_pipeline

X = {"number": 40, "claimant": 130, "date": 250, "status": 305, "cause": 340,
     "paid": 500, "reserve": 548, "subtotal": 590, "deductible": 630, "recovery": 668,
     "total": 726}
ROWS = (
    ("AL201406511-1", "Mariel Test", "07/07/2014", "Closed", "BACKED WITHOUT SAFETY",
     "$3,809", "$0", "$3,809", "($1,000)", "$0", "$2,809"),
    ("AL201407094-1", "Heber Test", "11/22/2014", "Open", "FAILURE TO CONTROL SPEED",
     "$1,013,662", "$2,500", "$1,016,162", "($1,000)", "$0", "$1,015,162"),
    ("AL201407200-1", "Thomas Test", "12/21/2014", "Closed", "PASSING UNSAFELY",
     "$2,505", "$0", "$2,505", "$0", "$0", "$2,505"),
)


def _pdf(tmp_path, *, section="Automobile Liability"):
    document = pymupdf.open()
    page = document.new_page(width=792, height=612)
    size = 6.5
    page.insert_text((44, 30), "Claim/Loss Report", fontsize=8)
    page.insert_text((44, 40), "Member Selected: Ridgeway Test County", fontsize=7)
    page.insert_text((44, 50), "Report Run Date: Feb 18, 2019", fontsize=7)
    y = 70.0
    for key, label in (("number", "Claim Number"), ("claimant", "Claimant Full Name"),
                       ("date", "Loss Date"), ("status", "Status"), ("cause", "Cause of Loss"),
                       ("paid", "Total Paid"), ("reserve", "Total"), ("subtotal", "Subtotal"),
                       ("deductible", "Deductible"), ("recovery", "Loss Recoveries"),
                       ("total", "Total")):
        page.insert_text((X[key], y), label, fontsize=size)
    page.insert_text((X["reserve"] - 8, y + 7), "Outstanding", fontsize=size)
    page.insert_text((X["reserve"] - 3, y + 13), "Reserve", fontsize=size)
    y += 30
    if section:
        page.insert_text((36, y), section, fontsize=size)
        y += 10
    for number, who, date, status, cause, *money in ROWS:
        for key, value in zip(("number", "claimant", "date", "status", "cause"),
                              (number, who, date, status, cause)):
            page.insert_text((X[key], y), value, fontsize=size)
        for key, value in zip(("paid", "reserve", "subtotal", "deductible", "recovery",
                               "total"), money):
            width = pymupdf.get_text_length(value, fontsize=size)
            page.insert_text((X[key] + 30 - width, y), value, fontsize=size)
        y += 10
    path = tmp_path / "pool.pdf"
    document.save(path)
    document.close()
    return path


def _read(tmp_path, **kwargs):
    return run_pipeline(_pdf(tmp_path, **kwargs), use_vision=False,
                        profiles_dir=tmp_path / "profiles")


def _dollars(text):
    negative = text.startswith("(")
    value = Decimal(text.strip("()$").replace(",", ""))
    return -value if negative else value


def test_the_wrapped_label_is_read_whole(tmp_path):
    headers = [r.source_header_raw for r in _read(tmp_path).document.column_mapping]
    assert "Total Outstanding Reserve" in headers


def test_the_outstanding_reserve_is_read(tmp_path):
    by_number = {c.claim_number: c for c in _read(tmp_path).document.claims}
    assert sorted(by_number) == sorted(r[0] for r in ROWS)
    for row in ROWS:
        claim = by_number[row[0]]
        assert claim.reserve_total == _dollars(row[6])
        assert claim.paid_total == _dollars(row[5])


def test_a_heading_under_the_table_edge_does_not_join_a_label(tmp_path):
    mapping = _read(tmp_path, section="General Liability").document.column_mapping
    assert any(r.source_header_raw == "Claim Number" and r.canonical_field == "claim_number"
               for r in mapping)


def test_words_with_a_number_in_them_cast_no_locale_vote():
    """A footer ("Report Run Date: Feb 18,") falling under a money column on
    every page outvoted the amounts and left every "$3,809" ambiguous."""
    from core.normalize import infer_locale

    tokens = ["$3,809", "$1,123,052"] + ["Report Run Date: Feb 18,"] * 12
    inference = infer_locale(tokens)
    assert inference.locale == "us" and inference.confident
    assert infer_locale(["1.234,56 €", "Page 3 of 12"]).locale == "eu"
