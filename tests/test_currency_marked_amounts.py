"""Two amounts in one cell, each wearing its own currency marker.

A currency symbol is an adornment on one number, and stripping it before
asking whether the cell's whitespace groups one number or fuses two is what
lets "5.700,50 €" be read at all. But the stripping removed *every* marker, so
a cell holding two separately-marked amounts came out as one:

    "$1 $234"   ->  "1 234"    ->  a valid three-digit group  ->  1234
    "$12 $345"  ->  "12 345"   ->  a valid three-digit group  ->  12345

Neither figure is printed anywhere. The cell says one dollar and two hundred
and thirty-four dollars, and the reading invents twelve hundred and
thirty-four. At the base commit both cells were refused; this is a
regression the currency handling introduced.

The discriminator is positional, not a count of markers. A carrier writes the
marker at one end of the amount or the other -- "$1,234.56", "5.700,50 €",
"$ 1 234,56", and "$1,234.56 USD" all put every marker outside the digits. No
convention writes one *between* the digit groups of a single number. So a
marker with digits on both sides of it is evidence of a second amount, and
that is the whole of the test.

Cases below are synthetic cell text (spec section 9). They are asserted
through ``unplaced_money``, the real consumer, as well as the helper: the
helper returning False is only interesting because a value reaches a claim
field because of it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.pipeline import ColumnMapping, _is_smeared, unplaced_evidence
from core.schema import RawRow

MAPPING = ColumnMapping(
    headers=["Claim Number", "Paid Total"],
    fields={0: "claim_number", 1: "paid_total"},
)


def _amount(text: str, locale: str = "us"):
    """What the reader makes of this cell under an established money column.

    The context is stated rather than left to be inferred: this file's subject
    is how a cell's currency markers are read, not how a table establishes
    that its numbers are money, and a fixture that left the second open would
    be asking two questions at once.
    """
    row = RawRow(cells=["", text], page=1, line_index=10)
    return unplaced_evidence(
        row, MAPPING, locale, context="monetary-only"
    ).amounts.get("paid_total")


@pytest.mark.parametrize(
    "text, locale",
    [
        ("$1 $234", "us"),
        ("$12 $345", "us"),
        ("$1 $2 $3", "us"),
        ("€5 €700", "eu"),
        ("5.700,50 € 200,00 €", "eu"),
        ("$1,234.56 $2,345.67", "us"),
    ],
)
def test_two_marked_amounts_never_become_one(text, locale):
    """The regression itself, asserted where it does damage."""
    assert _is_smeared(text) is True, text
    assert _amount(text, locale) is None, (
        f"{text!r} produced an amount that is printed nowhere: {_amount(text, locale)}"
    )


@pytest.mark.parametrize(
    "text, locale, value",
    [
        ("$1,234.56", "us", Decimal("1234.56")),
        ("5.700,50 €", "eu", Decimal("5700.50")),
        ("1 200,00 €", "eu", Decimal("1200.00")),
        ("€ 1 234,56", "eu", Decimal("1234.56")),
        ("$ 1,234.56", "us", Decimal("1234.56")),
        ("$1,234.56 USD", "us", Decimal("1234.56")),
        ("1,234.56 CR", "us", Decimal("-1234.56")),
    ],
)
def test_one_amount_keeps_its_adornments(text, locale, value):
    """Every marker sits outside the digits, so the cell is one number.

    ``"$1,234.56 USD"`` carries two markers and is still one amount: both are
    at the ends. Counting markers would reject it; asking where they sit does
    not.
    """
    assert _is_smeared(text) is False, text
    assert _amount(text, locale) == (text, value)


@pytest.mark.parametrize(
    "text",
    [
        "4 30,000.00",   # a claim count fused to the amount beside it
        "36571 44694",   # two unrelated runs, neither a three-digit group
        "4 .00",
    ],
)
def test_unmarked_smears_are_still_refused(text):
    """The whitespace rule this sits on top of is unchanged."""
    assert _is_smeared(text) is True
    assert _amount(text) is None
