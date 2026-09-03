"""Multi-line wrapped headers whose leaf line carries a stray digit.

A carrier that wraps a column label across several printed lines sometimes
lets a short, unrelated number sit on the leaf line: a footnote marker, a
sub-level ordinal ("Level 01"), or a row-index column the page prints on
every line of the table, header included. ``header_block`` used to treat any
digit at all as proof a line was data, not a label, so that leaf line was
dropped -- and with it, the only words that told two identically-worded
columns apart.

Reproduced structurally from two real loss runs (spec section 9): a
workers'-comp carrier whose leaf header line prints a repeating row-index
that lands on the header's own row, and whose "O/R" (Outstanding/Reserve)
abbreviation is rendered with a zero glyph on some pages and a letter O on
others. Neither is carrier-specific: the fix is a shape test (does this
digit look like a date, an amount, or an identifier?) applied wherever a
wrapped header appears.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.extract_digital import Line, Word, _is_label_line, find_header_line, header_block
from core.pipeline import run_pipeline


def _word(text, x0, x1, top=100.0):
    return Word(text=text, x0=x0, x1=x1, top=top, bottom=top + 6.0)


def _line(index, top, *cells):
    """``cells`` is (text, x0, x1) triples already spaced on the page."""
    return Line(words=tuple(_word(text, x0, x1, top) for text, x0, x1 in cells), index=index)


CHAR_WIDTH = 2.5
PITCH = 7.0

#: A self-sufficient three-cell header: each cell resolves to a field on its
#: own, so it always wins as the scored header line regardless of what sits
#: above or below it.
HEADER_CELLS = (("Claim Number", 30, 80), ("Loss Date", 200, 240), ("Total Incurred", 300, 360))


def _block_indices(lines):
    found = find_header_line(lines, CHAR_WIDTH)
    assert found is not None, "no line scored as a header at all"
    header_index, _ = found
    block = header_block(lines, header_index, CHAR_WIDTH)
    return header_index, [line.index for line in block]


# --- New behaviour: a bare short digit does not disqualify a label line ----


def test_a_leading_row_index_does_not_exclude_the_leaf_label_line():
    """The row-index column prints "1" on the header's own leaf line.

    Every other cell on the line is genuine, self-sufficient vocabulary, so
    the line belongs in the block.
    """
    lines = [
        _line(0, 0.0, *HEADER_CELLS),
        _line(
            1,
            PITCH,
            ("1", 10, 20),
            ("Paid Indemnity", 30, 80),
            ("Paid Medical", 200, 250),
            ("Paid Expense", 300, 350),
        ),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert block == [0, 1], f"leaf line with only a stray '1' was dropped: {block}"


def test_a_sub_level_ordinal_does_not_exclude_its_line():
    """"Structure Level 01" -- a two-digit ordinal inside a real label."""
    lines = [
        _line(0, 0.0, *HEADER_CELLS),
        _line(
            1,
            PITCH,
            ("Paid Indemnity", 30, 80),
            ("01", 90, 100),
            ("Paid Medical", 200, 250),
            ("Paid Expense", 300, 350),
        ),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert block == [0, 1]


def test_a_misrendered_letter_o_does_not_exclude_its_line():
    """"O/R" (Outstanding/Reserve) printed with a zero glyph for the O."""
    lines = [
        _line(0, 0.0, *HEADER_CELLS),
        _line(
            1,
            PITCH,
            ("Paid Indemnity", 30, 80),
            ("0/R", 90, 100),
            ("Paid Medical", 200, 250),
            ("Paid Expense", 300, 350),
        ),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert block == [0, 1]


def test_the_walk_keeps_going_across_several_qualifying_lines():
    lines = [
        _line(0, 0.0, *HEADER_CELLS),
        _line(
            1,
            PITCH,
            ("1", 10, 20),
            ("Paid Indemnity", 30, 80),
            ("Paid Medical", 200, 250),
            ("Paid Expense", 300, 350),
        ),
        _line(
            2,
            2 * PITCH,
            ("2", 10, 20),
            ("Reserve Indemnity", 30, 90),
            ("Reserve Medical", 200, 260),
            ("Reserve Expense", 300, 360),
        ),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert block == [0, 1, 2]


# --- Preserved behaviour: a genuine value still ends the walk -------------


def test_header_block_still_stops_at_a_printed_date():
    """"Valuation Date: 04/18/2022" must never join the header block."""
    lines = [
        _line(0, 0.0, ("Valuation", 30, 65), ("Date:", 70, 100), ("04/18/2022", 105, 160)),
        _line(1, PITCH, *HEADER_CELLS),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 1
    assert 0 not in block, f"a printed date was pulled into the header block: {block}"


def test_header_block_still_stops_at_a_printed_amount():
    """A subtotal sitting directly under the header must not join it."""
    lines = [
        _line(0, 0.0, *HEADER_CELLS),
        _line(1, PITCH, ("Section", 30, 65), ("$25,297.00", 200, 250)),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert 1 not in block, f"a printed amount was pulled into the header block: {block}"


def test_header_block_still_stops_at_a_claim_identifier():
    """A real claim number is data-shaped even without a $ sign or a slash."""
    lines = [
        _line(0, 0.0, *HEADER_CELLS),
        _line(1, PITCH, ("WC550C44573", 30, 90), ("PEREZ.ROBERT", 200, 260)),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert 1 not in block, f"a claim number was pulled into the header block: {block}"


def test_a_bare_digit_line_with_no_vocabulary_stays_excluded():
    """Digits alone are not enough -- the rest of the line must still read
    as loss-run labels, or a page number sitting at the right pitch would be
    swept into the block."""
    lines = [
        _line(0, 0.0, *HEADER_CELLS),
        _line(1, PITCH, ("Page", 30, 55), ("7", 60, 68), ("of", 72, 85), ("10", 88, 100)),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert 1 not in block, f"a page-number line with no vocabulary was admitted: {block}"


def test_a_blank_cell_between_labels_does_not_crash_the_walk():
    populated = [w for w in _line(0, 0.0, *HEADER_CELLS, ("", 500, 500)).words if w.text]
    lines = [
        Line(words=tuple(populated), index=0),
        _line(1, PITCH, ("1", 10, 20), ("Paid Indemnity", 30, 80), ("Paid Medical", 200, 250), ("Paid Expense", 300, 350)),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert block == [0, 1]


def test_single_line_headers_are_unaffected():
    """The common case: one header line, real data right below it."""
    lines = [
        _line(0, 0.0, *HEADER_CELLS),
        _line(
            1,
            PITCH,
            ("WC1234", 30, 70),
            ("01/02/2023", 200, 250),
            ("$4,500.00", 300, 350),
        ),
    ]
    header_index, block = _block_indices(lines)
    assert header_index == 0
    assert block == [0]


# --- End to end: the disambiguation this exists for ------------------------


FONT = "helv"
SIZE = 7.0


def _write(page, text, x, y):
    page.insert_text((x, y), text, fontname=FONT, fontsize=SIZE)


def _build_wrapped_money_header(path) -> None:
    """A two-line wrapped header whose leaf line is the only place the
    Indemnity/Medical/Expense sub-columns are actually named -- with a bare
    row-index digit sitting in front of them, as a repeating key column
    would print on every line including the header's own.

    "Claim" heads two different columns here (its own number, and the
    claimant's name), the way Austin's real header repeats a bare parent
    word over several children -- which is also what keeps this block from
    reading as a multi-line record: no single line names a claim number on
    its own, so nothing here proposes one claim per several lines.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=750, height=400)
    _write(page, "Acme Mutual Insurance Company", 30, 20)

    _write(page, "Claim", 30, 50)
    _write(page, "Claim", 150, 50)
    _write(page, "Loss Date", 270, 50)
    _write(page, "Total Incurred", 630, 50)

    _write(page, "1", 10, 61)  # the repeating row-index, on the header's own row
    _write(page, "Number", 30, 61)
    _write(page, "Claimant Name", 150, 61)
    _write(page, "Paid Indemnity", 390, 61)
    _write(page, "Paid Medical", 470, 61)
    _write(page, "Paid Expense", 550, 61)

    rows = [
        ("WC0001", "SMITH.JOHN", "01/05/2023", "1000.00", "250.00", "50.00", "1300.00"),
        ("WC0002", "DOE.JANE", "02/11/2023", "0.00", "0.00", "125.00", "125.00"),
    ]
    y = 90
    xs = (30, 150, 270, 390, 470, 550, 630)
    for values in rows:
        for x, value in zip(xs, values):
            _write(page, value, x, y)
        y += 16

    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def wrapped_money_result(tmp_path_factory):
    path = tmp_path_factory.mktemp("wrapped_money") / "wrapped_money.pdf"
    _build_wrapped_money_header(path)
    return run_pipeline(path, use_vision=False)


