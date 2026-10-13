"""A change of printed policy number between unnumbered pages begins another report.

With no page numbering anywhere, every page was joined to the report before
it, whatever it printed. Two reports for the same insured under one generic
heading, told apart only by their policy numbers, were read as one report and
could read CLEAN -- filing one policy's claims under the other's number.

A policy number printed in the page's header band is now part of the page's
evidence, and an unnumbered page printing a different one from the unnumbered
report before it begins another report (inferred, like a positively different
heading after a finished report). The same number, or none, changes nothing.

Synthetic PDFs only.
"""

from __future__ import annotations

import pymupdf

from core.pipeline import run_pipeline
from core.review import canonical_status
from core.runs import PageEvidence, plan_runs
from core.schema import DocumentStatus, RunConfidence

LEFT, LINE = 36.0, 14.0
COLUMNS = (0, 95, 170, 240, 325, 410)
HEADERS = ("Claim Number", "Loss Date", "Status", "Paid Total", "Reserve Total",
           "Incurred Total")


def _rows(prefix, start, count, digits=False):
    rows = []
    for n in range(count):
        number = f"{prefix}{start + n}" if digits else f"{prefix}-{start + n}"
        paid = 1000 + 250 * n
        rows.append((number, f"{1 + n:02d}/{14 + n:02d}/2024", "CLOSED", f"{paid:,.2f}",
                     "0.00", f"{paid:,.2f}"))
    return rows


def _pdf(tmp_path, pages):
    document = pymupdf.open()
    for policy, rows in pages:
        page = document.new_page(width=612, height=792)
        y = 40.0
        for line in ("LOSS RUN REPORT", "Named Insured: Ridgeway Test Freight LLC",
                     f"Policy Number: {policy}", "Valuation Date: 12/31/2024"):
            page.insert_text((LEFT, y), line, fontsize=9)
            y += LINE
        y += LINE
        for x, label in zip(COLUMNS, HEADERS):
            page.insert_text((LEFT + x, y), label, fontsize=8)
        y += LINE
        for row in rows:
            for x, cell in zip(COLUMNS, row):
                page.insert_text((LEFT + x, y), cell, fontsize=8)
            y += LINE
    path = tmp_path / "policies.pdf"
    document.save(path)
    document.close()
    return path


def _read(tmp_path, pages):
    return run_pipeline(_pdf(tmp_path, pages), use_vision=False,
                        profiles_dir=tmp_path / "profiles")


def test_two_policies_are_two_reports_and_every_claim_is_read(tmp_path):
    first, second = _rows("AG", 24201, 6), _rows("7310", 470, 2, digits=True)
    result = _read(tmp_path, [("GL-7003", first), ("CA-3303", second)])
    runs = result.document.runs
    assert [run.pages for run in runs] == [[1], [2]]
    assert [c.claim_number for c in result.document.claims] == [r[0] for r in first + second]
    assert [run.policy_number for run in runs] == ["GL-7003", "CA-3303"]


def test_a_merged_reading_of_two_policies_can_no_longer_read_clean(tmp_path):
    """The defect: one run, CLEAN, one policy's claims under the other's number."""
    first, second = _rows("AG", 24201, 6), _rows("7310", 470, 2, digits=True)
    result = _read(tmp_path, [("GL-7003", first), ("CA-3303", second)])
    merged = len(result.document.runs) < 2
    assert not (merged and canonical_status(result.reconciliation) is DocumentStatus.CLEAN)


def test_the_same_policy_on_every_page_stays_one_report(tmp_path):
    rows = _rows("AG", 24301, 8)
    result = _read(tmp_path, [("GL-7003", rows[:4]), ("GL-7003", rows[4:])])
    assert result.document.runs == []
    assert len(result.document.claims) == 8


def test_spacing_and_case_do_not_make_a_different_policy(tmp_path):
    rows = _rows("AG", 24401, 6)
    result = _read(tmp_path, [("GL-7003", rows[:3]), ("gl-7003", rows[3:])])
    assert result.document.runs == []


def test_the_planner_splits_only_on_a_printed_difference():
    def evidence(page, policy):
        return PageEvidence(page=page, identity="ridgeway freight", policy=policy)

    split = plan_runs([1, 2], {1: evidence(1, "GL-7003"), 2: evidence(2, "CA-3303")}, [1, 2])
    assert [s.pages for s in split] == [[1], [2]]
    assert split[1].confidence is RunConfidence.INFERRED and not split[1].ambiguous
    for second in ("GL-7003", None):
        joined = plan_runs([1, 2], {1: evidence(1, "GL-7003"), 2: evidence(2, second)}, [1, 2])
        assert [s.pages for s in joined] == [[1, 2]]
