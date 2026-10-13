"""Per-run printed facts are pinned to the run that printed them.

In a packet each run is reconciled against what *it* printed about itself --
its own totals (R-04) and its own claim count (R-05), gathered per run and
handed to the rules through :func:`core.reconcile.run_view`. Nothing here
guarded those two lines of code: one changed line in ``run_view`` or in the
per-run collection would have silenced the strongest rules in the engine on
every packet, with every other test still green.

These tests pin, end to end and by exact rule id and ``run_id``:

* each run is checked against its own printed count and totals, and a run
  whose printed facts are wrong is flagged alone;
* a pooled GRAND TOTAL row -- a board packet's packet-wide footer -- is not
  checked against any single run's claims, and its count is recorded as
  evidence (unresolved, R-27) rather than adopted, dropped or checked;
* a run whose own printed count is wrong still fires when a pooled
  document-level count happens to agree with its rows -- the pooled figure
  never stands in for the run's own;
* a run that prints neither a count nor a total is checked against nothing
  and reported through no invented finding;
* ``run_view`` carries a run's pages and printed facts, so a claim whose
  source page moves between runs moves with it.

A pooled GRAND TOTAL row whose count is printed in grand-total wording
("GRAND TOTAL Claims: 5") is refused by the count path, exactly as the totals
path refuses the same row: the count path now applies the totals path's covers
guard, so a grand-wording count that is not a count of the run can never become
that run's printed count. Everything is synthetic (spec section 9).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.extract_digital import document_claim_count
from core.reconcile import run_view
from core.schema import DocumentStatus
from tests.test_packet_adversarial import LETTER, _read, _total
from tests.test_packet_claim_series import (
    LARGE_CARRIER,
    LARGE_HEADERS,
    SMALL_CARRIER,
    SMALL_HEADERS,
    SMALL_RUN,
    _large_run,
)

RUN_ONE = _large_run(3)
RUN_TWO = list(SMALL_RUN[:2])

#: The packet's pooled figures: what a board packet's footer totals across
#: every run bound in it, whatever each run printed of its own.
POOLED_TOTAL = _total([*RUN_ONE, *RUN_TWO])


def _pooled_row(count_text=None):
    """A packet-wide GRAND TOTAL row, optionally stating a pooled claim count."""
    cells = ["GRAND TOTAL", "", "", POOLED_TOTAL[3], POOLED_TOTAL[4], POOLED_TOTAL[5]]
    if count_text is not None:
        cells[1] = count_text
    return tuple(cells)


def _packet(tmp_path, *, first_after=("Number of claims: 3",),
            second_after=("Number of claims: 2",), first_total=..., second_total=...,
            pooled=(), name="packet.pdf"):
    """Two settled one-page runs; each page may print its own count and total,
    and the second may also carry a pooled packet-wide row."""
    sheets = [
        {"top": ("Page 1 of 1", LARGE_CARRIER, *LETTER), "rows": RUN_ONE,
         "total": _total(RUN_ONE) if first_total is ... else first_total,
         "after": first_after},
        {"top": ("Page 1 of 1", SMALL_CARRIER, *LETTER), "headers": SMALL_HEADERS,
         "rows": [*RUN_TWO, *pooled],
         "total": _total(RUN_TWO) if second_total is ... else second_total,
         "after": second_after},
    ]
    return _read(tmp_path, sheets, name=name)


def _findings(result, rule_id):
    return [f for f in result.reconciliation.findings if f.rule_id == rule_id]


def _shaped(findings):
    return sorted(
        (f.run_id, f.field, str(f.expected), str(f.actual)) for f in findings
    )


# ==========================================================================
# Each run is checked against what it printed itself
# ==========================================================================


def test_each_run_is_checked_against_its_own_printed_count_and_totals(tmp_path):
    result = _packet(tmp_path)
    one, two = result.document.runs
    assert one.printed_claim_count == 3 and two.printed_claim_count == 2
    assert one.printed_totals["incurred_total"] == Decimal("3000.00")
    assert two.printed_totals["incurred_total"] == Decimal("2800.00")
    assert not _findings(result, "R-04") and not _findings(result, "R-05")
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.CLEAN}


def test_a_run_whose_printed_count_is_wrong_is_flagged_alone(tmp_path):
    """Run 1 prints "Number of claims: 2" over three claims; run 2 is right."""
    result = _packet(tmp_path, first_after=("Number of claims: 2",))
    assert _shaped(_findings(result, "R-05")) == [("run-1", "claim_count", "2", "3")]
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.NEEDS_REVIEW, "run-2": DocumentStatus.CLEAN}


def test_a_run_whose_printed_total_is_wrong_is_flagged_alone(tmp_path):
    """Run 2 prints an incurred total its claims do not reach; run 1 ties."""
    result = _packet(
        tmp_path, second_total=("TOTAL", "", "", "2,050.00", "750.00", "3,800.00"))
    assert _shaped(_findings(result, "R-04")) == [
        ("run-2", "incurred_total", "3800.00", "2800.00")]
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.NEEDS_REVIEW}


# ==========================================================================
# The pooled-total trap, both ways
# ==========================================================================


def test_a_pooled_grand_total_is_not_checked_against_one_run(tmp_path):
    """A board packet's footer totals every run bound in the PDF. Its row sits
    at the foot of the last run's table stating the pooled count ("5") that is
    not run 2's: the row must not be checked against run 2's claims (a
    discrepancy nobody made), and its count must be recorded as unresolved
    evidence rather than adopted as run 2's own."""
    result = _packet(tmp_path, pooled=[_pooled_row("Number of claims: 5")])
    one, two = result.document.runs
    # The pooled money never became a run's printed total.
    assert one.printed_totals["incurred_total"] == Decimal("3000.00")
    assert two.printed_totals["incurred_total"] == Decimal("2800.00")
    assert not _findings(result, "R-04"), _shaped(_findings(result, "R-04"))
    assert not _findings(result, "R-05"), _shaped(_findings(result, "R-05"))
    # The pooled count was not adopted either: it is evidence, unresolved.
    assert two.printed_claim_count is None
    assert sorted(entry["count"] for entry in two.printed_count_evidence) == [2, 5]
    assert one.printed_count_evidence == [{"page": 1, "count": 3}]
    # And the document stays loud about what could not be checked.
    unresolved = _findings(result, "R-27")
    assert [(f.run_id, f.field) for f in unresolved] == [("run-2", "claim_count")]
    assert result.reconciliation.run_status["run-1"] is DocumentStatus.CLEAN


