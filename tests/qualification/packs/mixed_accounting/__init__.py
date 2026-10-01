"""E6: twelve mixed-reader and accounting cases, truth from construction.

``build_cases(root)`` writes each case's PDF under ``root`` (scanned pages as
pictures) and returns the cases (``tools.qualification.cases``). Scanned
pages are read by an offline stand-in that transcribes exactly what the page
prints; the missing-replay case points at an empty recordings directory.

| Case | What it tests | Expectation |
|---|---|---|
| bounded-digital-then-scan | one numbered report, page 2 scanned | supported |
| bounded-scan-then-digital | one numbered report, page 1 scanned | supported |
| two-series-digital-packet | two carriers' reports, two numbering series | supported |
| mixed-series-packet | a digital report and a scanned report | supported |
| policy-change-boundary | two reports told apart only by their policy numbers | supported |
| cover-page-then-report | a cover letter before a two-page report | supported |
| foreign-page-bridged | another carrier's page inside a report's numbering | supported |
| missing-replay | a scanned report with no recording | review |
| same-number-two-carriers | one claim number printed by two carriers | supported |
| run-counts-packet | each report prints its own claim count | supported |
| section-counts-only | per-section counts and no report count | review |
| printed-count-mismatch | the printed count disagrees with the rows | review |
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from tests.qualification.packs.mixed_accounting.pages import (
    Page,
    Row,
    document_spec,
    offline_reader,
    printed,
    total_of,
    write,
)
from tools.qualification.cases import Expectation, QualificationCase, document_truth

ASHGROVE = "ASHGROVE MUTUAL INSURANCE COMPANY"
BLUE_LEDGER = "BLUE LEDGER CASUALTY COMPANY"
INSURED = "Named Insured: Ridgeway Test Freight LLC"
VALUATION = "Valuation Date: 12/31/2024"


def _rows(prefix: str, start: int, count: int, *, digits: bool = False) -> tuple[Row, ...]:
    rows = []
    for n in range(count):
        number = f"{prefix}{start + n}" if digits else f"{prefix}-{start + n}"
        month = 1 + (n * 2) % 11
        paid = 1000 + 375 * n
        reserve = 0 if n % 2 == 0 else 2500 + 100 * n
        status = "CLOSED" if reserve == 0 else "OPEN"
        # Days from 13 up: a date that reads only one way, so no case turns on
        # the date-order convention.
        rows.append(Row((number, f"{month:02d}/{13 + n % 15:02d}/2024", status, f"{paid:,.2f}",
                         f"{reserve:,.2f}", f"{paid + reserve:,.2f}"), number))
    return tuple(rows)


def _top(carrier: str | None, policy: str) -> tuple[str, ...]:
    return (*((carrier,) if carrier else ()), "LOSS RUN REPORT", INSURED,
            f"Policy Number: {policy}", VALUATION)


def _run(run_id: str, pages: Sequence[int], count: int | None, total, status="CLEAN"):
    return {"id": run_id, "pages": list(pages), "status": status,
            "printed": printed(count, total)}


def _case(root: Path, case_id: str, pages: Sequence[Page], spec: dict[str, Any], *,
          expectation=Expectation.SUPPORTED, replay: Path | None = None) -> QualificationCase:
    pdf = write(pages, root / f"{case_id}.pdf")
    scanned = any(page.scanned for page in pages)
    extractor = offline_reader(pages) if scanned and replay is None else None
    return QualificationCase(case_id, pdf, document_truth(case_id, pdf, spec), expectation,
                             replay=replay, extractor=extractor)


# --------------------------------------------------------------------------
# The cases
# --------------------------------------------------------------------------


def _bounded(scanned_page: int) -> tuple[list[Page], dict[str, Any]]:
    rows = _rows("AG", 24001, 8)
    total = total_of(rows)
    pages = [
        Page(_top(ASHGROVE, "GL-7001"), rows[:5], footer=("Page 1 of 2",),
             scanned=scanned_page == 1),
        Page(_top(ASHGROVE, "GL-7001"), rows[5:], total, footer=("Page 2 of 2",),
             scanned=scanned_page == 2),
    ]
    return pages, document_spec(pages, status="CLEAN", printed_facts=printed(None, total))


def _two_reports(scanned_second: bool) -> tuple[list[Page], dict[str, Any]]:
    first, second = _rows("AG", 24101, 5), _rows("7310", 450, 3, digits=True)
    pages = [
        Page(_top(ASHGROVE, "GL-7002"), first, total_of(first), footer=("Page 1 of 1",)),
        Page(_top(BLUE_LEDGER, "CA-3302"), second, total_of(second), footer=("Page 1 of 1",),
             scanned=scanned_second),
    ]
    runs = [_run("r1", [1], None, total_of(first)), _run("r2", [2], None, total_of(second))]
    return pages, document_spec(pages, status="CLEAN", runs=runs,
                                family="mixed" if scanned_second else "digital")


def _policy_change() -> tuple[list[Page], dict[str, Any]]:
    """Two one-page reports for the same insured under the same generic
    heading, unnumbered, told apart only by the policy number each prints. A person reads
    two reports; each is complete, so a correct reading is two clean runs --
    and must never merge them, which files one policy's claims under another."""
    first, second = _rows("AG", 24201, 6), _rows("7310", 470, 2, digits=True)
    pages = [Page(_top(None, "GL-7003"), first), Page(_top(None, "CA-3303"), second)]
    runs = [_run("r1", [1], None, None), _run("r2", [2], None, None)]
    return pages, document_spec(pages, status="CLEAN", runs=runs, family="digital")


