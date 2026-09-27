"""A merged account history is never less trustworthy than its runs.

A claim is its number within one carrier and one policy; each source is a
logical run with its own facts; and the account reads NEEDS_REVIEW whenever
any run it draws on does, or a claim's identity cannot be settled.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from core.account import build_account, build_accounts
from core.export import account_to_bytes
from core.schema import (
    Claim,
    ClaimStatus,
    DocumentStatus,
    Finding,
    FindingScope,
    LogicalRun,
    LossRunDocument,
    ReconciliationResult,
    Severity,
)

TERM_2023 = (date(2023, 1, 1), date(2023, 12, 31))
CLEAN = ReconciliationResult(status=DocumentStatus.CLEAN)


def _claim(number: str, incurred: str = "1000.00", page: int = 1,
           loss: date = date(2023, 5, 1)) -> Claim:
    return Claim(claim_number=number, date_of_loss=loss, claim_status=ClaimStatus.OPEN,
                 paid_total=Decimal(incurred), reserve_total=Decimal("0"),
                 recovery_total=Decimal("0"), incurred_total=Decimal(incurred),
                 source_page=page)


def _doc(name: str, valuation: date, claims: list[Claim], *, carrier: str | None = "Carrier A",
         policy: str | None = "POL-1", periods=(TERM_2023,)) -> LossRunDocument:
    return LossRunDocument(source_filename=name, file_sha256=name, named_insured="Acme Haulage",
                           carrier=carrier, policy_number=policy, valuation_date=valuation,
                           policy_periods=list(periods), claims=claims)


def _clean(*documents: LossRunDocument) -> dict[str, ReconciliationResult]:
    return {document.document_id: CLEAN for document in documents}


def test_the_same_claim_number_under_two_carriers_is_two_claims():
    one = _doc("a.pdf", date(2023, 12, 31), [_claim("100", "1000.00")], carrier="Carrier A")
    two = _doc("b.pdf", date(2024, 6, 30), [_claim("100", "4000.00")], carrier="Carrier B")
    account = build_account("Acme Haulage", [one, two], _clean(one, two))
    assert len(account.histories) == 2
    assert all(history.development is None for history in account.histories)
    assert sorted(float(c.incurred_total) for c in account.claims) == [1000.0, 4000.0]
    assert account.status is DocumentStatus.CLEAN


def test_the_same_claim_number_under_two_policies_is_two_claims():
    one = _doc("a.pdf", date(2023, 12, 31), [_claim("100")], policy="GL-1")
    two = _doc("b.pdf", date(2024, 6, 30), [_claim("100", "3000.00")], policy="AU-9")
    account = build_account("Acme Haulage", [one, two], _clean(one, two))
    assert len(account.histories) == 2


def test_the_same_claim_under_the_same_carrier_and_policy_develops():
    one = _doc("a.pdf", date(2023, 12, 31), [_claim("100", "1000.00")])
    two = _doc("b.pdf", date(2024, 6, 30), [_claim("100", "4000.00")])
    account = build_account("Acme Haulage", [one, two], _clean(one, two))
    [history] = account.histories
    assert history.development == Decimal("3000.00")
    assert account.status is DocumentStatus.CLEAN


def test_a_run_that_does_not_name_its_carrier_is_not_merged_and_not_trusted():
    one = _doc("a.pdf", date(2023, 12, 31), [_claim("100")])
    two = _doc("b.pdf", date(2024, 6, 30), [_claim("100", "4000.00")], carrier=None)
    account = build_account("Acme Haulage", [one, two], _clean(one, two))
    assert len(account.histories) == 2
    assert all(history.uncertain for history in account.histories)
    assert account.status is DocumentStatus.NEEDS_REVIEW
    assert any("100" in reason for reason in account.reasons())


def test_a_document_that_needs_review_never_makes_a_trusted_account():
    one = _doc("a.pdf", date(2023, 12, 31), [_claim("100")])
    two = _doc("b.pdf", date(2024, 6, 30), [_claim("200")])
    unsafe = ReconciliationResult(status=DocumentStatus.CLEAN, findings=[Finding(
        rule_id="R-15", scope=FindingScope.DOCUMENT, subject="document", condition="c",
        category="extraction", severity=Severity.WARN, message="unreadable amount")])
    account = build_account("Acme Haulage", [one, two],
                            {one.document_id: CLEAN, two.document_id: unsafe})
    assert account.status is DocumentStatus.NEEDS_REVIEW
    assert {h.claim_number: h.trusted for h in account.histories} == {"100": True, "200": False}
    assert any("b.pdf: needs review" in reason for reason in account.reasons())


def test_without_reconciliations_nothing_is_trusted():
    one = _doc("a.pdf", date(2023, 12, 31), [_claim("100")])
    assert build_accounts([one])[0].status is DocumentStatus.NEEDS_REVIEW


def _packet() -> LossRunDocument:
    runs = [
        LogicalRun(run_id="run-1", pages=[1], confidence="printed", carrier="Carrier A",
                   policy_number="GL-1", valuation_date=date(2023, 12, 31),
                   named_insured="Acme Haulage",
                   policy_period_start=TERM_2023[0], policy_period_end=TERM_2023[1]),
        LogicalRun(run_id="run-2", pages=[2], confidence="printed", carrier="Carrier B",
                   policy_number="AU-9", valuation_date=date(2024, 3, 31),
                   policy_period_start=TERM_2023[0], policy_period_end=TERM_2023[1]),
    ]
    return LossRunDocument(
        source_filename="packet.pdf", file_sha256="p", named_insured="Acme Haulage",
        carrier="Carrier A", policy_number="GL-1", valuation_date=date(2023, 12, 31),
        claims=[_claim("100", "1000.00", page=1), _claim("100", "7000.00", page=2)], runs=runs)


def test_a_packets_runs_are_sources_with_their_own_valuations():
    packet = _packet()
    result = ReconciliationResult(status=DocumentStatus.CLEAN, run_status={
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.CLEAN})
    account = build_account("Acme Haulage", [packet], {packet.document_id: result})
    assert [(s.run_id, s.carrier, s.valuation_date) for s in account.sources] == [
        ("run-1", "Carrier A", date(2023, 12, 31)), ("run-2", "Carrier B", date(2024, 3, 31))]
    assert account.valuation_dates == [date(2023, 12, 31), date(2024, 3, 31)]
    # Carrier B's claim 100 is not Carrier A's claim 100 developing.
    assert len(account.histories) == 2
    assert all(history.development is None for history in account.histories)


def test_one_unclean_run_of_a_packet_is_marked_and_the_account_needs_review():
    packet = _packet()
    result = ReconciliationResult(status=DocumentStatus.NEEDS_REVIEW, run_status={
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.NEEDS_REVIEW})
    account = build_account("Acme Haulage", [packet], {packet.document_id: result})
    trusted = {h.appearances[0].run_id: h.trusted for h in account.histories}
    assert trusted == {"run-1": True, "run-2": False}
    assert account.status is DocumentStatus.NEEDS_REVIEW


def test_the_account_workbook_says_whether_it_can_be_used():
    one = _doc("a.pdf", date(2023, 12, 31), [_claim("100")])
    two = _doc("b.pdf", date(2024, 6, 30), [_claim("100", "4000.00")], carrier=None)
    account = build_account("Acme Haulage", [one, two], _clean(one, two))
    book = load_workbook(BytesIO(account_to_bytes(account)))
    assert book.sheetnames[0] == "Account Status"
    status = [row for row in book["Account Status"].iter_rows(min_row=2, values_only=True)]
    assert status[0][:2] == ("Acme Haulage", "Needs review")
    review = [row[-1] for row in book["Claims"].iter_rows(min_row=2, values_only=True)]
    assert all(review)
