"""The golden-file harness and the thresholds from spec section 10.

These are the numbers that decide whether the product may be charged for.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.schema import DocumentStatus, Severity
from tests.accuracy import aggregate, score_all, score_fixture
from tests.golden.fixtures import DIGITAL_FIXTURES
from tests.golden.generate import load_meta

DIGITAL_NAMES = [fixture.name for fixture in DIGITAL_FIXTURES]

#: Spec section 10 thresholds.
MONEY_ACCURACY_DIGITAL = 0.995
TEXT_ACCURACY_DIGITAL = 0.97


@pytest.fixture(scope="module")
def reports(golden_dir):
    return score_all(golden_dir, DIGITAL_NAMES)


def test_at_least_eight_fixtures_exist():
    assert len(DIGITAL_FIXTURES) >= 8


def test_fixtures_span_the_required_variations():
    descriptions = " ".join(f.description.lower() for f in DIGITAL_FIXTURES)
    formats = {f.number_format for f in DIGITAL_FIXTURES}
    assert "us" in formats and "eu" in formats
    assert "paren" in formats and "trailing_minus" in formats
    assert any(f.rows_per_page < len(f.claims) for f in DIGITAL_FIXTURES)  # multi-page
    assert any("medical" in " ".join(c.field or "" for c in f.columns)
               for f in DIGITAL_FIXTURES)  # WC medical columns
    assert "r-01" in descriptions or "arithmetic" in descriptions


def test_money_accuracy_meets_the_digital_threshold(reports):
    total = aggregate(reports.values())
    assert total.money.accuracy >= MONEY_ACCURACY_DIGITAL, "\n".join(
        [total.summary()] + [str(m) for m in total.money.mismatches[:20]]
    )


def test_text_accuracy_meets_the_digital_threshold(reports):
    total = aggregate(reports.values())
    assert total.other.accuracy >= TEXT_ACCURACY_DIGITAL, "\n".join(
        [total.summary()] + [str(m) for m in total.other.mismatches[:20]]
    )


@pytest.mark.parametrize("name", DIGITAL_NAMES)
def test_every_fixture_meets_the_threshold_on_its_own(reports, name):
    report = reports[name]
    assert report.money.accuracy >= MONEY_ACCURACY_DIGITAL, report.summary()
    assert report.rows_match, (
        f"{name}: {report.extracted_rows}/{report.expected_rows} rows, "
        f"missing {report.missing_claims}, unexpected {report.unexpected_claims}"
    )


def test_zero_silent_nulls_as_zeros(reports):
    """The spec's hard rule: a blank cell never becomes 0.00."""
    total = aggregate(reports.values())
    offenders = [
        m for report in reports.values() for m in report.all_mismatches
        if m.kind == "null_as_zero"
    ]
    assert total.money.nulls_as_zeros == 0, "\n".join(str(m) for m in offenders)


def test_r04_footer_tie_passes_on_every_clean_fixture(golden_dir):
    """R-04 must tie on 100% of fixtures that print a footer total."""
    failures = []
    for fixture in DIGITAL_FIXTURES:
        if not fixture.print_totals:
            continue
        _, result = score_fixture(fixture.name, golden_dir / f"{fixture.name}.pdf")
        r04 = [f for f in result.reconciliation.findings if f.rule_id == "R-04"]
        if r04:
            failures.append(f"{fixture.name}: {[str(f) for f in r04]}")
    assert not failures, "\n".join(failures)


def test_r05_claim_count_ties_on_every_fixture(golden_dir):
    failures = []
    for fixture in DIGITAL_FIXTURES:
        if not fixture.print_claim_count:
            continue
        _, result = score_fixture(fixture.name, golden_dir / f"{fixture.name}.pdf")
        r05 = [f for f in result.reconciliation.findings if f.rule_id == "R-05"]
        if r05:
            failures.append(f"{fixture.name}: {[str(f) for f in r05]}")
    assert not failures, "\n".join(failures)


