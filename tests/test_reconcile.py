"""Tests for the reconciliation engine (CLAUDE.md §6).

Hand-constructed claim lists only — no PDFs. Each rule is tested for both the
finding it should raise and the false positives it must not raise.
"""

from datetime import date
from decimal import Decimal

import pytest

from core.reconcile import (
    DocumentStatus,
    ReconcileConfig,
    Severity,
    reconcile,
)
from core.schema import ClaimRecord, ClaimStatus, LossRunDocument, NullReason


def D(s) -> Decimal:
    return Decimal(str(s))


def make_claim(**kw) -> ClaimRecord:
    """A claim that satisfies every rule, overridable per test."""
    base = dict(
        claim_number="CLM-001",
        date_of_loss=date(2024, 3, 15),
        date_reported=date(2024, 3, 20),
        claim_status=ClaimStatus.OPEN,
        paid_total=D("1000.00"),
        reserve_total=D("500.00"),
        recovery_total=D("0.00"),
        incurred_total=D("1500.00"),
        source_page=1,
    )
    base.update(kw)
    return ClaimRecord(**base)


def make_doc(claims=None, **kw) -> LossRunDocument:
    base = dict(
        source_filename="test.pdf",
        file_sha256="a" * 64,
        valuation_date=date(2024, 12, 31),
        page_count=1,
    )
    base.update(kw)
    return LossRunDocument(claims=claims if claims is not None else [make_claim()], **base)


def ids_for(result, rule_id: str):
    return [f for f in result.findings if f.rule_id == rule_id]


class TestCleanDocument:
    def test_clean_document_has_no_errors(self):
        result = reconcile(make_doc())
        assert result.errors == []
        assert result.status is DocumentStatus.CLEAN

    def test_clean_document_with_printed_totals_ties(self):
        claims = [
            make_claim(claim_number="A", paid_total=D(100), reserve_total=D(50), incurred_total=D(150)),
            make_claim(claim_number="B", paid_total=D(200), reserve_total=D(25), incurred_total=D(225)),
        ]
        doc = make_doc(
            claims,
            printed_totals={"paid_total": D(300), "incurred_total": D(375)},
            printed_claim_count=2,
        )
        result = reconcile(doc)
        assert result.errors == []
        assert result.status is DocumentStatus.CLEAN


class TestR01:
    def test_catches_row_arithmetic_error(self):
        doc = make_doc([make_claim(incurred_total=D("1600.00"))])
        findings = ids_for(reconcile(doc), "R-01")
        assert len(findings) == 1
        assert findings[0].severity is Severity.ERROR
        assert findings[0].expected == D("1500.00")
        assert findings[0].actual == D("1600.00")
        assert findings[0].delta == D("100.00")

    def test_recovery_reduces_incurred(self):
        doc = make_doc([make_claim(recovery_total=D(200), incurred_total=D(1300))])
        assert ids_for(reconcile(doc), "R-01") == []

    def test_missing_input_is_not_an_arithmetic_finding(self):
        doc = make_doc([make_claim(paid_total=None)])
        assert ids_for(reconcile(doc), "R-01") == []

    def test_within_tolerance_passes(self):
        doc = make_doc([make_claim(incurred_total=D("1500.01"))])
        assert ids_for(reconcile(doc), "R-01") == []

    def test_outside_tolerance_fails(self):
        doc = make_doc([make_claim(incurred_total=D("1500.02"))])
        assert len(ids_for(reconcile(doc), "R-01")) == 1

    def test_configurable_tolerance(self):
        doc = make_doc([make_claim(incurred_total=D("1501.00"))])
        cfg = ReconcileConfig(money_tolerance=D("1.00"))
        assert ids_for(reconcile(doc, cfg), "R-01") == []


class TestR02R03:
    def test_paid_components_must_sum(self):
        doc = make_doc([
            make_claim(paid_indemnity=D(600), paid_medical=D(300), paid_expense=D(50))
        ])  # sums to 950, paid_total is 1000
        findings = ids_for(reconcile(doc), "R-02")
        assert len(findings) == 1
        assert findings[0].delta == D(50)

    def test_paid_components_that_sum_pass(self):
        doc = make_doc([
            make_claim(paid_indemnity=D(600), paid_medical=D(350), paid_expense=D(50))
        ])
        assert ids_for(reconcile(doc), "R-02") == []

    def test_partial_components_still_checked(self):
        # Carrier prints indemnity + medical only; they must still tie.
        doc = make_doc([make_claim(paid_indemnity=D(600), paid_medical=D(400))])
        assert ids_for(reconcile(doc), "R-02") == []

    def test_no_components_no_finding(self):
        assert ids_for(reconcile(make_doc()), "R-02") == []

    def test_reserve_components_must_sum(self):
        doc = make_doc([
            make_claim(reserve_indemnity=D(300), reserve_medical=D(100), reserve_expense=D(50))
        ])  # 450 vs reserve_total 500
        findings = ids_for(reconcile(doc), "R-03")
        assert len(findings) == 1
        assert findings[0].severity is Severity.ERROR


