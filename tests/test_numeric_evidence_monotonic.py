"""Preservation must be monotonic, and establishment must never be a veto.

The previous unit answered *when* a cell's classification is taken and what
may override it. This one is about the shape of the rule that does the
classifying, which had acquired a property no evidence rule may have: adding
more unreadable figures to a page made *fewer* of them survive.

That came from asking a column to out-vote itself. ``established_money_columns``
counted a column's readable cells against its unreadable ones and, where the
unreadable side won, discarded every refused cell in it. So one merged cell on
a page of claims was kept and two merged cells were both dropped -- the second
failure retroactively erasing the first. A rule meant to preserve evidence
turned the quantity of evidence into the reason for losing it.

The correction is to stop treating "not established" as "established as not
money". A column's other rows are good evidence about *certainty*; they are not
grounds for silently discarding a digit-bearing cell under a heading that says
money. Discarding now requires positive evidence of something else: a narrative
run the cell belongs to, a label that names it, or furniture established for
that occurrence. Absence of corroboration leaves the cell unresolved, which is
reported rather than resolved.

Seven failures, all of that shape.

**Monotonicity.** One merged cell survived; two did not.

**A monetary-only table.** A table whose header block says its columns are
money, holding one merged cell and nothing else, kept nothing -- the header's
own statement overruled by the fact that no peer value happened to parse.

**A claim row missing its identifier.** A date, a status and a description to
the left of the amount are the strongest available evidence that the row is a
claim. They were read as prose running in from the left, so the amount was
folded away as narrative.

**Sparse continuation pages.** Furniture is decided on normalised row text
pooled across pages. An occurrence nothing protected put its text into that
pool, the pool crossed the threshold, and the text then deleted an occurrence
that *was* protected on another page.

**A label speaking for the whole row.** ``Software version 1.20`` exempted the
unmarked ``500.00`` beside it, because only cells carrying a currency mark
survived a labelled row. Requiring a currency symbol to keep an unrelated
amount is the erasure this thread exists to prevent, arriving as an exemption.

**Narrative runs.** A continuation has to be traced to a description, across
whatever money columns its own words are sitting in -- not inferred from the
first populated cell to the left, which on a claim row is a status.

**Structural scope versus readability.** A readable policy subtotal outranked
an unreadable GRAND TOTAL, so the document total was the subtotal's and R-26
never named the row nobody could read. Which row is the document's total is a
question about what the row says it is; whether it parsed is a different
question with a different answer.

Fixtures are generated PDFs driven through ``run_pipeline``: no corpus, no
platform-specific paths.
"""

from __future__ import annotations

import pymupdf
import pytest

from core.pipeline import run_pipeline
from core.schema import DocumentStatus

LEFT = 40.0
LINE = 14.0
COLUMNS = (0.0, 90.0, 175.0, 250.0, 350.0, 450.0)
HEADERS = ("Claim No", "Date of Loss", "Status", "Loss Description", "Paid Total",
           "Total Incurred")