def test_a_run_wrong_count_still_fires_when_a_pooled_count_ties(tmp_path):
    """Run 1 prints "Number of claims: 2" over three claims. The packet's
    pooled count is 3 -- run 1's own extracted count, by coincidence. The
    per-run check reads the count run 1 itself printed, so it fires; a pooled
    figure that happens to agree with the rows may never stand in for it."""
    result = _packet(
        tmp_path, first_after=("Number of claims: 2",),
        pooled=[_pooled_row("Number of claims: 3")])
    one, two = result.document.runs
    assert one.printed_claim_count == 2
    assert _shaped(_findings(result, "R-05")) == [("run-1", "claim_count", "2", "3")]
    assert result.reconciliation.run_status["run-1"] is DocumentStatus.NEEDS_REVIEW
    # The pooled count left run 2's own count unresolved, not adopted.
    assert two.printed_claim_count is None
    assert sorted(entry["count"] for entry in two.printed_count_evidence) == [2, 3]


def test_a_pooled_grand_count_is_never_the_runs_own_count(tmp_path):
    """The same pooled row, its count printed the way grand totals word it.

    The count path applies the totals path's covers guard: the pooled
    grand-wording count ("GRAND TOTAL Claims: 5") names a number that is not
    run 2's extracted count (2), so it is refused rather than adopted. Run 2's
    own printed count (2) still stands, so R-05 fires on nothing."""
    result = _packet(tmp_path, pooled=[_pooled_row("GRAND TOTAL Claims: 5")])
    one, two = result.document.runs
    assert two.printed_totals["incurred_total"] == Decimal("2800.00")
    assert two.printed_claim_count == 2
    assert one.printed_claim_count == 3
    assert not _findings(result, "R-05"), _shaped(_findings(result, "R-05"))
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.CLEAN}


def test_a_runs_own_wrong_count_still_fires_beside_a_pooled_grand_row(tmp_path):
    """Run 2's own count ("Number of claims: 1") is wrong for its two claims,
    and a pooled grand-wording row (5) sits on the same page. The guard refuses
    the pooled figure; the run is still checked against the count it printed
    itself, so R-05 fires for run 2 alone."""
    result = _packet(
        tmp_path, second_after=("Number of claims: 1",),
        pooled=[_pooled_row("GRAND TOTAL Claims: 5")])
    two = result.document.runs[1]
    assert two.printed_claim_count == 1
    assert _shaped(_findings(result, "R-05")) == [("run-2", "claim_count", "1", "2")]
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.NEEDS_REVIEW}


