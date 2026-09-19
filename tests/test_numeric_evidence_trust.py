"""CLEAN must mean the printed evidence was checked, not merely captured.

A green badge is the product's only load-bearing claim. It says an underwriter
may price off these numbers. Every failure below let a document reach it while
something printed on the page had been read and then quietly set aside --
which is worse than reading nothing, because nothing looks like nothing and a
green badge looks like an answer.

Three families, all one theme.

**Evidence that was captured and never checked.** A subtotal nothing could
scope, a total cell the reader refused, a grand-total row whose paid column
could not be read while its reserve and incurred columns tied. Each was
reported as a warning and the document went CLEAN. A warning that nobody must
act on and a warning that invalidates the badge cannot be the same signal.

**Scope invented to make a check run.** A subtotal is only checked against
claims the document says it covers. A matching count is not that: it is one
arithmetic coincidence, and it will match a subtotal printed *above* the
claims, or match twice on a page carrying two subtotals, or match a section
that spans pages. Membership has to come from where the rows physically sit,
and where that does not settle it the section stays unscoped.

**Numbers classified by how they look.** Requiring a separator or a symbol
before a cell counts as money loses a whole-unit ``9400`` and every ``0.00``;
accepting anything numeric turns a version string, a year, an Excel date
serial and a claim identifier into money. Neither is a reading. A cell is
money when the row and its table say so, is not money when they say otherwise,
and is *unresolved* when they say nothing -- and unresolved is reported in
those words rather than resolved by guessing.

Everything here is synthetic and builds its tables in memory: no PDF, no
corpus, no platform-specific paths.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.extract_digital import document_claim_count
from core.pipeline import ColumnMapping, build_claims, scope_sections
from core.reconcile import reconcile
from core.schema import (
    Claim,
    assign_row_ids,
    DocumentStatus,
    LossRunDocument,
    PrintedSection,
    RawRow,
    RawTable,
    Severity,
)

MONEY_HEADERS = ["Claim Number", "Loss Date", "Status", "Paid Total", "Incurred Total"]
MAPPING = ColumnMapping(
    headers=MONEY_HEADERS,
    fields={0: "claim_number", 1: "date_of_loss", 2: "claim_status",
            3: "paid_total", 4: "incurred_total"},
)


def _row(cells, page=1, line=0, kind="data"):
    return RawRow(cells=list(cells), page=page, line_index=line, kind=kind)


def _table(rows, totals=(), page=1, headers=None):
    return RawTable(
        page=page,
        headers=list(headers or MONEY_HEADERS),
        rows=list(rows),
        total_rows=list(totals),
        strategy="words",
    )


def _claim(number, page=1, row=0, **kw):
    """A claim with the physical identity the pipeline assigns it."""
    claim = Claim(
        claim_number=number, source_page=page, source_row=row,
        date_of_loss=kw.pop("date_of_loss", None), **kw
    )
    assign_row_ids([claim])
    return claim


def _document(**kw) -> LossRunDocument:
    kw.setdefault("source_filename", "synthetic.pdf")
    kw.setdefault("file_sha256", "synthetic")
    kw.setdefault("valuation_date", "2024-10-03")
    return LossRunDocument(**kw)


def _status(doc) -> DocumentStatus:
    return reconcile(doc).status


def _blocking(doc):
    return [f for f in reconcile(doc).findings if f.severity is Severity.ERROR]


# --------------------------------------------------------------------------
# 1-3. Captured evidence that was never checked must not read CLEAN
# --------------------------------------------------------------------------


def test_a_partly_unreadable_grand_total_row_cannot_be_clean():
    """The grand-total row's paid cell is unreadable; reserve and incurred tie.

    The tying columns are real and R-04 is right to pass them. What must not
    happen is the third column vanishing: "4 30,000.00" is a claim count fused
    to an amount, refusing it is correct, and dropping it silently means R-04
    stops checking paid while the badge still says the document reconciles.
    Supplying two good totals does not buy the row an exemption for the third.
    """
    doc = _document(
        claims=[_claim("SYN-1", row=1, reserve_total=Decimal("100.00"),
                       incurred_total=Decimal("100.00"))],
        printed_totals={"reserve_total": Decimal("100.00"),
                        "incurred_total": Decimal("100.00")},
        unreadable_totals={"paid_total": "4 30,000.00"},
        unreadable_totals_page=1,
    )
    assert _status(doc) is DocumentStatus.NEEDS_REVIEW
    reported = [f for f in _blocking(doc) if "30,000.00" in (f.message or "")]
    assert reported, [f.message for f in reconcile(doc).findings]
    assert "paid total" in reported[0].message.lower()


def test_an_unscoped_section_subtotal_cannot_be_clean():
    """A subtotal nothing can be checked against leaves the document unproven."""
    doc = _document(
        claims=[_claim("SYN-1", row=1, incurred_total=Decimal("10.00"))],
        printed_sections=[
            PrintedSection(label="Policy Total", page=1, line_index=9,
                           printed_totals={"incurred_total": Decimal("10.00")})
        ],
    )
    assert _status(doc) is DocumentStatus.NEEDS_REVIEW
    assert any(f.rule_id == "R-25" for f in _blocking(doc))


def test_an_unreadable_section_total_cell_cannot_be_clean():
    """R-26 says a printed column is not being checked. That blocks."""
    doc = _document(
        claims=[_claim("SYN-1", row=1, incurred_total=Decimal("10.00"))],
        printed_sections=[
            PrintedSection(
                label="Policy Total", page=1, line_index=9,
                printed_claim_count=1, covers_rows=["p1r1"], scope_known=True,
                printed_totals={"incurred_total": Decimal("10.00")},
                unreadable_totals={"paid_total": "4 30,000.00"},
            )
        ],
    )
    assert _status(doc) is DocumentStatus.NEEDS_REVIEW
    assert any(f.rule_id == "R-26" for f in _blocking(doc))


# --------------------------------------------------------------------------
# 4-6. Scope may not be invented
# --------------------------------------------------------------------------


def test_a_matching_page_count_alone_does_not_establish_membership():
    """Two claims on the page and a subtotal saying two is a coincidence.

    The subtotal here belongs to a policy whose other claims are on another
    page; the count agrees by accident. Nothing about the page says these two
    rows are the ones it totals.
    """
    section = PrintedSection(
        label="Policy B Total", page=1, line_index=2, printed_claim_count=2,
        printed_totals={"incurred_total": Decimal("999.00")},
    )
    claims = [_claim("SYN-1", row=5), _claim("SYN-2", row=6)]
    scope_sections([section], claims)
    assert section.scope_known is False, section.covers_rows
    assert section.covers_rows == []


def test_a_subtotal_printed_above_its_page_s_claims_scopes_nothing():
    """A total cannot total rows printed after it.

    The reproduced failure: a subtotal on line 2 with count 2, and two claims
    on lines 5 and 6, was handed those two claims and passed R-25 against
    figures that describe a different set entirely.
    """
    section = PrintedSection(
        label="Total", page=1, line_index=2, printed_claim_count=2,
        printed_totals={"incurred_total": Decimal("50.00")},
    )
    claims = [_claim("SYN-1", row=5, incurred_total=Decimal("10.00")),
              _claim("SYN-2", row=6, incurred_total=Decimal("40.00"))]
    scope_sections([section], claims)
    assert section.scope_known is False
    doc = _document(claims=claims, printed_sections=[section])
    assert not [
        f for f in reconcile(doc).findings
        if f.rule_id == "R-25" and f.condition.startswith("mismatch")
    ], "a subtotal above its claims was checked against them"
    assert _status(doc) is DocumentStatus.NEEDS_REVIEW


def test_two_subtotals_on_one_page_cannot_both_take_the_same_claims():
    """Each subtotal totals what lies between it and the one before it."""
    first = PrintedSection(label="Total A", page=1, line_index=9,
                           printed_claim_count=2,
                           printed_totals={"incurred_total": Decimal("30.00")})
    second = PrintedSection(label="Total B", page=1, line_index=10,
                            printed_claim_count=2,
                            printed_totals={"incurred_total": Decimal("30.00")})
    claims = [_claim("SYN-1", row=1, incurred_total=Decimal("10.00")),
              _claim("SYN-2", row=2, incurred_total=Decimal("20.00"))]
    scope_sections([first, second], claims)
    assert not (first.covers_rows and second.covers_rows and
                first.covers_rows == second.covers_rows), (
        f"both subtotals claimed the same rows: {first.covers_rows}"
    )
    assert second.scope_known is False, second.covers_rows


def test_a_subtotal_over_its_own_claims_is_still_scoped():
    """The guard must not cost the case it exists to serve.

    AIG's shape: the claims, then the subtotal beneath them saying how many.
    """
    section = PrintedSection(label="Pol-Asco-Mod: 000 Claim Count = 2",
                             page=1, line_index=20, printed_claim_count=2,
                             printed_totals={"incurred_total": Decimal("30.00")})
    claims = [_claim("SYN-1", row=9, incurred_total=Decimal("10.00")),
              _claim("SYN-2", row=12, incurred_total=Decimal("20.00"))]
    scope_sections([section], claims)
    assert section.scope_known is True
    assert section.covers_rows == ["p1r9", "p1r12"]
    doc = _document(claims=claims, printed_sections=[section])
    assert not [f for f in reconcile(doc).findings if f.rule_id == "R-25"]


# --------------------------------------------------------------------------
# 7-8. Counts: no arithmetic coincidence, and no silent discard
# --------------------------------------------------------------------------


def test_accidental_count_arithmetic_does_not_make_a_document_total():
    """1, 2 and 3 on three pages: 3 happens to be 1+2 and totals nothing.

    Three policy sections of one, two and three claims sum to six. Reading 3
    as the report's count reports six correctly extracted claims as a
    discrepancy, on the rule that checks against what the carrier printed.
    """
    assert document_claim_count({
        1: "Policy A\nClaim Count = 1",
        2: "Policy B\nClaim Count = 2",
        3: "Policy C\nClaim Count = 3",
    }) is None


def test_a_report_total_still_establishes_the_document_count():
    """Illinois's evidence: the label says the scope, not the arithmetic."""
    assert document_claim_count({
        7: "# Claims: 8 $11,233.08\nReport Totals:\n# Claims: 50 $358,193.05",
    }) == 50


