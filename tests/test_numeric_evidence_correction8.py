"""Correction-8: a neighbouring cell's word is not proof about this cell.

An independent review of correction-7 (9e2f2f1) found that its fix only
covers the two named test cases' own numeric *format*. "held for | 45,000.00
| days" survives because "45,000.00" carries a thousands separator and a
decimal fraction -- but "held for | 45000 | days", the identical phrase with
the comma removed, still disappears and reappears inside an unrelated
claim's own description. So does "held for | 500 | days", "outstanding for
| 9400 | days", and "number of | 9400 | claims": every one of the phrasings
this whole thread exists to protect, the moment the dollar figure happens to
be a whole number -- which whole-dollar reserve and paid amounts, and any
carrier that omits thousands separators, print constantly.

The review traced this to how correction-7's classifier read the row: the
label ("held for"), the number, and the unit word ("days") were three
separate physical cells, and the classifier treated them as one sentence by
concatenating their text across cell boundaries. A twelve-word denylist
("amount", "balance", ... "total") was the only brake on that reading, so
any label not on the list -- "Entry", "Incurred", "Outstanding", "Case
reserve of", "Adjuster estimate", or nothing at all -- passed straight
through and the exemption fired anyway.

This unit removes the cross-cell reading for the duration/count path
entirely. The label and the unit word are no longer read into the decision
at all; the only question is now whether the *number's own cell* already
carries its unit ("30 days" printed as one cell is proof the source tied
them together) or whether the row establishes itself as a claim, or whether
another mapped field already speaks for it. Nothing about a neighbouring
cell's wording enters into it, so there is no list -- of financial words or
of temporal ones -- for the next document to fall outside of.

Every regression below inspects the resulting claim descriptions directly,
per this unit's own requirement: a value silently relocated there passes
every check that only asks "is it still unplaced evidence".
"""

from __future__ import annotations

import pymupdf
import pytest

from core.pipeline import (
    ColumnMapping,
    build_claims,
    run_pipeline,
    unplaced_evidence,
)
from core.schema import DocumentStatus, RawRow, RawTable

LEFT = 25.0
LINE = 14.0
COLUMNS = (0.0, 80.0, 155.0, 230.0, 355.0, 430.0, 505.0)
HEADERS = (
    "Claim No", "Date of Loss", "Status", "Loss Description",
    "Paid Total", "Reserve Total", "Total Incurred",
)
MAPPING = ColumnMapping(
    headers=list(HEADERS),
    fields={
        0: "claim_number", 1: "date_of_loss", 2: "claim_status",
        3: "loss_description", 4: "paid_total", 5: "reserve_total",
        6: "incurred_total",
    },
)
CLAIMS = (
    ("CN-1001", "03/12/2024", "OPEN", "Ladder fall", "1,200.00", "3,800.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "Rear-end collision", "2,450.00", "0.00", "2,450.00"),
    ("CN-1003", "09/21/2024", "OPEN", "Water ingress", "775.50", "1,224.50", "2,000.00"),
)


def _raw(cells, *, page=1, line=0, kind="data"):
    return RawRow(cells=list(cells), page=page, line_index=line, kind=kind)


def _claim_rows(*, page=1, start=1):
    return [_raw(cells, page=page, line=line) for line, cells in enumerate(CLAIMS, start=start)]


def _table(rows, *, page=1):
    return RawTable(page=page, headers=list(HEADERS), rows=list(rows), strategy="words")


def _write(path, rows):
    document = pymupdf.open()
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
    for offset, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in rows:
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


def _descriptions(claims):
    """Every claim's description, concatenated -- checked on every test."""
    return " ".join(claim.loss_description or "" for claim in claims)


def _r23(result):
    return [f for f in result.reconciliation.findings if f.rule_id == "R-23"]


def _build(extra_rows, *, page=1, start=1):
    claims, warnings, unplaced = build_claims(
        [_table(_claim_rows(page=page, start=start) + list(extra_rows), page=page)],
        MAPPING, "us", "mdy",
    )
    return claims, warnings, unplaced


def _assert_value_is_live_evidence(unplaced, claims, value):
    """Present in some evidence channel; absent from every claim's own
    description. The description check is what actually catches a value
    that vanished from `unplaced` by being relocated rather than resolved.
    """
    carried = {
        text
        for row in unplaced
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }
    assert value in carried, f"{value!r} disappeared entirely: unplaced={unplaced}"
    assert value not in _descriptions(claims), (
        f"{value!r} was silently folded into a claim description: "
        f"{[(c.claim_number, c.loss_description) for c in claims]}"
    )


# --------------------------------------------------------------------------
# The eleven named cases: none may disappear, be folded into another
# claim's description, or let the document read CLEAN.
# --------------------------------------------------------------------------

CASES = [
    ("held for", "500", "days"),
    ("held for", "45000", "days"),
    ("outstanding for", "9400", "days"),
    ("outstanding for", "128500", "days"),
    ("number of", "9400", "claims"),
    ("Entry", "500", "days"),
    ("Entry", "9400", "claims"),
    ("Incurred", "500", "days"),
    ("Outstanding", "9400", "claims"),
    ("Case reserve of", "500", "days"),
    ("Adjuster estimate", "9400", "claims"),
]
CASE_IDS = [
    "held-for-500-days",
    "held-for-45000-days",
    "outstanding-for-9400-days",
    "outstanding-for-128500-days",
    "number-of-9400-claims",
    "entry-500-days",
    "entry-9400-claims",
    "incurred-500-days",
    "outstanding-label-9400-claims",
    "case-reserve-of-500-days",
    "adjuster-estimate-9400-claims",
]


@pytest.mark.parametrize("before, value, after", CASES, ids=CASE_IDS)
def test_whole_unit_value_survives_duration_and_count_grammar(before, value, after):
    row = _raw(["", "", "", before, value, after, ""], line=99)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, value)


