"""A section total that says how many claims it covers instead of "Total".

Carriers that group a run by policy print one subtotal row per policy. Most
label it with the word "total" in some arrangement, which is what
``_is_total_line`` looks for. AIG's IntelliRisk export does not: its row reads

    Pol-Asco-Mod: 0011227988-029-002  Claim Count = 4  30,000.00 … 30,692.75

The row is a total by every other measure -- it sits under the last claim of
its policy, it carries a figure under every money column, and it says outright
how many claims those figures cover -- but it never uses the word, so it is
read as a claim row instead. Three things follow, and all three were measured
on the real document before this test was written:

* Its money is reported by R-23 as an amount that could not be attached to any
  claim. That is a $30,692.75 "missing" figure that is not missing: it is the
  sum of the four claims printed directly above it.
* R-19 counts it among the page's claim rows, then reports that rows were lost
  in stitching. Nothing was lost.
* The per-policy totals the carrier committed to -- the only numbers on the
  document that R-04 and the Loss Summary sheet can verify anything against --
  are never collected at all.

The label alone is not the evidence. A column *headed* "Claim Count" names a
column and totals nothing, so the count has to actually be there: the row is a
total because it says how many claims it covers, not because it uses the
phrase. The last test holds that line.

Geometry and row shape follow AIG's real page; policy identifiers, claimant
names and descriptions are synthetic (spec section 9).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.extract_digital import extract_pdf
from core.pipeline import (
    ColumnMapping,
    collect_printed_sections,
    collect_printed_totals,
)

pymupdf = pytest.importorskip("pymupdf")


#: x positions of AIG's columns, in the page's own points.
_COLUMNS = [
    ("claim", 36.0),
    ("dates", 150.0),
    ("class", 214.0),
    ("ind", 420.0),
    ("med", 470.0),
    ("exp", 516.0),
    ("rsv", 566.0),
    ("rec", 616.0),
    ("inc", 660.0),
]

_HEADER = [
    [("Claimant Name", 36.0), ("Loss Type", 150.0), ("Div / H.O.", 214.0),
     ("Accident / Loss Description", 280.0)],
    [("Claim # / OneClaim #", 36.0), ("St/Terr/Ctry", 150.0), ("Status", 214.0),
     ("Ind/BI", 420.0), ("Med/PD", 470.0), ("Alloc Exp", 516.0),
     ("Total", 566.0), ("Total", 616.0), ("Total", 660.0)],
    [("Loss Date", 36.0), ("Receipt Date", 100.0), ("Closed Date", 150.0),
     ("Major Class Code/Description", 214.0), ("Paid", 420.0), ("Paid", 470.0),
     ("Paid", 516.0), ("Reserves", 566.0), ("Recoveries", 616.0),
     ("Incurred", 660.0)],
]

#: Four synthetic claims in AIG's three-line record shape, with the money the
#: subtotal below them adds up to.
_CLAIMS = [
    ("502-000001-001/1000000001US", "ALVARADO,ROSA", "02/16/2025", "02/19/2025", "",
     ["", "", "25.00", "500.00", "", "525.00"]),
    ("502-000002-001/1000000002US", "BENNETT,PAULA", "07/27/2024", "09/20/2024",
     "01/23/2026", ["15,000.00", "", "17.75", "", "", "15,017.75"]),
    ("502-000002-002/1000000002US", "CHANDLER,MAE", "07/27/2024", "09/20/2024",
     "01/23/2026", ["15,000.00", "", "", "", "", "15,000.00"]),
    ("502-000002-003/1000000002US", "DELACROIX,AUGUSTA", "07/27/2024", "10/07/2024",
     "02/27/2025", ["", "", "150.00", "", "", "150.00"]),
]

#: The row under them: AIG's own wording, with a synthetic policy identifier.
_SUBTOTAL_LABEL = "Pol-Asco-Mod: 0011227988-029-002"
_SUBTOTAL_COUNT = "Claim Count = 4"
_SUBTOTAL_MONEY = ["30,000.00", ".00", "192.75", "500.00", ".00", "30,692.75"]

_MONEY_X = [420.0, 470.0, 516.0, 566.0, 616.0, 660.0]
_SIZE = 6.0
_LINE = 8.0


def _draw(page, text, x, y, bold=False):
    page.insert_text(
        pymupdf.Point(x, y), text,
        fontname="hebo" if bold else "helv", fontsize=_SIZE,
    )


def _render(path, *, count_text: str = _SUBTOTAL_COUNT, count_column: bool = False):
    document = pymupdf.open()
    page = document.new_page(width=792, height=612)
    y = 40.0
    _draw(page, "AIG Loss Run", 36.0, y)
    y += _LINE
    _draw(page, "Valuation Date: 03/13/2026", 36.0, y)
    y += _LINE * 2

    header = [list(row) for row in _HEADER]
    if count_column:
        # A carrier whose table really does carry a claim-count column: the
        # phrase is in the header, and no count follows it there.
        header[1] = header[1] + [("Claim Count", 700.0)]
    for row in header:
        for text, x in row:
            _draw(page, text, x, y, bold=True)
        y += _LINE
    y += 2

    for number, name, loss, received, closed, money in _CLAIMS:
        _draw(page, name, 36.0, y)
        _draw(page, "AUTO", 150.0, y)
        _draw(page, "027/294", 214.0, y)
        _draw(page, "SYNTHETIC DESCRIPTION OF LOSS", 280.0, y)
        y += _LINE
        _draw(page, number, 36.0, y)
        _draw(page, "Acc/Ben: FL/", 150.0, y)
        _draw(page, "Closed" if closed else "Open", 214.0, y)
        y += _LINE
        _draw(page, loss, 36.0, y)
        _draw(page, received, 100.0, y)
        _draw(page, closed, 150.0, y)
        _draw(page, "007 -AUTO LIABILITY (P.D.)", 214.0, y)
        for text, x in zip(money, _MONEY_X):
            if text:
                _draw(page, text, x, y)
        y += _LINE

    _draw(page, _SUBTOTAL_LABEL, 36.0, y, bold=True)
    if count_text:
        _draw(page, count_text, 214.0, y, bold=True)
    for text, x in zip(_SUBTOTAL_MONEY, _MONEY_X):
        _draw(page, text, x, y, bold=True)

    document.save(str(path))
    document.close()
    return path


def _table(tmp_path, **kwargs):
    extraction = extract_pdf(_render(tmp_path / "counted.pdf", **kwargs))
    assert extraction.tables, "fixture produced no table"
    return extraction.tables[0]


def _row_texts(rows):
    return [row.text() for row in rows]


def test_fixture_reproduces_the_four_claims(tmp_path):
    """The fixture must read the claims correctly, or the assertions below
    would be measuring a broken page rather than the subtotal rule."""
    table = _table(tmp_path)
    at = table.headers.index("Claim # / OneClaim #")
    numbers = [row.cells[at] for row in table.rows]
    for number, *_ in _CLAIMS:
        assert number in numbers, f"{number} not read: {numbers}"
    incurred = table.headers.index("Total Incurred")
    assert [row.cells[incurred] for row in table.rows][:4] == [
        money[-1] for _, _, _, _, _, money in _CLAIMS
    ]


def test_the_counted_subtotal_is_read_as_a_total_row(tmp_path):
    """The row says how many claims its figures cover. That is a total."""
    table = _table(tmp_path)
    assert any(_SUBTOTAL_COUNT in text for text in _row_texts(table.total_rows)), (
        f"subtotal not recognised; total rows: {_row_texts(table.total_rows)}"
    )
    assert not any(_SUBTOTAL_COUNT in text for text in _row_texts(table.rows)), (
        "the subtotal is still being read as a claim row"
    )


def test_the_subtotal_money_is_not_reported_as_unattached(tmp_path):
    """R-23's finding on this row was $30,692.75 that was never missing: it
    is the sum of the four claims printed directly above it."""
    table = _table(tmp_path)
    for row in table.rows:
        assert "30,692.75" not in row.text(), (
            "the section's own total is still sitting in the claim rows, where "
            "it will be reported as money that could not be placed"
        )


def test_the_section_total_becomes_checkable(tmp_path):
    """The point of recognising it: the per-policy figures the carrier
    committed to are collected, so they can be verified against."""
    table = _table(tmp_path)
    mapping = ColumnMapping(
        headers=table.headers,
        fields={
            table.headers.index("Ind/BI Paid"): "paid_indemnity",
            table.headers.index("Alloc Exp Paid"): "paid_expense",
            table.headers.index("Total Reserves"): "reserve_total",
            table.headers.index("Total Incurred"): "incurred_total",
        },
    )
    sections = collect_printed_sections([table], mapping, "us", "mdy")
    assert sections, "the per-policy subtotal was not collected"
    section = sections[0]
    assert section.printed_claim_count == 4, section.printed_claim_count
    assert section.printed_totals.get("incurred_total") == Decimal("30692.75"), (
        section.printed_totals
    )
    # It totals four claims, so it must never stand in as a document total for
    # a different number of claims.
    assert collect_printed_totals([table], mapping, "us", claim_count=9) == {}


def test_the_count_is_never_read_as_one_of_the_amounts(tmp_path):
    """Recognising the row is only half of it: what is read off it has to be
    right, and on this page the count sits hard against the first money column.

    Two ways it lands there, and this document shows both. Where the label and
    the count run into the amount beside them the cell reads "4 30,000.00",
    which is not a number in any convention -- a space groups in threes -- and
    is worth 430,000.00 if the space is simply deleted. Where the label ends on
    a column boundary instead, the bare "4" lands under the money heading on
    its own. Neither is that column's total. A subtotal is the row a reviewer
    trusts most, so a figure that cannot be read off it is withheld rather than
    guessed at.
    """
    table = _table(tmp_path)
    mapping = ColumnMapping(
        headers=table.headers,
        fields={
            table.headers.index("Ind/BI Paid"): "paid_indemnity",
            table.headers.index("Alloc Exp Paid"): "paid_expense",
            table.headers.index("Total Reserves"): "reserve_total",
            table.headers.index("Total Incurred"): "incurred_total",
        },
    )
    printed = collect_printed_sections([table], mapping, "us", "mdy")[0].printed_totals
    assert "paid_indemnity" not in printed, (
        f"the claim count was read as an amount: {printed.get('paid_indemnity')}"
    )
    # Everything the row states in a column of its own is still read.
    assert printed["paid_expense"] == Decimal("192.75")
    assert printed["incurred_total"] == Decimal("30692.75")
    # And nothing that was withheld came back wearing another column's name.
    assert Decimal("430000.00") not in printed.values(), printed
    assert Decimal("4") not in printed.values(), printed


def test_a_count_in_its_own_cell_is_not_an_amount(tmp_path):
    """The second form, isolated: the label ends where the column does, so the
    count stands alone under a money heading with nothing glued to it."""
    from core.pipeline import _is_the_claim_count
    from core.schema import RawRow

    row = RawRow(
        cells=["Pol-Asco-Mod: 0011227988-029-001", "", "Claim Count =", "0", ".00"],
        page=1,
        line_index=20,
        kind="total",
    )
    assert _is_the_claim_count(row, 3), "the bare count was taken for an amount"
    assert not _is_the_claim_count(row, 4), ".00 is the column's own total"

    # A label that already carries its count has been read; what follows it is
    # money, and dropping that would lose a real figure.
    read = RawRow(
        cells=["Pol-Asco-Mod: 0011227988-029-002", "Claim Count = 4", "30000", ".00"],
        page=1,
        line_index=21,
        kind="total",
    )
    assert not _is_the_claim_count(read, 2), (
        "an amount beside an already-counted label was dropped"
    )


def test_a_claim_count_column_heading_is_not_a_total(tmp_path):
    """The guard. A column headed "Claim Count" states no count of its own,
    and a header that became a totals row would take the table with it."""
    table = _table(tmp_path, count_column=True)
    for row in table.total_rows:
        assert "Ind/BI" not in row.text(), (
            f"the header line was read as a total row: {row.text()}"
        )
    assert any(_SUBTOTAL_COUNT in text for text in _row_texts(table.total_rows)), (
        "the real subtotal was lost while guarding the header"
    )


def test_being_a_header_settles_it_before_the_label_is_read(tmp_path):
    """Where the guard actually lives.

    The count is required so that a bare column heading cannot promote a
    header line -- but the digit test alone cannot carry that weight, because
    the money column beside the label supplies a digit of its own: on this page
    "Claim Count" followed by "30,000.00" reads as a count of thirty thousand.
    So the header question is asked first and answered structurally, and the
    label is never consulted on a line that is a header.
    """
    table = _table(tmp_path, count_column=True)
    for row in table.total_rows + list(table.rows):
        assert "Major Class Code/Description" not in row.text(), (
            f"a header line was kept as a row: {row.text()}"
        )