def _cover_page() -> tuple[list[Page], dict[str, Any]]:
    rows = _rows("AG", 24301, 7)
    total = total_of(rows)
    pages = [
        Page(prose=("Ridgeway Test Freight LLC", "Attn: Underwriting",
                    "Please find enclosed the loss history you requested for the",
                    "commercial auto and general liability renewal.", "Regards,",
                    "Ashgrove Mutual Service Centre")),
        Page(_top(ASHGROVE, "GL-7004"), rows[:4], footer=("Page 1 of 2",)),
        Page(_top(ASHGROVE, "GL-7004"), rows[4:], total, footer=("Page 2 of 2",)),
    ]
    return pages, document_spec(pages, status="CLEAN", roles={1: "not_loss_run"},
                                printed_facts=printed(None, total), family="digital")


def _foreign_page() -> tuple[list[Page], dict[str, Any]]:
    own, foreign = _rows("AG", 24401, 6), _rows("7310", 490, 1, digits=True)
    pages = [
        Page(_top(ASHGROVE, "GL-7005"), own[:3], footer=("Page 1 of 2",)),
        Page(_top(BLUE_LEDGER, "CA-3305"), foreign),
        Page(_top(ASHGROVE, "GL-7005"), own[3:], total_of(own), footer=("Page 2 of 2",)),
    ]
    runs = [_run("r1", [1, 3], None, total_of(own)), _run("r2", [2], None, None)]
    return pages, document_spec(pages, status="CLEAN", runs=runs, family="digital")


def _missing_replay() -> tuple[list[Page], dict[str, Any]]:
    """The input lacks a recording for its scanned page: the honest reading of
    that input is unread, and must not pass as clean."""
    rows = _rows("AG", 24501, 5)
    total = total_of(rows)
    pages = [Page(_top(ASHGROVE, "GL-7006"), rows, total, footer=("Page 1 of 1",),
                  scanned=True)]
    return pages, document_spec(pages, status="NEEDS_REVIEW",
                                printed_facts=printed(None, total), family="scanned")