class TestR04:
    """The rule that sells the product."""

    def test_column_sum_ties_to_printed_total(self):
        claims = [
            make_claim(claim_number="A", paid_total=D(100), reserve_total=D(0), incurred_total=D(100)),
            make_claim(claim_number="B", paid_total=D(250), reserve_total=D(0), incurred_total=D(250)),
        ]
        doc = make_doc(claims, printed_totals={"paid_total": D(350)})
        assert ids_for(reconcile(doc), "R-04") == []

    def test_mismatch_reports_delta(self):
        claims = [
            make_claim(claim_number="A", paid_total=D(100), reserve_total=D(0), incurred_total=D(100)),
            make_claim(claim_number="B", paid_total=D(250), reserve_total=D(0), incurred_total=D(250)),
        ]
        doc = make_doc(claims, printed_totals={"paid_total": D(500)})
        findings = ids_for(reconcile(doc), "R-04")
        assert len(findings) == 1
        assert findings[0].severity is Severity.ERROR
        assert findings[0].expected == D(500)
        assert findings[0].actual == D(350)
        assert findings[0].delta == D(-150)

    def test_missing_row_makes_the_tie_impossible(self):
        """A dropped row must not silently shift the column sum."""
        claims = [
            make_claim(claim_number="A", paid_total=D(100), reserve_total=D(0), incurred_total=D(100)),
            make_claim(claim_number="B", paid_total=None, reserve_total=D(0), incurred_total=D(250)),
        ]
        doc = make_doc(claims, printed_totals={"paid_total": D(350)})
        findings = ids_for(reconcile(doc), "R-04")
        assert len(findings) == 1
        assert findings[0].actual is None
        assert "no value" in findings[0].message

    def test_every_printed_column_checked(self):
        claims = [make_claim(paid_total=D(100), reserve_total=D(50), incurred_total=D(150))]
        doc = make_doc(
            claims,
            printed_totals={"paid_total": D(999), "reserve_total": D(999), "incurred_total": D(150)},
        )
        assert len(ids_for(reconcile(doc), "R-04")) == 2

    def test_no_printed_totals_no_findings(self):
        assert ids_for(reconcile(make_doc()), "R-04") == []


class TestR05:
    def test_row_count_matches(self):
        doc = make_doc(printed_claim_count=1)
        assert ids_for(reconcile(doc), "R-05") == []

    def test_dropped_row_detected(self):
        doc = make_doc(printed_claim_count=5)
        findings = ids_for(reconcile(doc), "R-05")
        assert len(findings) == 1
        assert findings[0].expected == 5
        assert findings[0].actual == 1
        assert findings[0].delta == D(-4)

    def test_absent_count_no_finding(self):
        assert ids_for(reconcile(make_doc()), "R-05") == []


class TestR06R07:
    def test_missing_valuation_date_is_error(self):
        doc = make_doc(valuation_date=None)
        findings = ids_for(reconcile(doc), "R-06")
        assert len(findings) == 1
        assert findings[0].severity is Severity.ERROR

    def test_present_valuation_date_passes(self):
        assert ids_for(reconcile(make_doc()), "R-06") == []

    @pytest.mark.parametrize("missing", ["claim_number", "date_of_loss", "incurred_total"])
    def test_required_fields(self, missing):
        doc = make_doc([make_claim(**{missing: None})])
        findings = ids_for(reconcile(doc), "R-07")
        assert [f.field for f in findings] == [missing]
        assert findings[0].severity is Severity.ERROR

    def test_null_reason_surfaced_in_message(self):
        claim = make_claim(
            incurred_total=None,
            field_issues={"incurred_total": NullReason.AMBIGUOUS_SEPARATOR},
        )
        findings = ids_for(reconcile(make_doc([claim])), "R-07")
        assert NullReason.AMBIGUOUS_SEPARATOR in findings[0].message

    def test_row_without_claim_number_is_still_locatable(self):
        doc = make_doc([make_claim(claim_number=None)])
        findings = ids_for(reconcile(doc), "R-07")
        assert findings[0].row_index == 0
        assert "row 1" in findings[0].message


