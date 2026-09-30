"""A submission: the loss runs that arrived together, and what became of each.

An MGA's underwriting assistant does not receive one PDF. A broker sends an
email with several attachments -- some loss runs, perhaps a spreadsheet, a
second copy of the same PDF, something that is not a loss run at all. Before
any number in those runs can be used, the assistant has to be able to say
what arrived, what could be read, and what is still outstanding.

Three facts are kept apart, and none may stand in for another:

* **Intake** -- whether an attachment was accepted, rejected, recognised as a
  duplicate, or failed to stage. It is decided from the email alone.
* **Processing** -- whether an accepted PDF went through the pipeline.
* **Trust** -- whether a processed document reconciles. That is
  ``core.review.canonical_status`` and nothing else.

A submission whose intake is incomplete is never reported as complete and
clean, however clean its readable documents are: a rejected attachment may be
the loss run that matters.

Email metadata (sender, subject, declared date) is held here in memory for the
session only. The email body and the raw message are never kept. Identifiers
are generated; nothing an email header says is used as one.

Nothing in this module imports Streamlit or the email package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.account import UNNAMED_ACCOUNT, AccountRollup, build_accounts
from core.review import blocks_trust, canonical_status
from core.schema import DocumentStatus, LossRunDocument, ReconciliationResult

#: The large-loss line an underwriter reads first. The same threshold as the
#: document workbook's Large Loss sheet, so the two never disagree.
LARGE_CLAIM_THRESHOLD = Decimal("25000")


class IntakeOutcome(str, Enum):
    """What happened to an attachment when the email was read."""

    #: A PDF that passed validation and was staged for processing.
    ACCEPTED = "accepted"
    #: Not something LossLift reads: another format, an unsafe name, too large.
    REJECTED = "rejected"
    #: The same bytes as an earlier attachment. Kept in the inventory, read once.
    DUPLICATE = "duplicate"
    #: Should have been readable and could not be staged or decoded.
    FAILED = "failed"


class ProcessingState(str, Enum):
    """What happened to an accepted attachment in the pipeline."""

    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


#: Plain-language labels for the inventory.
OUTCOME_LABELS = {
    IntakeOutcome.ACCEPTED: "Accepted",
    IntakeOutcome.REJECTED: "Rejected",
    IntakeOutcome.DUPLICATE: "Duplicate",
    IntakeOutcome.FAILED: "Failed",
}
PROCESSING_LABELS = {
    ProcessingState.NOT_APPLICABLE: "Not read",
    ProcessingState.PENDING: "Waiting",
    ProcessingState.PROCESSED: "Read",
    ProcessingState.FAILED: "Could not be read",
}


def new_id(prefix: str) -> str:
    """A generated identifier, independent of anything the email says."""
    return f"{prefix}-{uuid4().hex[:12]}"


@dataclass
class Attachment:
    """One attachment found in the email, with its intake outcome.

    ``display_filename`` is the decoded, validated name for people to read.
    It is never used as a path: staged bytes live under a generated name.
    ``reason`` is a fixed, privacy-safe sentence -- never parser output.
    """

    attachment_id: str
    position: int
    display_filename: str
    declared_mime: str
    size_bytes: int
    sha256: str | None
    outcome: IntakeOutcome
    reason: str = ""
    #: For a duplicate: the attachment whose bytes it repeats.
    duplicate_of: str | None = None
    processing: ProcessingState = ProcessingState.NOT_APPLICABLE
    processing_reason: str = ""
    #: The processed document, once there is one.
    document_id: str | None = None
    #: A person's decision that a rejected or failed attachment is not a loss
    #: run this submission needs. Recorded beside the outcome, never in place
    #: of it: the attachment stays rejected.
    set_aside: bool = False

    @property
    def resolved(self) -> bool:
        """Whether this attachment leaves nothing outstanding at intake.

        A duplicate is resolved: its bytes are read through the attachment it
        repeats. A rejection or failure is not -- it may be the loss run that
        matters -- until a person sets it aside.
        """
        if self.outcome is IntakeOutcome.DUPLICATE:
            return True
        if self.outcome is IntakeOutcome.ACCEPTED:
            return self.processing is ProcessingState.PROCESSED
        return self.set_aside


@dataclass
class Submission:
    """The attachments of one saved email, and what became of each."""

    submission_id: str
    uploaded_at: datetime
    attachments: list[Attachment] = field(default_factory=list)
    #: Optional email metadata. Session memory only; never exported unredacted
    #: without the user's choice, never sent to telemetry, never an identifier.
    email_date: datetime | None = None
    sender: str | None = None
    subject: str | None = None
    #: False when the message could not be read completely: a structural MIME
    #: defect, or a limit reached. Then the inventory may be missing parts, and
    #: the submission cannot be called complete.
    inventory_complete: bool = True
    #: Why intake stopped or could not account for everything, in plain words.
    intake_problems: list[str] = field(default_factory=list)

    @classmethod
    def new(cls) -> "Submission":
        return cls(submission_id=new_id("sub"), uploaded_at=datetime.now(timezone.utc))

    def attachment(self, attachment_id: str) -> Attachment | None:
        return next((a for a in self.attachments if a.attachment_id == attachment_id), None)

    def attachment_for_document(self, document_id: str) -> Attachment | None:
        return next((a for a in self.attachments if a.document_id == document_id), None)

    @property
    def document_ids(self) -> list[str]:
        """Processed documents, in attachment order, each once."""
        return [a.document_id for a in self.attachments if a.document_id]

    def count(self, outcome: IntakeOutcome) -> int:
        return sum(1 for a in self.attachments if a.outcome is outcome)

    @property
    def unresolved(self) -> list[Attachment]:
        return [a for a in self.attachments if not a.resolved]

    @property
    def intake_complete(self) -> bool:
        """Every attachment accounted for and every accepted PDF read."""
        return self.inventory_complete and not self.unresolved

    @property
    def set_aside_count(self) -> int:
        return sum(1 for a in self.attachments if a.set_aside)

    def set_attachment_aside(self, attachment_id: str, aside: bool = True) -> bool:
        """Record (or withdraw) that a rejected or failed attachment is not needed.

        Only a rejection or failure can be set aside; an accepted PDF that did
        not read, or an email that could not be read completely, cannot be
        waved through this way. Returns whether the decision was recorded.
        """
        attachment = self.attachment(attachment_id)
        if attachment is None or attachment.outcome not in (
                IntakeOutcome.REJECTED, IntakeOutcome.FAILED):
            return False
        attachment.set_aside = aside
        return True


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Provenance:
    """Where a surfaced fact was read: attachment, document, run, page."""

    attachment_id: str | None
    document_id: str
    run_id: str | None
    page: int | None
    row: int | None = None


@dataclass(frozen=True)
class LargeClaim:
    claim_number: str
    date_of_loss: date | None
    incurred_total: Decimal
    valued_at: date | None
    provenance: Provenance
    trusted: bool


@dataclass(frozen=True)
class DocumentLine:
    """One processed document as the submission sees it."""

    attachment_id: str | None
    document_id: str
    status: str  # "mapping" | "needs_review" | "clean"
    runs: tuple[str, ...]
    claims: int
    valuation_dates: tuple[date, ...]
    blocking_findings: int


@dataclass(frozen=True)
class AccountSummary:
    """One insured's merged history within the submission."""

    name: str
    established: bool
    status: DocumentStatus
    reasons: tuple[str, ...]
    claims: int
    open_claims: int
    incurred_total: Decimal | None
    incurred_unavailable: str | None
    valuation_dates: tuple[date, ...]
    policy_periods: tuple[str, ...]
    period_notes: tuple[str, ...]
    dropped_claims: tuple[str, ...]
    large_claims: tuple[LargeClaim, ...]
    sources: tuple[Provenance, ...]