def _same_number() -> tuple[list[Page], dict[str, Any]]:
    """The same claim number printed by two carriers: two occurrences, never
    one. Two carriers may issue the same number (R-11 is scoped to one carrier
    and policy), and the loss dates differ, so it is not one claim counted
    twice: two settled reports read CLEAN."""
    first = _rows("5500", 101, 4, digits=True)
    repeat = Row(("5500103", "06/02/2024", "OPEN", "2,200.00", "1,800.00", "4,000.00"),
                 "5500103")
    second = (*_rows("5500", 201, 2, digits=True), repeat)
    pages = [
        Page(_top(ASHGROVE, "GL-7007"), first, total_of(first), footer=("Page 1 of 1",)),
        Page(_top(BLUE_LEDGER, "CA-3307"), second, total_of(second), footer=("Page 1 of 1",)),
    ]
    runs = [_run("r1", [1], None, total_of(first)),
            _run("r2", [2], None, total_of(second))]
    return pages, document_spec(pages, status="CLEAN", runs=runs, family="digital")


def _run_counts() -> tuple[list[Page], dict[str, Any]]:
    first, second = _rows("AG", 24601, 4), _rows("7310", 510, 2, digits=True)
    pages = [
        Page(_top(ASHGROVE, "GL-7008"), first, total_of(first),
             after=(f"Total Claims: {len(first)}",), footer=("Page 1 of 1",)),
        Page(_top(BLUE_LEDGER, "CA-3308"), second, total_of(second),
             after=(f"Total Claims: {len(second)}",), footer=("Page 1 of 1",)),
    ]
    runs = [_run("r1", [1], len(first), total_of(first)),
            _run("r2", [2], len(second), total_of(second))]
    return pages, document_spec(pages, status="CLEAN", runs=runs, family="digital")


def _section_counts() -> tuple[list[Page], dict[str, Any]]:
    """Each page counts its own section; nothing states the report's count,
    so the printed count cannot be checked (R-27 blocks by policy)."""
    first, second = _rows("AG", 24701, 3), _rows("AG", 24751, 2)
    pages = [
        Page(_top(ASHGROVE, "GL-7009"), first, after=(f"Claim Count = {len(first)}",),
             footer=("Page 1 of 2",)),
        Page(_top(ASHGROVE, "GL-7009"), second, total_of((*first, *second)),
             after=(f"Claim Count = {len(second)}",), footer=("Page 2 of 2",)),
    ]
    sections = [{"page": 1, "printed": printed(len(first), None)},
                {"page": 2, "printed": printed(len(second), None)}]
    return pages, document_spec(pages, status="NEEDS_REVIEW", sections=sections,
                                printed_facts=printed(None, total_of((*first, *second))),
                                family="digital")


def _count_mismatch() -> tuple[list[Page], dict[str, Any]]:
    rows = _rows("AG", 24801, 5)
    pages = [Page(_top(ASHGROVE, "GL-7010"), rows, total_of(rows),
                  after=("Total Claims: 6",), footer=("Page 1 of 1",))]
    return pages, document_spec(pages, status="NEEDS_REVIEW",
                                printed_facts=printed(6, total_of(rows)), family="digital")


def build_cases(root: Path) -> list[QualificationCase]:
    root = Path(root)
    review = Expectation.REVIEW
    return [
        _case(root, "bounded-digital-then-scan", *_bounded(2)),
        _case(root, "bounded-scan-then-digital", *_bounded(1)),
        _case(root, "two-series-digital-packet", *_two_reports(False)),
        _case(root, "mixed-series-packet", *_two_reports(True)),
        _case(root, "policy-change-boundary", *_policy_change()),
        _case(root, "cover-page-then-report", *_cover_page()),
        _case(root, "foreign-page-bridged", *_foreign_page()),
        _case(root, "missing-replay", *_missing_replay(), expectation=review,
              replay=root / "missing-replay-recordings"),
        _case(root, "same-number-two-carriers", *_same_number()),
        _case(root, "run-counts-packet", *_run_counts()),
        _case(root, "section-counts-only", *_section_counts(), expectation=review),
        _case(root, "printed-count-mismatch", *_count_mismatch(), expectation=review),
    ]
