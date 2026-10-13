"""A pooled total naming another claim count is never a run's own total --
including for a run that printed no total of its own.

``pipeline._document_total`` refuses a totals row that names a claim count
other than the one extracted (``_covers(...) < 0``: "whatever it totals, it
is not these claims"). Where the run printed its own total, that row would
lose on rank anyway, so the existing pinning tests (``test_run_view_pinning``)
never notice if the refusal is removed: a mutation turning ``if covers < 0:``
into ``if False:`` survived them on Linux (2026-10-13 cross-check of #55).

The refusal only decides the outcome for a run with **no** total of its own:
the pooled packet-wide row is then the only candidate, and adopting it checks
the packet's money against one run's claims -- an R-04 discrepancy nobody
made. This pins that case. Synthetic documents only.
"""

from __future__ import annotations

from tests.test_run_view_pinning import _findings, _packet, _pooled_row, _shaped


def test_a_run_without_its_own_total_does_not_adopt_a_pooled_total(tmp_path):
    result = _packet(tmp_path, second_after=(), second_total=None,
                     pooled=[_pooled_row("Number of claims: 5")])
    one, two = result.document.runs
    assert one.printed_totals, "run 1 prints its own total and keeps it"
    assert two.printed_totals == {}, two.printed_totals
    assert not _findings(result, "R-04"), _shaped(_findings(result, "R-04"))


def test_a_pooled_row_without_a_count_is_still_not_run_twos_total(tmp_path):
    """The boundary the guard does not cover: a pooled row that names no count
    is judged by rank alone. Pinned so a change to that path is a decision."""
    result = _packet(tmp_path, second_after=(), second_total=None,
                     pooled=[_pooled_row()])
    two = result.document.runs[1]
    r04 = [(f.run_id, f.field) for f in _findings(result, "R-04")]
    # Whatever the rank does here, it must never be silent: either the pooled
    # money is not adopted, or adopting it is flagged against run 2's claims.
    assert two.printed_totals == {} or ("run-2", "incurred_total") in r04