def test_ambiguous_counts_are_preserved_and_require_review():
    """Discarding the counts is as wrong as adopting one of them.

    Where the document states several counts and none is the report's, no
    count can be checked -- but the reader has still read numbers the carrier
    printed about how many claims there are, and dropping them leaves nothing
    to say why R-05 never ran.
    """
    doc = _document(
        claims=[_claim("SYN-1", row=1)],
        printed_claim_count=None,
        printed_count_evidence=[{"page": 1, "count": 1}, {"page": 2, "count": 2}],
    )
    assert _status(doc) is DocumentStatus.NEEDS_REVIEW
    unresolved = [f for f in _blocking(doc) if "count" in (f.message or "").lower()]
    assert unresolved, [f.message for f in reconcile(doc).findings]


# --------------------------------------------------------------------------
# 9-11. What a number is, decided by context and never by shape
# --------------------------------------------------------------------------


def _unplaced_for(rows, headers=None, mapping=None, page=1):
    table = _table(rows, page=page, headers=headers)
    _claims, _warnings, unplaced = build_claims(
        [table], mapping or MAPPING, locale="us", date_order="mdy"
    )
    return unplaced


@pytest.mark.parametrize(
    "printed",
    ["9400", "9400 CR", "(9400)", "0.00", "0"],
)
def test_money_in_an_established_monetary_table_never_disappears(printed):
    """A monetary table's own column is the context. Shape adds nothing.

    ``9400`` has no separator and ``0.00`` is zero; both were dropped, and a
    document whose only unattached figure was one of them went CLEAN with no
    evidence at all that anything had been read and set aside.
    """
    unplaced = _unplaced_for([
        _row(["CLM-1", "01/05/2024", "Closed", "1,000.00", "1,000.00"], line=1),
        _row(["", "", "", printed, ""], line=2),
    ])
    assert unplaced, f"{printed!r} disappeared"
    row = unplaced[0]
    carried = {**row.amounts, **row.ambiguous_values}
    assert "paid_total" in carried, carried
    assert carried["paid_total"] == printed