@dataclass(frozen=True)
class SubmissionSummary:
    submission_id: str
    intake_complete: bool
    #: "ready" only when intake is complete and every document and account is
    #: clean; otherwise "incomplete" (intake) or "needs_review" (trust).
    status: str
    #: Rejected or failed attachments a person set aside as not needed. When
    #: non-zero, "ready" is always shown qualified by it.
    set_aside: int
    named_insured: str | None
    named_insured_note: str
    documents: tuple[DocumentLine, ...]
    accounts: tuple[AccountSummary, ...]
    blockers: tuple[str, ...]

    @property
    def claims(self) -> int:
        return sum(account.claims for account in self.accounts)


STATUS_LABELS = {
    "ready": "Complete and reconciled",
    "needs_review": "Needs review",
    "incomplete": "Incomplete: attachments outstanding",
}


def status_label(summary: "SubmissionSummary") -> str:
    """The submission's status in plain words, qualified where it must be."""
    label = STATUS_LABELS[summary.status]
    if summary.status == "ready" and summary.set_aside:
        label += f" ({summary.set_aside} attachment(s) set aside by a reviewer)"
    return label


def _document_status(result: Any) -> str:
    if result.needs_mapping:
        return "mapping"
    status = canonical_status(result.reconciliation)
    return "clean" if status is DocumentStatus.CLEAN else "needs_review"


