"""The same loss run, printed at two sizes, must read the same.

A wide carrier sheet is routinely fitted onto A4 or Letter before it is sent.
Everything on the page comes down with it: two-point glyphs, gutters under a
point, lines two points apart. Nothing about the table has changed — a reader
sees the same columns, just smaller — so the engine must see them too.

It did not. Several thresholds were stated in absolute points and so meant
something different on every page:

* the narrowest gap that counts as a column boundary, four points, which is
  four characters wide when a character is one point, so every column on the
  page merged into one;
* how far outside its column a word may sit and still belong to it, likewise;
* and beneath those, the tolerance the word extractor itself uses to decide
  which characters form a word, which at three points joined one column to the
  next and one line to the line below. A company and the programme name printed
  beside it came back as "ComEpuarnoyp eSEan".

Scale invariance is the property being tested here, so the fixtures render one
table twice — at ordinary size, and at a quarter of it — and assert the two
readings agree. The rest ask what the tightening could cost a page that never
had the problem.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pymupdf
import pytest

from core.extract_digital import extract_pdf
from core.pipeline import run_pipeline

FONT = "helv"

#: (x, label) at ordinary size. Every fixture scales these by one factor.
#:
#: The columns are set tight, as a wide sheet's are: about eleven points of
#: blank between one value and the next. That is comfortably a gutter at
#: ordinary size and comfortably a gutter at a quarter of it -- but under four
#: points once shrunk, which is what the absolute floor used to demand.
COLUMNS = (
    (30, "Claim"),
    (77, "Loss Date"),
    (124, "Status"),
    (159, "Claimant"),
    (214, "Paid"),
    (256, "Reserve"),
    (298, "Incurred"),
)

CLAIMS = (
    ("CS-40118", "3/14/2024", "Closed", "Rosa Venn", "1,200.00", "0.00", "1,200.00"),
    ("CS-40119", "5/02/2024", "Open", "Dale Hooker", "400.00", "900.00", "1,300.00"),
    ("CS-40120", "9/18/2024", "Open", "Priya Nair", "0.00", "2,000.00", "2,000.00"),
)


def _render(path, scale: float, *, size: float = 8.0, rows=None,
            footer: bool = False) -> None:
    """The same table, shrunk by ``scale`` onto a page shrunk to match."""
    rows = CLAIMS if rows is None else rows
    # Columns are set for eight-point text; larger type needs them further
    # apart or the values collide, which is a fact about the fixture and not
    # about the engine.
    spread = size / 8.0
    document = pymupdf.open()
    page = document.new_page(width=380 * spread * scale, height=220 * scale)
    fontsize = size * scale

    def write(text: str, x: float, y: float) -> None:
        page.insert_text(
            (x * spread * scale, y * scale), text, fontname=FONT, fontsize=fontsize
        )

    write("Cornerstone Specialty Insurance", 30, 26)
    write("Valuation Date: 12/31/2024", 220, 26)
    for x, label in COLUMNS:
        write(label, x, 55)
    y = 80
    for row in rows:
        for (x, _), value in zip(COLUMNS, row):
            write(value, x, y)
        y += 20
    if footer:
        # A strapline far below the table, at its own positions.
        for x, word in ((30, "Report"), (70, "LossRunSummary"), (200, "Page"), (230, "1")):
            write(word, x, y + 70)
    document.save(str(path))
    document.close()


def _claims(path):
    return {c.claim_number: c for c in run_pipeline(path, use_vision=False).document.claims}


@pytest.fixture(scope="module")
def full_size(tmp_path_factory):
    path = tmp_path_factory.mktemp("scale") / "full.pdf"
    _render(path, 1.0)
    return path


@pytest.fixture(scope="module")
def quarter_size(tmp_path_factory):
    """A quarter scale: two-point glyphs, gutters under a point."""
    path = tmp_path_factory.mktemp("scale") / "quarter.pdf"
    _render(path, 0.25)
    return path


# --------------------------------------------------------------------------
# The property: reading a table does not depend on how big it is printed
# --------------------------------------------------------------------------


def test_a_shrunken_page_finds_the_same_columns(full_size, quarter_size):
    assert extract_pdf(quarter_size).tables[0].headers == [
        label for _, label in COLUMNS
    ]
    assert (
        extract_pdf(quarter_size).tables[0].headers
        == extract_pdf(full_size).tables[0].headers
    )


def test_a_shrunken_page_yields_the_same_claims(full_size, quarter_size):
    small, large = _claims(quarter_size), _claims(full_size)
    assert set(small) == set(large) == {row[0] for row in CLAIMS}
    for number, claim in large.items():
        other = small[number]
        assert (claim.date_of_loss, claim.claim_status, claim.claimant_name) == (
            other.date_of_loss, other.claim_status, other.claimant_name
        )
        assert (claim.paid_total, claim.reserve_total, claim.incurred_total) == (
            other.paid_total, other.reserve_total, other.incurred_total
        )


def test_the_amounts_survive_the_reduction(quarter_size):
    """Not merely equal to each other — equal to what the carrier printed."""
    claims = _claims(quarter_size)
    assert claims["CS-40119"].paid_total == Decimal("400.00")
    assert claims["CS-40119"].reserve_total == Decimal("900.00")
    assert claims["CS-40119"].incurred_total == Decimal("1300.00")
    assert claims["CS-40118"].date_of_loss == date(2024, 3, 14)


def test_a_shrunken_page_does_not_interleave_its_columns(quarter_size):
    """The failure this fixture exists for: letters of one column inside another."""
    row = extract_pdf(quarter_size).tables[0].rows[0]
    assert row.cells[0] == "CS-40118"
    assert row.cells[3] == "Rosa Venn"
    assert all(" " not in cell or cell.count(" ") <= 1 for cell in row.cells[:3])


# --------------------------------------------------------------------------
# What the tightening could cost a page that never had the problem
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def large_print(tmp_path_factory):
    """Set larger than ordinary. Tolerances must not loosen to match."""
    path = tmp_path_factory.mktemp("scale") / "large.pdf"
    _render(path, 1.0, size=14.0)
    return path


def test_larger_than_ordinary_text_reads_normally(large_print):
    claims = _claims(large_print)
    assert set(claims) == {row[0] for row in CLAIMS}
    assert claims["CS-40120"].reserve_total == Decimal("2000.00")


@pytest.fixture(scope="module")
def mixed_sizes(tmp_path_factory):
    """A banner heading over a small table, as letterheads are set."""
    path = tmp_path_factory.mktemp("scale") / "mixed.pdf"
    document = pymupdf.open()
    page = document.new_page(width=380, height=220)
    page.insert_text((30, 34), "GREAT BASIN INDEMNITY", fontname=FONT, fontsize=22)
    page.insert_text((220, 34), "Valuation Date: 12/31/2024", fontname=FONT, fontsize=8)
    for x, label in COLUMNS:
        page.insert_text((x, 66), label, fontname=FONT, fontsize=7)
    y = 92
    for row in CLAIMS:
        for (x, _), value in zip(COLUMNS, row):
            page.insert_text((x, y), value, fontname=FONT, fontsize=7)
        y += 18
    document.save(str(path))
    document.close()
    return path


def test_a_banner_heading_does_not_set_the_scale_for_the_table(mixed_sizes):
    """The table is most of the page's text, so the table's size wins."""
    claims = _claims(mixed_sizes)
    assert set(claims) == {row[0] for row in CLAIMS}
    assert claims["CS-40118"].paid_total == Decimal("1200.00")


@pytest.fixture(scope="module")
def short_table_with_footer(tmp_path_factory):
    """Two rows and a strapline far below them.

    The table's pitch was taken as the median gap between its lines. On a table
    this short the footer's own distance is half that sample, so it dragged the
    median past the limit it then had to exceed: the footer stayed, became a
    row, and its words closed the gutters between the real columns.
    """
    path = tmp_path_factory.mktemp("scale") / "footer.pdf"
    _render(path, 1.0, rows=CLAIMS[:2], footer=True)
    return path


def test_a_footer_below_a_short_table_is_still_recognised(short_table_with_footer):
    numbers = {c.claim_number for c in
               run_pipeline(short_table_with_footer, use_vision=False).document.claims}
    assert numbers == {row[0] for row in CLAIMS[:2]}
    rows = extract_pdf(short_table_with_footer).tables[0].rows
    assert all("LossRunSummary" not in row.text() for row in rows)
