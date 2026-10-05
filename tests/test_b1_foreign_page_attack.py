"""B1 blind adversary: the qualification case ``foreign-page-bridged``.

A foreign carrier's unnumbered page sits inside a numbered report (its blank
page slot), so the run planner bridges it into the neighbouring run. The
behaviour contract under attack:

- the foreign page's printed claim rows must be read into the correct logical
  run with the correct canonical status, not merely named by R-23/R-28 while
  the claim stays missing;
- fail-closed behaviour must not weaken: a row that is not read is recorded
  (refused / unplaced) and reachable by a rule, and the document never reads
  CLEAN with a claim-like row unaccounted;
- no cross-run contamination: a claim from the foreign page is never counted
  under, and never borrows the vote or printed facts of, the neighbouring run;
- run boundaries hold: a page is placed in exactly one run, and identity
  collisions are named, never silently merged or split.

Every test asserts the SAFE outcome only, from the contract and invariants
(docs/agent/INVARIANTS.md 1, 2, 4, 7) -- not from any implementation. All
documents are synthetic (spec section 9): invented carriers, numbers, amounts.
Offline and deterministic: vision off, model keys unset.
"""

from __future__ import annotations

from datetime import date

import pytest

from core.runs import PageEvidence, heading_of, identity_of, paginations_in, plan_runs
from core.schema import DocumentStatus
from tests.test_packet_adversarial import LETTER, _read, _sheet, _total
from tests.test_packet_claim_series import (
    LARGE_CARRIER,
    LARGE_HEADERS,
    SMALL_CARRIER,
    SMALL_HEADERS,
    SMALL_RUN,
    _large_run,
)

#: The bridged scenario: report A numbers pages "1 of 3" and "3 of 3"; its
#: page 2 slot is filled by a foreign carrier's unnumbered page.
LARGE = _large_run(8)
FOREIGN_PAGE = 2
A_PAGES = (1, 3)

#: What the foreign page prints. Its claim-number shape differs from A's.
FOREIGN_CLAIM = SMALL_RUN[0]
BARE_FOREIGN = (FOREIGN_CLAIM[0], FOREIGN_CLAIM[1], FOREIGN_CLAIM[2], "", "", "")
DATED_CODE = ("0HA-MATERIAL", "11/23/2022", "", "", "", "")
#: A row whose identifier happens to sit in the neighbouring run's shape.
A_SHAPED = ("71009999", "05/05/2022", "OPEN", "2,000.00", "0.00", "2,000.00")
A_SHAPED_ZERO = ("71009999", "05/05/2022", "OPEN", "0.00", "0.00", "0.00")
#: The same claim number and loss date A prints on its own page 1.
A_COLLISION = ("71004411", "02/11/2022", "CLOSED", "500.00", "0.00", "500.00")
#: The foreign report's own valuation, different from A's.
FOREIGN_LETTER = ("LOSS RUN REPORT", "Valuation Date: 06/30/2023")

NAMING_RULES = ("R-22", "R-23", "R-28", "R-29")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# Sheet builders
# --------------------------------------------------------------------------


def _a_page(rows, *, label=None, total=None, after=()):
    top = ((label,) if label else ()) + (LARGE_CARRIER, *LETTER)
    return {"top": top, "rows": rows, "total": total, "after": after}


def _foreign_page(rows, *, label=None, headers=SMALL_HEADERS, letter=LETTER,
                  letterhead=True):
    if not letterhead:
        return {"top": (), "headers": headers, "rows": rows}
    top = ((label,) if label else ()) + (SMALL_CARRIER, *letter)
    return {"top": top, "headers": headers, "rows": rows}


def _bridged(foreign_rows, **foreign_kwargs):
    return [
        _a_page(LARGE[:3], label="Page 1 of 3"),
        _foreign_page(foreign_rows, **foreign_kwargs),
        _a_page(LARGE[3:], label="Page 3 of 3", total=_total(LARGE)),
    ]


# --------------------------------------------------------------------------
# Shared safe-outcome assertions
# --------------------------------------------------------------------------


def _numbers(result):
    return [claim.claim_number for claim in result.document.claims]


def _rules(result):
    return sorted({finding.rule_id for finding in result.reconciliation.findings})