class TestR08:
    def test_closed_with_reserve_warns(self):
        doc = make_doc([make_claim(claim_status=ClaimStatus.CLOSED)])
        findings = ids_for(reconcile(doc), "R-08")
        assert len(findings) == 1
        assert findings[0].severity is Severity.WARN

    def test_closed_with_zero_reserve_passes(self):
        doc = make_doc([
            make_claim(claim_status=ClaimStatus.CLOSED, reserve_total=D(0), incurred_total=D(1000))
        ])
        assert ids_for(reconcile(doc), "R-08") == []

    def test_open_with_reserve_passes(self):
        assert ids_for(reconcile(make_doc()), "R-08") == []


class TestR09:
    def test_loss_before_policy_period(self):
        doc = make_doc(
            [make_claim(date_of_loss=date(2023, 1, 1), date_reported=date(2023, 1, 2))],
            policy_period_start=date(2024, 1, 1),
            policy_period_end=date(2024, 12, 31),
        )
        assert len(ids_for(reconcile(doc), "R-09")) == 1

    def test_loss_inside_policy_period(self):
        doc = make_doc(
            policy_period_start=date(2024, 1, 1), policy_period_end=date(2024, 12, 31)
        )
        assert ids_for(reconcile(doc), "R-09") == []

    def test_no_policy_period_no_finding(self):
        assert ids_for(reconcile(make_doc()), "R-09") == []


class TestR10:
    def test_reported_before_loss(self):
        doc = make_doc([make_claim(date_of_loss=date(2024, 5, 1), date_reported=date(2024, 4, 1))])
        findings = ids_for(reconcile(doc), "R-10")
        assert len(findings) == 1
        assert findings[0].field == "date_reported"

    def test_date_after_valuation(self):
        doc = make_doc(
            [make_claim(date_of_loss=date(2025, 6, 1), date_reported=date(2025, 6, 2))],
            valuation_date=date(2024, 12, 31),
        )
        assert len(ids_for(reconcile(doc), "R-10")) == 2

    def test_ordered_dates_pass(self):
        assert ids_for(reconcile(make_doc()), "R-10") == []


class TestR11R12:
    def test_duplicate_on_same_page(self):
        claims = [make_claim(claim_number="DUP"), make_claim(claim_number="DUP")]
        findings = ids_for(reconcile(make_doc(claims)), "R-11")
        assert len(findings) == 1
        assert findings[0].severity is Severity.WARN

    def test_same_claim_across_pages_is_r12_not_r11(self):
        claims = [
            make_claim(claim_number="DUP", source_page=1),
            make_claim(claim_number="DUP", source_page=2),
        ]
        result = reconcile(make_doc(claims, page_count=2))
        assert ids_for(result, "R-11") == []
        assert len(ids_for(result, "R-12")) == 1

    def test_unique_claims_pass(self):
        claims = [make_claim(claim_number="A"), make_claim(claim_number="B")]
        result = reconcile(make_doc(claims))
        assert ids_for(result, "R-11") == []
        assert ids_for(result, "R-12") == []


class TestR13:
    def test_outlier_flagged(self):
        claims = [
            make_claim(claim_number="A", paid_total=D(100), reserve_total=D(0), incurred_total=D(100)),
            make_claim(claim_number="B", paid_total=D(120), reserve_total=D(0), incurred_total=D(120)),
            make_claim(claim_number="C", paid_total=D(110), reserve_total=D(0), incurred_total=D(110)),
            make_claim(claim_number="D", paid_total=D(1_000_000), reserve_total=D(0), incurred_total=D(1_000_000)),
        ]
        findings = ids_for(reconcile(make_doc(claims)), "R-13")
        assert any(f.claim_number == "D" and f.field == "paid_total" for f in findings)

    def test_similar_values_pass(self):
        claims = [
            make_claim(claim_number=str(i), paid_total=D(100 * i), reserve_total=D(0), incurred_total=D(100 * i))
            for i in range(1, 6)
        ]
        assert ids_for(reconcile(make_doc(claims)), "R-13") == []

    def test_too_few_rows_for_a_median(self):
        claims = [
            make_claim(claim_number="A", paid_total=D(1), reserve_total=D(0), incurred_total=D(1)),
            make_claim(claim_number="B", paid_total=D(10_000_000), reserve_total=D(0), incurred_total=D(10_000_000)),
        ]
        assert ids_for(reconcile(make_doc(claims)), "R-13") == []


class TestR14:
    def test_negative_paid_is_info_not_error(self):
        doc = make_doc([make_claim(paid_total=D(-500), reserve_total=D(0), incurred_total=D(-500))])
        findings = ids_for(reconcile(doc), "R-14")
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO
        assert reconcile(doc).status is DocumentStatus.CLEAN

    def test_positive_paid_no_finding(self):
        assert ids_for(reconcile(make_doc()), "R-14") == []


