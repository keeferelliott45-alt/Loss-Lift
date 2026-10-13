"""Dropped means the claim's number is absent, not that identity is unstated.

A claim is reported ``dropped`` only when its number does not appear in a later
run that covers the period. A later run that *prints* the same number keeps the
claim on the books even when it leaves carrier or policy unstated -- identity
resolving to UNKNOWN is "cannot tell", never "gone". Only an absent number is a
candidate omission, and then only when the later covering run is the same
carrier and policy (a number missing from another carrier's run is not missing
at all).

These pin the fix for ``core.account._later_run_omits`` probing identity
instead of presence (fuzz/LOG.md, campaign 2).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.account import build_account
from core.schema import Claim, ClaimStatus, LogicalRun, LossRunDocument

TERM_2023 = (date(2023, 1, 1), date(2023, 12, 31))
TERM_2024 = (date(2024, 1, 1), date(2024, 12, 31))


def _claim(number: str, loss: date, incurred: str = "1000.00", page: int = 1,
           status: ClaimStatus = ClaimStatus.OPEN) -> Claim:
    return Claim(
        claim_number=number,
        date_of_loss=loss,
        claim_status=status,
        paid_total=Decimal(incurred),
        reserve_total=Decimal("0"),
        recovery_total=Decimal("0"),
        incurred_total=Decimal(incurred),
        source_page=page,
    )


def _run(
    filename: str,
    valuation: date,
    claims: list[Claim],
    *,
    insured: str | None = "Acme Haulage",
    carrier: str | None = "Carrier A",
    policy: str | None = "POL-1",
    periods: tuple[tuple[date, date], ...] = (TERM_2023,),
) -> LossRunDocument:
    return LossRunDocument(
        source_filename=filename,
        file_sha256=filename,
        named_insured=insured,
        carrier=carrier,
        policy_number=policy,
        valuation_date=valuation,
        policy_periods=list(periods),
        claims=claims,
    )


def _dropped(account) -> set[str]:
    return {history.claim_number for history in account.dropped}


def _history_from(account, filename: str):
    """The one history whose appearances include a run read from this file."""
    return next(
        history for history in account.histories
        if any(a.source_filename == filename for a in history.appearances)
    )


def test_a_later_run_printing_the_number_without_identity_is_not_dropped():
    """The exact repro: same number printed later, carrier/policy unstated.

    Identity is UNKNOWN, so the two appearances stay apart -- but the number is
    present in a later covering run, so neither is an omission.
    """
    earlier = _run(
        "earlier.pdf", date(2023, 12, 31),
        [_claim("X-1", date(2023, 5, 1))],
        carrier="Carrier A", policy="POL-1",
    )
    later = _run(
        "later.pdf", date(2024, 6, 30),
        [_claim("X-1", date(2023, 5, 1), "4000.00")],
        carrier=None, policy=None,
    )
    account = build_account("Acme Haulage", [earlier, later])
    assert _dropped(account) == set()


def test_a_later_run_omitting_the_number_still_reports_it_dropped():
    earlier = _run(
        "earlier.pdf", date(2023, 12, 31),
        [_claim("X-1", date(2023, 5, 1))],
        carrier="Carrier A", policy="POL-1",
    )
    later = _run(
        "later.pdf", date(2024, 6, 30),
        [_claim("X-9", date(2023, 8, 1), "250.00")],
        carrier="Carrier A", policy="POL-1",
    )
    account = build_account("Acme Haulage", [earlier, later])
    assert _dropped(account) == {"X-1"}


def test_a_renumbered_claim_is_reported_dropped():
    earlier = _run(
        "earlier.pdf", date(2023, 12, 31),
        [_claim("X-1", date(2023, 5, 1))],
        carrier="Carrier A", policy="POL-1",
    )
    later = _run(
        "later.pdf", date(2024, 6, 30),
        [_claim("X-2", date(2023, 5, 1), "1000.00")],
        carrier="Carrier A", policy="POL-1",
    )
    account = build_account("Acme Haulage", [earlier, later])
    assert _dropped(account) == {"X-1"}


def test_one_later_run_printing_the_number_keeps_it_from_being_dropped():
    """Presence in any later covering run wins over omission by another.

    The printing run states no carrier or policy, so it does not join the
    earlier history; the same-carrier run that omits the number does not make
    the earlier claim dropped while the number is still printed later.
    """
    earlier = _run(
        "earlier.pdf", date(2023, 6, 30),
        [_claim("X-1", date(2023, 5, 1))],
        carrier="Carrier A", policy="POL-1",
    )
    prints_it = _run(
        "prints.pdf", date(2023, 12, 31),
        [_claim("X-1", date(2023, 5, 1), "2000.00")],
        carrier=None, policy=None,
    )
    omits_it = _run(
        "omits.pdf", date(2024, 6, 30),
        [_claim("X-9", date(2023, 8, 1), "250.00")],
        carrier="Carrier A", policy="POL-1",
    )
    account = build_account("Acme Haulage", [earlier, prints_it, omits_it])
    assert _history_from(account, "earlier.pdf") not in account.dropped


def test_a_packet_run_that_does_not_cover_the_period_leaves_dropped_empty():
    """Per-run terms decide coverage: a run of another term is not this claim's.

    A packet's run-2 covers only 2024, so X-1 (a 2023 loss) is simply not that
    run's business -- absent from it is not omitted.
    """
    packet = LossRunDocument(
        source_filename="packet.pdf", file_sha256="p", named_insured="Acme Haulage",
        carrier="Carrier A", policy_number="POL-1", valuation_date=date(2023, 6, 30),
        claims=[_claim("X-1", date(2023, 5, 1), page=1),
                _claim("X-9", date(2024, 3, 1), page=2)],
        runs=[
            LogicalRun(run_id="run-1", pages=[1], confidence="printed",
                       carrier="Carrier A", policy_number="POL-1",
                       valuation_date=date(2023, 6, 30),
                       named_insured="Acme Haulage",
                       policy_period_start=TERM_2023[0], policy_period_end=TERM_2023[1]),
            LogicalRun(run_id="run-2", pages=[2], confidence="printed",
                       carrier="Carrier A", policy_number="POL-1",
                       valuation_date=date(2024, 6, 30),
                       named_insured="Acme Haulage",
                       policy_period_start=TERM_2024[0], policy_period_end=TERM_2024[1]),
        ],
    )
    account = build_account("Acme Haulage", [packet])
    assert _dropped(account) == set()


def test_a_packet_run_that_covers_the_period_reports_the_omission():
    """The contrast to the test above: run-2 covers 2023, so X-1 is missing."""
    packet = LossRunDocument(
        source_filename="packet.pdf", file_sha256="p", named_insured="Acme Haulage",
        carrier="Carrier A", policy_number="POL-1", valuation_date=date(2023, 6, 30),
        claims=[_claim("X-1", date(2023, 5, 1), page=1),
                _claim("X-9", date(2023, 8, 1), page=2)],
        runs=[
            LogicalRun(run_id="run-1", pages=[1], confidence="printed",
                       carrier="Carrier A", policy_number="POL-1",
                       valuation_date=date(2023, 6, 30),
                       named_insured="Acme Haulage",
                       policy_period_start=TERM_2023[0], policy_period_end=TERM_2023[1]),
            LogicalRun(run_id="run-2", pages=[2], confidence="printed",
                       carrier="Carrier A", policy_number="POL-1",
                       valuation_date=date(2024, 6, 30),
                       named_insured="Acme Haulage",
                       policy_period_start=TERM_2023[0], policy_period_end=TERM_2023[1]),
        ],
    )
    account = build_account("Acme Haulage", [packet])
    assert _dropped(account) == {"X-1"}
