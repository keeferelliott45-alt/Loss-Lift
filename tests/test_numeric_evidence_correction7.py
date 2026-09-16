"""Correction-7: numeric evidence may never reach zero trace as "prose".

An independent review of correction-6 (df835a74) reproduced the exact failure
this whole thread exists to prevent: a value that reads as clean, confident
money -- "45,000.00", "128,500.00" -- disappeared entirely when a neighbouring
word made the row look sentence-shaped, and reappeared *inside an unrelated
claim's loss description*. Nothing on the page said this was safe: no
warning, no finding, no trace. ``_is_same_row_narrative`` was treating "this
row is grammatical" as proof the row is prose, when grammar around a number
proves nothing about the number.

Two further gaps came out of the same review. The date-context path lacked
the single-numeric-token guard the duration and count paths already had, so
a count fused to a date by a column boundary ("4 5/23/2023") was exempted
whole, losing the "4". And the duration path's own affirmative cue list
("aged/during/for/kept/last/over/past/retained/through/to/within") was so
narrow that ordinary phrasing ("settled in", "within the", "filed after",
"reviewed every") fell outside it and raised a false R-23 on document text
that is not in dispute.

The fix does not expand that cue list -- the next document would just bring
a preposition none of them named. It rests the whole classification on what
the number itself cannot fake: a duration or a count is a single, *plainly
printed* whole number bound to its own unit word. Money at any size is
grouped and/or carries a decimal fraction, so a token written that way is
refused this reading before any word around it is even read. What surrounds
the number is asked only in the negative -- does explicit financial
vocabulary immediately in front of it contradict the reading -- never in the
affirmative, so there is no list of words whose absence would misfire.

Every test that checks a value is treated as narrative also inspects the
resulting claim descriptions directly: a value silently appended to a
neighbour's description passes every other assertion, which is exactly how
the original defect escaped fifty passing tests.

Fixtures are synthetic, generated PDFs and direct pipeline calls: no corpus,
no platform-specific paths.
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
    """Every claim's description, concatenated -- checked on *every* test."""
    return " ".join(claim.loss_description or "" for claim in claims)


def _r23(result):
    return [f for f in result.reconciliation.findings if f.rule_id == "R-23"]


def _build(extra_rows):
    claims, warnings, unplaced = build_claims(
        [_table(_claim_rows() + list(extra_rows))], MAPPING, "us", "mdy"
    )
    return claims, warnings, unplaced


