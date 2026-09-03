"""A table split across pages by column, and why LossLift refuses to join it.

The Austin document prints one Excel sheet as two PDF pages: page 1 carries
columns A through AI, page 2 carries AJ through BT, and a row-index column --
1, 2, 3, ... -- repeats down the left edge of both, printed at the identical
vertical position on each. That repetition is what "columns to repeat at
left" does when a wide sheet is sliced for printing, and it is real, strong,
structural evidence that the two pages are one table, not two.

It is not evidence of what any *value* means. Page 2's own header is a
four-line wrapped block whose column labels arrive scrambled -- "AT Claim
Future Reserve -", "AU Claim Future Reserve -" twice over -- and its money
columns are currently mapped onto a date serial and a coincidental zero. A
correct row correspondence built on top of an unconfirmed column mapping
would produce a confident-looking, wrong number. So this rule stops at the
row correspondence: it tells a reviewer that two pages are one table and
that the second half's figures could not be safely attached to a claim. It
does not attach anything.

Every fixture here is invented -- letters and integers on a blank page, no
carrier name, no claimant, no real structure copied wholesale.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.pipeline import run_pipeline
from core.schema import DocumentStatus, Severity

LINE = 14.0
LEFT = 40.0
COL_WIDTH = 60.0


def _excel_letters(n: int):
    """A, B, ... Z, AA, AB, ... -- the first n Excel column codes."""
    out = []
    i = 1
    while len(out) < n:
        letters = ""
        k = i
        while k > 0:
            k, rem = divmod(k - 1, 26)
            letters = chr(65 + rem) + letters
        out.append(letters)
        i += 1
    return out


def _draw_table(page, letters, rows, y0=50.0):
    """One page: a letter band, then one row per (key, *cells) tuple.

    Every row's first cell is placed at the same x as every other row's, and
    the same y this same key uses on any *other* page the caller draws with
    the matching y-plan -- callers align pages by passing the same y0 and row
    spacing, exactly as one spreadsheet's row heights survive a column-wise
    page split.
    """
    x = LEFT
    for letter in letters:
        page.insert_text((x, y0), letter, fontsize=8.5)
        x += COL_WIDTH / 3
    y = y0 + LINE * 2
    for row in rows:
        x = LEFT
        for cell in row:
            if cell != "":
                page.insert_text((x, y), str(cell), fontsize=8.5)
            x += COL_WIDTH
        y += LINE
    return y


def _two_page_split(
    tmp_path,
    name,
    left_letters,
    right_letters,
    left_rows,
    right_rows,
    extra_pages=None,
):
    document = pymupdf.open()
    left = document.new_page(width=900, height=500)
    _draw_table(left, left_letters, left_rows)
    if extra_pages:
        for letters, rows in extra_pages:
            mid = document.new_page(width=900, height=500)
            _draw_table(mid, letters, rows)
    right = document.new_page(width=900, height=500)
    _draw_table(right, right_letters, right_rows)
    path = tmp_path / name
    document.save(path)
    document.close()
    return path


#: Six invented "claims": key, an identifier, a date, a status word.
LEFT_ROWS = [
    (2, "CLM-2001", "01/02/2024", "OPEN"),
    (3, "CLM-2002", "02/14/2024", "CLOSED"),
    (4, "CLM-2003", "03/09/2024", "OPEN"),
    (5, "CLM-2004", "04/21/2024", "OPEN"),
    (6, "CLM-2005", "05/30/2024", "CLOSED"),
    (7, "CLM-2006", "06/18/2024", "OPEN"),
]
#: The same six keys, money-shaped values, no identifier at all -- exactly
#: Austin's page 2 shape, an unattached row of figures.
RIGHT_ROWS = [
    (2, "1,200.00", "300.00", "1,500.00"),
    (3, "800.00", "0.00", "800.00"),
    (4, "2,400.00", "600.00", "3,000.00"),
    (5, "0.00", "0.00", "0.00"),
    (6, "950.00", "50.00", "1,000.00"),
    (7, "1,100.00", "400.00", "1,500.00"),
]

LEFT_LETTERS = _excel_letters(4)          # A B C D
RIGHT_LETTERS = _excel_letters(8)[4:8]    # E F G H -- consecutive with A-D


def _finding_of(result, rule_id):
    return [f for f in result.reconciliation.findings if f.rule_id == rule_id]


# --------------------------------------------------------------------------
# 1. The Austin structural pattern itself
# --------------------------------------------------------------------------


def test_the_austin_shape_is_detected_and_reported_not_joined(tmp_path):
    """Two consecutive-lettered pages, a matching row-index key, aligned rows.

    LossLift must say this is one split table -- and must not invent a paid
    total for CLM-2001 from a row that has no claim number on it.
    """
    path = _two_page_split(
        tmp_path, "austin-shape.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, RIGHT_ROWS
    )
    result = run_pipeline(path, use_vision=False)

    assert result.document.column_split_pages == [(1, 2)]
    r24 = _finding_of(result, "R-24")
    assert len(r24) == 1
    assert r24[0].severity is Severity.ERROR
    assert r24[0].condition == "pages-1-2"

    # The refusal to join, not just the finding: no claim gained page-2 money.
    for claim in result.document.claims:
        assert claim.paid_total is None
        assert claim.incurred_total is None
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 2. A different, still-legitimate split/companion shape
# --------------------------------------------------------------------------


def test_a_three_column_split_is_detected_across_every_adjacent_pair(tmp_path):
    """The mechanism is not tuned to exactly two pages.

    A sheet sliced into three consecutive column ranges must be recognised
    on both of its page boundaries, independently.
    """
    third_letters = _excel_letters(12)[8:12]  # I J K L -- consecutive with E-H
    third_rows = [(k, "extra") for k, *_ in LEFT_ROWS]
    document = pymupdf.open()
    p1 = document.new_page(width=900, height=500)
    _draw_table(p1, LEFT_LETTERS, LEFT_ROWS)
    p2 = document.new_page(width=900, height=500)
    _draw_table(p2, RIGHT_LETTERS, RIGHT_ROWS)
    p3 = document.new_page(width=900, height=500)
    _draw_table(p3, third_letters, third_rows)
    path = tmp_path / "three-way.pdf"
    document.save(path)
    document.close()

    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == [(1, 2), (2, 3)]
    assert len(_finding_of(result, "R-24")) == 2


# --------------------------------------------------------------------------
# 3. Same row count, different/ambiguous records -- MUST NOT join
# --------------------------------------------------------------------------


def test_matching_row_count_with_different_keys_is_not_detected(tmp_path):
    """Six rows on each side is not evidence; the same six keys would be."""
    # Shift every key by one: {2..7} on the left, {3..8} on the right.
    mismatched_right = [(row[0] + 1,) + row[1:] for row in RIGHT_ROWS]
    path = _two_page_split(
        tmp_path, "mismatched-keys.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, mismatched_right
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == []
    assert not _finding_of(result, "R-24")


# --------------------------------------------------------------------------
# 4. Reordered rows -- MUST NOT positionally misassign
# --------------------------------------------------------------------------


def test_reordered_rows_on_one_side_are_not_detected(tmp_path):
    """The same six keys, printed in a different order on the right.

    A genuine column-split page break never does this -- both halves come
    from one rendering pass over the same rows -- so if it happens, the two
    tables are not what they appear to be, and the row-position check (keyed
    by value, not by list order) catches it: each key's row still has to sit
    at the SAME y on both sides, which a reordering breaks for every row
    after the first swap.
    """
    reordered_right = [RIGHT_ROWS[1], RIGHT_ROWS[0]] + RIGHT_ROWS[2:]
    path = _two_page_split(
        tmp_path, "reordered.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, reordered_right
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == []
    assert not _finding_of(result, "R-24")


# --------------------------------------------------------------------------
# 5 & 6. Missing / extra row on one side -- MUST NOT join
# --------------------------------------------------------------------------


def test_a_row_missing_on_one_side_is_not_detected(tmp_path):
    path = _two_page_split(
        tmp_path, "missing-row.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, RIGHT_ROWS[:-1]
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == []
    assert not _finding_of(result, "R-24")


def test_an_extra_row_on_one_side_is_not_detected(tmp_path):
    extra = RIGHT_ROWS + [(8, "999.00", "0.00", "999.00")]
    path = _two_page_split(
        tmp_path, "extra-row.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, extra
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == []
    assert not _finding_of(result, "R-24")


# --------------------------------------------------------------------------
# 7. A repeated key on one side -- ambiguous, MUST NOT join
# --------------------------------------------------------------------------


def test_a_duplicate_key_on_one_side_is_not_detected(tmp_path):
    """Key 4 appears twice on the right: which physical row is "4" now?"""
    duplicated_right = RIGHT_ROWS[:3] + [RIGHT_ROWS[2]] + RIGHT_ROWS[3:]
    path = _two_page_split(
        tmp_path, "duplicate-key.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, duplicated_right
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == []
    assert not _finding_of(result, "R-24")


# --------------------------------------------------------------------------
# 8. An intervening unrelated page breaks adjacency -- MUST NOT join
# --------------------------------------------------------------------------


def test_an_intervening_unrelated_page_blocks_detection(tmp_path):
    """Page 2 sits between the two true halves; adjacency alone decides.

    Nothing about the intervening page's own content matters here -- it need
    not even look like a table -- only that it breaks the left and right
    halves from being physically next to each other.
    """
    document = pymupdf.open()
    p1 = document.new_page(width=900, height=500)
    _draw_table(p1, LEFT_LETTERS, LEFT_ROWS)
    p2 = document.new_page(width=900, height=500)
    p2.insert_text((LEFT, 60.0), "AN UNRELATED PAGE OF PROSE", fontsize=10)
    p3 = document.new_page(width=900, height=500)
    _draw_table(p3, RIGHT_LETTERS, RIGHT_ROWS)
    path = tmp_path / "intervening.pdf"
    document.save(path)
    document.close()

    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == []
    assert not _finding_of(result, "R-24")


# --------------------------------------------------------------------------
# 9. Excel date serials under misleading mappings -- unaffected, still safe
# --------------------------------------------------------------------------


def test_excel_date_serials_are_still_never_read_as_money(tmp_path):
    """The Austin shape, with the actual bare-integer date serials in it.

    R-24 reports the structure; it must not make R-23 or parse_money treat
    "44806" as an amount, before or after this rule exists.
    """
    right_with_serials = [
        (2, "44806", "393", "9.45"),
        (3, "44694", "505", "9.45"),
        (4, "36571", "8628", "19502.18"),
        (5, "0", "0", "0"),
        (6, "0", "0", "0"),
        (7, "0", "0", "0"),
    ]
    path = _two_page_split(
        tmp_path, "date-serials.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, right_with_serials
    )
    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == [(1, 2)]
    assert _finding_of(result, "R-24")
    assert not _finding_of(result, "R-23")
    for claim in result.document.claims:
        assert claim.paid_total is None


# --------------------------------------------------------------------------
# 10. Existing money-format handling is untouched
# --------------------------------------------------------------------------


def test_us_and_eu_money_elsewhere_in_the_document_is_unaffected(tmp_path):
    """The new rule reads no money at all; existing parsing is a control."""
    from core.normalize import parse_money

    assert parse_money("1,234.56", "us").value == Decimal("1234.56")
    assert parse_money("1.234,56", "eu").value == Decimal("1234.56")
    # And the split-table fixture itself does not disturb either.
    path = _two_page_split(
        tmp_path, "money-control.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, RIGHT_ROWS
    )
    result = run_pipeline(path, use_vision=False)
    assert result.locale.locale in ("us", None) or result.locale.locale == "us"


# --------------------------------------------------------------------------
# 11. Provenance of the finding itself
# --------------------------------------------------------------------------


def test_the_finding_names_the_pages_and_nothing_it_cannot_defend(tmp_path):
    path = _two_page_split(
        tmp_path, "provenance.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, RIGHT_ROWS
    )
    result = run_pipeline(path, use_vision=False)
    finding = _finding_of(result, "R-24")[0]
    assert finding.scope.value == "document"
    assert finding.subject == "document"
    assert finding.category.value == "extraction"
    assert "1" in finding.message and "2" in finding.message
    assert finding.claim_number is None
    assert finding.field is None


# --------------------------------------------------------------------------
# 12. Reconciliation behaviour when reconstruction is (rightly) refused
# --------------------------------------------------------------------------


def test_r24_blocks_clean_and_survives_a_rerun(tmp_path):
    path = _two_page_split(
        tmp_path, "blocks-clean.pdf", LEFT_LETTERS, RIGHT_LETTERS, LEFT_ROWS, RIGHT_ROWS
    )
    result = run_pipeline(path, use_vision=False)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW

    from core.pipeline import rerun_reconciliation

    rerun = rerun_reconciliation(result.document)
    assert any(f.rule_id == "R-24" for f in rerun.findings), (
        "the finding is derived from the document's own column_split_pages, "
        "so a plain re-run must reproduce it without re-reading the PDF"
    )


# --------------------------------------------------------------------------
# No false positives on the ordinary, non-split corpus shape
# --------------------------------------------------------------------------


def test_an_ordinary_single_table_document_is_never_flagged(tmp_path):
    """A ordinary one-page loss run has no letter band at all -- silence."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    y = 50.0
    for line in ("MERIDIAN MUTUAL ASSURANCE", "LOSS RUN REPORT", "Valuation Date: 12/31/2024"):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip((0, 90, 170, 250), ("Claim No", "Date", "Status", "Total")):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in (("CN-1", "01/01/2024", "OPEN", "500.00"),):
        for offset, cell in zip((0, 90, 170, 250), row):
            page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    path = tmp_path / "ordinary.pdf"
    document.save(path)
    document.close()

    result = run_pipeline(path, use_vision=False)
    assert result.document.column_split_pages == []
    assert not _finding_of(result, "R-24")
