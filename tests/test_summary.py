"""Loss summary by policy term."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.schema import Claim, ClaimStatus, LossRunDocument, PrintedSection
from core.summary import summarise_by_period


def _claim(number: str, loss: date, incurred: str, status=ClaimStatus.CLOSED) -> Claim:
    return Claim(
        claim_number=number,
        date_of_loss=loss,
        claim_status=status,
        paid_total=Decimal(incurred),
        reserve_total=Decimal("0"),
        recovery_total=Decimal("0"),
        incurred_total=Decimal(incurred),
    )


def _document(**kwargs) -> LossRunDocument:
    base = {"source_filename": "x.pdf", "file_sha256": "abc"}
    return LossRunDocument(**{**base, **kwargs})


def _section(start: date, count: int, incurred: str) -> PrintedSection:
    return PrintedSection(
        label=start.isoformat(),
        period_start=start,
        printed_claim_count=count,
        printed_totals={
            "paid_total": Decimal(incurred),
            "incurred_total": Decimal(incurred),
        },
    )


TERMS = [(date(2023, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2024, 12, 31))]
CLAIMS = [
    _claim("A-1", date(2023, 3, 1), "100.00"),
    _claim("A-2", date(2023, 9, 1), "400.00", ClaimStatus.OPEN),
    _claim("B-1", date(2024, 6, 1), "250.00"),
]


def test_claims_split_by_declared_term():
    summary = summarise_by_period(_document(policy_periods=TERMS, claims=CLAIMS))
    assert [period.claims for period in summary] == [2, 1]
    assert summary[0].totals["incurred_total"] == Decimal("500.00")
    assert summary[1].totals["incurred_total"] == Decimal("250.00")
    assert summary[0].open_claims == 1 and summary[0].closed_claims == 1


def test_largest_loss_is_named_per_term():
    summary = summarise_by_period(_document(policy_periods=TERMS, claims=CLAIMS))
    assert summary[0].largest_loss == Decimal("400.00")
    assert summary[0].largest_claim_number == "A-2"


def test_term_ties_to_the_subtotal_printed_for_it():
    document = _document(
        policy_periods=TERMS,
        claims=CLAIMS,
        printed_sections=[
            _section(date(2023, 1, 1), 2, "500.00"),
            _section(date(2024, 1, 1), 1, "250.00"),
        ],
    )
    summary = summarise_by_period(document)
    assert [period.ties() for period in summary] == [True, True]
    assert summary[0].difference("incurred_total") == Decimal("0")


def test_one_term_failing_does_not_hide_behind_the_others():
    """The point of the per-term check: name the year that is wrong."""
    document = _document(
        policy_periods=TERMS,
        claims=CLAIMS,
        printed_sections=[
            _section(date(2023, 1, 1), 2, "900.00"),   # carrier says 900, we read 500
            _section(date(2024, 1, 1), 1, "250.00"),
        ],
    )
    summary = summarise_by_period(document)
    assert summary[0].ties() is False
    assert summary[0].difference("incurred_total") == Decimal("-400.00")
    assert summary[1].ties() is True


def test_a_short_claim_count_fails_the_term():
    document = _document(
        policy_periods=TERMS,
        claims=CLAIMS,
        printed_sections=[_section(date(2023, 1, 1), 5, "500.00")],
    )
    summary = summarise_by_period(document)
    assert summary[0].ties() is False


def test_unchecked_term_reports_no_verdict_rather_than_passing():
    """Nothing printed to compare against is not the same as agreement."""
    summary = summarise_by_period(_document(policy_periods=TERMS, claims=CLAIMS))
    assert summary[0].has_printed_check is False
    assert summary[0].ties() is None


def test_claims_outside_every_term_are_still_counted():
    stray = _claim("OLD-1", date(2019, 5, 1), "75.00")
    summary = summarise_by_period(
        _document(policy_periods=TERMS, claims=[*CLAIMS, stray])
    )
    assert sum(period.claims for period in summary) == 4
    assert summary[-1].label == "Outside any stated term"
    assert summary[-1].claims == 1


def test_documents_without_declared_terms_group_by_loss_year():
    summary = summarise_by_period(_document(claims=CLAIMS))
    assert [period.label for period in summary] == ["2023", "2024"]
    assert [period.claims for period in summary] == [2, 1]


def test_no_claims_gives_no_rows():
    assert summarise_by_period(_document()) == []