def test_indemnity_medical_expense_land_in_separate_fields(wrapped_money_result):
    """Before the fix, the leaf line ("Paid Indemnity"/"Paid Medical"/"Paid
    Expense") was dropped for carrying the stray row-index digit."""
    claims = {c.claim_number: c for c in wrapped_money_result.document.claims}
    assert set(claims) == {"WC0001", "WC0002"}
    assert claims["WC0001"].paid_indemnity == Decimal("1000.00")
    assert claims["WC0001"].paid_medical == Decimal("250.00")
    assert claims["WC0001"].paid_expense == Decimal("50.00")
    assert claims["WC0002"].paid_expense == Decimal("125.00")


def test_the_row_index_digit_does_not_become_a_claim_field(wrapped_money_result):
    """The stray "1" on the header's own row must not surface as data on
    any real claim -- it is header noise, not a column of its own."""
    for claim in wrapped_money_result.document.claims:
        assert claim.claim_number in {"WC0001", "WC0002"}


# --- A known, deliberate limitation: a 3+-digit word inside a header -----
#
# "Coverage Year 2024" reads, to a person, unmistakably as a header line.
# _looks_like_value flags "2024" as value-shaped (three digits together is
# its bar for "this is what a claim's money or count looks like"), and that
# is a blanket disqualification for the whole line -- it applies before the
# header_score>=3 relaxation that lets a line with a bare short digit
# through, so no amount of surrounding vocabulary saves a line that also
# carries a 3+-digit token.
#
# This is a real gap, not a hypothetical: it was checked directly against
# it before writing this test. It is left as-is rather than loosened,
# because no document in the corpus this codebase is validated against
# (19 real loss runs, spanning every carrier format currently supported)
# contains a year-bearing or other 3+-digit header word, so there is no
# evidence a fix earns its risk. The failure direction matters more than
# the gap itself: excluding a genuine header line under-counts the header
# block, and every path downstream of that (record-layout detection,
# ordinary column mapping) already degrades safely from an under-tall
# block -- to fewer recognised columns, or a clean fall-back to the
# per-line path -- never to a column mis-mapped or a value misattributed.
# That is the same fail-closed direction the rest of this module chooses
# throughout, so this is documented as an accepted limitation, not fixed
# blind. Loosening it is exactly the kind of unproven, broad heuristic
# change spec section 2 asks Claude Code to justify with real documents
# first.
def test_year_bearing_header_word_is_a_known_unfixed_gap():
    line = Line(
        words=(
            _word("Coverage", 30, 90, 100.0),
            _word("Year", 95, 130, 100.0),
            _word("2024", 135, 170, 100.0),
        ),
        index=5,
    )
    assert _is_label_line(line, CHAR_WIDTH) is False, (
        "if this ever starts passing, _looks_like_value's digit-count bar "
        "changed -- re-check it against the real corpus before relying on "
        "the new behavior, don't just update this assertion"
    )


def test_a_genuine_data_row_is_still_correctly_excluded():
    """The same conservative rule that catches "Coverage Year 2024" is not
    accidentally letting real claim rows through as header text -- the
    thing it exists to prevent."""
    data_row = _line(
        13, 200.0,
        ("WC550C47269", 30, 85), ("CHAPMAN.GARY", 95, 150), ("10/08/2021", 160, 210),
    )
    assert _is_label_line(data_row, CHAR_WIDTH) is False
