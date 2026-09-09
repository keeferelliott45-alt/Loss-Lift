"""Refusing a value is not the same as erasing it, and neither is a guess.

The hardening commit made unresolved evidence block the badge. Five holes in
it survived, and every one of them is the same mistake in a different place:
something the reader could not resolve was made to disappear, or something it
had not established was treated as settled.

**A refusal is not a disposal.** ``"4 30,000.00"`` is a claim count fused to an
amount by a column boundary. Reading it as 430,000 would invent a figure, so it
is refused -- and then it was dropped on the floor, leaving the page with one
fewer printed number than it has. The refusal was right; the silence was not.

**A label speaks for its own token, not for the row.** A row reading
``Software version 1.20 ... $500.00`` says what the 1.20 is. It says nothing
whatever about the $500 beside it, and letting the word "version" erase the
whole row loses real money to a string match.

**Mapped-only is not monetary-only.** A table whose *mapped* fields are all
money may still have populated columns nobody mapped -- which is exactly the
case where the mapping is least trustworthy. Concluding "every number here is
money" from the fields that happened to map is circular.

**Vision counts are counts.** A scanned page reports its claim count like any
other page, and the digital path spent a whole commit learning not to adopt a
section's count as the report's. The vision path took the first count it was
handed, whatever its scope, and short-circuited all of it.

**Provenance is a fact or it is unknown.** An unreadable document total row has
a page and a row; the finding printed ``row-None`` for it. Where the reading
genuinely has no row -- a vision result that reports a figure without one --
that must be *said*, not papered over with a number nobody measured.

Everything here is synthetic and in-memory: no PDF, no corpus, no
platform-specific path.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.pipeline import (
    ColumnMapping,
    build_claims,
    table_money_context,
    unplaced_evidence,
)
from core.reconcile import reconcile
from core.schema import (
    Claim,
    DocumentStatus,
    LossRunDocument,
    RawRow,
    RawTable,
    Severity,
    assign_row_ids,
)

CLAIMS_HEADERS = ["Claim Number", "Loss Date", "Status", "Note", "Paid Total"]
CLAIMS_MAPPING = ColumnMapping(
    headers=CLAIMS_HEADERS,
    fields={0: "claim_number", 1: "date_of_loss", 2: "claim_status",
            3: None, 4: "paid_total"},
)


def _row(cells, page=1, line=0):
    return RawRow(cells=list(cells), page=page, line_index=line)


def _claims_table(rows, page=1):
    return RawTable(
        page=page, headers=list(CLAIMS_HEADERS), rows=list(rows), strategy="words"
    )


def _claim(number, page=1, row=1, **kw):
    claim = Claim(claim_number=number, source_page=page, source_row=row, **kw)
    assign_row_ids([claim])
    return claim


def _document(**kw) -> LossRunDocument:
    kw.setdefault("source_filename", "synthetic.pdf")
    kw.setdefault("file_sha256", "synthetic")
    kw.setdefault("valuation_date", "2024-10-03")
    return LossRunDocument(**kw)


def _unplaced(rows, page=1):
    _c, _w, unplaced = build_claims(
        [_claims_table(rows, page=page)], CLAIMS_MAPPING,
        locale="us", date_order="mdy",
    )
    return unplaced


#: A populated claim row, so the table establishes itself as carrying claims.
_A_REAL_CLAIM = _row(["CLM-1", "01/05/2024", "Closed", "", "1,000.00"], line=1)


# --------------------------------------------------------------------------
# 2. Ambiguous merged text may be refused, but not erased
# --------------------------------------------------------------------------


def test_merged_numeric_text_survives_as_unresolved_evidence():
    """"4 30,000.00" is two values a column boundary ran together.

    Refusing it is correct -- ``parse_money`` would return 430,000, a figure
    printed nowhere. But the row then vanished entirely, and a page's only
    unattached number went with it.
    """
    unplaced = _unplaced([_A_REAL_CLAIM, _row(["", "", "", "", "4 30,000.00"], line=2)])
    assert unplaced, "the merged cell was erased"
    row = unplaced[0]
    assert row.amounts == {}, "merged text was parsed as an amount"
    assert row.ambiguous_values.get("paid_total") == "4 30,000.00", row
    assert Decimal("430000") not in row.parsed_amounts.values()


def test_a_document_carrying_merged_text_cannot_be_clean():
    """It is evidence, so it blocks, like every other unresolved value."""
    unplaced = _unplaced([_A_REAL_CLAIM, _row(["", "", "", "", "4 30,000.00"], line=2)])
    doc = _document(claims=[_claim("SYN-1")], unplaced_rows=unplaced)
    result = reconcile(doc)
    assert result.status is DocumentStatus.NEEDS_REVIEW
    finding = next(f for f in result.findings if f.rule_id == "R-23")
    assert "4 30,000.00" in finding.message, finding.message
    assert "430,000" not in finding.message and "430000" not in finding.message


# --------------------------------------------------------------------------
# 3. A non-money label speaks only for its own token
# --------------------------------------------------------------------------


def test_a_version_label_does_not_erase_the_money_beside_it():
    """"Software version 1.20" says what 1.20 is. The $500.00 is untouched."""
    evidence = unplaced_evidence(
        _row(["", "", "", "Software version 1.20", "$500.00"], line=2),
        CLAIMS_MAPPING, "us", context="claims",
    )
    carried = {**evidence.amounts, **evidence.ambiguous}
    assert "paid_total" in carried, (
        f"the label erased an unrelated monetary cell: {evidence}"
    )
    assert carried["paid_total"][0] == "$500.00"


def test_the_money_beside_a_version_label_still_blocks():
    """Whether it is read as money or left unresolved, it must not vanish."""
    unplaced = _unplaced([
        _A_REAL_CLAIM,
        _row(["", "", "", "Software version 1.20", "$500.00"], line=2),
    ])
    assert unplaced, "the row disappeared"
    doc = _document(claims=[_claim("SYN-1")], unplaced_rows=unplaced)
    result = reconcile(doc)
    assert result.status is DocumentStatus.NEEDS_REVIEW
    assert any("500.00" in f.message for f in result.findings if f.rule_id == "R-23")


def test_a_label_still_covers_the_value_it_names():
    """The guard the rule exists for is unchanged: alone on its row, the
    version is not money -- and there is nothing left unresolved about it.

    An intervening unit kept the value as unresolved on the reasoning that
    "explained" is not "absent". Both channels are reported, so that put a row
    the document had already explained onto the exceptions list. The label
    covers the one figure beside it; neither channel takes it.
    """
    evidence = unplaced_evidence(
        _row(["", "", "", "Software version", "1.20"], line=2),
        CLAIMS_MAPPING, "us", context="claims",
    )
    assert evidence.amounts == {}, evidence
    assert evidence.ambiguous == {}, evidence


# --------------------------------------------------------------------------
# 4. Mapped-only is not monetary-only
# --------------------------------------------------------------------------


def test_unmapped_populated_columns_deny_a_monetary_only_reading():
    """"Reference | Paid Total" maps one field, and it is money. That is not
    evidence that the table is monetary.

    The unmapped "Reference" column is populated on every row, which is
    precisely the state in which the mapping is least trustworthy. Reading
    "every mapped field is money" as "every number here is money" concludes
    from the columns that happened to map that the ones that did not are
    irrelevant.
    """
    headers = ["Reference", "Paid Total"]
    mapping = ColumnMapping(headers=headers, fields={1: "paid_total"})
    table = RawTable(
        page=1, headers=headers, strategy="words",
        rows=[_row(["Serial", "45292.00"], line=3)],
    )
    assert table_money_context(table, mapping, set()) != "monetary-only"
    evidence = unplaced_evidence(
        _row(["Serial", "45292.00"], line=3), mapping, "us",
        context=table_money_context(table, mapping, set()),
    )
    assert evidence.amounts == {}, f"45292.00 was called money: {evidence}"
    assert evidence.ambiguous.get("paid_total", (None,))[0] == "45292.00"


def test_a_genuinely_monetary_only_table_is_still_read_as_money():
    """The case the classification exists for must survive the correction:
    every populated column is mapped, and every mapped column is money."""
    headers = ["Paid Total", "Incurred Total"]
    mapping = ColumnMapping(
        headers=headers, fields={0: "paid_total", 1: "incurred_total"}
    )
    table = RawTable(
        page=1, headers=headers, strategy="words",
        rows=[_row(["9400", "9400"], line=3)],
    )
    assert table_money_context(table, mapping, set()) == "monetary-only"


# --------------------------------------------------------------------------
# 1. Vision counts obey the same scope rules as digital ones
# --------------------------------------------------------------------------


def test_vision_counts_are_evidence_and_never_a_document_total():
    """A scanned page's count carries no wording, so it settles nothing.

    These three tests previously asserted that a single count, or the same
    count repeated, could become the document's. Neither can. The digital path
    tells a section's count from the report's by reading the words around the
    number -- "Report Totals:" over "# Claims: 50" -- and the vision schema
    returns the number alone. Three sections of three claims each agree at
    three while the document holds nine; agreement is the same coincidence the
    digital path already refuses. Extending the schema to carry that wording is
    the real fix and is deliberately not attempted here, so the honest answer
    is that the count is unresolved.
    """
    from core.pipeline import vision_claim_evidence

    def tables(*counts):
        return [
            RawTable(page=page, headers=[], strategy="vision",
                     printed_claim_count=count)
            for page, count in enumerate(counts, start=1)
        ]

    # Distinct, repeated and single alike: evidence, with its pages, and never
    # a document total.
    assert vision_claim_evidence(tables(3, 2, 1)) == [
        {"page": 1, "count": 3}, {"page": 2, "count": 2}, {"page": 3, "count": 1}
    ]
    assert vision_claim_evidence(tables(3, 3, 3)) == [
        {"page": 1, "count": 3}, {"page": 2, "count": 3}, {"page": 3, "count": 3}
    ]
    assert vision_claim_evidence(tables(6)) == [{"page": 1, "count": 6}]
    assert vision_claim_evidence(tables(None)) == []


def test_unresolved_vision_counts_are_preserved_and_require_review():
    """Every count is kept with the page it came from, and R-27 reports it."""
    doc = _document(
        claims=[_claim("SYN-%d" % i, row=i) for i in range(1, 4)],
        printed_claim_count=None,
        printed_count_evidence=[
            {"page": 1, "count": 3}, {"page": 2, "count": 2}, {"page": 3, "count": 1},
        ],
    )
    result = reconcile(doc)
    assert result.status is DocumentStatus.NEEDS_REVIEW
    finding = next(f for f in result.findings if f.rule_id == "R-27")
    assert finding.severity is Severity.ERROR
    for count, page in ((3, 1), (2, 2), (1, 3)):
        assert f"{count} on page {page}" in finding.message, finding.message


# --------------------------------------------------------------------------
# 5. Row provenance is a fact, or it is explicitly unknown
# --------------------------------------------------------------------------


def test_an_unreadable_document_total_carries_its_row():
    """The row is known here -- the reader read it off a printed line."""
    doc = _document(
        claims=[_claim("SYN-1")],
        unreadable_totals={"paid_total": "4 30,000.00"},
        unreadable_totals_page=7,
        unreadable_totals_row=21,
    )
    finding = next(f for f in reconcile(doc).findings if f.rule_id == "R-26")
    assert "row-None" not in finding.condition, finding.condition
    assert "21" in finding.condition, finding.condition
    # The reviewer-facing line counts from one, as every other "page N, line M"
    # in the product does; the finding's identity stays on the index.
    assert "page 7, line 22" in finding.message, finding.message


def test_an_unknown_total_row_says_so_rather_than_inventing_one():
    """A vision result may report a figure with no line to point at.

    Saying "row 0" would be a measurement nobody made. The finding says the
    row is unknown, and still keeps its own identity apart from any other
    column's.
    """
    doc = _document(
        claims=[_claim("SYN-1")],
        unreadable_totals={"paid_total": "4 30,000.00", "reserve_total": "1 2"},
        unreadable_totals_page=7,
        unreadable_totals_row=None,
    )
    findings = [f for f in reconcile(doc).findings if f.rule_id == "R-26"]
    assert len(findings) == 2
    assert len({f.condition for f in findings}) == 2, [f.condition for f in findings]
    for finding in findings:
        assert "row unknown" in finding.message.lower(), finding.message


def test_two_unreadable_columns_on_one_row_stay_two_findings():
    """Dismissing one column must never answer for the other."""
    doc = _document(
        claims=[_claim("SYN-1")],
        unreadable_totals={"paid_total": "4 30,000.00", "reserve_total": "1 2"},
        unreadable_totals_page=7,
        unreadable_totals_row=21,
    )
    findings = [f for f in reconcile(doc).findings if f.rule_id == "R-26"]
    assert len({f.condition for f in findings}) == 2, [f.condition for f in findings]


# --------------------------------------------------------------------------
# The hardening commit's guarantees must survive all of the above
# --------------------------------------------------------------------------


@pytest.mark.parametrize("printed", ["9400", "0.00", "0", "9400 CR", "(9400)"])
def test_whole_unit_and_zero_preservation_is_unchanged(printed):
    unplaced = _unplaced([_A_REAL_CLAIM, _row(["", "", "", "", printed], line=2)])
    assert unplaced, printed
    carried = {**unplaced[0].amounts, **unplaced[0].ambiguous_values}
    assert "paid_total" in carried, (printed, unplaced[0])


@pytest.mark.parametrize(
    "prose",
    ["Property Claim", "Wind; Named Storm", "insured with the listed policy"],
)
def test_prose_sharing_a_money_column_is_not_numeric_evidence(prose):
    """Found by the corpus, not by reasoning: preserving refused text has to
    mean refused *numeric* text.

    ``_is_smeared`` asks whether whitespace groups one number or fuses two,
    and it only ever meant anything about text already known to be numeric --
    ask it about "Property Claim" and it says True, because "Claim" is not a
    three-digit group. Consulting it before parsing turned a page's disclaimer
    paragraph into unresolved monetary evidence, one finding per line.
    """
    evidence = unplaced_evidence(
        _row(["", "", "", "", prose], line=2), CLAIMS_MAPPING, "us", context="claims"
    )
    assert not evidence, f"prose became numeric evidence: {evidence}"


def test_a_genuine_two_number_smear_is_still_evidence():
    """The guard above must not undo the fix it protects."""
    evidence = unplaced_evidence(
        _row(["", "", "", "", "4 30,000.00"], line=2),
        CLAIMS_MAPPING, "us", context="claims",
    )
    assert evidence.unreadable == {"paid_total": "4 30,000.00"}, evidence
    assert evidence.amounts == {} and evidence.ambiguous == {}