class TestR15:
    def test_ambiguous_separator_surfaces(self):
        claim = make_claim(
            paid_medical=None, field_issues={"paid_medical": NullReason.AMBIGUOUS_SEPARATOR}
        )
        findings = ids_for(reconcile(make_doc([claim])), "R-15")
        assert len(findings) == 1
        assert findings[0].field == "paid_medical"
        assert findings[0].severity is Severity.WARN

    def test_plain_blank_is_not_an_exception(self):
        claim = make_claim(paid_medical=None, field_issues={"paid_medical": NullReason.BLANK})
        assert ids_for(reconcile(make_doc([claim])), "R-15") == []

    @pytest.mark.parametrize(
        "reason",
        [NullReason.DOUBLE_DASH, NullReason.INVALID_DATE, NullReason.UNPARSEABLE, NullReason.AMBIGUOUS_DATE_ORDER],
    )
    def test_review_reasons(self, reason):
        claim = make_claim(paid_expense=None, field_issues={"paid_expense": reason})
        assert len(ids_for(reconcile(make_doc([claim])), "R-15")) == 1


class TestR16:
    def test_mixed_currency_warns(self):
        doc = make_doc(currency_symbols_seen=["$", "€"])
        findings = ids_for(reconcile(doc), "R-16")
        assert len(findings) == 1
        assert findings[0].severity is Severity.WARN

    def test_single_currency_passes(self):
        assert ids_for(reconcile(make_doc(currency_symbols_seen=["$", "$"])), "R-16") == []


class TestDocumentStatus:
    def test_error_forces_needs_review(self):
        doc = make_doc(valuation_date=None)
        assert reconcile(doc).status is DocumentStatus.NEEDS_REVIEW

    def test_warnings_alone_stay_clean(self):
        doc = make_doc([make_claim(claim_status=ClaimStatus.CLOSED)])
        result = reconcile(doc)
        assert result.warnings
        assert result.status is DocumentStatus.CLEAN

    def test_findings_sorted_errors_first(self):
        claims = [make_claim(claim_status=ClaimStatus.CLOSED, incurred_total=D(9999))]
        result = reconcile(make_doc(claims, valuation_date=None))
        severities = [f.severity for f in result.findings]
        assert severities == sorted(severities, key=lambda s: {"ERROR": 0, "WARN": 1, "INFO": 2}[s.value])


class TestDeliberatelyWrongFixture:
    """The spec's acceptance test for M2: a document seeded with known defects
    must produce exactly the expected set of findings — no more, no less."""

    def build(self) -> LossRunDocument:
        claims = [
            # 1. Clean row.
            make_claim(claim_number="C-100", paid_total=D(1000), reserve_total=D(500),
                       recovery_total=D(0), incurred_total=D(1500)),
            # 2. R-01: arithmetic error of +100.
            make_claim(claim_number="C-101", paid_total=D(2000), reserve_total=D(1000),
                       recovery_total=D(0), incurred_total=D(3100)),
            # 3. R-08: closed with a live reserve.
            make_claim(claim_number="C-102", claim_status=ClaimStatus.CLOSED,
                       paid_total=D(750), reserve_total=D(250), recovery_total=D(0),
                       incurred_total=D(1000)),
            # 4. R-07 + R-15: unreadable incurred total.
            make_claim(claim_number="C-103", paid_total=D(400), reserve_total=D(100),
                       recovery_total=D(0), incurred_total=None,
                       field_issues={"incurred_total": NullReason.AMBIGUOUS_SEPARATOR}),
        ]
        return make_doc(
            claims,
            printed_claim_count=5,          # R-05: one row was dropped
            printed_totals={"paid_total": D(4150)},  # R-04: 4150 printed vs 4150 extracted → ties
            policy_period_start=date(2024, 1, 1),
            policy_period_end=date(2024, 12, 31),
        )

    def test_exact_findings(self):
        result = reconcile(self.build())
        by_rule = {}
        for f in result.findings:
            by_rule.setdefault(f.rule_id, []).append(f)

        assert set(by_rule) == {"R-01", "R-05", "R-07", "R-08", "R-15"}
        assert len(by_rule["R-01"]) == 1
        assert by_rule["R-01"][0].claim_number == "C-101"
        assert by_rule["R-01"][0].delta == D(100)
        assert len(by_rule["R-05"]) == 1
        assert by_rule["R-05"][0].delta == D(-1)
        assert [f.claim_number for f in by_rule["R-07"]] == ["C-103"]
        assert [f.claim_number for f in by_rule["R-08"]] == ["C-102"]
        assert [f.claim_number for f in by_rule["R-15"]] == ["C-103"]
        assert result.status is DocumentStatus.NEEDS_REVIEW

    def test_r04_ties_despite_other_errors(self):
        """Paid column still ties even though the document has other defects —
        R-04 must not fire on unrelated problems."""
        result = reconcile(self.build())
        assert [f for f in result.findings if f.rule_id == "R-04"] == []
