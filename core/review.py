"""Turning findings into a reviewer's work, without letting review look like proof.

LossLift holds three different kinds of fact and must never let one wear
another's clothes: what the carrier printed, what LossLift made of it, and what
a person decided afterwards. The first two live on the claim; the third lives
in :class:`~core.schema.ReviewLog`. This module is what reads them together.

Two consequences are load-bearing:

* **A resolution never removes a finding.** The rule engine is not told that
  anyone reviewed anything. It runs over the claims and produces exactly what
  it produced before, so "somebody looked at this" and "this no longer fails"
  remain separate answers to separate questions.
* **Reviewing is not reconciling.** A reviewer confirming a large loss has not
  made the document tie. If R-04 failed before the review it fails after it.
  The only thing that can move a reconciliation result is a changed value, and
  then only because the engine ran again over it.

So a document is described by four readings that are computed separately and
shown separately. Collapsing them into one badge is how a dismissed warning
starts looking like a document that balances.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.schema import (
    LOCAL_REVIEWER,
    DocumentStatus,
    Finding,
    LossRunDocument,
    ReconciliationResult,
    ReviewAction,
    ReviewLog,
    Resolution,
    finding_key,
)

__all__ = [
    "LOCAL_REVIEWER",
    "FINANCIAL_RULES",
    "EXTRACTION_RULES",
    "ReviewAction",
    "ReviewLog",
    "Resolution",
    "ReviewSummary",
    "blocks_trust",
    "bucket_of",
    "canonical_run_status",
    "canonical_status",
    "trust_class",
    "finding_key",
    "resolution_for",
    "summarise_review",
]

#: Rules that ask whether the numbers add up — the identities, and the two that
#: check against something the carrier printed rather than something the app
#: computed. These decide whether the document *reconciles*.
FINANCIAL_RULES = frozenset({"R-01", "R-02", "R-03", "R-04", "R-05"})

#: Rules that ask whether the document could be *read*: a missing valuation
#: date, a required field that came back null, a duplicate the extractor made,
#: an unreadable amount, nothing extracted at all, a column whose meaning could
#: not be settled. A document failing these has not been understood, which is a
#: different problem from one whose figures disagree.
EXTRACTION_RULES = frozenset({"R-06", "R-07", "R-11", "R-12", "R-15", "R-20", "R-21"})

#: Everything else is an observation about otherwise-readable data — a large
#: loss, a claim reported before its date of loss, an unstated deductible
#: basis. Those are an underwriter's judgement, never a defect.
FINANCIAL = "financial"
EXTRACTION = "extraction"
UNDERWRITING = "underwriting"


def bucket_of(finding: Finding) -> str:
    """Which of the three questions this finding belongs to."""
    return finding.category.value


# --------------------------------------------------------------------------
# The one status policy
#
# Every layer that says whether a document can be used without a person --
# the queue, the review card, the workbook, a structured export, telemetry,
# the corpus gate -- asks these functions and nothing else. The engine's own
# ``ReconciliationResult.status`` answers a narrower question (did any rule
# raise an ERROR) and is kept as the spec defines it; it is one input here,
# never a substitute. A WARN that says the document could not be read cleanly
# (an unreadable amount, a duplicate the extractor made) blocks trust exactly
# as an ERROR does, so no layer can call such a document reconciled.
# --------------------------------------------------------------------------


#: Rules that say something may not be accounted for at all -- claims or
#: pages the reading could not place, rather than values that need a check:
#: rows lost in stitching, no claims, unread source pages, money on a row no
#: claim took, an unsettled run boundary, claim-like rows the vote refused.
UNACCOUNTED_RULES = frozenset({"R-19", "R-20", "R-22", "R-23", "R-28", "R-29"})

def blocks_trust(finding: Finding) -> bool:
    """Whether this finding alone stops the document being trusted unreviewed.

    Financial and extraction findings do, at any severity; an underwriting
    observation never does. An ERROR always does -- the schema forbids an
    underwriting ERROR, and this holds even if that ever changes. So does any
    finding saying a claim or page may not be accounted for at all, whatever
    its category and severity: R-19 stays the spec's WARN, but rows seen on a
    page and not read are a missing claim, not an underwriting observation.
    """
    return (bucket_of(finding) != UNDERWRITING or finding.severity.value == "ERROR"
            or finding.rule_id in UNACCOUNTED_RULES)


def canonical_status(
    reconciliation: ReconciliationResult | None, *, needs_mapping: bool = False
) -> DocumentStatus:
    """CLEAN only when nothing anywhere says the document needs a person.

    Fails closed: no reconciliation, a column mapping still to confirm, an
    engine NEEDS_REVIEW, any run not clean, or any finding that blocks trust
    each make it NEEDS_REVIEW.
    """
    if reconciliation is None or needs_mapping:
        return DocumentStatus.NEEDS_REVIEW
    if reconciliation.status is not DocumentStatus.CLEAN:
        return DocumentStatus.NEEDS_REVIEW
    if any(status is not DocumentStatus.CLEAN for status in reconciliation.run_status.values()):
        return DocumentStatus.NEEDS_REVIEW
    if any(blocks_trust(finding) for finding in reconciliation.findings):
        return DocumentStatus.NEEDS_REVIEW
    return DocumentStatus.CLEAN


AUTO_SAFE = "auto_safe"
NEEDS_REVIEW = "needs_review"
UNRESOLVED = "unresolved"


def trust_class(
    reconciliation: ReconciliationResult | None, *, needs_mapping: bool = False
) -> str:
    """Which of three outcomes a processed document is, for measurement.

    ``auto_safe``: the canonical status is CLEAN -- usable without a person.
    ``unresolved``: something may not be accounted for at all (see
    ``UNACCOUNTED_RULES``), or the columns were never mapped.
    ``needs_review``: everything is accounted for, but some value or check
    needs a person. A refinement of NEEDS_REVIEW only; it never changes a
    document's status.
    """
    if canonical_status(reconciliation, needs_mapping=needs_mapping) is DocumentStatus.CLEAN:
        return AUTO_SAFE
    if reconciliation is None or needs_mapping:
        return UNRESOLVED
    if any(finding.rule_id in UNACCOUNTED_RULES for finding in reconciliation.findings):
        return UNRESOLVED
    return NEEDS_REVIEW


def canonical_run_status(
    reconciliation: ReconciliationResult | None, run_id: str
) -> DocumentStatus:
    """One logical run's status under the same policy.

    A finding carrying no run applies to every run of the packet: it was
    raised about the whole document (or deduplicated because every run raised
    it). A run the engine gave no status is not known to be clean.
    """
    if reconciliation is None:
        return DocumentStatus.NEEDS_REVIEW
    if reconciliation.run_status.get(run_id) is not DocumentStatus.CLEAN:
        return DocumentStatus.NEEDS_REVIEW
    if any(
        blocks_trust(finding)
        for finding in reconciliation.findings
        if finding.run_id in (None, run_id)
    ):
        return DocumentStatus.NEEDS_REVIEW
    return DocumentStatus.CLEAN


@dataclass(frozen=True)
class Bucket:
    """One question's answer: what was raised, and what remains unlooked-at."""

    findings: list[Finding]
    unresolved: list[Finding]

    @property
    def total(self) -> int:
        return len(self.findings)

    @property
    def outstanding(self) -> int:
        return len(self.unresolved)

    @property
    def passes(self) -> bool:
        """Whether the document passes this question.

        Deliberately blind to review. Dismissing a finding records that
        somebody looked; it does not make the numbers tie, and a document must
        never read healthier than the checks that were run against it.
        """
        return not self.findings