def test_identical_whole_unit_rows_repeated_across_pages_all_survive():
    """Repetition is what a running footer looks like -- and also what a
    spreadsheet continuation looks like. A row carrying figures no claim took
    is evidence on every page it appears on, not furniture."""
    tables = [
        _table(
            [_row(["CLM-%d" % page, "01/05/2024", "Closed", "500.00", "500.00"],
                  page=page, line=1),
             _row(["", "", "", "9400", ""], page=page, line=2)],
            page=page,
        )
        for page in (1, 2, 3)
    ]
    _claims, _warnings, unplaced = build_claims(
        tables, MAPPING, locale="us", date_order="mdy"
    )
    assert {row.page for row in unplaced} == {1, 2, 3}, [
        (r.page, r.amounts, r.ambiguous_values) for r in unplaced
    ]


@pytest.mark.parametrize(
    "label, printed",
    [
        ("Software version", "1.20"),
        ("Policy year", "2024.00"),
        ("Excel serial date", "45292.00"),
        ("Claim identifier", "12,345"),
    ],
)
def test_contextually_established_non_money_does_not_become_money(label, printed):
    """The row says what the number is. Formatting and column do not override it.

    ``2024.00`` and ``45292.00`` both parse and both sit under a money column,
    and both were reported as money that could not be placed -- a figure the
    carrier never printed as a figure at all.
    """
    unplaced = _unplaced_for([
        _row(["CLM-1", "01/05/2024", "Closed", "1,000.00", "1,000.00"], line=1),
        _row([label, "", "", printed, ""], line=2),
    ])
    # Neither channel. An intervening unit weakened this to "not money, but
    # kept as unresolved", on the reasoning that the label might be naming a
    # different figure on the row. It cannot: the row holds one figure, the
    # label sits beside it, and there is nothing left for the label to be
    # about. What that weakening bought was an exceptions list carrying every
    # row the document had already explained, and an exceptions list nobody
    # finishes reading is one nobody reads.
    carried = [
        {**row.amounts, **row.ambiguous_values} for row in unplaced
    ]
    assert not any("paid_total" in c and c["paid_total"] == printed for c in carried), (
        f"{label} {printed!r} was read as money: {carried}"
    )


