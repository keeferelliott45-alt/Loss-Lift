"""Every rule in spec section 6, against hand-built claim lists."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.reconcile import ReconcileConfig, reconcile, registered_rule_ids
from core.schema import (
    Claim,
    ClaimStatus,
    DocumentStatus,
    LossRunDocument,
    NullReason,
    Severity,
)


def build_doc(claims=None, **kwargs) -> LossRunDocument:
    """A document that is clean unless a test makes it dirty."""
    defaults = dict(
        source_filename="test.pdf",
        file_sha256="deadbeef",
        valuation_date=date(2024, 12, 31),
        policy_period_start=date(2024, 1, 1),
        policy_period_end=date(2024, 12, 31),
    )
    defaults.update(kwargs)
    return LossRunDocument(claims=claims or [], **defaults)


def good_claim(number: str = "CLM-1", **kwargs) -> Claim:
    """A claim that satisfies R-01, R-02, R-03 and R-07."""
    defaults = dict(
        claim_number=number,
        date_of_loss=date(2024, 3, 4),
        date_reported=date(2024, 3, 10),
        claim_status=ClaimStatus.OPEN,
        paid_indemnity=Decimal("100.00"),
        paid_medical=Decimal("50.00"),
        paid_expense=Decimal("25.00"),
        paid_total=Decimal("175.00"),
        reserve_indemnity=Decimal("200.00"),
        reserve_medical=Decimal("100.00"),
        reserve_expense=Decimal("50.00"),
        reserve_total=Decimal("350.00"),
        recovery_total=Decimal("25.00"),
        incurred_total=Decimal("500.00"),
    )
    defaults.update(kwargs)
    return Claim(**defaults)


def ids_for(result, rule_id: str) -> list:
    return [f for f in result.findings if f.rule_id == rule_id]


def test_a_clean_document_is_clean():
    result = reconcile(build_doc([good_claim()]))
    # R-18 stands on every document that does not state its deductible basis
    # or ALAE treatment, which is nearly all of them. It is a soft flag and
    # never blocks a clean badge; anything else here would be a real defect.
    assert [f.rule_id for f in result.findings] == ["R-18", "R-18"]
    assert result.errors == []
    assert result.status is DocumentStatus.CLEAN


def test_every_rule_is_registered():
    assert registered_rule_ids() == [f"R-{i:02d}" for i in range(1, 24)]


def test_a_document_with_no_claims_is_never_clean():
    """The worst failure available: nothing read, reported as reconciled.

    Every other rule is silent on an empty document — no rows, no arithmetic
    to check — so without R-20 an unreadable table exports a green badge and
    an empty sheet. Whether the account is genuinely loss-free or the table
    was missed is a reviewer's call, not the app's.
    """
    result = reconcile(build_doc([]))
    assert [f.rule_id for f in result.errors] == ["R-20"]
    assert result.status is DocumentStatus.NEEDS_REVIEW


def test_future_rule_with_wrong_physical_subject_fails_loudly(monkeypatch):
    import core.reconcile as engine
    from core.schema import Finding, FindingScope

    doc = build_doc([good_claim("A"), good_claim("B")])
    wrong = Finding(
        rule_id="R-22", scope=FindingScope.CLAIM,
        category="extraction",
        subject=doc.claims[1].row_id, claim_number="A",
        severity=Severity.ERROR, message="bad row association",
    )
    monkeypatch.setattr(engine, "_RULES", [("R-22", lambda d, c: [wrong])])
    with pytest.raises(ValueError, match="belongs to claim"):
        engine.reconcile(doc)


def test_future_rule_cannot_publish_duplicate_finding_identities(monkeypatch):
    import core.reconcile as engine
    from core.schema import Finding, FindingScope

    one = Finding(
        rule_id="R-22", scope=FindingScope.DOCUMENT, subject="document",
        category="financial",
        field="paid_total", severity=Severity.ERROR,
        message="first discrepancy", expected=Decimal("10"), actual=Decimal("20"),
    )
    two = one.model_copy(update={"expected": Decimal("30"), "message": "second discrepancy"})
    monkeypatch.setattr(engine, "_RULES", [("R-22", lambda d, c: [one, two])])
    with pytest.raises(ValueError, match="duplicate finding identities"):
        engine.reconcile(build_doc())


def test_a_document_with_claims_does_not_trip_the_empty_rule():
    result = reconcile(build_doc([good_claim()]))
    assert "R-20" not in {f.rule_id for f in result.findings}


def test_one_failed_source_page_blocks_an_otherwise_clean_document():
    """Processed pages cannot prove what an unreadable source page contained."""
    document = build_doc(
        [good_claim()],
        page_count=3,
        processed_pages=[1, 3],
        failed_pages=[2],
    )

    result = reconcile(document)

    assert [finding.rule_id for finding in result.errors] == ["R-22"]
    assert result.status is DocumentStatus.NEEDS_REVIEW


def test_every_successfully_processed_source_page_allows_clean_status():
    document = build_doc(
        [good_claim()],
        page_count=2,
        processed_pages=[1, 2],
    )

    result = reconcile(document)

    assert "R-22" not in result.rule_ids()
    assert result.status is DocumentStatus.CLEAN


def test_a_source_page_without_any_recorded_outcome_blocks_clean_status():
    document = build_doc(
        [good_claim()],
        page_count=2,
        processed_pages=[1],
    )

    result = reconcile(document)

    finding = next(item for item in result.errors if item.rule_id == "R-22")
    assert finding.page == 2
    assert finding.actual == "no processing outcome was recorded"
    assert result.status is DocumentStatus.NEEDS_REVIEW


# --- R-01 ------------------------------------------------------------------


def test_r01_catches_a_broken_incurred_identity():
    claim = good_claim(incurred_total=Decimal("600.00"))
    result = reconcile(build_doc([claim]))
    findings = ids_for(result, "R-01")
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].expected == Decimal("500.00")
    assert findings[0].actual == Decimal("600.00")
    assert findings[0].delta == Decimal("100.00")
    assert result.status is DocumentStatus.NEEDS_REVIEW


def test_r01_respects_tolerance():
    claim = good_claim(incurred_total=Decimal("500.01"))
    assert ids_for(reconcile(build_doc([claim])), "R-01") == []
    strict = ReconcileConfig(money_tolerance=Decimal("0"))
    assert len(ids_for(reconcile(build_doc([claim]), strict), "R-01")) == 1


def test_r01_tolerance_is_configurable_for_whole_unit_carriers():
    claim = good_claim(incurred_total=Decimal("500.40"))
    rounded = ReconcileConfig(money_tolerance=Decimal("0.50"))
    assert ids_for(reconcile(build_doc([claim]), rounded), "R-01") == []


def test_r01_skips_rows_with_no_paid_or_reserve():
    claim = good_claim(
        paid_total=None, reserve_total=None, paid_indemnity=None,
        paid_medical=None, paid_expense=None, reserve_indemnity=None,
        reserve_medical=None, reserve_expense=None, recovery_total=None,
    )
    assert ids_for(reconcile(build_doc([claim])), "R-01") == []


def test_r01_treats_missing_recovery_as_no_recovery():
    claim = good_claim(recovery_total=None, incurred_total=Decimal("525.00"))
    assert ids_for(reconcile(build_doc([claim])), "R-01") == []


def test_r01_uses_the_components_when_a_carrier_prints_no_paid_total():
    """AIG prints indemnity, medical and expense paid, and no total of them."""
    broken = good_claim(
        paid_total=None, recovery_total=None, incurred_total=Decimal("600.00")
    )
    findings = ids_for(reconcile(build_doc([broken])), "R-01")
    assert len(findings) == 1
    assert findings[0].expected == Decimal("525.00")  # 175 paid + 350 reserve

    whole = good_claim(
        paid_total=None, recovery_total=None, incurred_total=Decimal("525.00")
    )
    assert ids_for(reconcile(build_doc([whole])), "R-01") == []


def test_r01_will_not_unlock_itself_from_half_a_group_of_components():
    """One component present is not a paid total; the rest may be unmapped.

    CNA prints an Expenses Total this engine does not map, and no total of
    either paid or reserve. Adding up the indemnity that *is* mapped and
    calling it the paid figure turned that unmapped column into a six-figure
    arithmetic error against a carrier that had made none. With neither side
    of the identity stated, the rule has nothing to check and says so.
    """
    claim = good_claim(
        paid_total=None, paid_medical=None, paid_expense=None,
        reserve_total=None, reserve_indemnity=Decimal("0.00"),
        reserve_medical=None, reserve_expense=None,
        recovery_total=None, incurred_total=Decimal("600.00"),
    )
    assert ids_for(reconcile(build_doc([claim])), "R-01") == []


# --- R-02 / R-03 -----------------------------------------------------------


def test_r02_catches_a_broken_paid_breakdown():
    claim = good_claim(paid_medical=Decimal("60.00"))  # parts now 185 vs 175
    findings = ids_for(reconcile(build_doc([claim])), "R-02")
    assert len(findings) == 1
    assert findings[0].expected == Decimal("185.00")
    assert findings[0].delta == Decimal("-10.00")


def test_r03_catches_a_broken_reserve_breakdown():
    claim = good_claim(reserve_expense=Decimal("60.00"))
    findings = ids_for(reconcile(build_doc([claim])), "R-03")
    assert len(findings) == 1
    assert findings[0].expected == Decimal("360.00")


def test_r02_skips_formats_without_components():
    claim = good_claim(paid_indemnity=None, paid_medical=None, paid_expense=None)
    assert ids_for(reconcile(build_doc([claim])), "R-02") == []


def test_r02_uses_the_components_that_exist():
    # GL has no medical column; indemnity + expense must still tie.
    claim = good_claim(
        paid_medical=None, paid_total=Decimal("125.00"), incurred_total=Decimal("450.00")
    )
    assert ids_for(reconcile(build_doc([claim])), "R-02") == []


# --- R-04: the rule that sells the product ---------------------------------


def test_r04_ties_to_the_printed_footer_total():
    claims = [good_claim("A"), good_claim("B")]
    doc = build_doc(claims, printed_totals={"incurred_total": Decimal("1000.00")})
    assert ids_for(reconcile(doc), "R-04") == []


def test_r04_catches_a_missing_row():
    doc = build_doc(
        [good_claim("A")], printed_totals={"incurred_total": Decimal("1000.00")}
    )
    findings = ids_for(reconcile(doc), "R-04")
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].expected == Decimal("1000.00")
    assert findings[0].actual == Decimal("500.00")
    assert findings[0].delta == Decimal("-500.00")


def test_r04_mentions_null_cells_that_could_explain_the_gap():
    claims = [good_claim("A"), good_claim("B", incurred_total=None)]
    doc = build_doc(claims, printed_totals={"incurred_total": Decimal("1000.00")})
    findings = ids_for(reconcile(doc), "R-04")
    assert "1 row(s) have no value" in findings[0].message


def test_r04_checks_every_printed_column():
    doc = build_doc(
        [good_claim("A")],
        printed_totals={
            "paid_total": Decimal("175.00"),
            "reserve_total": Decimal("999.00"),
            "incurred_total": Decimal("500.00"),
        },
    )
    findings = ids_for(reconcile(doc), "R-04")
    assert len(findings) == 1
    assert findings[0].field == "reserve_total"


def test_r04_ignores_columns_with_no_printed_total():
    doc = build_doc([good_claim("A")], printed_totals={"incurred_total": None})
    assert ids_for(reconcile(doc), "R-04") == []


# --- R-05 ------------------------------------------------------------------


def test_r05_matches_the_printed_claim_count():
    doc = build_doc([good_claim("A"), good_claim("B")], printed_claim_count=2)
    assert ids_for(reconcile(doc), "R-05") == []


def test_r05_catches_a_dropped_row():
    doc = build_doc([good_claim("A")], printed_claim_count=2)
    findings = ids_for(reconcile(doc), "R-05")
    assert len(findings) == 1
    assert findings[0].expected == 2 and findings[0].actual == 1
    assert "fewer" in findings[0].message


def test_r05_catches_a_phantom_row():
    doc = build_doc([good_claim("A"), good_claim("B")], printed_claim_count=1)
    assert "more" in ids_for(reconcile(doc), "R-05")[0].message


def test_r05_is_silent_when_the_document_prints_no_count():
    assert ids_for(reconcile(build_doc([good_claim()])), "R-05") == []


# --- R-06 / R-07 -----------------------------------------------------------


def test_r06_requires_a_valuation_date():
    doc = build_doc([good_claim()], valuation_date=None)
    findings = ids_for(reconcile(doc), "R-06")
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert reconcile(doc).status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize("field_name", ["date_of_loss", "incurred_total"])
def test_r07_requires_core_fields(field_name):
    claim = good_claim(**{field_name: None})
    findings = ids_for(reconcile(build_doc([claim])), "R-07")
    assert [f.field for f in findings] == [field_name]
    assert findings[0].severity is Severity.ERROR


def test_r07_explains_why_the_field_is_null():
    claim = good_claim(
        incurred_total=None,
        field_issues={"incurred_total": NullReason.AMBIGUOUS_SEPARATOR},
    )
    findings = ids_for(reconcile(build_doc([claim])), "R-07")
    assert "AMBIGUOUS_SEPARATOR" in findings[0].message


# --- R-08 through R-14 -----------------------------------------------------


def test_r08_closed_claim_with_reserve():
    claim = good_claim(claim_status=ClaimStatus.CLOSED)
    findings = ids_for(reconcile(build_doc([claim])), "R-08")
    assert len(findings) == 1 and findings[0].severity is Severity.WARN


def test_r08_ignores_a_closed_claim_with_zero_reserve():
    claim = good_claim(
        claim_status=ClaimStatus.CLOSED,
        reserve_indemnity=Decimal("0"),
        reserve_medical=Decimal("0"),
        reserve_expense=Decimal("0"),
        reserve_total=Decimal("0"),
        incurred_total=Decimal("150.00"),
    )
    assert ids_for(reconcile(build_doc([claim])), "R-08") == []


def test_r09_loss_outside_the_policy_period():
    claim = good_claim(date_of_loss=date(2023, 5, 1), date_reported=date(2024, 3, 10))
    findings = ids_for(reconcile(build_doc([claim])), "R-09")
    assert len(findings) == 1 and findings[0].severity is Severity.WARN


def test_r09_needs_a_policy_period():
    claim = good_claim(date_of_loss=date(2019, 5, 1))
    doc = build_doc([claim], policy_period_start=None, policy_period_end=None)
    assert ids_for(reconcile(doc), "R-09") == []


def test_r10_reported_before_loss():
    claim = good_claim(date_of_loss=date(2024, 3, 4), date_reported=date(2024, 3, 1))
    findings = ids_for(reconcile(build_doc([claim])), "R-10")
    assert len(findings) == 1
    assert "before the loss date" in findings[0].message


def test_r10_reported_after_valuation():
    claim = good_claim(date_reported=date(2025, 6, 1))
    findings = ids_for(reconcile(build_doc([claim])), "R-10")
    assert len(findings) == 1
    assert "after the valuation date" in findings[0].message


def test_r10_accepts_correct_ordering():
    assert ids_for(reconcile(build_doc([good_claim()])), "R-10") == []


def test_r11_duplicate_claim_number_on_one_page():
    doc = build_doc([good_claim("DUP"), good_claim("DUP")])
    findings = ids_for(reconcile(doc), "R-11")
    assert len(findings) == 1 and findings[0].actual == 2


def test_r12_same_claim_across_two_pages():
    doc = build_doc([good_claim("SPLIT", source_page=1), good_claim("SPLIT", source_page=2)])
    result = reconcile(doc)
    assert len(ids_for(result, "R-12")) == 1
    assert ids_for(result, "R-11") == []  # R-12 owns the cross-page case


def test_r13_flags_a_hundredfold_outlier():
    claims = [good_claim(f"C{i}") for i in range(4)]
    claims.append(
        good_claim(
            "BIG",
            paid_total=Decimal("175000.00"),
            paid_indemnity=Decimal("175000.00"),
            paid_medical=None,
            paid_expense=None,
            reserve_total=Decimal("350.00"),
            recovery_total=Decimal("25.00"),
            incurred_total=Decimal("175325.00"),
        )
    )
    findings = ids_for(reconcile(build_doc(claims)), "R-13")
    assert any(f.claim_number == "BIG" and f.field == "paid_total" for f in findings)


def test_r13_stays_quiet_on_small_documents():
    doc = build_doc([good_claim("A"), good_claim("B", paid_total=Decimal("999999.00"),
                                                 paid_indemnity=Decimal("999999.00"),
                                                 paid_medical=None, paid_expense=None,
                                                 incurred_total=Decimal("1000324.00"))])
    assert ids_for(reconcile(doc), "R-13") == []


def test_r14_negative_paid_is_informational():
    claim = good_claim(
        paid_indemnity=Decimal("-175.00"), paid_medical=None, paid_expense=None,
        paid_total=Decimal("-175.00"), incurred_total=Decimal("150.00"),
    )
    findings = ids_for(reconcile(build_doc([claim])), "R-14")
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert reconcile(build_doc([claim])).status is DocumentStatus.CLEAN


# --- R-15 / R-16 -----------------------------------------------------------


def test_r15_reports_unresolved_cells():
    claim = good_claim(field_issues={"paid_medical": NullReason.AMBIGUOUS_SEPARATOR},
                       raw_cells={"paid_medical": "1.234"})
    findings = ids_for(reconcile(build_doc([claim])), "R-15")
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARN
    assert "1.234" in findings[0].message


def test_r15_ignores_blank_and_na_cells():
    claim = good_claim(
        field_issues={"paid_medical": NullReason.EMPTY, "cause_of_loss": NullReason.NOT_APPLICABLE}
    )
    assert ids_for(reconcile(build_doc([claim])), "R-15") == []


def test_r15_reports_document_level_issues():
    doc = build_doc([good_claim()],
                    document_issues={"valuation_date": NullReason.AMBIGUOUS_DATE_ORDER})
    findings = ids_for(reconcile(doc), "R-15")
    assert len(findings) == 1 and findings[0].field == "valuation_date"


def test_r16_mixed_currency():
    doc = build_doc([good_claim("A", currency="USD"), good_claim("B", currency="EUR")])
    findings = ids_for(reconcile(doc), "R-16")
    assert len(findings) == 1
    assert "EUR, USD" in findings[0].message


def test_r16_quiet_for_a_single_currency():
    doc = build_doc([good_claim("A", currency="USD"), good_claim("B", currency="USD")])
    assert ids_for(reconcile(doc), "R-16") == []


# --- Engine behaviour ------------------------------------------------------


def test_findings_lead_with_errors():
    claim = good_claim(claim_status=ClaimStatus.CLOSED, incurred_total=Decimal("999.00"))
    findings = reconcile(build_doc([claim])).findings
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=lambda s: {"ERROR": 0, "WARN": 1, "INFO": 2}[s.value])


def test_only_errors_flip_the_badge():
    warn_only = good_claim(claim_status=ClaimStatus.CLOSED)
    result = reconcile(build_doc([warn_only]))
    assert result.warnings and not result.errors
    assert result.status is DocumentStatus.CLEAN


def test_rules_can_be_disabled_per_profile():
    doc = build_doc([good_claim()], valuation_date=None)
    assert reconcile(doc).status is DocumentStatus.NEEDS_REVIEW
    config = ReconcileConfig(disabled_rules=frozenset({"R-06"}))
    assert reconcile(doc, config).status is DocumentStatus.CLEAN


def test_reconcile_does_not_mutate_the_document():
    doc = build_doc([good_claim(incurred_total=Decimal("600.00"))])
    before = doc.model_dump_json()
    reconcile(doc)
    assert doc.model_dump_json() == before


def test_result_helpers():
    claim = good_claim(incurred_total=Decimal("600.00"), claim_status=ClaimStatus.CLOSED)
    result = reconcile(build_doc([claim]))
    assert result.errors and result.warnings
    assert result.by_claim("CLM-1")
    assert "R-01" in result.rule_ids()


# --- The deliberately wrong document (spec section 12, M2 done-condition) ---


def test_a_deliberately_wrong_document_produces_exactly_the_expected_findings():
    """One document, one planted defect per rule, no collateral findings."""
    claims = [
        # R-01: incurred is 100 too high.
        good_claim("BAD-01", incurred_total=Decimal("600.00")),
        # R-02: paid parts sum to 185, total says 175.
        good_claim("BAD-02", paid_medical=Decimal("60.00")),
        # R-08: closed but holding reserve.
        good_claim("BAD-08", claim_status=ClaimStatus.CLOSED),
        # R-09: loss before the policy period.
        good_claim("BAD-09", date_of_loss=date(2023, 1, 5)),
        # R-14: negative paid.
        good_claim(
            "BAD-14",
            paid_indemnity=Decimal("-175.00"), paid_medical=None, paid_expense=None,
            paid_total=Decimal("-175.00"), incurred_total=Decimal("150.00"),
        ),
    ]
    doc = build_doc(
        claims,
        printed_claim_count=6,                                    # R-05: says 6, has 5
        printed_totals={"incurred_total": Decimal("9999.00")},    # R-04: nowhere near
    )
    result = reconcile(doc)

    assert sorted({f.rule_id for f in result.findings}) == [
        "R-01", "R-02", "R-04", "R-05", "R-08", "R-09", "R-14", "R-18",
    ]
    assert {f.claim_number for f in result.errors if f.claim_number} == {"BAD-01", "BAD-02"}
    assert result.status is DocumentStatus.NEEDS_REVIEW
