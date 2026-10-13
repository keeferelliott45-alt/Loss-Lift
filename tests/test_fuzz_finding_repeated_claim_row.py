"""Fuzz finding (2026-10-12 campaign): a figureless claim row repeated across a
page break is taken for page furniture and both copies disappear.

Found as UNNAMED in the hostile family (seed 21427,
``blank_date, missing_amounts, split_claim_across_pages``): the document read
NEEDS_REVIEW only because its printed total and count disagreed. Without them
it reads **CLEAN with a claim missing** -- a SILENT failure.

Mechanism (``core/pipeline.py``):

* ``page_furniture`` pools row text that repeats on at least
  ``max(2, pages // 2)`` pages, skipping only rows that carry numeric evidence;
* a claim whose row carries a claim number and a status but no date and no
  amount -- printed as the last row of page 1 and repeated as the first row of
  page 2, the usual "continued" layout -- has identical text on both pages;
* ``build_claims`` skips every occurrence of pooled text that carries no
  numeric evidence, before it asks whether the row opens a claim. Both copies
  are dropped: not read, not refused, not counted in ``rows_seen_per_page``,
  not warned about. No rule can see them.

The strict xfails are the defect; the plain tests are the boundaries any fix
must keep. Synthetic documents only (the fuzz generator).
"""

from __future__ import annotations

import copy

import pytest

from core.review import canonical_status
from core.schema import DocumentStatus
from tests.fuzz.oracle import SILENT, classify
from tests.fuzz.run import build_case, run_case

SEED, OPERATORS = 21427, ("blank_date", "missing_amounts", "split_claim_across_pages")
FIGURELESS = ("71004410", "", "CLOSED", "", "", "")
FULL = ("71004411", "11/23/2022", "OPEN", "500.00", "1,000.00", "1,500.00")
OTHER = ("71004412", "10/01/2022", "OPEN", "100.00", "0.00", "100.00")


def _case(page_one, page_two, *, printed=False):
    """The campaign's two-page report, with chosen rows and (by default) no
    printed total or count -- the shape in which nothing else can catch it."""
    case = copy.deepcopy(build_case(SEED, OPERATORS))
    case.pages[0].rows, case.pages[1].rows = list(page_one), list(page_two)
    if not printed:
        for page in case.pages:
            page.total, page.after = None, []
    return case


def _read(tmp_path, case, tag):
    result = run_case(case, tmp_path, tag)
    return result, [claim.claim_number for claim in result.document.claims]


# --- the defect ----------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=(
    "fuzz finding: page_furniture swallows a figureless claim row repeated "
    "across a page break; the campaign case reads CLEAN with a claim missing"))
def test_the_campaign_case_without_printed_figures_is_not_silent(tmp_path):
    case = copy.deepcopy(build_case(SEED, OPERATORS))
    for page in case.pages:
        page.total, page.after = None, []
    result = run_case(case, tmp_path, "campaign")
    assert classify(case, result).category != SILENT


@pytest.mark.xfail(strict=True, reason=(
    "fuzz finding: both copies of a repeated figureless claim row are dropped "
    "as running furniture before the row is asked whether it opens a claim"))
def test_a_continued_figureless_claim_is_read_or_named(tmp_path):
    result, numbers = _read(tmp_path, _case([OTHER, FIGURELESS], [FIGURELESS, FULL]), "continued")
    assert FIGURELESS[0] in numbers or \
        canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


@pytest.mark.xfail(strict=True, reason=(
    "fuzz finding: the dropped copies are not even counted as rows seen, so "
    "R-19 cannot notice them either (rows_seen_per_page applies the same "
    "furniture pool; the fix has to exempt claim rows there too)"))
def test_a_repeated_claim_row_is_counted_as_a_row_seen(tmp_path):
    result, _numbers = _read(tmp_path, _case([FIGURELESS], [FIGURELESS, FULL]), "seen")
    assert result.document.rows_seen_per_page.get(1, 0) >= 1


# --- boundaries any fix must keep ----------------------------------------------


def test_the_same_row_unrepeated_is_read_as_a_claim(tmp_path):
    result, numbers = _read(tmp_path, _case([FIGURELESS], [FULL]), "single")
    assert FIGURELESS[0] in numbers
    # Read, and its missing date and amounts are reported, never zeroed.
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def test_with_printed_figures_the_loss_is_loud(tmp_path):
    result, numbers = _read(tmp_path, _case([FIGURELESS], [FIGURELESS, FULL], printed=True),
                            "printed")
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def test_a_true_running_header_line_is_still_furniture(tmp_path):
    """A line with no claim identity repeated on every page stays out of the
    claims: the fix must exempt claim rows, not repetition in general."""
    case = _case([OTHER], [FULL])
    for page in case.pages:
        page.rows = [("CONFIDENTIAL", "", "", "", "", "")] + list(page.rows)
    result, numbers = _read(tmp_path, case, "header")
    assert "CONFIDENTIAL" not in numbers
    assert sorted(numbers) == sorted([OTHER[0], FULL[0]])
