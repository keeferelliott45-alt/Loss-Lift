"""Correction-9: duration/count grammar is retired as an evidence exemption.

An independent review of correction-8 (83d6b10) found that "same cell" was
not the structural boundary it was assumed to be. Every one of the twelve
phrases below, merged into the complete text of *one* mapped monetary
cell -- exactly the shape correction-8's classifier was built to trust --
still vanished from every evidence channel and reappeared inside an
unrelated claim's own description: "held for 45000 days" (the original
critical phrase correction-7 was built to protect, now with the comma
removed and the three cells fused into one) fails identically to how the
cross-cell version failed before.

The review traced this to the extraction architecture, not just the
classifier: `assign_to_columns` (core/extract_digital.py) buckets a stray
word into a money column whenever its midpoint drifts within
`COLUMN_SLACK_FACTOR` slack -- the function's own docstring calls this
"most of a column" wide -- and `split_words` groups words into one cell on
a bare position gap with no notion of which field a word belongs to. This
codebase's own test suite already proves a single reported cell can carry
text smeared in from a neighbouring column
(`test_an_identifier_smeared_with_a_neighbouring_column_is_kept` in
test_detail_block_rows.py). A cell boundary records how two geometric
heuristics happened to cluster words on one page, not what the carrier
printed as one thing. Correction-7 trusted cross-cell adjacency;
correction-8 narrowed that to same-cell adjacency; neither is actually
proof, because both are read off the same unreliable boundary.

This unit removes duration/count grammar as a basis for exemption
entirely -- no cell-shape heuristic, phrase list, financial-word veto,
magnitude rule, or numeric-format rule replaces it. A digit-bearing
candidate in a mapped money column now stays unresolved unless one of the
three narrower, already-independent mechanisms proves otherwise: an
explicit non-money label tied to that exact value (`_labelled_values`), a
recognised page marker, or an independently validated date relationship
(a date token whose own shape proves it, confirmed by date/term
vocabulary elsewhere on the row). None of the three reasons "about
duration or count" that any of the twelve phrases below could offer.

Every regression inspects the resulting claim descriptions directly, per
this unit's own requirement, and every one is also checked repeated across
pages to confirm page-furniture pooling cannot erase it either.
"""

from __future__ import annotations

import pymupdf
import pytest

from core.pipeline import (
    ColumnMapping,
    build_claims,
    run_pipeline,
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
    return " ".join(claim.loss_description or "" for claim in claims)


def _r23(result):
    return [f for f in result.reconciliation.findings if f.rule_id == "R-23"]


def _number_in(phrase):
    """The digit run a phrase carries -- what must never disappear.

    End-to-end, a wide multi-word phrase is not guaranteed to land back as
    the exact same single cell it was written as: real column assignment
    buckets *words*, not whole strings, so "outstanding for 128500 days"
    can legitimately re-segment into "outstanding for" (no digit, correctly
    dropped) and "128500 days" (kept) across two adjacent columns. That
    split is the extraction layer behaving as documented, not the defect
    this file exists to catch -- so the end-to-end checks assert on the
    number itself, which is what the invariant actually protects, rather
    than requiring the whole phrase to survive as one literal string.
    """
    import re

    return re.search(r"\d+", phrase).group()


def _build(extra_rows, *, page=1, start=1):
    claims, warnings, unplaced = build_claims(
        [_table(_claim_rows(page=page, start=start) + list(extra_rows), page=page)],
        MAPPING, "us", "mdy",
    )
    return claims, warnings, unplaced


def _assert_value_is_live_evidence(unplaced, claims, value, *, page=1, row=None):
    carried = {
        text
        for item in unplaced
        for text in list(item.amounts.values()) + list(item.ambiguous_values.values())
    }
    assert value in carried, f"{value!r} disappeared entirely: unplaced={unplaced}"
    assert value not in _descriptions(claims), (
        f"{value!r} was silently folded into a claim description: "
        f"{[(c.claim_number, c.loss_description) for c in claims]}"
    )
    matches = [
        item for item in unplaced
        if value in list(item.amounts.values()) + list(item.ambiguous_values.values())
    ]
    if row is not None:
        assert any(item.page == page and item.row == row for item in matches), matches


# --------------------------------------------------------------------------
# The twelve merged single-cell phrases: none may disappear, be appended to
# another claim's description, or let the document read CLEAN.
# --------------------------------------------------------------------------

MERGED_PHRASES = [
    "500 days",
    "9400 claims",
    "held for 45000 days",
    "outstanding for 128500 days",
    "Incurred 500 days",
    "Outstanding 9400 claims",
    "Owed 500 days",
    "Exposure 9400 claims",
    "Claim value 500 days",
    "Adjuster estimate 9400 claims",
    "Case reserve of 500 days",
    "Balance due of 9400 claims",
]
PHRASE_IDS = [
    "500-days",
    "9400-claims",
    "held-for-45000-days",
    "outstanding-for-128500-days",
    "incurred-500-days",
    "outstanding-9400-claims",
    "owed-500-days",
    "exposure-9400-claims",
    "claim-value-500-days",
    "adjuster-estimate-9400-claims",
    "case-reserve-of-500-days",
    "balance-due-of-9400-claims",
]


@pytest.mark.parametrize("phrase", MERGED_PHRASES, ids=PHRASE_IDS)
def test_merged_phrase_survives_as_unresolved_evidence(phrase):
    row = _raw(["", "", "", "", phrase, "", ""], line=99)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, phrase, page=1, row=99)