def _currencies(documents: Sequence[LossRunDocument],
                results: Mapping[str, Any]) -> tuple[set[str], bool]:
    """The currencies an account's amounts are in, and whether that is known.

    Unknown when any document raised R-16 (mixed currency symbols on one
    document): its own currency field then describes only part of it.
    """
    seen: set[str] = set()
    known = True
    for document in documents:
        seen.add(document.currency)
        seen.update(c.currency for c in document.claims if c.currency)
        result = results.get(document.document_id)
        reconciliation = getattr(result, "reconciliation", None)
        if reconciliation is not None and "R-16" in reconciliation.rule_ids():
            known = False
    return seen, known


def _incurred(account: AccountRollup, currencies: set[str], known: bool,
              separate_accounts: bool) -> tuple[Decimal | None, str | None]:
    """The merged incurred total, or the reason it cannot be given."""
    if separate_accounts:
        return None, ("not combined: this submission holds more than one insured, "
                      "or documents that do not name one")
    if not known or len(currencies) != 1:
        return None, "not totalled: the documents are in more than one currency, or it is unclear"
    if account.uncertain:
        return None, ("not totalled: some claims may be one claim counted twice, "
                      "because a run does not print its carrier or policy")
    values = [claim.incurred_total for claim in account.claims]
    if any(value is None for value in values):
        missing = sum(1 for value in values if value is None)
        return None, f"not totalled: {missing} claim(s) have no incurred amount"
    return sum(values, Decimal("0")), None


