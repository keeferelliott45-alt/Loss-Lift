"""Four date columns whose labels wrap, and a policy that has no claims.

Reproduces the structure of a real carrier loss run published in a school
district's procurement record. It is synthetic — the shape is copied, none of
the content is (spec section 9).

Two patterns that broke the pipeline and neither of the other fixtures has:

* **Every date column reads "Date" on its own line.** The word that says which
  date it is — Loss, Occur, Closed — sits on the line above. Read the lower
  line alone and the document has four indistinguishable "Date" columns and no
  date of loss, so every claim reports a missing required field. Merging the
  two lines scores no better by count, which is why the merge has to accept a
  tie: "Loss Date" and "Date" are worth the same to a scorer and are not worth
  the same to a reader.
* **A policy section with no claims at all.** An eleven page report can be
  mostly "No claims were found for this policy"; those pages must contribute
  no claims and no findings rather than a row of nulls.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pymupdf
import pytest

from core.pipeline import run_pipeline
from core.schema import Severity

FONT = "helv"
SIZE = 7.0

#: (x, upper line word, lower line word). The upper word is what distinguishes
#: one date column from the next three.
COLUMNS = (
    (30, "Date", "Reported"),
    (95, "Loss", "Date"),
    (160, "Occur", "Date"),
    (225, "Closed", "Date"),
    (295, None, "Claim Nbr"),
    (380, None, "Claimant Name"),
    (500, "Claim", "Status"),
    (560, "Indemnity", "Paid"),
    (640, "Indemnity", "Reserves"),
    (720, "Total", "Incurred"),
)

CLAIMS = (
    ("2/9/2015", "2/6/2015", "2/7/2015", "4/20/2015", "E2B95210",
     "Northside Campus", "CLOSED", 2403, 0, 2403),
    ("7/3/2014", "7/2/2014", "7/2/2014", "7/2/2015", "E2B53184",
     "Westgate Campus", "CLOSED", 41880, 2275786, 2317666),
)


def _write(page, text, x, y):
    page.insert_text((x, y), text, fontname=FONT, fontsize=SIZE)


def _header(page, y):
    for x, upper, lower in COLUMNS:
        if upper:
            _write(page, upper, x, y)
        _write(page, lower, x, y + 11)
    return y + 26


def _build(path) -> None:
    doc = pymupdf.open()

    # Page 1: a policy that ran clean. Real reports carry pages of these.
    page = doc.new_page(width=792, height=612)
    _write(page, "Continental Assurance Company", 30, 26)
    _write(page, "Policy: 5084732785   Effective 07/01/2012 - 07/01/2013", 30, 40)
    _header(page, 70)
    _write(page, "No claims were found for this policy.", 30, 110)
    _write(page, "Report ID : MV 335", 30, 560)
    _write(page, "Page 1 of 2", 400, 560)

    # Page 2: the policy that has the claims.
    page = doc.new_page(width=792, height=612)
    _write(page, "Continental Assurance Company", 30, 26)
    _write(page, "Policy: 5084732799   Effective 07/01/2014 - 07/01/2015", 30, 40)
    _write(page, "Numbers As of 5/22/2023", 30, 54)
    y = _header(page, 70)

    for row in CLAIMS:
        values = list(row[:7]) + [f"${row[7]:,}", f"${row[8]:,}", f"${row[9]:,}"]
        for (x, _, _), value in zip(COLUMNS, values):
            _write(page, value, x, y)
        y += 14

    _write(page, "Policy Total for Effective Date 07/01/2014:", 30, y + 6)
    for (x, _, _), total in zip(
        COLUMNS[7:],
        (sum(c[7] for c in CLAIMS), sum(c[8] for c in CLAIMS), sum(c[9] for c in CLAIMS)),
    ):
        _write(page, f"${total:,}", x, y + 6)
    _write(page, "Report ID : MV 335", 30, 560)
    _write(page, "Page 2 of 2", 400, 560)

    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    path = tmp_path_factory.mktemp("stacked") / "stacked_dates.pdf"
    _build(path)
    return run_pipeline(path, use_vision=False)


def test_the_wrapped_word_decides_which_date_is_which(result):
    """"Loss" over "Date" is the date of loss; the other three are not."""
    headers = " | ".join(result.mapping.headers).lower()
    assert "loss date" in headers or "date loss" in headers
    by_number = {c.claim_number: c for c in result.document.claims}
    assert by_number["E2B95210"].date_of_loss == date(2015, 2, 6)
    assert by_number["E2B53184"].date_of_loss == date(2014, 7, 2)


def test_every_claim_has_a_date_of_loss(result):
    """Reading only the lower header line left all four dates unmapped."""
    assert all(c.date_of_loss is not None for c in result.document.claims)


def test_a_policy_with_no_claims_contributes_none(result):
    """The empty page must add no claims, not a row of nulls."""
    assert [c.claim_number for c in result.document.claims] == [
        "E2B95210", "E2B53184"
    ]


def test_indemnity_columns_land_in_indemnity_fields(result):
    """"Indemnity Paid" is not paid_total, and must not be read as it."""
    claim = next(c for c in result.document.claims if c.claim_number == "E2B95210")
    assert claim.paid_indemnity == Decimal("2403")
    assert claim.reserve_indemnity == Decimal("0")
    assert claim.incurred_total == Decimal("2403")


def test_no_hard_failures(result):
    """Nothing above is wrong, so nothing should be reported as wrong."""
    errors = [
        f for f in result.reconciliation.findings if f.severity is Severity.ERROR
    ]
    assert errors == [], "\n".join(f"{f.rule_id} {f.message}" for f in errors)