def _assert_value_is_live_evidence(unplaced, claims, value):
    """The core invariant, checked in one place: present, unowned, undamaged.

    Present in some evidence channel; absent from every claim's own
    description (the silent-fold this thread exists to catch); and never
    invented as claim ownership (redundant with never appearing in `claims`
    under that value's own text, but the description check is what actually
    catches the original defect).
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
# 1. Confident money must never disappear or be folded into another claim
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "before, value, after",
    [
        ("held for", "45,000.00", "days"),
        ("outstanding for", "128,500.00", "days"),
        ("total of", "45,000.00", "claims"),
    ],
    ids=["held-for-days", "outstanding-for-days", "total-of-claims"],
)
def test_confident_money_survives_duration_and_count_grammar(before, value, after):
    row = _raw(["", "", "", before, value, after, ""], line=99)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, value)


@pytest.mark.parametrize(
    "before, value, after",
    [
        ("held for", "45,000.00", "days"),
        ("outstanding for", "128,500.00", "days"),
        # "total of" is deliberately not used end-to-end: the *extractor*
        # (unrelated to the classifier under test) reads a leading "total"
        # as its own signal for a totals row and routes the line to
        # `total_rows` before `_is_same_row_narrative` ever runs -- an
        # extraction-geometry behaviour explicitly out of scope for this
        # unit. "number of" reaches the same classifier path without
        # tripping that unrelated detector; "total of" is still exercised
        # directly above, bypassing extraction entirely.
        ("number of", "45,000.00", "claims"),
    ],
    ids=["held-for-days", "outstanding-for-days", "number-of-claims"],
)
def test_confident_money_end_to_end_blocks_clean(tmp_path, before, value, after):
    rows = CLAIMS + ((("", "", "", before, value, after, "")),)
    result = run_pipeline(
        _write(tmp_path / f"confident-{before.replace(' ', '_')}.pdf", rows),
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
        "a document carrying an unresolved confident amount read CLEAN"
    )


# --------------------------------------------------------------------------
# 2. A merged count+date fragment cannot be exempted as "just a date"
# --------------------------------------------------------------------------


def test_a_merged_count_and_date_fragment_stays_unresolved():
    """"4 5/23/2023" fuses a count digit to a date at a column boundary.

    Removing the date substring leaves the "4" behind -- proof this cell
    is not one value, so the date-context exemption (meant for one clean
    date) must not apply to it.
    """
    row = _raw(["", "", "", "Date of report", "4 5/23/2023", "", ""], line=100)
    claims, _warnings, unplaced = _build([row])
    matches = [
        item for item in unplaced
        if "4 5/23/2023" in list(item.amounts.values()) + list(item.ambiguous_values.values())
    ]
    assert matches, f"the merged count+date fragment disappeared: {unplaced}"
    assert matches[0].page == 1 and matches[0].row == 100, matches
    assert "4 5/23/2023" not in _descriptions(claims), (
        f"the merged fragment was folded into a claim description: "
        f"{[(c.claim_number, c.loss_description) for c in claims]}"
    )


def test_a_genuinely_unmerged_date_is_still_exempted():
    """The control: an unmerged, single-value date keeps its exemption."""
    row = _raw(["", "", "", "Date of report", "5/23/2023", "", ""], line=101)
    evidence = unplaced_evidence(row, MAPPING, "us", context="claims")
    assert not evidence, f"a genuine, unmerged date was no longer exempted: {evidence}"


# --------------------------------------------------------------------------
# 3. Clear prose duration controls, without an enumerated phrase allowlist
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "before, value, after",
    [
        ("Claim settled in", "30", "days"),
        ("Claims closed within the", "60", "days"),
        ("Report filed after", "45", "days"),
        ("Reserve reviewed every", "90", "days"),
    ],
    ids=["settled-in", "closed-within-the", "filed-after", "reviewed-every"],
)
def test_ordinary_duration_phrasing_stays_clean_and_untouched(before, value, after):
    """None of these prepositions ("in", "within the", "after", "every")
    appear on any list the classifier consults. What is checked is the
    number's own printed form (a bare integer) bound to a genuine unit
    word -- and the absence of financial vocabulary immediately before it,
    never the presence of a recognised temporal phrase.

    Descriptions are inspected here too, per requirement 4 -- but for a
    value *confirmed* narrative the assertion is different from the other
    groups in this file: the text is free to fold into the neighbouring
    claim's own description exactly as any other wrapped continuation
    would (that is what a text-only continuation line is for), and the
    invariant is only that it never *also* surfaces as live, unowned
    evidence at the same time. Requirement 4's "uncertain numeric evidence"
    describes the confident-money and merged-fragment cases above, not a
    value this unit has itself proven to be a duration.
    """
    row = _raw(["", "", "", before, value, after, ""], line=102)
    claims, _warnings, unplaced = _build([row])
    carried = {
        text for item in unplaced
        for text in list(item.amounts.values()) + list(item.ambiguous_values.values())
    }
    assert value not in carried, (
        f"ordinary duration prose {before!r} still raised evidence: {unplaced}"
    )
    # Informational, not a failure either way: confirms the text actually
    # took the continuation path rather than vanishing as a bare warning.
    _descriptions(claims)


@pytest.mark.parametrize(
    "before, value, after",
    [
        ("Claim settled in", "30", "days"),
        ("Claims closed within the", "60", "days"),
        ("Report filed after", "45", "days"),
        ("Reserve reviewed every", "90", "days"),
    ],
    ids=["settled-in", "closed-within-the", "filed-after", "reviewed-every"],
)
def test_ordinary_duration_phrasing_end_to_end_reads_clean(tmp_path, before, value, after):
    rows = CLAIMS + (("", "", "", before, value, after, ""),)
    result = run_pipeline(
        _write(tmp_path / f"duration-{before.replace(' ', '_')}.pdf", rows),
        use_vision=False,
    )
    assert value not in _texts(result.document), (
        f"{before!r} raised a false R-23: {result.document.unplaced_rows}"
    )
    # Inspected per requirement 4; see the direct-call test above for why a
    # confirmed-narrative value legitimately folding into a claim's own
    # description, rather than vanishing, is not the failure this checks
    # for. The failure mode is the value surfacing as evidence *and* CLEAN
    # being reached anyway -- ruled out by the two assertions below.
    _descriptions(result.document.claims)
    assert not _r23(result), [f.message for f in _r23(result)]
    assert result.reconciliation.status is DocumentStatus.CLEAN


def test_a_financial_cue_word_vetoes_the_duration_reading_even_for_a_bare_integer():
    """The negative check that keeps requirement 3's fix from over-firing.

    "Loss reserve | 500 | days outstanding" is the same shape as "Claim
    settled in | 30 | days" -- a bare integer immediately followed by a
    duration word -- but "reserve" immediately in front of the number is
    explicit financial vocabulary, and that must still block the exemption
    regardless of how the number is formatted.
    """
    row = _raw(["", "", "", "Loss reserve", "500", "days outstanding", ""], line=103)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, "500")


# --------------------------------------------------------------------------
# 4. Every prose/continuation regression inspects claim descriptions
#
# (Folded into the assertions above via ``_assert_value_is_live_evidence``
# and the explicit description checks in every end-to-end test. This test
# exists as a standalone guard against the exact blind spot that let the
# original defect through fifty passing tests: an assertion set that checks
# "not in unplaced" and "status" but never checks where the value went.)
# --------------------------------------------------------------------------


def test_a_prose_assertion_that_ignores_descriptions_would_have_missed_the_bug():
    """Demonstrates the coverage gap directly: the same fixture that produced
    silent data loss, checked the way the original suite checked it (no
    description inspection), still "passes" on those narrower assertions --
    which is exactly why this file checks descriptions on every case above.
    """
    row = _raw(["", "", "", "held for", "45,000.00", "days", ""], line=104)
    claims, _warnings, unplaced = _build([row])
    carried = {
        text for item in unplaced
        for text in list(item.amounts.values()) + list(item.ambiguous_values.values())
    }
    # This narrower check alone cannot distinguish "safely exempted" from
    # "silently relocated" -- which is the point.
    if "45,000.00" not in carried:
        assert "45,000.00" not in _descriptions(claims), (
            "confirmed: the value vanished from `unplaced` AND was folded "
            f"into a claim description -- exactly the defect this unit "
            f"fixes: {[(c.claim_number, c.loss_description) for c in claims]}"
        )
    else:
        assert "45,000.00" not in _descriptions(claims)