@pytest.mark.parametrize("before, value, after", CASES, ids=CASE_IDS)
def test_whole_unit_value_end_to_end_blocks_clean(tmp_path, before, value, after):
    rows = CLAIMS + ((("", "", "", before, value, after, "")),)
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in f"{before}-{value}")
    result = run_pipeline(
        _write(tmp_path / f"wholeunit-{safe_name}.pdf", rows),
        use_vision=False,
    )
    assert value in _texts(result.document), (
        f"{value!r} disappeared end-to-end: {result.document.unplaced_rows}"
    )
    assert value not in _descriptions(result.document.claims), (
        f"{value!r} was folded into a claim description end-to-end: "
        f"{[(c.claim_number, c.loss_description) for c in result.document.claims]}"
    )
    assert _r23(result), "no R-23 finding was raised for a live, unowned amount"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW, (
        "a document carrying an unresolved whole-unit amount read CLEAN"
    )


# --------------------------------------------------------------------------
# Repeated across pages: every occurrence keeps its own page/row identity,
# and repetition must not let page-furniture pooling erase it. Pooling
# consults the same classifier this unit fixes, so a misclassified row
# vanishes with *no* trace at all once it repeats -- not even folded into a
# description -- which is the more severe failure mode the review surfaced.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("before, value, after", CASES, ids=CASE_IDS)
def test_whole_unit_value_survives_repetition_across_pages(before, value, after):
    pages = (1, 2, 3)
    tables = [
        _table(
            _claim_rows(page=page, start=1)
            + [_raw(["", "", "", before, value, after, ""], page=page, line=99)],
            page=page,
        )
        for page in pages
    ]
    claims, _warnings, unplaced = build_claims(tables, MAPPING, "us", "mdy")

    matches = [
        item for item in unplaced
        if value in list(item.amounts.values()) + list(item.ambiguous_values.values())
    ]
    seen_pages = sorted(item.page for item in matches)
    assert seen_pages == list(pages), (
        f"{value!r} did not survive on every page it was printed on -- "
        f"expected {list(pages)}, found evidence on {seen_pages} "
        f"(furniture pooling erased the rest): {unplaced}"
    )
    assert all(item.row == 99 for item in matches), matches
    assert value not in _descriptions(claims), (
        f"{value!r} was folded into a claim description instead of kept as "
        f"per-page evidence: {[(c.claim_number, c.loss_description) for c in claims]}"
    )


def test_whole_unit_value_survives_repetition_across_pages_end_to_end(tmp_path):
    """One representative case through the real multi-page PDF path, not
    just direct ``build_claims`` calls, so the extraction layer's own page
    handling is exercised too.
    """
    document = pymupdf.open()
    for page_number in range(3):
        page = document.new_page(width=612, height=792)
        y = 50.0
        for line in (
            "MERIDIAN MUTUAL ASSURANCE", "LOSS RUN REPORT",
            "Named Insured: Northwind Fabrication Ltd",
            "Policy Number: GL-4417-2024",
            "Policy Period: 01/01/2024 to 12/31/2024",
            "Valuation Date: 12/31/2024", "Currency: USD",
        ):
            page.insert_text((LEFT, y), line, fontsize=9)
            y += LINE
        y += LINE
        for offset, label in zip(COLUMNS, HEADERS):
            page.insert_text((LEFT + offset, y), label, fontsize=8.5)
        y += LINE
        rows = CLAIMS + (("", "", "", "outstanding for", "9400", "days", ""),)
        for row in rows:
            for offset, cell in zip(COLUMNS, row):
                if cell:
                    page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
            y += LINE
    path = tmp_path / "repeated-across-pages.pdf"
    document.save(path)
    document.close()

    result = run_pipeline(path, use_vision=False)
    matching = [row for row in result.document.unplaced_rows if "9400" in row.ambiguous_values.values()]
    seen_pages = sorted(row.page for row in matching)
    assert seen_pages == [1, 2, 3], (
        f"'9400' did not survive on every page: {result.document.unplaced_rows}"
    )
    assert "9400" not in _descriptions(result.document.claims)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# The structural rule itself: a duration/count unit printed in the *same*