@dataclass(frozen=True)
class ReviewSummary:
    """Four readings of one document, computed and reported separately."""

    financial: Bucket
    extraction: Bucket
    underwriting: Bucket
    reviewed: int
    outstanding: int

    @property
    def total(self) -> int:
        return self.financial.total + self.extraction.total + self.underwriting.total

    @property
    def fully_reviewed(self) -> bool:
        """Every finding has been looked at. Says nothing about the figures."""
        return self.total > 0 and self.outstanding == 0

    def headline(self) -> str:
        """One line for the queue, naming the worst thing that is true.

        Order matters: a document that does not reconcile is described that way
        even if every finding has been reviewed, because review is not proof.
        """
        if not self.extraction.passes:
            return "not read cleanly"
        if not self.financial.passes:
            return "does not reconcile"
        if not self.underwriting.passes:
            return "reconciled, flags to review" if self.outstanding else "reconciled, flags reviewed"
        return "reconciled"


def summarise_review(
    findings: Sequence[Finding], log: ReviewLog | None = None
) -> ReviewSummary:
    """Sort findings into the three questions and count what remains open."""
    log = log or ReviewLog()
    buckets: dict[str, list[Finding]] = {FINANCIAL: [], EXTRACTION: [], UNDERWRITING: []}
    for finding in findings:
        buckets[bucket_of(finding)].append(finding)

    made = {name: Bucket(items, log.unresolved(items)) for name, items in buckets.items()}
    outstanding = sum(bucket.outstanding for bucket in made.values())
    return ReviewSummary(
        financial=made[FINANCIAL],
        extraction=made[EXTRACTION],
        underwriting=made[UNDERWRITING],
        reviewed=len(findings) - outstanding,
        outstanding=outstanding,
    )


def resolution_for(
    finding: Finding,
    action: ReviewAction,
    *,
    status_before: str,
    status_after: str,
    reviewer: str = LOCAL_REVIEWER,
    note: str = "",
    before: str | None = None,
    after: str | None = None,
    row_id: str = "",
    where: str = "",
) -> Resolution:
    """Build the record of a decision from the finding it was taken about.

    Everything the finding asserted is copied in, not summarised. An audit
    asking "what did this person actually agree to" gets the rule, the row, the
    figures and the discrepancy as they stood, and can see for itself whether
    the finding raised today is the same one.
    """
    return Resolution(
        key=finding_key(finding),
        action=action,
        reviewer=reviewer,
        note=note,
        rule_id=finding.rule_id,
        severity=finding.severity.value,
        message=finding.message,
        claim_number=finding.claim_number,
        field=finding.field,
        row_id=row_id,
        where=where,
        expected=None if finding.expected is None else str(finding.expected),
        actual=None if finding.actual is None else str(finding.actual),
        delta=None if finding.delta is None else str(finding.delta),
        before=before,
        after=after,
        status_before=status_before,
        status_after=status_after,
    )


def document_headline(document: LossRunDocument, findings: Sequence[Finding]) -> str:
    """The queue's one-line description of where a document stands."""
    return summarise_review(findings, document.review_log).headline()
