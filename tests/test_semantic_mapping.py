"""Semantic mapping integrity: is this the right column in the right field?

Reconciliation answers a different question from the one this file asks. R-01
through R-05 establish that the numbers add up. They cannot establish that the
numbers are the ones the carrier printed under those headings, because addition
does not care which addend is which.

So the invariant here is separate and stands on its own:

    A many-to-one source-to-canonical mapping is never silently accepted.

When two columns claim one field, one of them used to be dropped to unmapped
and nothing said so. The money in it vanished, every identity still balanced
over the values that survived, and the document could be called reconciled.
"""

from __future__ import annotations

from decimal import Decimal

from core.pipeline import build_mapping
from core.reconcile import reconcile
from core.schema import (
    Claim,
    DocumentStatus,
    LossRunDocument,
    MappingState,
    Severity,
)


def _document(headers: list[str], claims: list[Claim]) -> LossRunDocument:
    mapping = build_mapping(headers)
    return LossRunDocument(
        source_filename="x.pdf", file_sha256="abc",
        valuation_date=__import__("datetime").date(2024, 12, 31),
        claims=claims, column_mapping=mapping.decisions,
    )


def _claim(**kwargs) -> Claim:
    base = dict(
        claim_number="C-1",
        date_of_loss=__import__("datetime").date(2024, 3, 1),
        incurred_total=Decimal("500.00"),
    )
    return Claim(**{**base, **kwargs})


# --------------------------------------------------------------------------
# 1. Three columns printed "Total", each meaning something different
# --------------------------------------------------------------------------


def test_three_columns_named_total_are_not_silently_collapsed():
    """One wins incurred_total; the other two carried money into nothing."""
    mapping = build_mapping(["Claim Number", "Total", "Total", "Total"])
    states = [d.state for d in mapping.decisions]
    assert states.count(MappingState.AMBIGUOUS) == 2
    contested = [d for d in mapping.decisions if d.contested_field]
    assert {d.contested_field for d in contested} == {"incurred_total"}


def test_an_ambiguous_mapping_blocks_a_clean_document():
    """The arithmetic here is beyond reproach and the document still fails."""
    document = _document(
        ["Claim Number", "Total", "Total", "Total"],
        [_claim(paid_total=Decimal("500.00"), reserve_total=Decimal("0.00"))],
    )
    result = reconcile(document)
    assert [f.rule_id for f in result.errors] == ["R-21", "R-21"]
    assert result.status is DocumentStatus.NEEDS_REVIEW


def test_the_finding_names_the_printed_header_and_the_field_at_stake():
    """A reviewer needs to know which column, and which field it wanted."""
    document = _document(
        ["Claim Number", "Paid", "Amount Paid"],
        [_claim(paid_total=Decimal("500.00"))],
    )
    finding = next(f for f in reconcile(document).findings if f.rule_id == "R-21")
    assert "Amount Paid" in finding.message
    assert finding.field == "paid_total"


# --------------------------------------------------------------------------
# 2. Two materially distinct columns claiming one field
# --------------------------------------------------------------------------


def test_two_distinct_columns_claiming_one_field_are_flagged():
    """"Paid" and "Paid to Date" are not obviously the same figure."""
    mapping = build_mapping(["Claim Number", "Paid", "Paid to Date"])
    ambiguous = [d for d in mapping.decisions if d.state is MappingState.AMBIGUOUS]
    assert len(ambiguous) == 1
    assert ambiguous[0].contested_field == "paid_total"


# --------------------------------------------------------------------------
# 3. Aliases for one field, but only one column carries it
# --------------------------------------------------------------------------


def test_one_column_per_field_is_never_ambiguous():
    """The vocabulary holds many aliases; having many is not having a clash."""
    mapping = build_mapping(
        ["Claim Number", "Date of Loss", "Total Paid", "Reserves", "Total Incurred"]
    )
    assert not [d for d in mapping.decisions if d.state is MappingState.AMBIGUOUS]
    document = _document(
        ["Claim Number", "Date of Loss", "Total Paid", "Reserves", "Total Incurred"],
        [_claim(paid_total=Decimal("500.00"), reserve_total=Decimal("0.00"))],
    )
    assert reconcile(document).status is DocumentStatus.CLEAN


