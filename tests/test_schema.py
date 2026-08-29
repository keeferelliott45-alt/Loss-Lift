"""The schema's job is to refuse bad data at the boundary."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.schema import (
    CANONICAL_FIELDS,
    MONEY_FIELDS,
    Claim,
    ClaimStatus,
    ExtractionMethod,
    Finding,
    LossRunDocument,
    NullReason,
    Severity,
    SourceMethod,
    sum_present,
)


def test_money_rejects_floats():
    with pytest.raises(ValidationError, match="never float"):
        Claim(claim_number="C1", paid_total=1234.56)


def test_money_accepts_decimal_int_and_str():
    claim = Claim(claim_number="C1", paid_total=Decimal("1234.56"), reserve_total=5, recovery_total="2.50")
    assert claim.paid_total == Decimal("1234.56")
    assert claim.reserve_total == Decimal("5")
    assert claim.recovery_total == Decimal("2.50")


def test_claim_number_may_not_be_blank():
    with pytest.raises(ValidationError):
        Claim(claim_number="   ")


def test_claim_number_is_trimmed():
    assert Claim(claim_number=" C-1 ").claim_number == "C-1"


def test_unknown_fields_are_refused():
    with pytest.raises(ValidationError):
        Claim(claim_number="C1", total_paid=Decimal("1"))


def test_confidence_must_be_a_probability():
    with pytest.raises(ValidationError):
        Claim(claim_number="C1", field_confidence={"paid_total": 1.5})
    assert Claim(claim_number="C1", field_confidence={"paid_total": 0.85}).confidence_for("paid_total") == 0.85


def test_defaults_are_null_not_zero():
    claim = Claim(claim_number="C1")
    for field in MONEY_FIELDS:
        assert getattr(claim, field) is None, field
    assert claim.claim_status is ClaimStatus.UNKNOWN


def test_issue_tracking():
    claim = Claim(claim_number="C1", field_issues={"paid_total": NullReason.AMBIGUOUS_SEPARATOR})
    assert claim.issue("paid_total") is NullReason.AMBIGUOUS_SEPARATOR
    assert claim.issue("reserve_total") is None
    assert claim.needs_review() is True


def test_empty_cells_alone_do_not_demand_review():
    claim = Claim(claim_number="C1", field_issues={"paid_medical": NullReason.EMPTY})
    assert claim.needs_review() is False


def test_currency_must_be_iso_4217():
    doc = LossRunDocument(source_filename="a.pdf", file_sha256="x", currency="usd")
    assert doc.currency == "USD"
    with pytest.raises(ValidationError):
        LossRunDocument(source_filename="a.pdf", file_sha256="x", currency="dollars")


def test_locale_hint_is_restricted():
    with pytest.raises(ValidationError):
        LossRunDocument(source_filename="a.pdf", file_sha256="x", locale_hint="fr")


def test_page_count_cannot_be_negative():
    with pytest.raises(ValidationError):
        LossRunDocument(source_filename="a.pdf", file_sha256="x", page_count=-1)


def test_valuation_date_is_optional_at_the_schema_layer():
    # Missing valuation date is R-06's job to report, not a construction error:
    # the document still has to be shown to the user so they can fix it.
    doc = LossRunDocument(source_filename="a.pdf", file_sha256="x")
    assert doc.valuation_date is None


def test_column_total_skips_nulls():
    doc = LossRunDocument(
        source_filename="a.pdf",
        file_sha256="x",
        claims=[
            Claim(claim_number="A", paid_total=Decimal("100.50")),
            Claim(claim_number="B", paid_total=None),
            Claim(claim_number="C", paid_total=Decimal("-0.50")),
        ],
    )
    assert doc.column_total("paid_total") == Decimal("100.00")


def test_claims_by_number_groups_duplicates():
    doc = LossRunDocument(
        source_filename="a.pdf",
        file_sha256="x",
        claims=[Claim(claim_number="A"), Claim(claim_number="A"), Claim(claim_number="B")],
    )
    grouped = doc.claims_by_number()
    assert len(grouped["A"]) == 2 and len(grouped["B"]) == 1


def test_sum_present():
    assert sum_present([Decimal("1"), None, Decimal("2")]) == Decimal("3")
    assert sum_present([None, None]) is None
    assert sum_present([]) is None
    assert sum_present([Decimal("0")]) == Decimal("0")


def test_finding_round_trips():
    finding = Finding(
        rule_id="R-01",
        severity=Severity.ERROR,
        message="does not add up",
        claim_number="C1",
        field="incurred_total",
        expected=Decimal("100"),
        actual=Decimal("90"),
        delta=Decimal("10"),
    )
    assert isinstance(finding.expected, Decimal)
    assert "R-01" in str(finding)


def test_canonical_fields_all_exist_on_claim():
    for field in CANONICAL_FIELDS:
        assert field in Claim.model_fields, field


def test_document_id_is_unique():
    a = LossRunDocument(source_filename="a.pdf", file_sha256="x")
    b = LossRunDocument(source_filename="a.pdf", file_sha256="x")
    assert a.document_id != b.document_id


def test_enums_serialise_to_their_values():
    claim = Claim(claim_number="C1", source_method=SourceMethod.VISION)
    dumped = claim.model_dump(mode="json")
    assert dumped["source_method"] == "vision"
    doc = LossRunDocument(
        source_filename="a.pdf", file_sha256="x", extraction_method=ExtractionMethod.MIXED
    )
    assert doc.model_dump(mode="json")["extraction_method"] == "mixed"
