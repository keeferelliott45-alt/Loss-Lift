"""SAFE / SILENT / LOUD classification against the one status policy.

The oracle knows the truth about the case (``Case``): every claim really
printed, its money as ``Decimal``, which logical run printed it, and what each
page printed as totals and counts. It then reads only
``core.review.canonical_status`` and the findings, never any second definition
of status.

* SAFE   -- every true claim read exactly once in the right run with right
            money and no extra claims, or NEEDS_REVIEW with a finding that
            names the responsible page, run, claim or rule.
* SILENT -- canonical CLEAN while any of those is false. This is a bug.
* LOUD   -- NEEDS_REVIEW on a document that was in fact read correctly.
* UNNAMED-- NEEDS_REVIEW, but no finding names the defect.
* MERGED -- a packet's runs were not kept apart; tracked, not counted SILENT
            unless a claim is actually lost or duplicated by it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from core.review import canonical_status
from core.schema import DocumentStatus

from tests.fuzz.generator import Case, parse_printed

TOL = Decimal("0.005")

SAFE = "SAFE"
SILENT = "SILENT"
LOUD = "LOUD"
UNNAMED = "UNNAMED"
MERGED = "MERGED"

_COUNT_RE = re.compile(r"Number of claims:\s*(\d+)", re.IGNORECASE)

#: Findings that surface rows or pages the reading could not place. Used to
#: decide whether an unnamed claim is at least covered by a rule.
_UNACCOUNTED = frozenset({"R-19", "R-22", "R-23", "R-28", "R-29"})


@dataclass
class Defect:
    kind: str
    token: str
    named: bool
    detail: str = ""


@dataclass
class Outcome:
    category: str
    status: str
    defects: list[Defect] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    merged: bool = False

    @property
    def silent(self) -> bool:
        return self.category == SILENT


def _eq(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= TOL


def _printed(text: str) -> Decimal:
    """Read a printed money cell in US or European convention, signed or not."""
    return parse_printed(text)


def _mentions(findings, token: str) -> bool:
    for finding in findings:
        if token and (finding.claim_number == token or token in (finding.message or "")):
            return True
    return False


def _any_rule(findings, rules) -> bool:
    return any(finding.rule_id in rules for finding in findings)


def _heading(spec) -> str:
    """The page's own naming line, ignoring its page numbering."""
    lines = [line for line in spec.top if line]
    if lines and lines[0].lower().startswith("page "):
        return lines[1] if len(lines) > 1 else ""
    return lines[0] if lines else ""


def _numbering_restarts(spec) -> bool:
    return bool(spec.top) and spec.top[0].lower().startswith("page 1 ")


def _runs_distinguishable(case: Case, run_indexes=None) -> bool:
    """Whether these runs print furniture that tells them apart.

    Two unnumbered pages under the same heading with the same claim shapes are
    genuinely one report as far as the document says, so a reader that keeps
    them together is not losing a boundary. A different heading, or a page
    that restarts its numbering, is printed evidence that was ignored.
    """
    indexes = set(run_indexes) if run_indexes is not None else {c.run for c in case.claims}
    headings = {_heading(spec) for spec in case.pages if spec.run in indexes and spec.top}
    if len(headings) > 1:
        return True
    ordered = [spec for spec in case.pages if spec.run in indexes]
    return any(_numbering_restarts(spec) for spec in ordered[1:])


def classify(case: Case, result) -> Outcome:
    reconciliation = result.reconciliation
    document = result.document
    status = canonical_status(reconciliation)
    findings = list(reconciliation.findings)
    rules = sorted({finding.rule_id for finding in findings})

    claims = list(document.claims)
    read_numbers = [claim.claim_number for claim in claims]
    counts = Counter(read_numbers)
    truth = case.claim_by_number()
    defects: list[Defect] = []

    # -- value and identity defects ------------------------------------
    for number, seen in counts.items():
        if seen > 1:
            defects.append(Defect(
                "duplicate", number,
                named=_mentions(findings, number) or _any_rule(findings, {"R-11", "R-12"}),
                detail=f"{number} read {seen} times",
            ))

    for number, claim in truth.items():
        matches = [c for c in claims if c.claim_number == number]
        if not matches:
            named = _mentions(findings, number) or _any_rule(findings, _UNACCOUNTED)
            defects.append(Defect("missing", number, named=named,
                                  detail=f"run {claim.run} page {claim.page}"))
            continue
        if len(matches) > 1:
            continue
        read = matches[0]
        if not (_eq(read.paid_total, claim.paid)
                and _eq(read.reserve_total, claim.reserve)
                and _eq(read.incurred_total, claim.incurred)):
            named = (read.claim_number == claim.number
                     or _mentions(findings, number)
                     or _any_rule(findings, {"R-01", "R-04", "R-25"}))
            defects.append(Defect(
                "money", number, named=named,
                detail=f"expected {claim.incurred}, read {read.incurred_total}",
            ))

    for number in counts:
        if number not in truth:
            defects.append(Defect(
                "invented", number, named=_mentions(findings, number),
                detail="not a printed claim",
            ))

    # -- printed totals and counts against the truth --------------------
    for index, spec in enumerate(case.pages, start=1):
        if spec.total and spec.total[5]:
            printed = _printed(spec.total[5])
            expected = case.expected_total(spec.run)
            if not _eq(printed, expected):
                defects.append(Defect(
                    "printed-total", f"page{index}",
                    named=_any_rule(findings, {"R-04", "R-25", "R-26"}),
                    detail=f"printed {printed}, claims sum to {expected}",
                ))
        for line in spec.after:
            match = _COUNT_RE.search(line)
            if match:
                printed = int(match.group(1))
                expected = case.expected_count(spec.run)
                if printed != expected:
                    defects.append(Defect(
                        "printed-count", f"page{index}",
                        named=_any_rule(findings, {"R-05", "R-27"}),
                        detail=f"printed {printed}, {expected} claims",
                    ))

    # -- logical-run assignment ----------------------------------------
    merged = False
    page_run = {i + 1: spec.run for i, spec in enumerate(case.pages)}
    # A run whose every page a mutation removed is not a run any more: count
    # only runs that still print a claim.
    real_runs = {claim.run for claim in case.claims}
    if len(real_runs) > 1:
        if not document.runs:
            merged = _runs_distinguishable(case)
        else:
            for run in document.runs:
                covered = {page_run[p] for p in run.pages if p in page_run}
                if len(covered) > 1 and _runs_distinguishable(case, covered):
                    merged = True
    if merged and status is DocumentStatus.CLEAN:
        defects.append(Defect(
            "merged-runs", "document",
            named=_any_rule(findings, {"R-11", "R-28"}),
            detail="packet runs were not kept apart",
        ))

    # -- verdict --------------------------------------------------------
    unnamed = [d for d in defects if not d.named]
    if status is DocumentStatus.CLEAN and defects:
        category = SILENT
    elif not defects and status is not DocumentStatus.CLEAN:
        category = LOUD
    elif unnamed:
        category = UNNAMED
    elif merged and not document.runs:
        category = MERGED
    else:
        category = SAFE

    return Outcome(
        category=category,
        status=status.value,
        defects=defects,
        rules=rules,
        read=read_numbers,
        expected=[c.number for c in case.claims],
        merged=merged and not document.runs,
    )