def summarise_submission(
    submission: Submission,
    results: Mapping[str, Any],
    *,
    large_claim_threshold: Decimal = LARGE_CLAIM_THRESHOLD,
) -> SubmissionSummary:
    """What an underwriter can safely take from a submission, and what is open.

    ``results`` maps document ids to ``ExtractionResult`` for the submission's
    processed documents. Claims repeated across valuations are merged by
    ``core.account`` (never counted twice); duplicate attachments were never
    processed twice. Totals are given only where adding them up means
    something: one insured, one currency, certain claim identity.
    """
    blockers: list[str] = []
    removed_documents = False
    if not submission.inventory_complete:
        blockers.append("The email could not be read completely, so some attachments "
                        "may not be listed.")
    blockers.extend(submission.intake_problems)
    for attachment in submission.attachments:
        if attachment.set_aside:
            continue
        if attachment.outcome in (IntakeOutcome.REJECTED, IntakeOutcome.FAILED):
            blockers.append(
                f"Attachment {attachment.position} ({attachment.display_filename}) was "
                f"{attachment.outcome.value}: {attachment.reason} Confirm it is not a "
                f"loss run this submission needs."
            )
        elif (attachment.outcome is IntakeOutcome.ACCEPTED
              and attachment.processing is not ProcessingState.PROCESSED):
            blockers.append(
                f"Attachment {attachment.position} ({attachment.display_filename}) was "
                f"not read: {attachment.processing_reason or 'processing has not finished.'}"
            )
        elif attachment.document_id and attachment.document_id not in results:
            removed_documents = True
            blockers.append(
                f"Attachment {attachment.position} ({attachment.display_filename}) was "
                f"read, but its document has since been removed from this session."
            )

    processed = [
        (attachment, results[attachment.document_id])
        for attachment in submission.attachments
        if attachment.document_id and attachment.document_id in results
    ]
    documents: list[DocumentLine] = []
    for attachment, result in processed:
        document = result.document
        status = _document_status(result)
        blocking = sum(1 for f in result.reconciliation.findings if blocks_trust(f))
        documents.append(DocumentLine(
            attachment_id=attachment.attachment_id,
            document_id=document.document_id,
            status=status,
            runs=tuple(run.run_id for run in document.runs),
            claims=len(document.claims),
            valuation_dates=tuple(sorted(
                {run.valuation_date for run in document.runs if run.valuation_date}
                or ({document.valuation_date} if document.valuation_date else set())
            )),
            blocking_findings=blocking,
        ))
        where = f"Attachment {attachment.position} ({attachment.display_filename})"
        if status == "mapping":
            blockers.append(f"{where} needs its columns mapped before its claims can be used.")
        elif status == "needs_review":
            blockers.append(f"{where} needs review: {blocking} open issue(s).")

    by_document = {attachment.document_id: attachment for attachment, _ in processed}
    mapped = [result for _, result in processed if not result.needs_mapping]
    rollups = build_accounts(
        [r.document for r in mapped],
        {r.document.document_id: r.reconciliation for r in mapped},
    )
    separate = len(rollups) > 1 or any(r.name == UNNAMED_ACCOUNT for r in rollups)
    accounts: list[AccountSummary] = []
    for rollup in rollups:
        currencies, known = _currencies(rollup.documents, results)
        total, unavailable = _incurred(rollup, currencies, known, separate)
        large: list[LargeClaim] = []
        for history in rollup.histories:
            claim = history.current
            if claim.incurred_total is None or claim.incurred_total < large_claim_threshold:
                continue
            latest = history.appearances[-1]
            attachment = by_document.get(latest.document_id)
            large.append(LargeClaim(
                claim_number=history.claim_number,
                date_of_loss=claim.date_of_loss,
                incurred_total=claim.incurred_total,
                valued_at=history.valued_at,
                provenance=Provenance(
                    attachment_id=attachment.attachment_id if attachment else None,
                    document_id=latest.document_id,
                    run_id=latest.run_id,
                    page=claim.source_page,
                    row=claim.source_row,
                ),
                trusted=history.trusted,
            ))
        large.sort(key=lambda item: item.incurred_total, reverse=True)
        periods = rollup.periods
        # A policy term is only ever one a document printed. With none printed,
        # the account groups claims by year of loss, and says that is what it is.
        printed_terms = any(source.terms for source in rollup.sources)
        if printed_terms:
            terms = tuple(p.label for p in periods if p.start is not None)
            notes = tuple(f"{p.label}: {p.claims} claim(s)" for p in periods if p.start is None)
        else:
            terms = ()
            notes = (("No document prints a policy term; by year of loss: "
                      + ", ".join(f"{p.label} ({p.claims})" for p in periods)),) if periods else ()
        accounts.append(AccountSummary(
            name=rollup.name,
            established=rollup.name != UNNAMED_ACCOUNT,
            status=rollup.status,
            reasons=tuple(rollup.reasons()),
            claims=len(rollup.claims),
            open_claims=sum(
                1 for c in rollup.claims
                if c.claim_status is not None and c.claim_status.value in ("OPEN", "REOPENED")
            ),
            incurred_total=total,
            incurred_unavailable=unavailable,
            valuation_dates=tuple(rollup.valuation_dates),
            policy_periods=terms,
            period_notes=notes,
            dropped_claims=tuple(h.claim_number for h in rollup.dropped),
            large_claims=tuple(large),
            sources=tuple(
                Provenance(
                    attachment_id=(by_document[s.document_id].attachment_id
                                   if s.document_id in by_document else None),
                    document_id=s.document_id, run_id=s.run_id, page=None,
                )
                for s in rollup.sources
            ),
        ))
        for reason in rollup.reasons():
            if "needs review" in reason or "not reconciled" in reason:
                continue  # already said per document above
            blockers.append(f"{rollup.name}: {reason}")
        if rollup.dropped:
            blockers.append(
                f"{rollup.name}: {len(rollup.dropped)} claim(s) listed in an earlier "
                f"valuation are missing from a later one."
            )

    if len(rollups) == 1 and rollups[0].name != UNNAMED_ACCOUNT:
        insured, note = rollups[0].name, ""
    elif not rollups:
        insured, note = None, "not established: no document has been read yet"
    elif len(rollups) > 1:
        insured, note = None, (f"not established: the documents name {len(rollups)} "
                               f"different insureds or leave it unsaid")
    else:
        insured, note = None, "not established: no document names the insured"
    if len(rollups) > 1:
        blockers.append("The documents do not agree on one insured; their figures are "
                        "shown separately and not combined.")

    # One insured, named: a submission that names two, or none, is a question
    # for the underwriter however clean each document is.
    trusted = (
        all(line.status == "clean" for line in documents)
        and all(account.status is DocumentStatus.CLEAN for account in accounts)
        and bool(documents)
        and not separate
    )
    if not submission.intake_complete or removed_documents:
        status = "incomplete"
    elif trusted:
        status = "ready"
    else:
        status = "needs_review"
    if not documents and submission.intake_complete:
        blockers.append("No loss run was read from this email.")

    return SubmissionSummary(
        submission_id=submission.submission_id,
        intake_complete=submission.intake_complete,
        status=status,
        set_aside=submission.set_aside_count,
        named_insured=insured,
        named_insured_note=note,
        documents=tuple(documents),
        accounts=tuple(accounts),
        blockers=tuple(dict.fromkeys(blockers)),
    )