def test_a_pooled_grand_count_that_matches_no_run_is_not_adopted(tmp_path):
    """The pooled row's grand-wording count (7) matches neither run's extracted
    count (3, 2). It is refused outright and, run 2 printing no count of its
    own, the run is checked against nothing: no fabricated R-05 (which adopting
    the 7 would have raised against two claims), and the unadopted count is
    kept as unresolved evidence for R-27."""
    result = _packet(tmp_path, second_after=(), pooled=[_pooled_row("GRAND TOTAL Claims: 7")])
    one, two = result.document.runs
    assert one.printed_claim_count == 3
    assert two.printed_claim_count is None
    assert [entry["count"] for entry in two.printed_count_evidence] == [7]
    assert not _findings(result, "R-05"), _shaped(_findings(result, "R-05"))
    assert [(f.run_id, f.field) for f in _findings(result, "R-27")] == [
        ("run-2", "claim_count")]


def test_document_level_pooled_count_semantics_are_unchanged():
    """The guard is opt-in and per run. Called the way the document level always
    called it, a grand-wording count is still the document's own -- the packet
    path is the only caller that scopes it, and it does so by passing the count
    it extracted for that run."""
    totals = {2: "GRAND TOTAL Claims: 5"}
    assert document_claim_count(totals) == 5
    assert document_claim_count(totals, claim_count=2) is None
    assert document_claim_count(totals, claim_count=5) == 5


# ==========================================================================
# A run that prints no printed facts is checked against nothing
# ==========================================================================


def test_a_run_that_prints_no_count_or_total_is_checked_against_nothing(tmp_path):
    result = _read(tmp_path, [
        {"top": ("Page 1 of 1", LARGE_CARRIER, *LETTER), "rows": RUN_ONE},
        {"top": ("Page 1 of 1", SMALL_CARRIER, *LETTER), "headers": SMALL_HEADERS,
         "rows": RUN_TWO},
    ])
    for run in result.document.runs:
        assert run.printed_totals == {}
        assert run.printed_claim_count is None
        assert run.printed_count_evidence == []
    assert not _findings(result, "R-04")
    assert not _findings(result, "R-05")
    assert not _findings(result, "R-27")   # nothing stated, nothing unresolved
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.CLEAN}


def test_a_run_printing_nothing_inherits_no_invented_check(tmp_path):
    """Run 2 prints neither a count nor a total beside run 1, which prints
    both. Run 1 is checked and ties; nothing is invented for run 2 -- no
    fabricated total, no borrowed count, no unresolved-count complaint about
    figures it never stated."""
    result = _packet(tmp_path, second_after=(), second_total=None)
    one, two = result.document.runs
    assert one.printed_claim_count == 3 and one.printed_totals
    assert two.printed_claim_count is None and two.printed_totals == {}
    assert not _findings(result, "R-04")
    assert not _findings(result, "R-05")
    assert not _findings(result, "R-27")
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.CLEAN}


# ==========================================================================
# run_view: the run's own pages and printed facts, and nothing else's
# ==========================================================================


def test_run_view_carries_the_runs_own_pages_claims_and_printed_facts(tmp_path):
    result = _packet(tmp_path)
    document = result.document
    one, two = document.runs

    view = run_view(document, one)
    assert [claim.claim_number for claim in view.claims] == [row[0] for row in RUN_ONE]
    assert all(claim.source_page in one.pages for claim in view.claims)
    assert view.printed_claim_count == one.printed_claim_count == 3
    assert view.printed_totals == one.printed_totals
    assert view.printed_count_evidence == one.printed_count_evidence
    assert [claim.claim_number for claim in run_view(document, two).claims] == [
        row[0] for row in RUN_TWO]
    assert run_view(document, two).printed_claim_count == 2
    assert run_view(document, two).printed_totals == two.printed_totals


def test_moving_a_claims_page_moves_it_between_runs_views(tmp_path):
    """A claim's page is its provenance: edit the page and the claim moves
    between the runs' views, as ``run_claims`` and ``holds`` say it must."""
    result = _packet(tmp_path)
    document = result.document
    one, two = document.runs
    moved_number = RUN_ONE[0][0]
    claims = [
        claim.model_copy(update={"source_page": 2})
        if claim.claim_number == moved_number else claim
        for claim in document.claims
    ]
    mutated = document.model_copy(update={"claims": claims})
    assert moved_number not in [c.claim_number for c in run_view(mutated, one).claims]
    assert moved_number in [c.claim_number for c in run_view(mutated, two).claims]
    assert len(run_view(mutated, two).claims) == len(RUN_TWO) + 1


def test_run_view_reads_printed_facts_off_the_run_it_is_given(tmp_path):
    """The printed facts come from the run object handed in, never from the
    document's own -- a document-level figure would check every run against a
    total that never covered it."""
    result = _packet(tmp_path)
    document = result.document
    one = document.runs[0]
    assert run_view(document, one.model_copy(update={"printed_claim_count": 9})) \
        .printed_claim_count == 9
    changed = one.model_copy(update={
        "printed_totals": {"incurred_total": Decimal("999.00")}})
    assert run_view(document, changed).printed_totals == {
        "incurred_total": Decimal("999.00")}
