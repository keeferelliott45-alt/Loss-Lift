"""Merging one insured's loss runs into a single history."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.account import (
    UNNAMED_ACCOUNT,
    account_name,
    build_account,
    build_accounts,
    group_by_account,
)
from core.schema import Claim, ClaimStatus, LossRunDocument

TERM_2023 = (date(2023, 1, 1), date(2023, 12, 31))
TERM_2024 = (date(2024, 1, 1), date(2024, 12, 31))


def _claim(number: str, loss: date, incurred: str, status=ClaimStatus.OPEN) -> Claim:
    return Claim(
        claim_number=number,
        date_of_loss=loss,
        claim_status=status,
        paid_total=Decimal(incurred),
        reserve_total=Decimal("0"),
        recovery_total=Decimal("0"),
        incurred_total=Decimal(incurred),
    )


def _run(
    filename: str,
    valuation: date | None,
    claims: list[Claim],
    *,
    insured: str | None = "Acme Haulage",
    carrier: str = "Carrier A",
    periods: list[tuple[date, date]] | None = None,
) -> LossRunDocument:
    return LossRunDocument(
        source_filename=filename,
        file_sha256=filename,
        named_insured=insured,
        carrier=carrier,
        valuation_date=valuation,
        policy_periods=periods or [TERM_2023, TERM_2024],
        claims=claims,
    )


# One claim seen twice, at two valuations, having got worse in between.
OLD_RUN = _run(
    "2023.pdf",
    date(2023, 12, 31),
    [_claim("C-1", date(2023, 5, 1), "1000.00"), _claim("C-2", date(2023, 8, 1), "500.00")],
)
NEW_RUN = _run(
    "2024.pdf",
    date(2024, 12, 31),
    [
        _claim("C-1", date(2023, 5, 1), "4000.00"),
        _claim("C-2", date(2023, 8, 1), "500.00", ClaimStatus.CLOSED),
        _claim("C-3", date(2024, 3, 1), "250.00"),
    ],
)


def test_a_claim_in_two_runs_is_one_claim():
    account = build_account("Acme Haulage", [OLD_RUN, NEW_RUN])
    assert [h.claim_number for h in account.histories] == ["C-1", "C-2", "C-3"]
    assert len(account.claims) == 3


def test_the_latest_valuation_is_the_one_priced_from():
    account = build_account("Acme Haulage", [OLD_RUN, NEW_RUN])
    current = {h.claim_number: h.current for h in account.histories}
    assert current["C-1"].incurred_total == Decimal("4000.00")
    assert current["C-2"].claim_status is ClaimStatus.CLOSED


def test_order_of_documents_does_not_change_the_answer():
    """Valuation date decides which run is current, not upload order."""
    forwards = build_account("Acme", [OLD_RUN, NEW_RUN])
    backwards = build_account("Acme", [NEW_RUN, OLD_RUN])
    assert [h.current.incurred_total for h in forwards.histories] == [
        h.current.incurred_total for h in backwards.histories
    ]


def test_development_between_valuations_is_reported():
    account = build_account("Acme Haulage", [OLD_RUN, NEW_RUN])
    moved = {h.claim_number: h.development for h in account.histories}
    assert moved["C-1"] == Decimal("3000.00")   # 1,000 at 2023, 4,000 at 2024
    assert moved["C-2"] == Decimal("0")
    assert moved["C-3"] is None                 # seen once, so no movement known
    assert [h.claim_number for h in account.developed] == ["C-1"]


def test_an_unreadable_valuation_is_not_treated_as_no_movement():
    blank = _run("blank.pdf", date(2024, 12, 31), [
        Claim(claim_number="C-9", date_of_loss=date(2023, 2, 1), incurred_total=None),
    ])
    earlier = _run("earlier.pdf", date(2023, 12, 31), [_claim("C-9", date(2023, 2, 1), "100.00")])
    account = build_account("Acme", [earlier, blank])
    assert account.histories[0].development is None


def test_merged_book_is_summarised_by_term():
    account = build_account("Acme Haulage", [OLD_RUN, NEW_RUN])
    periods = {p.label: p for p in account.periods}
    term_2023 = periods["2023-01-01 to 2023-12-31"]
    assert term_2023.claims == 2
    assert term_2023.totals["incurred_total"] == Decimal("4500.00")  # not 1,500 + 4,500
    assert periods["2024-01-01 to 2024-12-31"].claims == 1


def test_merged_terms_claim_no_carrier_tie():
    """A carrier's subtotal covers its own claims, not the merged set."""
    account = build_account("Acme Haulage", [OLD_RUN, NEW_RUN])
    assert all(period.ties() is None for period in account.periods)


def test_a_claim_missing_from_a_later_run_of_its_term_is_flagged():
    later = _run("2024.pdf", date(2024, 12, 31), [_claim("C-3", date(2024, 3, 1), "250.00")])
    account = build_account("Acme", [OLD_RUN, later])
    assert [h.claim_number for h in account.dropped] == ["C-1", "C-2"]


def test_a_claim_outside_a_later_runs_terms_is_not_flagged():
    """A 2023 claim absent from a run covering only 2024 is not missing."""
    later = _run(
        "2024only.pdf", date(2024, 12, 31),
        [_claim("C-3", date(2024, 3, 1), "250.00")],
        periods=[TERM_2024],
    )
    account = build_account("Acme", [OLD_RUN, later])
    assert account.dropped == []


def test_documents_group_under_their_insured():
    other = _run("other.pdf", date(2024, 6, 30), [], insured="Beta Foods")
    grouped = group_by_account([OLD_RUN, NEW_RUN, other])
    assert set(grouped) == {"Acme Haulage", "Beta Foods"}
    assert len(grouped["Acme Haulage"]) == 2


def test_an_unnamed_insured_gets_its_own_heading():
    assert account_name(_run("x.pdf", None, [], insured=None)) == UNNAMED_ACCOUNT
    assert account_name(_run("x.pdf", None, [], insured="  Acme   Haulage ")) == "Acme Haulage"


def test_accounts_are_listed_with_the_fullest_first():
    other = _run("other.pdf", date(2024, 6, 30), [], insured="Beta Foods")
    accounts = build_accounts([other, OLD_RUN, NEW_RUN])
    assert [a.name for a in accounts] == ["Acme Haulage", "Beta Foods"]
    assert accounts[0].valuation_dates == [date(2023, 12, 31), date(2024, 12, 31)]