CLAIMS = (
    ("CN-1001", "03/12/2024", "OPEN", "Ladder fall", "1,200.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "Rear-end collision", "2,450.00", "2,450.00"),
    ("CN-1003", "09/21/2024", "OPEN", "Water ingress", "775.50", "2,000.00"),
)

MERGED = "4 / 30,000.00"
MERGED_TWO = "5 / 40,000.00"


def _write(path, rows, *, pages=1, page_rows=None, footer_rows=(), headers=HEADERS):
    """A loss run whose pages may differ, so a sparse page can be built."""
    document = pymupdf.open()
    for page_number in range(1, pages + 1):
        page = document.new_page(width=612, height=792)
        y = 50.0
        for line in (
            "MERIDIAN MUTUAL ASSURANCE",
            "LOSS RUN REPORT",
            "Named Insured: Northwind Fabrication Ltd",
            "Policy Number: GL-4417-2024",
            "Policy Period: 01/01/2024 to 12/31/2024",
            "Valuation Date: 12/31/2024",
            "Currency: USD",
        ):
            page.insert_text((LEFT, y), line, fontsize=9)
            y += LINE
        y += LINE
        for offset, label in zip(COLUMNS, headers):
            page.insert_text((LEFT + offset, y), label, fontsize=8.5)
        y += LINE
        body = rows if page_rows is None else page_rows(page_number)
        for row in body:
            for offset, cell in zip(COLUMNS, row):
                if cell:
                    page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
            y += LINE
        for footer in footer_rows:
            y += LINE
            for offset, cell in zip(COLUMNS, footer):
                if cell:
                    page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
    document.save(path)
    document.close()
    return path


def _texts(document):
    """Every printed cell the document is still carrying as evidence."""
    return {
        text
        for row in document.unplaced_rows
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }


def _r23(result):
    return [f for f in result.reconciliation.findings if f.rule_id == "R-23"]


def _descriptions(document):
    return " ".join(claim.loss_description or "" for claim in document.claims)


# --------------------------------------------------------------------------
# 1. More parse failures may never mean less evidence
# --------------------------------------------------------------------------


def test_a_second_unreadable_value_does_not_erase_the_first(tmp_path):
    """One merged cell survived; two did not.

    ``established_money_columns`` counted the paid column's readable cells
    against its unreadable ones. Three claims and one merged cell left the
    readable side ahead, so the merged cell was kept. Add a second merged cell
    and the count ties or loses, the column is refused, and *both* are
    discarded -- the second failure reaching back to erase the first.

    No evidence rule may behave that way. Finding more of something the reader
    could not read is a stronger reason to say so, never a weaker one.
    """
    one = _write(tmp_path / "one.pdf", CLAIMS[:1] + (("", "", "", "", MERGED, ""),))
    first = run_pipeline(one, use_vision=False)
    assert MERGED in _texts(first.document), "the baseline case regressed"

    two = _write(
        tmp_path / "two.pdf",
        CLAIMS[:1] + (("", "", "", "", MERGED, ""), ("", "", "", "", MERGED_TWO, "")),
    )
    result = run_pipeline(two, use_vision=False)
    carried = _texts(result.document)
    assert MERGED in carried and MERGED_TWO in carried, (
        f"adding a second unreadable value erased evidence: {carried}"
    )
    assert MERGED not in _descriptions(result.document), (
        "an unreadable amount was folded into a claim description"
    )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_evidence_never_shrinks_as_unreadable_values_are_added(tmp_path):
    """The same property stated as a property: preservation is monotonic."""
    extra = [("", "", "", "", MERGED, ""), ("", "", "", "", MERGED_TWO, ""),
             ("", "", "", "", "6 / 50,000.00", "")]
    kept = []
    for count in range(1, len(extra) + 1):
        path = _write(tmp_path / f"grow{count}.pdf", CLAIMS[:1] + tuple(extra[:count]))
        document = run_pipeline(path, use_vision=False).document
        kept.append(len(_texts(document) & {cell[4] for cell in extra[:count]}))
    assert kept == [1, 2, 3], (
        f"evidence preserved as unreadable rows were added: {kept}"
    )


# --------------------------------------------------------------------------
# 2. A header block that says money is not overruled by nothing parsing
# --------------------------------------------------------------------------


def test_a_monetary_only_table_keeps_its_one_unreadable_value(tmp_path):
    """Every column mapped money, one row, and that row unreadable.

    There is no peer value to corroborate it because there is no peer row.
    The table's own header block is the evidence, and it says these columns
    carry money; a cell under them that yields no value is a figure the reader
    could not read. Discarding it lost the only thing on the page.
    """
    path = _write(
        tmp_path / "monetary-only.pdf",
        (("", "", "", "", MERGED, ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert MERGED in _texts(result.document), (
        f"a monetary-only table erased its only value: "
        f"{[(r.amounts, r.ambiguous_values) for r in result.document.unplaced_rows]}"
    )
    assert _r23(result), "nothing was reported"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 3. A claim row missing its identifier is not prose
# --------------------------------------------------------------------------


def test_a_claim_row_without_an_identifier_keeps_its_amount(tmp_path):
    """A date, a status and a description, and then an unreadable amount.

    Everything to the left of the amount says "this is a claim". The
    identifier is missing, so no claim can be built and no claim may be given
    the money -- but the money is emphatically not narrative. It was treated
    as narrative because the test for a continuation asked only whether
    *something* non-money sat to the left, and on a claim row something always
    does.
    """
    path = _write(
        tmp_path / "no-identifier.pdf",
        CLAIMS + (("", "07/07/2024", "OPEN", "Forklift strike", MERGED, ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert MERGED in _texts(result.document), (
        f"claim-row evidence was read as prose: "
        f"{[(r.amounts, r.ambiguous_values) for r in result.document.unplaced_rows]}"
    )
    assert MERGED not in _descriptions(result.document), (
        "the amount was folded into a claim description"
    )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 4. Furniture is established per occurrence
# --------------------------------------------------------------------------


def test_a_sparse_repeat_cannot_delete_a_protected_occurrence(tmp_path):
    """Page 1 carries a claim and the merged cell; pages 2 and 3 carry only it.

    Furniture pools normalised row text across pages and deletes what repeats.
    On pages 2 and 3 nothing corroborated the merged cell, so those
    occurrences were not protected, their text entered the pool, the pool
    reached the threshold -- and the *protected* occurrence on page 1 was then
    deleted by its own text.

    One occurrence being indistinguishable from furniture cannot unmake
    another occurrence that was independently established as evidence.
    """
    def rows(page_number):
        if page_number == 1:
            return (CLAIMS[0], ("", "", "", "", MERGED, ""))
        return (("", "", "", "", MERGED, ""),)

    path = _write(tmp_path / "sparse.pdf", (), pages=3, page_rows=rows)
    result = run_pipeline(path, use_vision=False)
    document = result.document
    rows_kept = [
        row for row in document.unplaced_rows
        if MERGED in list(row.amounts.values()) + list(row.ambiguous_values.values())
    ]
    assert {row.page for row in rows_kept} == {1, 2, 3}, (
        f"occurrences were deleted as furniture: "
        f"{[(r.page, r.row, r.ambiguous_values) for r in document.unplaced_rows]}"
    )
    assert len({(row.page, row.row) for row in rows_kept}) == 3, "provenance collapsed"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 5. A label exempts the value it names, and no other
# --------------------------------------------------------------------------


def test_a_label_does_not_exempt_an_unmarked_amount_beside_it(tmp_path):
    """"Software version" beside 1.20 says nothing about the 500.00.

    The row was handled by keeping only cells written unmistakably as money --
    a currency mark or a credit marker. An ordinary ``500.00`` carries
    neither, so the label erased it. Requiring a dollar sign before an amount
    may survive an unrelated label is the same erasure this thread exists to
    stop, wearing an exemption's clothes.
    """
    path = _write(
        tmp_path / "label-and-amount.pdf",
        CLAIMS + (("", "", "", "Software version", "1.20", "500.00"),),
    )
    result = run_pipeline(path, use_vision=False)
    carried = _texts(result.document)
    assert "500.00" in carried, (
        f"an unrelated amount was erased by a label: {carried}"
    )
    assert "1.20" not in carried, (
        f"the labelled version number was reported as evidence: {carried}"
    )


def test_a_label_exempts_its_year_but_not_a_refused_cell(tmp_path):
    """"Policy year" beside 2024.00, and beside that a refused "$1 $234".

    The year is explained by its label and is exempt. The cell carrying two
    currency marks is not explained by anything: it is a figure the reader
    refused, on the same row, and the label has nothing to say about it.
    """
    path = _write(
        tmp_path / "year-and-refused.pdf",
        CLAIMS + (("", "", "", "Policy year", "2024.00", "$1 $234"),),
    )
    result = run_pipeline(path, use_vision=False)
    carried = _texts(result.document)
    assert "2024.00" not in carried, (
        f"the labelled year was reported as evidence: {carried}"
    )
    assert "$1 $234" in carried, (
        f"a refused monetary cell was erased by an unrelated label: {carried}"
    )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 6. Narrative runs stay narrative, and are traced to a description
# --------------------------------------------------------------------------


def test_a_wrapped_description_reaching_a_money_column_is_not_evidence(tmp_path):
    """The claim above wraps, and its tail lands under the paid heading."""
    path = _write(
        tmp_path / "wrap.pdf",
        CLAIMS + (("", "", "", "sustained further damage", "within last 30 days", ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert "within last 30 days" not in _texts(result.document), (
        f"a wrapped description became monetary evidence: {_texts(result.document)}"
    )
    assert not _r23(result), [f.message for f in _r23(result)]


def test_a_narrative_is_traced_across_the_money_column_it_crosses(tmp_path):
    """"reported within | 30 to | 60 days" over Description, Paid, Incurred.

    The tail in the incurred column has a money column immediately to its
    left, so a rule that stops at the first populated cell calls it a row of
    figures. The run has to be followed: "30 to" is itself narrative, and
    behind it "reported within" sits in the description column, which is where
    the sentence started.
    """
    path = _write(
        tmp_path / "narrative.pdf",
        CLAIMS + (("", "", "", "reported within", "30 to", "60 days"),),
    )
    result = run_pipeline(path, use_vision=False)
    carried = _texts(result.document)
    assert "30 to" not in carried and "60 days" not in carried, (
        f"a narrative crossing a money column became evidence: {carried}"
    )
    assert not _r23(result), [f.message for f in _r23(result)]


def test_a_labelled_amount_is_still_not_a_narrative(tmp_path):
    """The control on the other side: "Paid 30,000.00" is evidence.

    It has a word in it, like a narrative, and it stands in a money column
    with nothing to its left, unlike one.
    """
    path = _write(
        tmp_path / "paid.pdf", CLAIMS + (("", "", "", "", "Paid 30,000.00", ""),)
    )
    result = run_pipeline(path, use_vision=False)
    assert "Paid 30,000.00" in _texts(result.document), (
        f"a labelled amount was read as narrative: {_texts(result.document)}"
    )
    assert _r23(result), "nothing was reported"


# --------------------------------------------------------------------------
# 7. Structural scope outranks readability
# --------------------------------------------------------------------------


def test_an_unreadable_grand_total_outranks_a_readable_subtotal(tmp_path):
    """A policy subtotal that ties, and under it a GRAND TOTAL nobody can read.

    The subtotal is readable and correct, so it won the ranking and became the
    document's printed total -- and R-04 then checked the claims against a
    figure that covers one policy. The grand total, which is the row that
    covers the document, was dropped whole because it parsed nothing.

    Which row is the document's total is settled by what the row says it is.
    Whether it could be read is a separate fact, and the answer to it is R-26,
    not silence.
    """
    paid = "4,425.50"
    incurred = "9,450.00"
    path = _write(
        tmp_path / "two-totals.pdf",
        CLAIMS,
        footer_rows=(
            ("Policy Total", "", "", "", paid, incurred),
            ("GRAND TOTAL", "", "", "", "5 60,000.00", "3 4"),
        ),
    )
    result = run_pipeline(path, use_vision=False)
    document = result.document
    assert "5 60,000.00" in document.unreadable_totals.values(), (
        f"the readable subtotal was taken as the document total: "
        f"printed={document.printed_totals} unreadable={document.unreadable_totals}"
    )
    grand = document.unreadable_totals_row
    assert grand is not None
    finding = next(
        f for f in result.reconciliation.findings
        if f.rule_id == "R-26" and "document's total row" in f.message
    )
    assert f"line {grand + 1}" in finding.message, (grand, finding.message)
    # The readable subtotal is not lost -- it is a section, checked as one.
    assert any(
        (section.printed_totals or {}) for section in document.printed_sections
    ), [s.printed_totals for s in document.printed_sections]
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 8. Where every column is money, the paragraph above answers instead
# --------------------------------------------------------------------------


def test_a_paragraph_ending_in_a_money_grid_is_not_evidence(tmp_path):
    """A disclaimer wrapped across a block that maps nothing but money.

    There is no non-money column here for the narrative trace to reach, and
    the last line of the paragraph -- "last 30 days." -- carries a digit and
    stands alone in the first column with nothing to its left. Every test that
    reads along the row runs out of row.

    The evidence is the line above it: several columns of text, not one cell
    of which yields an amount, is a paragraph line, and a short line under it
    that also yields nothing is where that paragraph ended.

    Deliberately narrow, and the control below is what keeps it so: the row
    above must span more than one column, so a lone refused figure never lends
    its line to the row beneath it.

    Reproduced synthetically from a corpus document (spec section 9): a real
    loss run ends its money grid with exactly this notice.
    """
    money_headers = ("Loss Paid", "Expense Paid", "Loss Reserve",
                     "Expense Reserve", "Total Incurred")
    path = _write(
        tmp_path / "paragraph.pdf",
        (
            ("1,000.00", "2,000.00", "3,000.00", "4,000.00", "10,000.00"),
            ("This is not to", "be construed", "as an absolute",
             "statement of", "claims, but as"),
            ("a history for", "this insured", "with the listed",
             "policy numbers", "applicable within"),
            ("last 30 days.", "", "", "", ""),
        ),
        headers=money_headers,
    )
    result = run_pipeline(path, use_vision=False)
    assert "last 30 days." not in _texts(result.document), (
        f"the tail of a paragraph became monetary evidence: "
        f"{[(r.page, r.row, r.ambiguous_values) for r in result.document.unplaced_rows]}"
    )


def test_a_lone_refused_figure_does_not_lend_its_line_to_the_next(tmp_path):
    """The control. Two merged cells on consecutive rows are two figures.

    If a row carrying one refused cell counted as a paragraph line, the row
    under it would be read as that paragraph's tail -- and a page of merged
    cells would collapse into one long sentence, losing every figure below the
    first. This is requirement 1 approached from the paragraph rule's side.
    """
    money_headers = ("Loss Paid", "Expense Paid", "Loss Reserve",
                     "Expense Reserve", "Total Incurred")
    path = _write(
        tmp_path / "two-merged.pdf",
        ((MERGED, "", "", "", ""), (MERGED_TWO, "", "", "", "")),
        headers=money_headers,
    )
    result = run_pipeline(path, use_vision=False)
    carried = _texts(result.document)
    assert MERGED in carried and MERGED_TWO in carried, (
        f"a refused figure absorbed the one below it: {carried}"
    )


def test_an_unprotected_repeat_cannot_delete_a_protected_one(tmp_path):
    """The same line, evidence on one page and paragraph tail on two others.

    Furniture is pooled by normalised text across pages, so a line has to
    repeat before it can be deleted -- and the pool is built only from
    occurrences nothing protected. That is not enough on its own. Here the
    merged cell follows a wrapped disclaimer on pages 2 and 3, so those two
    occurrences read as the tail of a paragraph and go into the pool; the pool
    reaches the threshold; and the occurrence on page 1, which follows a row
    of figures and is unambiguously evidence, is then deleted by its own text.

    Whether a line is furniture is a question about *that* line, in the
    company of the lines around it. Two occurrences agreeing cannot unmake a
    third that the page independently established.
    """
    money_headers = ("Loss Paid", "Expense Paid", "Loss Reserve",
                     "Expense Reserve", "Total Incurred")

    figures = ("1,000.00", "2,000.00", "3,000.00", "4,000.00", "10,000.00")

    def rows(page_number):
        if page_number == 1:
            return (figures, (MERGED, "", "", "", ""))
        return (
            figures,
            ("This is not to", "be construed", "as an absolute",
             "statement of", "claims, but as"),
            ("a history for", "this insured", "with the listed",
             "policy numbers", "applicable within"),
            (MERGED, "", "", "", ""),
        )

    path = _write(tmp_path / "mixed-repeat.pdf", (), pages=3, page_rows=rows,
                  headers=money_headers)
    result = run_pipeline(path, use_vision=False)
    document = result.document
    kept = [
        row for row in document.unplaced_rows
        if MERGED in list(row.amounts.values()) + list(row.ambiguous_values.values())
    ]
    assert 1 in {row.page for row in kept}, (
        f"a repeat elsewhere deleted the occurrence page 1 established: "
        f"{[(r.page, r.row, r.ambiguous_values) for r in document.unplaced_rows]}"
    )