def test_printed_totals_are_read_from_the_document(golden_dir):
    _, result = score_fixture("us_basic", golden_dir / "us_basic.pdf")
    expected = load_meta("us_basic")["printed_totals"]
    for field_name, value in expected.items():
        assert result.document.printed_totals[field_name] == Decimal(value)


def test_document_metadata_matches_expectations(golden_dir):
    for fixture in DIGITAL_FIXTURES:
        _, result = score_fixture(fixture.name, golden_dir / f"{fixture.name}.pdf")
        meta = load_meta(fixture.name)
        document = result.document
        assert document.valuation_date.isoformat() == meta["valuation_date"], fixture.name
        assert document.policy_number == meta["policy_number"], fixture.name
        assert document.named_insured == meta["named_insured"], fixture.name
        assert document.currency == meta["currency"], fixture.name
        assert document.locale_hint == meta["locale_hint"], fixture.name
        assert document.policy_period_start.isoformat() == meta["policy_period_start"]
        assert document.policy_period_end.isoformat() == meta["policy_period_end"]


def test_locale_and_date_order_are_inferred_confidently(golden_dir):
    for fixture in DIGITAL_FIXTURES:
        _, result = score_fixture(fixture.name, golden_dir / f"{fixture.name}.pdf")
        assert result.locale.confident, f"{fixture.name}: locale unproven"
        assert result.date_order.confident, f"{fixture.name}: date order unproven"


# --- The specific fixtures that carry a planted defect ---------------------


def test_arithmetic_error_fixture_is_caught_by_r01(golden_dir):
    _, result = score_fixture("arithmetic_error", golden_dir / "arithmetic_error.pdf")
    r01 = [f for f in result.reconciliation.findings if f.rule_id == "R-01"]
    assert len(r01) == 1
    assert r01[0].claim_number == "FM-0003"
    assert r01[0].delta == Decimal("10000.00")
    assert r01[0].severity is Severity.ERROR
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_nulls_stay_null_and_zeros_stay_zero(golden_dir):
    _, result = score_fixture("nulls_not_zeros", golden_dir / "nulls_not_zeros.pdf")
    by_number = {claim.claim_number: claim for claim in result.document.claims}
    assert by_number["KR-0002"].recovery_total is None       # "N/A"
    assert by_number["KR-0003"].recovery_total is None       # blank
    assert by_number["KR-0004"].recovery_total == Decimal("0")  # "-0-"
    assert by_number["KR-0002"].issue("recovery_total") is not None


def test_negative_values_survive_every_convention(golden_dir):
    _, paren = score_fixture("accounting_negatives", golden_dir / "accounting_negatives.pdf")
    by_number = {c.claim_number: c for c in paren.document.claims}
    assert by_number["AU-0003"].incurred_total < 0     # recovery exceeded paid
    assert by_number["AU-0006"].paid_total < 0

    _, mainframe = score_fixture(
        "mainframe_trailing_minus", golden_dir / "mainframe_trailing_minus.pdf"
    )
    by_number = {c.claim_number: c for c in mainframe.document.claims}
    assert by_number["CPP0071197"].reserve_indemnity == Decimal("-3500.00")
    assert by_number["CPP0071195"].paid_total == Decimal("0")


def test_eu_and_us_documents_produce_identical_numbers(golden_dir):
    """Same claims, two carrier conventions, one canonical answer."""
    _, us = score_fixture("us_basic", golden_dir / "us_basic.pdf")
    _, eu = score_fixture("eu_format", golden_dir / "eu_format.pdf")
    us_values = [c.incurred_total for c in us.document.claims]
    eu_values = [c.incurred_total for c in eu.document.claims]
    assert us_values == eu_values
    assert us.document.locale_hint == "us" and eu.document.locale_hint == "eu"


def test_accuracy_report_is_printable(reports, capsys):
    total = aggregate(reports.values())
    print("\n" + "\n".join(r.summary() for r in reports.values()))
    print(total.summary())
    assert "money" in total.summary()
