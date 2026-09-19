"""A collected subtotal is not a checked subtotal.

Recognising AIG's per-policy totals put four numbers on the document. It did
not verify anything: the sections carry no policy period, the period summaries
attach a section by period start, and R-04 reads only the document-level
totals, which AIG has none of. Changing a captured section total from
30,692.75 to 999,999.00 left every finding and every summary identical. The
number had been captured and nothing was comparing it to anything.

That is the worse of the two failure modes this file guards. A document that
displays a carrier's printed subtotal, with no rule behind it, invites a
reviewer to believe a figure has been reconciled when nothing has looked at
it -- and the reconciliation layer is the entire product.

Scope has to come from evidence, not convenience. AIG prints its subtotal at
the foot of the page whose claims it totals, and says how many claims that is:
"Claim Count = 4" over a page holding exactly four extracted claims. A count
that matches the page is the carrier stating the scope. Where it does not
match -- a section spanning pages, a page whose claims were partly missed --
the scope is not established, and the section is reported as unchecked rather
than checked against whatever happens to be nearby.

The second failure mode is the silent one. Where a money cell on a totals row
cannot be read -- "4 30,000.00", the claim count fused to the amount beside it
-- refusing it is right, but dropping it without a word means R-04 quietly
stops checking that column and no one is told a printed figure was discarded.
The printed text and its page have to survive the refusal.

Geometry and row shape follow AIG's real pages; identifiers and names are
synthetic (spec section 9).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from core.pipeline import run_pipeline
from core.reconcile import reconcile

CORPUS = Path(
    "/tmp/claude-0/-home-user-Loss-Lift/61d8a697-b242-526b-9889-610e5598d380"
    "/scratchpad/real/aig.pdf"
)

pytestmark = pytest.mark.skipif(
    not CORPUS.exists(),
    reason="reference corpus not present; see the handoff for its location",
)


@pytest.fixture(scope="module")
def aig(tmp_path_factory):
    return run_pipeline(
        str(CORPUS),
        use_vision=False,
        profiles_dir=tmp_path_factory.mktemp("profiles"),
    ).document


def test_sections_carry_the_claims_they_cover(aig):
    """The scope, recorded rather than re-derived by every consumer."""
    # ``scope_known``, not a non-empty ``covers_rows``: page 3 covers no
    # claims and that is an established scope, not a failure to establish one.
    scoped = [s for s in aig.printed_sections if s.scope_known]
    assert len(scoped) == 4, [
        (s.page, s.printed_claim_count, s.covers_rows) for s in aig.printed_sections
    ]
    by_page = {s.page: s for s in aig.printed_sections}
    # Page 3 prints "No Claims for Policy" and a zero count; it covers nothing,
    # and that is a scope, not a failure to establish one.
    assert by_page[3].printed_claim_count == 0
    assert by_page[3].covers_rows == []
    for page in (2, 4, 5):
        rows = by_page[page].covers_rows
        assert len(rows) == 4, (page, rows)
        assert all(row.startswith(f"p{page}r") for row in rows), (page, rows)


def test_a_wrong_section_total_is_now_caught(aig):
    """The review's own probe: mutate a captured total, and a rule must move.

    Every finding was identical before this; the number was being displayed,
    not checked.
    """
    before = {(f.rule_id, f.message) for f in reconcile(aig).findings}
    changed = aig.model_copy(deep=True)
    target = next(
        s for s in changed.printed_sections
        if s.printed_totals.get("incurred_total") == Decimal("30692.75")
    )
    target.printed_totals["incurred_total"] = Decimal("999999.00")
    after = {(f.rule_id, f.message) for f in reconcile(changed).findings}
    assert after != before, "a wrong printed subtotal still raises nothing"
    new = after - before
    assert any(rule == "R-25" for rule, _ in new), sorted(new)


def test_the_real_sections_tie_against_their_own_claims(aig):
    """AIG's printed subtotals are correct, and now demonstrably so.

    Page 4 prints incurred 30,692.75, and its four claims are 525.00 +
    15,017.75 + 15,000.00 + 150.00. That equality is the product's whole
    proposition, and until now nothing was computing it.
    """
    findings = [f for f in reconcile(aig).findings if f.rule_id == "R-25"]
    mismatches = [f for f in findings if f.condition.startswith("mismatch")]
    assert mismatches == [], [f.message for f in mismatches]


def test_a_refused_money_cell_keeps_its_text_and_page(aig):
    """"4 30,000.00" is correctly refused; the refusal must not be silent.

    Page 4 prints paid indemnity 30,000.00 with the claim count fused to it by
    a column boundary. Reading it as 430,000.00 would be worse, so it is
    withheld -- and a reviewer is told which page, which column, and what the
    document actually shows.
    """
    findings = [f for f in reconcile(aig).findings if f.rule_id == "R-26"]
    assert findings, "the withheld printed total is reported nowhere"
    pages = {f.page for f in findings}
    assert 4 in pages, sorted(pages)
    on_four = next(f for f in findings if f.page == 4)
    assert "30,000.00" in on_four.message, on_four.message
    assert "paid indemnity" in on_four.message.lower(), on_four.message


def test_the_claim_records_are_untouched(aig):
    """None of this may move a claim. It reports on printed totals only."""
    assert len(aig.claims) == 12
    by_row = {c.row_id: c for c in aig.claims}
    assert by_row["p4r12"].incurred_total == Decimal("15017.75")
    assert by_row["p4r9"].reserve_total == Decimal("500.00")


LIBERTY = CORPUS.parent / "liberty.pdf"


@pytest.mark.skipif(not LIBERTY.exists(), reason="reference corpus not present")
def test_a_subtotal_printed_twice_on_one_page_keeps_two_identities(tmp_path):
    """Liberty prints its report total on page 41 in two identical rows.

    Keyed on page and label alone they are one object, and the engine's own
    duplicate-identity check rejected the document outright. They are two
    printed rows, so they are two sections and two findings: dismissing the
    one must never answer for the other.
    """
    document = run_pipeline(
        str(LIBERTY), use_vision=False, profiles_dir=tmp_path
    ).document
    on_41 = [s for s in document.printed_sections if s.page == 41]
    assert len(on_41) == 2, [(s.page, s.line_index, s.label) for s in on_41]
    assert on_41[0].line_index != on_41[1].line_index, on_41
    conditions = [
        f.condition for f in reconcile(document).findings if f.rule_id == "R-25"
    ]
    assert len(conditions) == len(set(conditions)), conditions