@pytest.mark.parametrize("phrase", MERGED_PHRASES, ids=PHRASE_IDS)
def test_merged_phrase_end_to_end_blocks_clean(tmp_path, phrase):
    rows = CLAIMS + (("", "", "", "", phrase, "", ""),)
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in phrase)
    result = run_pipeline(
        _write(tmp_path / f"merged-{safe_name}.pdf", rows),
        use_vision=False,
    )
    number = _number_in(phrase)
    evidence = _texts(result.document)
    assert any(number in text for text in evidence), (
        f"the number in {phrase!r} disappeared end-to-end: "
        f"{result.document.unplaced_rows}"
    )
    descriptions = _descriptions(result.document.claims)
    assert number not in descriptions, (
        f"the number in {phrase!r} was folded into a claim description "
        f"end-to-end: {[(c.claim_number, c.loss_description) for c in result.document.claims]}"
    )
    assert _r23(result), "no R-23 finding was raised for a live, unowned phrase"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW, (
        "a document carrying an unresolved merged phrase read CLEAN"
    )


@pytest.mark.parametrize("phrase", MERGED_PHRASES, ids=PHRASE_IDS)
def test_merged_phrase_survives_repetition_across_pages(phrase):
    pages = (1, 2, 3)
    tables = [
        _table(
            _claim_rows(page=page, start=1)
            + [_raw(["", "", "", "", phrase, "", ""], page=page, line=99)],
            page=page,
        )
        for page in pages
    ]
    claims, _warnings, unplaced = build_claims(tables, MAPPING, "us", "mdy")

    matches = [
        item for item in unplaced
        if phrase in list(item.amounts.values()) + list(item.ambiguous_values.values())
    ]
    seen_pages = sorted(item.page for item in matches)
    assert seen_pages == list(pages), (
        f"{phrase!r} did not survive on every page it was printed on -- "
        f"expected {list(pages)}, found evidence on {seen_pages} "
        f"(furniture pooling erased the rest): {unplaced}"
    )
    assert all(item.row == 99 for item in matches), matches
    assert phrase not in _descriptions(claims), (
        f"{phrase!r} was folded into a claim description instead of kept as "
        f"per-page evidence: {[(c.claim_number, c.loss_description) for c in claims]}"
    )


def test_merged_phrase_survives_repetition_across_pages_end_to_end(tmp_path):
    """One representative case through the real multi-page PDF path."""
    document = pymupdf.open()
    for _page_number in range(3):
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
        rows = CLAIMS + (("", "", "", "", "outstanding for 128500 days", "", ""),)
        for row in rows:
            for offset, cell in zip(COLUMNS, row):
                if cell:
                    page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
            y += LINE
    path = tmp_path / "merged-repeated-across-pages.pdf"
    document.save(path)
    document.close()

    result = run_pipeline(path, use_vision=False)
    matching = [
        row for row in result.document.unplaced_rows
        if any("128500" in value for value in row.ambiguous_values.values())
    ]
    seen_pages = sorted(row.page for row in matching)
    assert seen_pages == [1, 2, 3], (
        f"the number did not survive on every page: {result.document.unplaced_rows}"
    )
    assert "128500" not in _descriptions(result.document.claims)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# Preserved from correction-8: cross-cell whole-unit cases, merged date
# fragments, and genuinely unmerged labelled dates. Re-asserted here so
# this file stands on its own.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "before, value, after",
    [
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
    ],
    ids=[
        "held-for-500-days", "held-for-45000-days", "outstanding-for-9400-days",
        "outstanding-for-128500-days", "number-of-9400-claims", "entry-500-days",
        "entry-9400-claims", "incurred-500-days", "outstanding-label-9400-claims",
        "case-reserve-of-500-days", "adjuster-estimate-9400-claims",
    ],
)
def test_cross_cell_whole_unit_case_still_survives(before, value, after):
    row = _raw(["", "", "", before, value, after, ""], line=100)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, value)


def test_merged_count_and_date_fragment_still_stays_unresolved():
    row = _raw(["", "", "", "Date of report", "4 5/23/2023", "", ""], line=101)
    claims, _warnings, unplaced = _build([row])
    matches = [
        item for item in unplaced
        if "4 5/23/2023" in list(item.amounts.values()) + list(item.ambiguous_values.values())
    ]
    assert matches, f"the merged count+date fragment disappeared: {unplaced}"
    assert matches[0].page == 1 and matches[0].row == 101, matches
    assert "4 5/23/2023" not in _descriptions(claims)


def test_genuinely_unmerged_date_is_still_exempted():
    from core.pipeline import unplaced_evidence

    row = _raw(["", "", "", "Date of report", "5/23/2023", "", ""], line=102)
    evidence = unplaced_evidence(row, MAPPING, "us", context="claims")
    assert not evidence, f"a genuine, unmerged date was no longer exempted: {evidence}"
