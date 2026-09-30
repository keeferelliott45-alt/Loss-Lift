"""The carrier's printed count and totals stay with the claims they count.

Found by the corpus gate on PR #9 (run 36481207500): a 99-page report the run
planner split into six runs, five of them on boundaries read by OCR or on
nothing at all. The count the carrier printed (28, tying the 28 claims read)
was re-attributed to a run holding none of the claims, and the grand total to
another. A document whose count and total tied now reported R-04, R-05 and
R-20 discrepancies nobody made, and the only checks against what the carrier
printed were lost. The structure is reproduced here synthetically; nothing
from the document is committed.

Printed evidence is split per run only across a settled packet: every
boundary printed and unambiguous (``schema.split_is_settled``). Otherwise the
runs are kept -- R-28 still names the boundaries -- and R-04, R-05, R-25,
R-26 and R-27 are asked once, of the whole document.
"""

from __future__ import annotations

from decimal import Decimal

from core.schema import split_is_settled
from tests.test_packet_adversarial import LETTER, _read, _total
from tests.test_packet_claim_series import LARGE_CARRIER, _large_run

BODY = _large_run(8)
PRINTED_EVIDENCE = {"R-04", "R-05", "R-25", "R-26", "R-27"}


def _summary_after_report(tmp_path, total):
    """Claims on pages numbered 1 and 2 of 2; the grand total and the claim
    count on an unnumbered summary page after them."""
    return _read(tmp_path, [
        {"top": (LARGE_CARRIER, *LETTER), "rows": BODY[:4], "bottom": ("Page 1 of 2",)},
        {"top": (LARGE_CARRIER, *LETTER), "rows": BODY[4:], "bottom": ("Page 2 of 2",)},
        {"top": ("SUMMARY",), "rows": [], "total": total,
         "after": ("Number of claims: 8",)},
    ])


def _found(result, rule_id):
    return [f for f in result.reconciliation.findings if f.rule_id == rule_id]


def test_the_split_is_unsettled_and_still_named(tmp_path):
    result = _summary_after_report(tmp_path, _total(BODY))
    runs = result.document.runs
    assert len(runs) == 2 and not split_is_settled(runs)
    assert _found(result, "R-28"), "the unsettled boundary must still be named"


def test_a_tying_total_and_count_raise_nothing(tmp_path):
    """At 4ca0d41: R-04 x2, R-05 and R-20 on the summary page's run."""
    result = _summary_after_report(tmp_path, _total(BODY))
    document = result.document
    assert document.printed_claim_count == 8 == len(document.claims)
    assert set(document.printed_totals) >= {"paid_total", "incurred_total"}
    assert all(not run.printed_totals and run.printed_claim_count is None
               for run in document.runs)
    raised = {f.rule_id for f in result.reconciliation.findings}
    assert not raised & (PRINTED_EVIDENCE | {"R-20"}), sorted(raised)


def test_a_wrong_total_is_still_caught_for_the_whole_document(tmp_path):
    """Fail closed: moving the check to the whole document must not lose it."""
    total = list(_total(BODY))
    paid = Decimal(total[3].replace(",", "")) + Decimal("1.00")
    total[3] = f"{paid:,.2f}"
    result = _summary_after_report(tmp_path, tuple(total))
    r04 = _found(result, "R-04")
    assert [f.field for f in r04] == ["paid_total"]
    assert r04[0].run_id is None
    assert r04[0].actual is not None and r04[0].expected is not None


def test_a_settled_packet_still_checks_each_run_against_its_own_total(tmp_path):
    first, second = _large_run(4), _large_run(3)
    wrong = list(_total(second))
    wrong[5] = f"{Decimal(wrong[5].replace(',', '')) + Decimal('1.00'):,.2f}"
    result = _read(tmp_path, [
        {"top": (LARGE_CARRIER, *LETTER), "rows": first, "total": _total(first),
         "bottom": ("Page 1 of 1",)},
        {"top": ("Other Mutual Insurance Company", "LOSS RUN REPORT",
                 "Valuation Date: 12/31/2022"),
         "rows": second, "total": tuple(wrong), "bottom": ("Page 1 of 1",)},
    ])
    assert split_is_settled(result.document.runs)
    r04 = _found(result, "R-04")
    assert [(f.run_id, f.field) for f in r04] == [("run-2", "incurred_total")]
    assert not result.document.printed_totals, "a settled packet has no total of its own"
