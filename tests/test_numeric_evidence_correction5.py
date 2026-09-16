"""Six false-CLEAN and false-positive paths an independent review reproduced
in correction 4 (commit 2363b0c).

Every failure here is the same underlying defect: something that *could*
narrow toward "narrative" or "explained" was trusted as if it already had,
without the positive proof the invariant demands. Absence of a date or a
status is not proof of narrative; a label with no digit is not proof it names
whatever follows; a row that itself carries unresolved figures is not proof
the row after it also does; a structural or repeated-header line is not proof
of anything about the claim data around it; and parentheses are a sign
convention, not evidence a labelled year became money.

**A description-shaped row missing its identifier.** "New loss | 4 /
30,000.00" has no date and no status. The same-row narrative walk
(``_runs_in_from_a_description``) treated *any* populated non-money column --
including the claim-number cell holding a stray fragment -- as proof a
sentence started there, so the merged amount was folded into the previous
claim's description and vanished. A structured field (the claim number, a
date, the status) holding text that is not its own kind of value is not
thereby prose.

**A label that states no value.** "Version unknown" matches the same
vocabulary as "Policy year" or "Software version" and, carrying no digit of
its own, was trusted to name *whatever cell came next* -- so it swallowed an
unrelated ``$500.00`` whole. Saying "unknown" is not naming a value; it is the
row admitting it has none to give.

**Consecutive unresolved rows.** A row holding two merged cells across two
money columns satisfies ``_continues_the_line_above``'s "several populated
cells, none of them money" test just as well as genuine prose does, so the
row below it -- a third merged cell, standing alone -- was read as that row's
trailing words and discarded. Two rows of refused figures are two rows of
evidence, not one paragraph.

**A structural row precedes an unreadable value.** A row the pipeline itself
excludes from claim data (``kind == "meta"``, or a colon-labelled section
heading ``is_structural_row`` already recognises) still sits in
``table.rows`` at its own position, and the row after it saw it as
"previous". Spanning several columns of non-money text is what
``_continues_the_line_above`` asked for, and a section heading matches that
shape perfectly despite being furniture, not prose.

**A parenthesized year.** "Policy year 2024" beside "(2024)" is the row
stating one fact twice. The label-vs-value comparison used ``parse_money``'s
*signed* value on both sides, and parentheses flip that sign for the printed
value alone -- so 2024 and -2024 were compared and found not to match,
leaving the year to be read as money instead of exempted.

**Isolated prose is exempt; an isolated *value* in the same slot is not.**
Reconfirms, as this unit's own regressions rather than an inherited fixture,
that a phrase anchored to a genuine description column stays narrative and
raises nothing, while a genuine or ambiguous amount in the identical column
position -- with nothing narrative beside it -- is still preserved and still
blocks.

Fixtures are synthetic: constructed rows and tables called directly through
the pipeline functions, plus a handful of generated PDFs driven through
``run_pipeline`` for the end-to-end path. No corpus, no platform-specific
paths. Direct calls are used wherever PDF word-geometry could plausibly split
a label across columns and let a test pass without exercising the mechanism
it names -- requirement 2 is explicit about this risk.
"""

from __future__ import annotations

import pymupdf
import pytest

from core.pipeline import (
    ColumnMapping,
    build_claims,
    _labelled_value,
    run_pipeline,
    unplaced_evidence,
)
from core.schema import DocumentStatus, RawRow, RawTable

# --------------------------------------------------------------------------
# Shared fixtures
# --------------------------------------------------------------------------

HEADERS = ("Claim No", "Date of Loss", "Status", "Loss Description", "Paid Total",
           "Total Incurred")