# physical cell as the number is defensible proof; a neighbouring cell's
# word is not, no matter how grammatical the row reads across cell
# boundaries.
# --------------------------------------------------------------------------


def test_a_genuine_same_cell_duration_remains_exempt_and_reads_clean():
    """Correction-9 update: this test's own premise -- that "30 days"
    printed as one physical cell is proof the source tied the unit to this
    digit run -- turned out not to hold. `assign_to_columns` in the
    extraction layer buckets a stray word into a money column whenever its
    midpoint drifts within slack its own docstring calls "most of a column"
    wide, and this codebase already has a test proving a single reported
    cell can carry text smeared in from a neighbouring column
    (`test_an_identifier_smeared_with_a_neighbouring_column_is_kept`). A
    cell boundary is not evidence of what the carrier printed together, so
    correction-9 retired the same-cell duration/count exemption entirely
    rather than trying to patch it further. This value must now survive as
    unresolved evidence instead of vanishing -- exactly the "safe false
    positive over silent financial loss" trade correction-9's own invariant
    names.
    """
    row = _raw(["", "", "", "Claim settled in", "30 days", "", ""], line=105)
    claims, _warnings, unplaced = _build([row])
    carried = {
        text for item in unplaced
        for text in list(item.amounts.values()) + list(item.ambiguous_values.values())
    }
    assert any("30" in text for text in carried), (
        f"a whole-unit value vanished to zero trace: {unplaced}"
    )
    assert not any("30" in (claim.loss_description or "") for claim in claims), (
        f"it was folded into a claim description instead: "
        f"{[(c.claim_number, c.loss_description) for c in claims]}"
    )


def test_a_genuine_same_cell_count_needs_review_end_to_end(tmp_path):
    """Correction-9 update: see the direct-call test above for why this
    row's old CLEAN expectation is retired along with the same-cell
    duration/count exemption itself.
    """
    rows = CLAIMS + (("", "", "", "Number of related", "12 claims", "filed", ""),)
    result = run_pipeline(
        _write(tmp_path / "same-cell-count.pdf", rows), use_vision=False
    )
    evidence = _texts(result.document)
    assert any("12" in text for text in evidence), (
        f"a whole-unit value vanished to zero trace: {result.document.unplaced_rows}"
    )
    assert "12" not in _descriptions(result.document.claims), (
        f"it was folded into a claim description instead: "
        f"{[(c.claim_number, c.loss_description) for c in result.document.claims]}"
    )
    assert _r23(result)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_financial_cue_word_still_vetoes_a_same_cell_duration_reading():
    """The negative check survives the refactor, now checked within the
    one cell it actually has evidence about: "Reserve 500 days" prints the
    unit beside the number in the same cell, but "Reserve" immediately in
    front of it is explicit financial vocabulary, and that still blocks the
    exemption regardless of the co-location.
    """
    row = _raw(["", "", "", "Note", "Reserve 500 days", "", ""], line=106)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, "Reserve 500 days")


def test_a_neighbouring_cells_unit_word_is_not_proof_even_with_no_veto_word():
    """The direct, minimal demonstration of the fix: nothing at all sits in
    front of the number, and the row still may not exempt it merely because
    the *next* cell happens to say "days" -- the word has to be about this
    cell, not merely nearby.
    """
    row = _raw(["", "", "", "", "500", "days", ""], line=107)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, "500")


# --------------------------------------------------------------------------
# Preserved from correction-7: grouped/decimal values, merged date
# fragments, and genuinely unmerged labelled dates. These paths were not
# touched by this unit; re-asserted here so this file stands on its own.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "before, value, after",
    [
        ("held for", "45,000.00", "days"),
        ("outstanding for", "128,500.00", "days"),
        ("total of", "45,000.00", "claims"),
    ],
    ids=["held-for-days-grouped", "outstanding-for-days-grouped", "total-of-claims-grouped"],
)
def test_grouped_and_decimal_money_still_survives_duration_and_count_grammar(
    before, value, after
):
    row = _raw(["", "", "", before, value, after, ""], line=108)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, value)


def test_merged_count_and_date_fragment_still_stays_unresolved():
    row = _raw(["", "", "", "Date of report", "4 5/23/2023", "", ""], line=100)
    claims, _warnings, unplaced = _build([row])
    matches = [
        item for item in unplaced
        if "4 5/23/2023" in list(item.amounts.values()) + list(item.ambiguous_values.values())
    ]
    assert matches, f"the merged count+date fragment disappeared: {unplaced}"
    assert matches[0].page == 1 and matches[0].row == 100, matches
    assert "4 5/23/2023" not in _descriptions(claims)


def test_genuinely_unmerged_date_is_still_exempted():
    row = _raw(["", "", "", "Date of report", "5/23/2023", "", ""], line=101)
    evidence = unplaced_evidence(row, MAPPING, "us", context="claims")
    assert not evidence, f"a genuine, unmerged date was no longer exempted: {evidence}"
