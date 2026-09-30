"""Every fact ``run_view`` hands a packet's run is that run's own.

A packet is reconciled one loss run at a time, each run seen through
``reconcile.run_view`` as a document of its own. Each line of that function
is the only thing stopping a run being judged against another report's
printed count, total, valuation date or term -- and until now no test would
fail if one of them was deleted: R-05 per run worked, but one changed line
would have disabled it silently.

Each test below builds a two-run packet directly, where the document-level
fact and the other run's fact would both give the wrong answer, so dropping
or cross-wiring any single line of ``run_view`` fails at least one test.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.reconcile import reconcile, run_view
from core.schema import (
    Claim,
    ClaimStatus,
    LogicalRun,
    LineOfBusiness,
    LossRunDocument,
    PrintedSection,
    RefusedClaimRow,
    UnplacedRow,
    RunConfidence,
)


def _claim(number, page, row=1, loss=date(2023, 3, 1), incurred="100.00"):
    return Claim(
        claim_number=number, date_of_loss=loss, date_reported=loss,
        claim_status=ClaimStatus.CLOSED, source_page=page, source_row=row,
        paid_total=Decimal(incurred), reserve_total=Decimal("0"),
        incurred_total=Decimal(incurred),
    )


def _run(run_id, page, **facts):
    base = dict(
        valuation_date=date(2023, 12, 31),
        policy_period_start=date(2023, 1, 1),
        policy_period_end=date(2023, 12, 31),
    )
    base.update(facts)
    return LogicalRun(run_id=run_id, pages=[page], confidence=RunConfidence.PRINTED,
                      **base)


def _packet(run1=None, run2=None, claims=None, **document):
    runs = [run1 or _run("run-1", 1), run2 or _run("run-2", 2)]
    base = dict(
        source_filename="packet.pdf", file_sha256="0" * 64, page_count=2,
        valuation_date=date(2023, 12, 31),
        policy_period_start=date(2023, 1, 1),
        policy_period_end=date(2023, 12, 31),
        rows_seen_per_page={1: 2, 2: 1},
        processed_pages=[1, 2],
    )
    base.update(document)
    return LossRunDocument(
        runs=runs,
        claims=claims if claims is not None else [
            _claim("A-100", 1, 1), _claim("A-101", 1, 2), _claim("B-200", 2, 1),
        ],
        **base,
    )


def _on(result, rule, run_id):
    return [f for f in result.findings if f.rule_id == rule and f.run_id == run_id]


# --------------------------------------------------------------------------
# The view itself
# --------------------------------------------------------------------------


def test_the_view_carries_only_the_runs_own_facts():
    run2 = _run(
        "run-2", 2, carrier="Carrier B", named_insured="Insured B",
        policy_number="POL-B", valuation_date=date(2023, 6, 30),
        policy_period_start=date(2022, 7, 1), policy_period_end=date(2023, 6, 30),
        printed_claim_count=1, printed_totals={"incurred_total": Decimal("100.00")},
        line_of_business=LineOfBusiness.WC,
        printed_count_evidence=[{"page": 2, "row": 9}],
        unreadable_totals={"paid_total": "1O0.00"},
        unreadable_totals_page=2, unreadable_totals_row=9,
    )
    document = _packet(
        run2=run2, carrier="Carrier A", named_insured="Insured A",
        policy_number="POL-A", printed_claim_count=3,
        printed_totals={"incurred_total": Decimal("300.00")},
        line_of_business=LineOfBusiness.GL,
        printed_count_evidence=[{"page": 1, "row": 4}],
        unreadable_totals={"reserve_total": "??"},
        unreadable_totals_page=1, unreadable_totals_row=4,
        incomplete_report="pages 3-4 of 4 are absent",
        printed_sections=[PrintedSection(label="Term 1", page=1),
                          PrintedSection(label="Term 2", page=2)],
        unplaced_rows=[UnplacedRow(page=1, amounts={"paid_total": "5.00"}),
                       UnplacedRow(page=2, amounts={"paid_total": "7.00"})],
        refused_claim_rows=[RefusedClaimRow(page=1, identifier="ZZ-1"),
                            RefusedClaimRow(page=2, identifier="ZZ-2")],
    )
    view = run_view(document, run2)
    assert [c.claim_number for c in view.claims] == ["B-200"]
    assert (view.carrier, view.named_insured, view.policy_number) == (
        "Carrier B", "Insured B", "POL-B")
    assert view.valuation_date == date(2023, 6, 30)
    assert (view.policy_period_start, view.policy_period_end) == (
        date(2022, 7, 1), date(2023, 6, 30))
    assert view.printed_claim_count == 1
    assert view.printed_totals == {"incurred_total": Decimal("100.00")}
    assert view.line_of_business is LineOfBusiness.WC
    assert view.printed_count_evidence == [{"page": 2, "row": 9}]
    assert view.unreadable_totals == {"paid_total": "1O0.00"}
    assert (view.unreadable_totals_page, view.unreadable_totals_row) == (2, 9)
    assert [section.label for section in view.printed_sections] == ["Term 2"]
    assert [row.amounts for row in view.unplaced_rows] == [{"paid_total": "7.00"}]
    assert view.rows_seen_per_page == {2: 1}
    assert [r.identifier for r in view.refused_claim_rows] == ["ZZ-2"]
    assert view.runs == [] and not view.is_packet
    assert view.incomplete_report is None


# --------------------------------------------------------------------------
# The rules, per run
# --------------------------------------------------------------------------


def test_r05_counts_each_run_against_its_own_printed_count():
    """Run 2 prints 2 claims and reads 1. The document's 3 would tie with the
    packet's 3 claims; run 1's 2 would tie with run 2 reading... nothing."""
    document = _packet(
        run1=_run("run-1", 1, printed_claim_count=2),
        run2=_run("run-2", 2, printed_claim_count=2),
        printed_claim_count=3,
    )
    result = reconcile(document)
    assert not _on(result, "R-05", "run-1")
    missing = _on(result, "R-05", "run-2")
    assert len(missing) == 1
    assert (missing[0].expected, missing[0].actual) == (2, 1)
    assert result.run_status["run-1"].value == "CLEAN"
    assert result.run_status["run-2"].value == "NEEDS_REVIEW"


def test_r05_passes_when_every_run_ties_even_if_the_document_count_does_not():
    document = _packet(
        run1=_run("run-1", 1, printed_claim_count=2),
        run2=_run("run-2", 2, printed_claim_count=1),
        printed_claim_count=99,
    )
    assert not [f for f in reconcile(document).findings if f.rule_id == "R-05"]


def test_r04_ties_each_run_to_its_own_printed_total():
    document = _packet(
        run1=_run("run-1", 1, printed_totals={"incurred_total": Decimal("200.00")}),
        run2=_run("run-2", 2, printed_totals={"incurred_total": Decimal("150.00")}),
        printed_totals={"incurred_total": Decimal("300.00")},
    )
    result = reconcile(document)
    assert not _on(result, "R-04", "run-1")
    wrong = _on(result, "R-04", "run-2")
    assert wrong and wrong[0].delta is not None
    assert abs(wrong[0].delta) == Decimal("50.00")


def test_r06_is_raised_for_a_run_that_prints_no_valuation_date():
    document = _packet(run2=_run("run-2", 2, valuation_date=None))
    result = reconcile(document)
    assert _on(result, "R-06", "run-2")
    assert not _on(result, "R-06", "run-1")


def test_r09_judges_a_claim_against_its_own_runs_term():
    """B-200's loss falls inside the document's (run 1's) term and outside
    run 2's own."""
    run2 = _run("run-2", 2, policy_period_start=date(2024, 1, 1),
                policy_period_end=date(2024, 12, 31),
                valuation_date=date(2024, 12, 31))
    result = reconcile(_packet(run2=run2))
    outside = [f for f in result.findings if f.rule_id == "R-09"]
    assert [f.claim_number for f in outside] == ["B-200"]
    assert outside[0].run_id == "run-2"


def test_r19_reads_each_runs_own_pages():
    """Page 2 saw two rows and one claim survived; page 1 is whole."""
    result = reconcile(_packet(rows_seen_per_page={1: 2, 2: 2}))
    gaps = [f for f in result.findings if f.rule_id == "R-19"]
    assert gaps and {f.run_id for f in gaps} == {"run-2"}


def test_r29_names_a_refused_row_in_its_own_run_only():
    document = _packet(refused_claim_rows=[
        RefusedClaimRow(page=2, identifier="ZZ-9", report=True),
    ])
    result = reconcile(document)
    named = [f for f in result.findings if f.rule_id == "R-29"]
    assert len(named) == 1 and named[0].run_id == "run-2"
    assert "ZZ-9" in named[0].message
    assert result.run_status["run-1"].value == "CLEAN"