MAPPING = ColumnMapping(
    headers=list(HEADERS),
    fields={0: "claim_number", 1: "date_of_loss", 2: "claim_status",
            3: "loss_description", 4: "paid_total", 5: "incurred_total"},
)
CLAIMS = (
    ("CN-1001", "03/12/2024", "OPEN", "Ladder fall", "1,200.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "Rear-end collision", "2,450.00", "2,450.00"),
    ("CN-1003", "09/21/2024", "OPEN", "Water ingress", "775.50", "2,000.00"),
)
MERGED = "4 / 30,000.00"
MERGED_TWO = "5 / 40,000.00"
MERGED_THREE = "6 / 50,000.00"

LEFT = 40.0
LINE = 14.0
COLUMNS = (0.0, 90.0, 175.0, 250.0, 350.0, 450.0)


def _row(cells, page=1, line=0, kind="data"):
    return RawRow(cells=list(cells), page=page, line_index=line, kind=kind)


def _table(rows, page=1, headers=HEADERS, total_rows=()):
    return RawTable(page=page, headers=list(headers), rows=list(rows),
                     total_rows=list(total_rows), strategy="words")


def _claim_rows(claims=CLAIMS, start=1):
    return [_row(cells, line=index) for index, cells in enumerate(claims, start)]


def _write(path, rows, *, pages=1, page_rows=None, headers=HEADERS):
    """A loss run PDF for the end-to-end checks."""
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
    document.save(path)
    document.close()
    return path


def _texts(document):
    return {
        text
        for row in document.unplaced_rows
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }


def _r23(result):
    return [f for f in result.reconciliation.findings if f.rule_id == "R-23"]


def _descriptions(document):
    return " ".join(claim.loss_description or "" for claim in document.claims)


def _unplaced_of(claims_rows, extra_rows):
    """Run ``build_claims`` directly and return (claims, unplaced)."""
    table = _table(list(claims_rows) + list(extra_rows))
    claims, _warnings, unplaced = build_claims([table], MAPPING, "us", "mdy")
    return claims, unplaced


# --------------------------------------------------------------------------
# 1. A description-shaped row with no date or status is not narrative
# --------------------------------------------------------------------------


def test_a_missing_identifier_row_with_no_date_or_status_keeps_its_amount():
    """"New loss | 4 / 30,000.00": the claim-number cell holds a stray
    fragment, not an identifier, and nothing establishes claim data. The
    row's meaning is genuinely uncertain -- it must survive as unresolved
    evidence, not be folded into the claim above it.
    """
    extra = (_row(["New loss", "", "", "", MERGED, ""], line=99),)
    claims, unplaced = _unplaced_of(_claim_rows(), extra)
    assert MERGED not in _descriptions_from(claims), (
        f"the amount was folded into a claim description: "
        f"{[(c.claim_number, c.loss_description) for c in claims]}"
    )
    carried = {
        text
        for row in unplaced
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }
    assert MERGED in carried, (
        f"the numeric evidence disappeared entirely: unplaced={unplaced}"
    )
    # No claim ownership is invented for it.
    assert all(c.claim_number != "New loss" for c in claims), claims


def _descriptions_from(claims):
    return " ".join(c.loss_description or "" for c in claims)


def test_a_missing_identifier_row_end_to_end(tmp_path):
    """The same case, through the whole pipeline: it must require review,
    never read CLEAN, and never disappear."""
    path = _write(
        tmp_path / "new-loss.pdf",
        CLAIMS + (("New loss", "", "", "", MERGED, ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert MERGED in _texts(result.document), (
        f"evidence lost end-to-end: {result.document.unplaced_rows}"
    )
    assert MERGED not in _descriptions(result.document)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 2. A label that states no value does not consume the cell beside it
# --------------------------------------------------------------------------


CLAIMS_HEADERS = ["Claim Number", "Loss Date", "Status", "Note", "Paid Total"]
CLAIMS_MAPPING = ColumnMapping(
    headers=CLAIMS_HEADERS,
    fields={0: "claim_number", 1: "date_of_loss", 2: "claim_status",
            3: None, 4: "paid_total"},
)


@pytest.mark.parametrize(
    "label, value",
    [
        ("Version unknown", "$500.00"),
        ("Software version unknown", "500.00"),
    ],
)
def test_a_label_stating_no_value_does_not_erase_the_adjacent_cell(label, value):
    """"Version unknown" is not a naming of $500.00 -- it is the row saying
    there is nothing to report about the version. Direct row construction,
    not a generated PDF: this must exercise ``_labelled_value`` on exactly
    the intended two-cell row, not a row whose geometry happens to split the
    label in a way that sidesteps the mechanism being tested.
    """
    row = _row(["", "", "", label, value], line=2)
    named = _labelled_value(row, CLAIMS_MAPPING)
    assert named is None, (
        f"{label!r} was trusted to name {value!r}: named={named!r}"
    )
    evidence = unplaced_evidence(row, CLAIMS_MAPPING, "us", context="claims")
    carried = {**evidence.amounts, **evidence.ambiguous, **{
        k: (v, None) for k, v in evidence.unreadable.items()
    }}
    assert "paid_total" in carried, (
        f"{label!r} erased the adjacent value entirely: {evidence}"
    )


def test_a_label_stating_no_value_end_to_end(tmp_path):
    """Confirms the direct-call result above holds through the whole stack.

    Only "Version unknown" is driven through a generated PDF. "Software
    version unknown" is three words wide enough that the word-clustering
    extractor can split it across a column boundary, landing "unknown" in a
    different cell than the direct test controls for -- exactly the
    geometry risk requirement 2 calls out. The direct test above is the
    reliable one for that case; this end-to-end check is the reliable one
    for this case, and both exercise ``_labelled_value`` on the row the bug
    actually lives in rather than a row geometry happened to rearrange.
    """
    path = _write(
        tmp_path / "no-value-label.pdf",
        CLAIMS + (("Version unknown", "", "", "", "$500.00", ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert "$500.00" in _texts(result.document), (
        f"the label erased $500.00 end-to-end: {result.document.unplaced_rows}"
    )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 3. Consecutive unresolved rows all survive with distinct provenance
# --------------------------------------------------------------------------


def test_a_second_unresolved_row_is_not_absorbed_by_the_first():
    """Row 1 holds two merged cells; row 2, right under it, holds one more.
    Row 1 being unresolved evidence must not license reading row 2 as its
    trailing words -- that is not narrative continuation, it is a second row
    of figures.
    """
    row1 = _row(["", "", "", "", MERGED, MERGED_TWO], line=10)
    row2 = _row(["", "", "", "", MERGED_THREE, ""], line=11)
    claims, unplaced = _unplaced_of(_claim_rows(), (row1, row2))
    carried_by_row = {
        row.row: list(row.amounts.values()) + list(row.ambiguous_values.values())
        for row in unplaced
    }
    all_carried = {text for values in carried_by_row.values() for text in values}
    assert MERGED in all_carried and MERGED_TWO in all_carried, (
        f"row 1's evidence regressed: {carried_by_row}"
    )
    assert MERGED_THREE in all_carried, (
        f"row 2 was silently absorbed by row 1: {carried_by_row}"
    )
    assert MERGED_THREE not in _descriptions_from(claims), (
        "row 2's amount was folded into a claim description"
    )
    # Distinct provenance: two different (page, row) identities, not one.
    rows_carrying = {
        row.row for row in unplaced
        if any(
            value in (MERGED, MERGED_TWO, MERGED_THREE)
            for value in list(row.amounts.values()) + list(row.ambiguous_values.values())
        )
    }
    assert 10 in rows_carrying and 11 in rows_carrying, (
        f"provenance collapsed onto one row: {rows_carrying}"
    )


def test_consecutive_unresolved_rows_end_to_end(tmp_path):
    path = _write(
        tmp_path / "consecutive.pdf",
        CLAIMS + (
            ("", "", "", "", MERGED, MERGED_TWO),
            ("", "", "", "", MERGED_THREE, ""),
        ),
    )
    result = run_pipeline(path, use_vision=False)
    carried = _texts(result.document)
    assert MERGED in carried and MERGED_TWO in carried and MERGED_THREE in carried, (
        f"a consecutive unresolved row vanished: {carried}"
    )
    pages_and_rows = {
        (row.page, row.row) for row in result.document.unplaced_rows
        if any(
            value in (MERGED, MERGED_TWO, MERGED_THREE)
            for value in list(row.amounts.values()) + list(row.ambiguous_values.values())
        )
    }
    assert len(pages_and_rows) >= 2, (
        f"the two rows collapsed onto one identity: {pages_and_rows}"
    )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 4. A structural or repeated-header row cannot prove paragraph continuation
# --------------------------------------------------------------------------


def test_a_meta_row_does_not_license_discarding_the_row_after_it():
    """A ``kind="meta"`` row -- the extractor's own finding that a line ends a
    record rather than continuing one, e.g. a policy-period heading folded
    into the body -- still occupies a position in ``table.rows`` and becomes
    "previous" for the row after it. Spanning several non-money columns is
    not proof of prose when the row is furniture the pipeline itself already
    excludes from claim data.
    """
    meta_row = _row(["Policy Period:", "01/01/2024", "to", "12/31/2024", "", ""],
                     line=50, kind="meta")
    following = _row(["", "", "", "", MERGED, ""], line=51)
    claims, unplaced = _unplaced_of(_claim_rows(), (meta_row, following))
    carried = {
        text for row in unplaced
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }
    assert MERGED in carried, (
        f"a meta row wrongly licensed discarding the evidence after it: "
        f"unplaced={unplaced}"
    )
    assert MERGED not in _descriptions_from(claims)


def test_a_structural_heading_row_does_not_license_discarding_either():
    """The other structural-row detector: a colon-labelled section heading in
    the claim-number cell (``is_structural_row``), kind left as ordinary
    ``"data"``, and its words spread across several cells the way a genuine
    printed heading spans several columns. Same failure, different detector.
    """
    heading_row = _row(["Policy Period:", "01/01/2024", "to", "12/31/2024", "", ""],
                        line=60, kind="data")
    following = _row(["", "", "", "", MERGED, ""], line=61)
    claims, unplaced = _unplaced_of(_claim_rows(), (heading_row, following))
    carried = {
        text for row in unplaced
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }
    assert MERGED in carried, (
        f"a structural heading row wrongly licensed discarding the evidence "
        f"after it: unplaced={unplaced}"
    )
    assert MERGED not in _descriptions_from(claims)


# --------------------------------------------------------------------------
# 5. Parentheses are a sign convention, not proof a labelled year is money
# --------------------------------------------------------------------------


def test_a_parenthesized_year_is_still_named_by_its_label():
    """"Policy year 2024" beside "(2024)" states one fact twice. Comparing
    signed values makes 2024 and -2024 look like two different numbers;
    comparing the underlying magnitude recognises them as the same one.
    """
    row = _row(["", "", "", "Policy year 2024", "(2024)"], line=5)
    named = _labelled_value(row, CLAIMS_MAPPING)
    assert named == "paid_total", (
        f"the parenthesized year was not recognised as the labelled value: "
        f"named={named!r}"
    )
    evidence = unplaced_evidence(row, CLAIMS_MAPPING, "us", context="claims")
    assert evidence.amounts == {}, (
        f"a parenthesized year was promoted to money: {evidence}"
    )
    assert evidence.ambiguous == {} and evidence.unreadable == {}, (
        f"a parenthesized year was left as unresolved evidence rather than "
        f"exempted: {evidence}"
    )


def test_a_parenthesized_year_end_to_end_raises_no_r23(tmp_path):
    path = _write(
        tmp_path / "paren-year.pdf",
        CLAIMS + (("", "", "", "Policy year 2024", "(2024)", ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert "(2024)" not in _texts(result.document), (
        f"the parenthesized year was carried as evidence: "
        f"{result.document.unplaced_rows}"
    )
    assert not any("(2024)" in (f.message or "") for f in _r23(result)), (
        [f.message for f in _r23(result)]
    )


# --------------------------------------------------------------------------
# 6. Isolated narrative is exempt; a value in the same slot is not
# --------------------------------------------------------------------------


def test_isolated_prose_anchored_to_a_description_now_needs_review(tmp_path):
    """"within last 30 days" anchored to a real description cell on the same
    row.

    Correction-9 update: this test originally trusted the description cell
    beside it, plus the duration wording in its own cell, as proof the
    figure was narrative rather than money placed by geometry. Neither is
    one of the narrow mechanisms correction-9 leaves standing (an explicit
    label tied to this exact value, a page marker, a validated date, or an
    actual non-money column), so the figure must now survive as unresolved
    evidence instead.
    """
    path = _write(
        tmp_path / "isolated-prose.pdf",
        CLAIMS + (("", "", "", "sustained further damage",
                    "within last 30 days", ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert "within last 30 days" in _texts(result.document), (
        f"a whole-unit value vanished to zero trace: {result.document.unplaced_rows}"
    )
    assert "within last 30 days" not in _descriptions(result.document), (
        f"it was folded into a claim description instead: {result.document.claims}"
    )
    assert _r23(result), "no R-23 finding was raised for a live, unowned figure"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_an_isolated_value_in_the_same_column_still_requires_review(tmp_path):
    """The control: the identical column position (Paid Total), no
    narrative anchor beside it -- a genuine/ambiguous value here must still
    be preserved and still block, precisely because nothing establishes it
    as prose.
    """
    path = _write(
        tmp_path / "isolated-value.pdf",
        CLAIMS + (("", "", "", "", MERGED, ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert MERGED in _texts(result.document), (
        f"a genuine value in the same slot was erased: {_texts(result.document)}"
    )
    assert _r23(result), "nothing was reported for the isolated value"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