def _refused(result):
    return [row.identifier for row in result.document.refused_claim_rows]


def _named_by(result, number):
    return sorted({finding.rule_id for finding in result.reconciliation.findings
                   if number in finding.message
                   or getattr(finding, "claim_number", None) == number
                   or number in (finding.subject or "")})


def _holders(result, page):
    return [run for run in result.document.runs if run.holds(page)]


def _where(result):
    return (f"runs={[(r.pages, r.ambiguous, sorted(r.blind) if hasattr(r, 'blind') else None) for r in result.document.runs]} "
            f"claims={_numbers(result)} refused={_refused(result)} "
            f"unplaced={[row.page for row in result.document.unplaced_rows]} "
            f"status={result.reconciliation.status.value} rules={_rules(result)}")


def _assert_accounted(result, number, page):
    """SAFE: the row is read as exactly one claim, or recorded (refused or
    unplaced) and reachable by a rule, and then the document is reviewed."""
    count = _numbers(result).count(number)
    if count:
        assert count == 1, f"{number} read {count} times with no collision finding"
        return True
    named = _named_by(result, number)
    recorded = number in _refused(result) or any(
        row.page == page for row in result.document.unplaced_rows)
    located = any(finding.rule_id in NAMING_RULES and str(page) in finding.message
                  for finding in result.reconciliation.findings)
    assert recorded and (named or located), (
        f"{number} printed on page {page} was neither read nor recorded and "
        f"named: {_where(result)}")
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW, (
        f"{number} is unaccounted yet the document reads "
        f"{result.reconciliation.status.value}: {_where(result)}")
    return False


def _assert_read_into_its_own_run(result, number, page, neighbours):
    """SAFE: a claim read off the foreign page is never counted under the
    neighbouring run -- the run holding it holds none of the neighbour's
    claims, and does not carry the neighbour's letterhead."""
    if not _numbers(result).count(number):
        return
    runs = result.document.runs
    if not runs:
        absorbed = [claim.claim_number for claim in result.document.claims
                    if claim.source_page in neighbours]
        assert not absorbed, (
            f"{number} was read into the document's single run beside the "
            f"neighbouring report's claims {absorbed}: {_where(result)}")
        return
    holders = _holders(result, page)
    assert len(holders) == 1, (
        f"page {page} is held by {len(holders)} runs: "
        f"{[run.pages for run in runs]}")
    holder = holders[0]
    absorbed = [claim.claim_number for claim in result.document.claims
                if claim.source_page in neighbours and holder.holds(claim.source_page)]
    assert not absorbed, (
        f"{number} (page {page}) was counted under run {holder.run_id} "
        f"(pages {holder.pages}) beside the neighbouring claims {absorbed}: "
        f"{_where(result)}")
    if holder.carrier:
        assert holder.carrier != LARGE_CARRIER, (
            f"the run holding {number} carries the neighbouring letterhead "
            f"{holder.carrier!r}: {_where(result)}")


def _assert_boundaries(result):
    """SAFE: a page sits in exactly one run, and every claim's source page is
    held by the run its claim was read into."""
    seen = {}
    for run in result.document.runs:
        for page in run.pages:
            assert page not in seen, (
                f"page {page} is placed in {seen[page]} and {run.run_id}")
            seen[page] = run.run_id
    if not result.document.runs:
        return
    for claim in result.document.claims:
        holders = _holders(result, claim.source_page)
        assert len(holders) == 1, (
            f"claim {claim.claim_number} (page {claim.source_page}) is held by "
            f"{[run.pages for run in holders]}: {_where(result)}")


def _assert_a_claims_read(result, rows):
    missing = [row[0] for row in rows if _numbers(result).count(row[0]) != 1]
    assert not missing, f"report A lost claims {missing}: {_where(result)}"


def _assert_clean_only_when_complete(result):
    """SAFE: CLEAN means every claim-like row was read into its run -- no
    refused rows, no unplaced money, and no run left unclean."""
    if result.reconciliation.status is not DocumentStatus.CLEAN:
        return
    assert not result.document.refused_claim_rows, _where(result)
    assert not result.document.unplaced_rows, _where(result)
    run_status = getattr(result.reconciliation, "run_status", {}) or {}
    for run_id, status in run_status.items():
        assert status is DocumentStatus.CLEAN, (
            f"document reads CLEAN while {run_id} is {status.value}")