# --------------------------------------------------------------------------
# 5. A flat, unambiguous carrier format raises nothing new
#
# The whole golden corpus is the real proof of this: 14 carrier formats still
# reconcile and per-carrier accuracy is unchanged. This states the intent.
# --------------------------------------------------------------------------


def test_an_ordinary_flat_format_is_not_sent_for_review():
    document = _document(
        ["Claim Number", "Date of Loss", "Status", "Paid Indemnity",
         "Paid Medical", "Paid Total", "Reserve Total", "Total Incurred"],
        [_claim(
            paid_indemnity=Decimal("100.00"), paid_medical=Decimal("400.00"),
            paid_total=Decimal("500.00"), reserve_total=Decimal("0.00"),
        )],
    )
    result = reconcile(document)
    assert "R-21" not in {f.rule_id for f in result.findings}
    assert result.status is DocumentStatus.CLEAN


# --------------------------------------------------------------------------
# 6. Arithmetically perfect, semantically swapped
# --------------------------------------------------------------------------


def _swapped_pair() -> tuple[LossRunDocument, LossRunDocument]:
    """The same claim with its two paid components exchanged.

    Indemnity 100 / medical 400 and indemnity 400 / medical 100 both sum to
    500, so R-02 holds either way, and R-01 and R-04 never see the components
    at all. The two documents are arithmetically indistinguishable and
    describe materially different claims.
    """
    headers = ["Claim Number", "Date of Loss", "Paid Indemnity", "Paid Medical",
               "Paid Total", "Reserve Total", "Total Incurred"]
    correct = _document(headers, [_claim(
        paid_indemnity=Decimal("100.00"), paid_medical=Decimal("400.00"),
        paid_total=Decimal("500.00"), reserve_total=Decimal("0.00"),
    )])
    swapped = _document(headers, [_claim(
        paid_indemnity=Decimal("400.00"), paid_medical=Decimal("100.00"),
        paid_total=Decimal("500.00"), reserve_total=Decimal("0.00"),
    )])
    return correct, swapped


def test_arithmetic_cannot_tell_a_swap_from_the_truth():
    """The point of the whole file, stated as an assertion.

    Both documents reconcile CLEAN. Nothing in the rule engine distinguishes
    them, because every identity they touch is a sum. Arithmetic validity is
    therefore not evidence of semantic validity, and must never be treated as
    though it were.
    """
    correct, swapped = _swapped_pair()
    left, right = reconcile(correct), reconcile(swapped)
    assert left.status is right.status is DocumentStatus.CLEAN
    assert [f.rule_id for f in left.findings] == [f.rule_id for f in right.findings]


def test_when_the_headers_cannot_settle_it_the_document_is_held():
    """A swap is undetectable, so the labels have to carry the meaning.

    Where they do not — two columns printed "Paid" and nothing to say which
    component each is — the engine refuses the document rather than picking
    one. This is the protection that a swap makes necessary: it cannot catch
    the swap, so it must not let an unlabelled pair through unexamined.
    """
    document = _document(
        ["Claim Number", "Date of Loss", "Paid", "Paid", "Total Incurred"],
        [_claim(paid_total=Decimal("500.00"))],
    )
    result = reconcile(document)
    assert any(f.rule_id == "R-21" and f.severity is Severity.ERROR
               for f in result.findings)
    assert result.status is DocumentStatus.NEEDS_REVIEW


def test_mapping_evidence_is_retained_for_every_column():
    """Each decision keeps the printed header and how it was reached."""
    mapping = build_mapping(["Claim Number", "Total", "Total"])
    first = mapping.decisions[0]
    assert first.source_header_raw == "Claim Number"
    assert first.source_header_normalized == "claim number"
    assert first.canonical_field == "claim_number"
    assert first.state is MappingState.DETERMINISTIC
    assert mapping.decisions[2].state is MappingState.AMBIGUOUS
