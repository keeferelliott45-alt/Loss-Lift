"""Correction-10: list-marker text patterns are retired as an evidence exemption.

A final integration review of correction-9 (154e4d6) adversarially tested the
one remaining exemption path the unit had left untouched: list markers. It
found the identical defect class, one door over. ``_LIST_ITEM`` matches
``(500)``, ``(9400)``, ``500.``, and ``9400)`` exactly as readily as it
matches a genuine ``(4)`` -- the pattern is purely syntactic, with no regard
for magnitude, and parentheses are also valid accounting-negative notation
(spec section 4: "(1,234.56) negative, accounting style"). Combined with
``len(_words(span)) >= 10``, any of these shapes followed by ten or more
words of surrounding text -- exactly the layout a real disclaimer paragraph
produces, and exactly the layout column bleed produces when a note column's
text drifts into a money column's slack (correction-9's own review of
``assign_to_columns``) -- is exempted, refused both evidence channels, and
silently folded into an unrelated claim's own description. This reproduces
the identical failure mode correction-7 through correction-9 fixed for
duration and count grammar, via a mechanism that predates and was left
untouched by every one of those units.

This unit removes the list-marker exemption entirely. It is not replaced by
a magnitude threshold, a word-count threshold, a phrase allowlist, a
financial-word denylist, a numeric-format heuristic, or an assumed
enumeration sequence -- each of those was tried, in one guise or another,
for duration and count grammar, and each only relocated which shape of
adjacent text got trusted rather than fixing the underlying problem. A
digit-bearing candidate in a mapped money column that opens with ``(N)``,
``N.``, or ``N)`` now stays unresolved unless one of the two narrower
mechanisms left standing proves otherwise: the cell is entirely a page
marker, or it is a date whose own shape proves it. The existing
``(4) any unauthorized ...`` disclaimer control loses its exemption along
with the adversarial cases -- there is no way to trust one without trusting
the other, since both look identical from inside the classifier.

Every regression inspects the resulting claim descriptions directly and is
also checked repeated across pages, so that page-furniture pooling (which
consults the same classifier) cannot erase what this unit protects either.
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
    if row is not None:
        matches = [
            item for item in unplaced
            if value in list(item.amounts.values()) + list(item.ambiguous_values.values())
        ]
        assert any(item.page == page and item.row == row for item in matches), matches


# --------------------------------------------------------------------------
# The four adversarial marker shapes, at both word-count boundaries named in
# the task: a 9-word span (already correctly live under the parent, since
# the old threshold was >= 10) and an 11-word span (wrongly exempted under
# the parent). After this unit neither number matters at all.
# --------------------------------------------------------------------------

SHAPES = ["(500)", "(9400)", "500.", "9400)"]
TAIL_9 = "any unauthorized payment or reserve adjustment applied to this"
TAIL_11 = "any unauthorized payment or reserve adjustment applied to this claim retroactively"

CASES_9 = [f"{shape} {TAIL_9}" for shape in SHAPES]
CASES_11 = [f"{shape} {TAIL_11}" for shape in SHAPES]
ALL_CASES = CASES_9 + CASES_11
CASE_IDS = [
    "500-paren-9w", "9400-paren-9w", "500-dot-9w", "9400-paren-close-9w",
    "500-paren-11w", "9400-paren-11w", "500-dot-11w", "9400-paren-close-11w",
]


@pytest.mark.parametrize("phrase", ALL_CASES, ids=CASE_IDS)
def test_marker_shaped_value_survives_as_one_complete_cell(phrase):
    row = _raw(["", "", "", "", phrase, "", ""], line=99)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, phrase, page=1, row=99)


@pytest.mark.parametrize("phrase", ALL_CASES, ids=CASE_IDS)
def test_marker_shaped_value_survives_split_across_several_cells(phrase):
    """The marker and the first word or two sit in the candidate cell, the
    rest of the sentence in the next cell -- the contiguous span still
    reaches the same word count, and correction-9's own review found this
    is exactly how a genuinely long phrase behaves once real extraction
    (not a hand-built row) gets involved.
    """
    shape, _, tail = phrase.partition(" ")
    head, _, rest = tail.partition(" ", )
    candidate = f"{shape} {head}"
    row = _raw(["", "", "", "", candidate, rest, ""], line=98)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, candidate, page=1, row=98)


@pytest.mark.parametrize("phrase", ALL_CASES, ids=CASE_IDS)
def test_marker_shaped_value_survives_repetition_across_pages(phrase):
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


def _number_in(phrase):
    """The digit run a phrase carries -- what must never disappear.

    End-to-end, a wide multi-word phrase is not guaranteed to land back as
    the exact same single cell it was written as: real column assignment
    buckets *words*, not whole strings, so a long marker-shaped phrase can
    legitimately re-segment across adjacent columns (correction-9 found the
    identical thing for duration/count phrases). The end-to-end check
    therefore asserts on the number itself, which is what the invariant
    actually protects.
    """
    import re

    return re.search(r"\d+", phrase).group()


@pytest.mark.parametrize("phrase", CASES_11, ids=CASE_IDS[4:])
def test_marker_shaped_value_end_to_end_blocks_clean(tmp_path, phrase):
    rows = CLAIMS + (("", "", "", "", phrase, "", ""),)
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in phrase)
    result = run_pipeline(
        _write(tmp_path / f"marker-{safe_name}.pdf", rows),
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
    findings = _r23(result)
    assert findings, "no R-23 finding was raised for a live, unowned phrase"
    for finding in findings:
        lowered = (finding.message or "").lower()
        assert "list" not in lowered and "marker" not in lowered and "prose" not in lowered, (
            f"R-23 wording should stay neutral about what the value is: {finding.message}"
        )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW, (
        "a document carrying an unresolved marker-shaped phrase read CLEAN"
    )


# --------------------------------------------------------------------------
# The exact existing multi-column disclaimer fixture (test_numeric_evidence_
# routing.py), with the candidate cell substituted for each adversarial
# shape. The word count here comes from the row's other four cells, exactly
# as it did for the original "(4) any unauthorized" control.
# --------------------------------------------------------------------------

DISCLAIMER_HEADERS = ["Paid Indemnity", "Paid ALAE", "Reserve Indemnity",
                      "Reserve ALAE", "Total Incurred"]
DISCLAIMER_MAPPING = ColumnMapping(
    headers=DISCLAIMER_HEADERS,
    fields={0: "paid_indemnity", 1: "paid_expense", 2: "reserve_indemnity",
            3: "reserve_expense", 4: "incurred_total"},
)


def _disclaimer_tables(candidate_cell):
    rows = [
        ["recipient will use this information", "only for its own internal",
         "purposes or for such purposes", "authorized by the insured;",
         candidate_cell],
        ["disclosure must be reported", "within the", "last 30 days.", "", ""],
    ]
    return [
        RawTable(page=1, headers=list(DISCLAIMER_HEADERS), strategy="words",
                 rows=[RawRow(cells=list(cells), page=1, line_index=i)
                       for i, cells in enumerate(rows)],
                 total_rows=[])
    ]


@pytest.mark.parametrize("shape", SHAPES, ids=["500-paren", "9400-paren", "500-dot", "9400-paren-close"])
def test_marker_shaped_value_in_the_exact_disclaimer_fixture_survives(shape):
    candidate = f"{shape} any unauthorized"
    tables = _disclaimer_tables(candidate)
    claims, _warnings, unplaced = build_claims(tables, DISCLAIMER_MAPPING, "us", "mdy")
    carried = {
        text for row in unplaced
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }
    assert candidate in carried, (
        f"{candidate!r} disappeared from the disclaimer fixture: {unplaced}"
    )
    assert "last 30 days." in carried, (
        f"the genuine duration fragment in the same fixture disappeared: {unplaced}"
    )
    assert not claims, claims


def test_the_original_list_marker_control_now_also_needs_review():
    """The existing "(4) any unauthorized ..." control from
    test_numeric_evidence_routing.py, reproduced here directly: it must
    lose its exemption along with the adversarial shapes, since nothing
    in the classifier could tell a genuine list index from a disguised
    amount -- both matched the identical pattern.
    """
    tables = _disclaimer_tables("(4) any unauthorized")
    claims, _warnings, unplaced = build_claims(tables, DISCLAIMER_MAPPING, "us", "mdy")
    carried = {
        text for row in unplaced
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }
    assert carried == {"(4) any unauthorized", "last 30 days."}, (
        f"expected both fragments to surface as evidence: {carried}"
    )
    assert not claims, claims


# --------------------------------------------------------------------------
# Preserved from correction-9: duration/count, merged date fragments,
# unmerged dates, and page markers. Re-asserted here so this file stands on
# its own.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "before, value, after",
    [
        ("held for", "45000", "days"),
        ("outstanding for", "128500", "days"),
    ],
    ids=["held-for-45000-days", "outstanding-for-128500-days"],
)
def test_duration_count_value_still_survives(before, value, after):
    row = _raw(["", "", "", before, value, after, ""], line=100)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, value)


def test_same_cell_duration_still_survives():
    row = _raw(["", "", "", "", "held for 45000 days", "", ""], line=101)
    claims, _warnings, unplaced = _build([row])
    _assert_value_is_live_evidence(unplaced, claims, "held for 45000 days")


def test_merged_count_and_date_fragment_still_stays_unresolved():
    row = _raw(["", "", "", "Date of report", "4 5/23/2023", "", ""], line=102)
    claims, _warnings, unplaced = _build([row])
    matches = [
        item for item in unplaced
        if "4 5/23/2023" in list(item.amounts.values()) + list(item.ambiguous_values.values())
    ]
    assert matches, f"the merged count+date fragment disappeared: {unplaced}"
    assert matches[0].page == 1 and matches[0].row == 102, matches
    assert "4 5/23/2023" not in _descriptions(claims)


def test_genuinely_unmerged_date_is_still_exempted():
    from core.pipeline import unplaced_evidence

    row = _raw(["", "", "", "Date of report", "5/23/2023", "", ""], line=103)
    evidence = unplaced_evidence(row, MAPPING, "us", context="claims")
    assert not evidence, f"a genuine, unmerged date was no longer exempted: {evidence}"


def test_exact_page_marker_still_exempted():
    from core.pipeline import _is_same_row_narrative

    row = _raw(["", "", "", "", "Page 5 of 12", "", ""], line=104)
    assert _is_same_row_narrative(row, MAPPING, 4), (
        "a pure page marker lost its exemption"
    )


def test_page_marker_with_extra_content_is_not_exempted():
    from core.pipeline import _is_same_row_narrative

    row = _raw(["", "", "", "", "Page 5 of 12 500", "", ""], line=105)
    assert not _is_same_row_narrative(row, MAPPING, 4), (
        "extra content riding along with a page marker was wrongly exempted"
    )