# ==========================================================================
# The bridged page's own claim row
# ==========================================================================


def test_af1_foreign_claim_with_amounts_is_read_or_recorded_and_named(tmp_path):
    """The foreign page prints a full claim row in its own shape, with money.
    SAFE: it is read into exactly one run, or recorded (refused / unplaced)
    and named by a rule -- never missing while the packet moves on."""
    result = _read(tmp_path, _bridged([FOREIGN_CLAIM]))
    read = _assert_accounted(result, FOREIGN_CLAIM[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE)
    _assert_boundaries(result)
    _assert_clean_only_when_complete(result)
    if read:
        _assert_read_into_its_own_run(result, FOREIGN_CLAIM[0], FOREIGN_PAGE, A_PAGES)


def test_af2_foreign_claim_without_amounts_is_recorded_and_named(tmp_path):
    """A date and a status but no figures: the row cannot become a claim with
    money, but it is a claim-like row. SAFE: read, or recorded in
    refused_claim_rows and named -- and never a silent CLEAN."""
    result = _read(tmp_path, _bridged([BARE_FOREIGN]))
    _assert_accounted(result, BARE_FOREIGN[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE)
    _assert_boundaries(result)
    _assert_clean_only_when_complete(result)


# ==========================================================================
# Cross-run contamination: the foreign row in the neighbour's shape
# ==========================================================================


def test_af3_foreign_claim_in_the_neighbours_shape_is_not_counted_under_it(tmp_path):
    """The foreign page prints its claim with an identifier that happens to
    match the neighbouring run's claim-number shape. Bridged, the neighbour's
    vote accepts it and the foreign carrier's claim is read into A's run --
    counted under another report. SAFE: if the row is read at all, it is not
    in a run holding A's claims, and does not borrow A's letterhead."""
    result = _read(tmp_path, _bridged([A_SHAPED]))
    read = _assert_accounted(result, A_SHAPED[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE)
    _assert_boundaries(result)
    _assert_clean_only_when_complete(result)
    if read:
        _assert_read_into_its_own_run(result, A_SHAPED[0], FOREIGN_PAGE, A_PAGES)


def test_af3b_a_neighbour_total_that_absorbs_the_foreign_claim_is_not_clean(tmp_path):
    """The same, with A's printed grand total retyped so it happens to include
    the foreign claim's money: R-04 then ties over the contaminated run and
    the packet can read CLEAN with a foreign carrier's claim counted under A.
    SAFE: the claim is not counted under A's run, whatever the totals say."""
    sheets = [
        _a_page(LARGE[:3], label="Page 1 of 3"),
        _foreign_page([A_SHAPED]),
        _a_page(LARGE[3:], label="Page 3 of 3", total=_total([*LARGE, A_SHAPED])),
    ]
    result = _read(tmp_path, sheets)
    read = _assert_accounted(result, A_SHAPED[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE)
    _assert_clean_only_when_complete(result)
    if read:
        _assert_read_into_its_own_run(result, A_SHAPED[0], FOREIGN_PAGE, A_PAGES)
        assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW, (
            f"a foreign carrier's claim was absorbed into report A and the "
            f"packet reads CLEAN: {_where(result)}")


def test_af4_zero_amount_foreign_claim_in_the_neighbours_shape_is_not_a_false_clean(
        tmp_path):
    """A zero-amount foreign claim in A's shape changes no sum and meets no
    printed count, so a contaminated read disturbs nothing and can read CLEAN
    while the foreign carrier's claim sits in A's run. SAFE: CLEAN only if
    the claim was read into its own run."""
    result = _read(tmp_path, _bridged([A_SHAPED_ZERO]))
    read = _assert_accounted(result, A_SHAPED_ZERO[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE)
    _assert_clean_only_when_complete(result)
    if result.reconciliation.status is DocumentStatus.CLEAN:
        assert read, f"CLEAN with the foreign claim unaccounted: {_where(result)}"
        _assert_read_into_its_own_run(result, A_SHAPED_ZERO[0], FOREIGN_PAGE, A_PAGES)


# ==========================================================================
# The foreign page must not lend its vote, or borrow A's
# ==========================================================================


def test_af5_a_cause_code_on_the_foreign_page_never_becomes_a_claim(tmp_path):
    """The foreign page prints a claim row and a continuation line carrying a
    cause code. Whether the foreign rows are read under A's vote or judged on
    their own, the code is not a claim. SAFE: the code never appears in the
    claims, and the foreign claim row is still accounted."""
    result = _read(tmp_path, _bridged([FOREIGN_CLAIM, DATED_CODE]))
    assert DATED_CODE[0] not in _numbers(result), (
        f"the foreign page's cause code became a claim: {_where(result)}")
    _assert_accounted(result, FOREIGN_CLAIM[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE)
    _assert_clean_only_when_complete(result)


def test_af6_a_repeated_claim_number_across_the_foreign_page_is_named(tmp_path):
    """The foreign page repeats a claim number and loss date that A prints on
    its own page. SAFE: the collision is loud -- a finding names the number,
    or the repeat is recorded as refused -- and the packet is reviewed. Two
    occurrences are never merged into one silent claim."""
    result = _read(tmp_path, _bridged([A_COLLISION]))
    assert _numbers(result).count("71004410") == 1, _where(result)
    assert _numbers(result).count(A_COLLISION[0]) >= 1, _where(result)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW, (
        f"the same claim number and loss date print on pages 1 and 2 yet the "
        f"document reads {result.reconciliation.status.value}: {_where(result)}")
    assert _named_by(result, A_COLLISION[0]) or A_COLLISION[0] in _refused(result), (
        f"the repeated identity on page 2 is neither named nor recorded: "
        f"{_where(result)}")


# ==========================================================================
# Run boundaries
# ==========================================================================


def test_af7_the_foreign_page_is_held_by_exactly_one_run(tmp_path):
    """SAFE: the bridged page is placed in exactly one run, and every claim's
    source page is held by the run it was read into."""
    result = _read(tmp_path, _bridged([FOREIGN_CLAIM]))
    _assert_boundaries(result)
    _assert_accounted(result, FOREIGN_CLAIM[0], FOREIGN_PAGE)


def test_af8_an_incomplete_report_is_not_hidden_by_the_foreign_page(tmp_path):
    """A prints "Page 1 of 3" and nothing else of its own follows: pages 2-3
    are missing, and the foreign page fills the first gap. SAFE: the foreign
    row is accounted, A's read claims survive, and the packet never reads
    CLEAN with the foreign claim absorbed into A's run."""
    result = _read(tmp_path, [
        _a_page(LARGE[:3], label="Page 1 of 3"),
        _foreign_page([FOREIGN_CLAIM]),
    ])
    read = _assert_accounted(result, FOREIGN_CLAIM[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE[:3])
    _assert_boundaries(result)
    _assert_clean_only_when_complete(result)
    if read:
        _assert_read_into_its_own_run(result, FOREIGN_CLAIM[0], FOREIGN_PAGE, (1,))


def test_af9_a_numbered_foreign_report_between_a_pages_keeps_its_own_run(tmp_path):
    """The foreign page numbers itself "Page 1 of 1" and sits inside A's
    count. It is a complete one-page report: its claim is read into its own
    run -- never into A's -- and A's claims stay in A's runs."""
    result = _read(tmp_path, [
        _a_page(LARGE[:4], label="Page 1 of 2"),
        _foreign_page([FOREIGN_CLAIM], label="Page 1 of 1"),
        _a_page(LARGE[4:], label="Page 2 of 2", total=_total(LARGE)),
    ])
    read = _assert_accounted(result, FOREIGN_CLAIM[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE)
    _assert_boundaries(result)
    _assert_clean_only_when_complete(result)
    if read:
        _assert_read_into_its_own_run(result, FOREIGN_CLAIM[0], FOREIGN_PAGE, (1, 3))


def test_af10_two_foreign_pages_inside_the_count_are_both_accounted(tmp_path):
    """Two unnumbered foreign pages fill A's missing slots 2 and 3. SAFE:
    every foreign claim row is accounted, and any that is read is not counted
    under A's run."""
    result = _read(tmp_path, [
        _a_page(LARGE[:2], label="Page 1 of 4"),
        _foreign_page([SMALL_RUN[0]]),
        _foreign_page([SMALL_RUN[1]]),
        _a_page(LARGE[2:], label="Page 4 of 4", total=_total(LARGE)),
    ])
    _assert_accounted(result, SMALL_RUN[0][0], 2)
    _assert_accounted(result, SMALL_RUN[1][0], 3)
    _assert_a_claims_read(result, LARGE)
    _assert_boundaries(result)
    _assert_clean_only_when_complete(result)
    _assert_read_into_its_own_run(result, SMALL_RUN[0][0], 2, (1, 4))
    _assert_read_into_its_own_run(result, SMALL_RUN[1][0], 3, (1, 4))


def test_af11_a_foreign_page_without_a_letterhead_is_accounted(tmp_path):
    """No letterhead at all on the foreign page -- only its table. SAFE: its
    claim row is still read or recorded and named, and if read it does not
    join A's run."""
    result = _read(tmp_path, _bridged([FOREIGN_CLAIM], letterhead=False))
    read = _assert_accounted(result, FOREIGN_CLAIM[0], FOREIGN_PAGE)
    _assert_a_claims_read(result, LARGE)
    _assert_boundaries(result)
    _assert_clean_only_when_complete(result)
    if read:
        _assert_read_into_its_own_run(result, FOREIGN_CLAIM[0], FOREIGN_PAGE, A_PAGES)


# ==========================================================================
# Borrowed facts
# ==========================================================================


def test_af12_foreign_run_facts_come_from_the_foreign_pages_own_lines(tmp_path):
    """The foreign report prints its own valuation date, different from A's.
    SAFE: a run holding the foreign claim never reports A's valuation as its
    own -- its facts come from its pages or it says nothing."""
    result = _read(tmp_path, [
        _a_page(LARGE[:4], label="Page 1 of 2"),
        _foreign_page([FOREIGN_CLAIM], label="Page 1 of 1", letter=FOREIGN_LETTER),
        _a_page(LARGE[4:], label="Page 2 of 2", total=_total(LARGE)),
    ])
    _assert_a_claims_read(result, LARGE)
    holders = _holders(result, FOREIGN_PAGE)
    for run in holders:
        if run.carrier == SMALL_CARRIER:
            assert run.valuation_date in (None, date(2023, 6, 30)), (
                f"run {run.run_id} borrowed the valuation "
                f"{run.valuation_date}: {_where(result)}")
            assert run.valuation_date_text in (None, "06/30/2023"), (
                f"run {run.run_id} borrowed the valuation text "
                f"{run.valuation_date_text!r}: {_where(result)}")


# ==========================================================================
# The planner itself
# ==========================================================================


def _ev(page, label=None, heading=None):
    found = tuple(paginations_in(label, "header")) if label else ()
    return PageEvidence(page, paginations=found, identity=identity_of(heading),
                        heading=heading_of(heading))


def test_af13_planner_never_absorbs_a_foreign_page_without_a_mark(tmp_path):
    """Two foreign pages fill the numbered report's missing slots. SAFE: any
    foreign page placed inside the numbered run is marked blind, or the run
    says the boundary is unsettled -- never silently absorbed."""
    evidence = {
        1: _ev(1, "Page 1 of 4", "Northfield loss run"),
        2: _ev(2, heading="Harbor Crest loss run"),
        3: _ev(3, heading="Harbor Crest loss run"),
        4: _ev(4, "Page 4 of 4", "Northfield loss run"),
    }
    segments = plan_runs([1, 2, 3, 4], evidence, {1, 2, 3, 4})
    for segment in segments:
        absorbed = ({2, 3} & set(segment.pages)) and ({1, 4} & set(segment.pages))
        if absorbed:
            assert ({2, 3} & segment.blind) or segment.ambiguous, (
                f"pages 2-3 were absorbed into pages {segment.pages} with no "
                f"blind mark and no unsettled boundary "
                f"(blind={sorted(segment.blind)})")
