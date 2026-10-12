"""A digital report bound beside a scanned report: the mixed-series-packet gap.

Qualification case ``mixed-series-packet`` (23/24 met) reads NEEDS_REVIEW where
a person reads two clean reports. Root cause, measured on ``main``:

* the scanned report's boundary rests on the vision model's reading of its
  ``Page 1 of 1`` label, so its run has ``RunConfidence.MODEL``;
* ``core.schema.split_is_settled`` is all-or-nothing over the packet, so that
  one run un-settles every boundary -- including the digital report's, whose
  own printed ``Page 1 of 1`` closes it;
* ``core.pipeline`` (``per_run_evidence = split_is_settled(runs)``) then
  attaches no printed totals to *either* run, and ``core.reconcile.reconcile``
  checks the whole packet's claims against the document-level total, which
  is the first report's alone. R-04 reports a financial discrepancy of exactly
  the second report's total -- a discrepancy nobody made (spec section 6,
  R-04) -- and R-25 calls the second report's own total unscoped.

The strict xfails below are the gap; the plain tests are the fail-closed
behaviour any fix must keep. Synthetic documents only; scanned pages are read
by the pack's offline transcription, never a model.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.pipeline import run_pipeline
from core.review import canonical_status
from core.schema import DocumentStatus
from tests.qualification.packs.mixed_accounting import (
    ASHGROVE,
    BLUE_LEDGER,
    _rows,
    _top,
)
from tests.qualification.packs.mixed_accounting.pages import (
    Page,
    offline_reader,
    total_of,
    write,
)

FIRST = _rows("AG", 24101, 5)
SECOND = _rows("7310", 450, 3, digits=True)


def _pages(*, second_scanned=True, first_scanned=False, first_footer="Page 1 of 1",
           second_total=None):
    return [
        Page(_top(ASHGROVE, "GL-7002"), FIRST, total_of(FIRST), footer=(first_footer,),
             scanned=first_scanned),
        Page(_top(BLUE_LEDGER, "CA-3302"), SECOND, second_total or total_of(SECOND),
             footer=("Page 1 of 1",), scanned=second_scanned),
    ]


def _read(tmp_path, pages, *, extractor=None, name="packet.pdf"):
    pdf = write(pages, tmp_path / name)
    scanned = any(page.scanned for page in pages)
    return run_pipeline(
        pdf, use_vision=scanned, profiles_dir=tmp_path / "profiles",
        vision_extractor=(extractor or offline_reader(pages)) if scanned else None,
    )


def _status(result):
    return canonical_status(result.reconciliation)


def _rules(result):
    return sorted({finding.rule_id for finding in result.reconciliation.findings})


# --- the control: the same two reports, both digital ------------------------


def test_the_all_digital_twin_reads_as_two_clean_runs(tmp_path):
    result = _read(tmp_path, _pages(second_scanned=False))
    assert [run.confidence.value for run in result.document.runs] == ["printed", "printed"]
    assert _status(result) is DocumentStatus.CLEAN
    assert all(run.printed_totals for run in result.document.runs)


# --- the gap -----------------------------------------------------------------


def test_the_scanned_run_is_bounded_by_the_model_and_unsettles_the_packet(tmp_path):
    """Pins the mechanism the fix has to change, so a fix cannot land unnoticed."""
    result = _read(tmp_path, _pages())
    runs = result.document.runs
    assert [run.confidence.value for run in runs] == ["printed", "model"]
    assert [run.pages for run in runs] == [[1], [2]]
    assert not any(run.ambiguous for run in runs)
    assert len(result.document.claims) == len(FIRST) + len(SECOND)


@pytest.mark.xfail(strict=True, reason=(
    "mixed-series-packet: one model-read boundary un-settles the whole packet "
    "(split_is_settled is all-or-nothing), so neither run gets its printed totals"))
def test_each_run_keeps_its_own_printed_totals(tmp_path):
    result = _read(tmp_path, _pages())
    assert all(run.printed_totals for run in result.document.runs)


@pytest.mark.xfail(strict=True, reason=(
    "mixed-series-packet: R-04 compares all eight claims with the first report's "
    "total and reports the second report's total as a discrepancy"))
def test_no_financial_discrepancy_is_reported_that_nobody_made(tmp_path):
    result = _read(tmp_path, _pages())
    assert "R-04" not in _rules(result)


@pytest.mark.xfail(strict=True, reason=(
    "mixed-series-packet: truth is two clean reports; reads NEEDS_REVIEW (R-04, R-25)"))
def test_a_clean_digital_report_beside_a_clean_scanned_report_reads_clean(tmp_path):
    result = _read(tmp_path, _pages())
    assert _status(result) is DocumentStatus.CLEAN


# --- what any fix must keep: fail closed -------------------------------------


def test_today_the_gap_fails_closed(tmp_path):
    """The over-flag is loud, never silent: whatever the reason, not CLEAN.

    The fix deletes this test together with the three strict xfails above."""
    result = _read(tmp_path, _pages())
    assert _status(result) is DocumentStatus.NEEDS_REVIEW


def test_two_model_read_reports_stay_unsettled(tmp_path):
    """No printed boundary anywhere: the split rests on readings alone."""
    result = _read(tmp_path, _pages(first_scanned=True, second_scanned=True))
    assert _status(result) is DocumentStatus.NEEDS_REVIEW


def test_a_scanned_report_whose_total_disagrees_still_needs_review(tmp_path):
    wrong = list(total_of(SECOND))
    wrong[-1] = "99,999.00"
    result = _read(tmp_path, _pages(second_total=tuple(wrong)))
    assert _status(result) is DocumentStatus.NEEDS_REVIEW


def test_a_digital_report_cut_short_before_the_scan_still_needs_review(tmp_path):
    """The first report prints 1 of 2 and its second page is missing: it is not
    closed, so nothing about the scanned page follows from its numbering."""
    result = _read(tmp_path, _pages(first_footer="Page 1 of 2"))
    assert _status(result) is DocumentStatus.NEEDS_REVIEW


def test_an_unread_scanned_report_still_needs_review(tmp_path):
    pages = _pages()
    blind = offline_reader([pages[0], replace(pages[1], scanned=False)])
    result = _read(tmp_path, pages, extractor=blind)
    assert _status(result) is DocumentStatus.NEEDS_REVIEW