def test_ambiguous_numbers_are_kept_neutrally_and_require_review():
    """Neither money nor discarded: unresolved, said in those words.

    A bare number under a money column, on a row with nothing else on it, in a
    table that has not established itself as monetary. It may be an amount. It
    may be a page number the column boundary caught. The reading does not know,
    and saying either would be a guess.
    """
    headers = ["Reference", "Note", "Amount"]
    mapping = ColumnMapping(headers=headers, fields={0: "claim_number", 2: "paid_total"})
    unplaced = _unplaced_for(
        [_row(["", "", "9400"], line=2)], headers=headers, mapping=mapping
    )
    assert unplaced, "ambiguous numeric evidence was discarded"
    row = unplaced[0]
    assert row.ambiguous_values.get("paid_total") == "9400", row
    assert "paid_total" not in row.amounts, "an unresolved value was called money"

    doc = _document(claims=[_claim("SYN-1", row=1)], unplaced_rows=[row])
    findings = [f for f in reconcile(doc).findings if f.rule_id == "R-23"]
    assert findings, "unresolved evidence raised nothing"
    message = findings[0].message.lower()
    assert "9400" in findings[0].message
    assert "could not be attached to any claim" not in message, findings[0].message
    assert _status(doc) is DocumentStatus.NEEDS_REVIEW
    assert findings[0].claim_number is None, "unresolved evidence was given an owner"
