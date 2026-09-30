"""The submission workbook: what arrived, what can be used, what is open.

Every value passes the same writer as the document workbook
(``core.xlsx_safety.TextPolicy``): strings are stored as strings, anything
that could start a formula is quote-prefixed, control characters are replaced
and counted. There are no hyperlinks, hidden sheets or external references.

Redaction (``redact=True``) removes what identifies the people and the
account: the email's sender and subject, every attachment and source file
name, the named insured, policy numbers, and the claimant names and loss
descriptions already covered by the document export. Those cells are
replaced outright, and the same values are scrubbed from every other text
cell -- blockers and reasons included -- and from the workbook's metadata.
Internal ids and SHA-256 hashes are kept: they identify files to the audit
trail without saying what is in them.

The email body and the raw message are never exported; LossLift never kept
them.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from openpyxl import Workbook

from core.account import UNNAMED_ACCOUNT, build_accounts
from core.export import _autosize, _fill_row, _header_row
from core.review import canonical_run_status, canonical_status
from core.submission import (
    OUTCOME_LABELS,
    PROCESSING_LABELS,
    Submission,
    SubmissionSummary,
    status_label,
)
from core.xlsx_safety import REDACTED_VALUE, RedactionPolicy, TextPolicy

SHEETS = ("Submission", "Attachments", "Documents", "Accounts", "Claims",
          "Large Claims", "Blockers")


def _identifying(submission: Submission, results: Mapping[str, Any]) -> RedactionPolicy:
    """The email, file, insured and policy values, for scrubbing quoted text.

    Applied only to the free-text cells that can quote them (blockers,
    reasons, notes) -- never to headers or LossLift's own labels, where a
    short subject such as "Claim" would otherwise scrub "Claim number" out of
    every sheet. The cells that hold these values directly are replaced whole.
    """
    documents = [r.document for r in results.values()]
    values: set[str] = set()
    for value in (submission.sender, submission.subject):
        if value and value.strip():
            values.add(value.strip())
    for attachment in submission.attachments:
        if attachment.display_filename.strip():
            values.add(attachment.display_filename.strip())
    for document in documents:
        for value in (document.source_filename, document.named_insured, document.policy_number):
            if value and value.strip():
                values.add(value.strip())
        for run in document.runs:
            for value in (run.named_insured, run.policy_number):
                if value and value.strip():
                    values.add(value.strip())
    return RedactionPolicy(values=tuple(sorted(values, key=len, reverse=True)))


def _utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # An email date with no zone ("-0000") is not in this server's local
        # time; read it as UTC rather than shifting it by the server's offset.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def build_submission_workbook(
    submission: Submission,
    summary: SubmissionSummary,
    results: Mapping[str, Any],
    *,
    redact: bool = False,
) -> Workbook:
    """The submission as one workbook, under the export text and redaction policy.

    ``results`` maps the submission's document ids to ``ExtractionResult``.
    """
    documents = [r.document for r in results.values()]
    # Claimant names and loss descriptions: scrubbed from every cell, as in the
    # document workbook.
    policy = TextPolicy(
        redaction=RedactionPolicy.from_documents(documents) if redact else None)
    identifying = _identifying(submission, results) if redact else None

    def hidden(value: Any) -> Any:
        """A cell that identifies someone: withheld whole when redacting."""
        if redact and value not in (None, ""):
            return REDACTED_VALUE
        return value

    def quoted(text: Any) -> Any:
        """Free text that may quote a file name, the insured or the email."""
        if identifying is None or not isinstance(text, str):
            return text
        return identifying.scrub(text)

    workbook = Workbook()
    properties = workbook.properties
    properties.creator = policy.prepare_text("LossLift")
    properties.title = policy.prepare_text("LossLift submission summary")
    properties.subject = policy.prepare_text(submission.submission_id)
    properties.description = None
    properties.keywords = None

    # --- Submission ------------------------------------------------------
    sheet = workbook.active
    sheet.title = "Submission"
    widths = _header_row(sheet, ["Field", "Value"], policy)
    counts = {label: submission.count(outcome) for outcome, label in OUTCOME_LABELS.items()}
    rows: list[tuple[str, Any]] = [
        ("Submission ID", submission.submission_id),
        ("Uploaded", _utc(submission.uploaded_at)),
        ("Email date", _utc(submission.email_date)),
        ("Sender", hidden(submission.sender)),
        ("Subject", hidden(submission.subject)),
        ("Status", status_label(summary)),
        ("Every attachment accounted for", "yes" if summary.intake_complete else "no"),
        ("Email read completely", "yes" if submission.inventory_complete else "no"),
        ("Named insured", hidden(summary.named_insured) or quoted(summary.named_insured_note)),
        ("Attachments", len(submission.attachments)),
        *[(f"Attachments {label.lower()}", n) for label, n in counts.items()],
        ("Set aside by a reviewer", submission.set_aside_count),
        ("Documents read", len(summary.documents)),
        ("Open blockers", len(summary.blockers)),
        ("Identifying data redacted", "yes" if redact else "no"),
        ("Exported", _utc(datetime.now(timezone.utc))),
        ("Not exported", "The email body and the raw message are never kept or exported."),
    ]
    for index, (label, value) in enumerate(rows, start=2):
        _fill_row(sheet, index, [label, value], widths, policy)
    _autosize(sheet, widths)

    # --- Attachments -----------------------------------------------------
    sheet = workbook.create_sheet("Attachments")
    widths = _header_row(sheet, [
        "#", "Attachment ID", "File name", "Declared type", "Size (bytes)", "SHA-256",
        "Outcome", "Reason", "Duplicate of", "Set aside by reviewer", "Reading",
        "Reading note", "Document ID",
    ], policy)
    for index, a in enumerate(submission.attachments, start=2):
        _fill_row(sheet, index, [
            a.position, a.attachment_id, hidden(a.display_filename), a.declared_mime,
            a.size_bytes, a.sha256, OUTCOME_LABELS[a.outcome], quoted(a.reason),
            a.duplicate_of, "yes" if a.set_aside else "no", PROCESSING_LABELS[a.processing],
            quoted(a.processing_reason), a.document_id,
        ], widths, policy)
    sheet.freeze_panes = "A2"
    _autosize(sheet, widths)

    # --- Documents and runs ------------------------------------------------
    sheet = workbook.create_sheet("Documents")
    widths = _header_row(sheet, [
        "Attachment ID", "Document ID", "Source file", "Run ID", "Pages", "Carrier",
        "Named insured", "Policy number", "Valuation date", "Claims", "Status",
        "Needs column mapping",
    ], policy)
    row = 2
    for line in summary.documents:
        result = results[line.document_id]
        document = result.document
        runs = document.runs or [None]
        for run in runs:
            if run is None:
                facts = document
                status = canonical_status(result.reconciliation,
                                          needs_mapping=result.needs_mapping)
                pages = f"1-{document.page_count}" if document.page_count else None
                claims = len(document.claims)
            else:
                facts = run
                status = canonical_run_status(result.reconciliation, run.run_id,
                                              needs_mapping=result.needs_mapping)
                pages = run.page_range
                claims = len(document.run_claims(run))
            _fill_row(sheet, row, [
                line.attachment_id, document.document_id, hidden(document.source_filename),
                None if run is None else run.run_id, pages, facts.carrier,
                hidden(facts.named_insured), hidden(facts.policy_number),
                facts.valuation_date, claims,
                "Reconciled" if status.value == "CLEAN" else "Needs review",
                "yes" if result.needs_mapping else "no",
            ], widths, policy)
            row += 1
    sheet.freeze_panes = "A2"
    _autosize(sheet, widths)

    # --- Accounts --------------------------------------------------------
    sheet = workbook.create_sheet("Accounts")
    widths = _header_row(sheet, [
        "Named insured", "Status", "Claims", "Open claims", "Total incurred",
        "Why no total", "Valuation dates", "Policy terms", "Claims outside a term",
        "Missing from a later valuation", "Why review",
    ], policy)
    for index, account in enumerate(summary.accounts, start=2):
        _fill_row(sheet, index, [
            hidden(account.name) if account.established else account.name,
            "Reconciled" if account.status.value == "CLEAN" else "Needs review",
            account.claims, account.open_claims, _float(account.incurred_total),
            quoted(account.incurred_unavailable),
            ", ".join(d.isoformat() for d in account.valuation_dates) or None,
            "; ".join(account.policy_periods) or None,
            quoted("; ".join(account.period_notes)) or None,
            ", ".join(account.dropped_claims) or None,
            quoted("; ".join(account.reasons)) or None,
        ], widths, policy)
    _autosize(sheet, widths)

    # --- Claims (every surfaced claim, with where it was read) ----------------
    by_document = {line.document_id: line.attachment_id for line in summary.documents}
    mapped = [r for r in results.values() if not r.needs_mapping]
    rollups = build_accounts([r.document for r in mapped],
                             {r.document.document_id: r.reconciliation for r in mapped})
    sheet = workbook.create_sheet("Claims")
    widths = _header_row(sheet, [
        "Named insured", "Claim number", "Date of loss", "Status", "Paid", "Reserve",
        "Recovery", "Incurred", "Valued at", "Seen in valuations", "Attachment ID",
        "Document ID", "Run ID", "Page", "Row", "Review",
    ], policy)
    row = 2
    for rollup in rollups:
        for history in rollup.histories:
            claim = history.current
            latest = history.appearances[-1]
            _fill_row(sheet, row, [
                hidden(rollup.name) if rollup.name != UNNAMED_ACCOUNT else rollup.name,
                history.claim_number, claim.date_of_loss,
                claim.claim_status.value if claim.claim_status else None,
                _float(claim.paid_total), _float(claim.reserve_total),
                _float(claim.recovery_total), _float(claim.incurred_total),
                history.valued_at, len(history.appearances),
                by_document.get(latest.document_id), latest.document_id, latest.run_id,
                claim.source_page, claim.source_row,
                history.uncertain or ("" if history.trusted else "read from a document that needs review"),
            ], widths, policy)
            row += 1
    sheet.freeze_panes = "A2"
    _autosize(sheet, widths)

    # --- Large claims ----------------------------------------------------
    sheet = workbook.create_sheet("Large Claims")
    widths = _header_row(sheet, [
        "Named insured", "Claim number", "Date of loss", "Incurred", "Valued at",
        "Attachment ID", "Document ID", "Run ID", "Page", "Row", "Review",
    ], policy)
    row = 2
    for account in summary.accounts:
        for claim in account.large_claims:
            _fill_row(sheet, row, [
                hidden(account.name) if account.established else account.name,
                claim.claim_number, claim.date_of_loss, _float(claim.incurred_total),
                claim.valued_at, claim.provenance.attachment_id,
                claim.provenance.document_id, claim.provenance.run_id,
                claim.provenance.page, claim.provenance.row,
                "" if claim.trusted else "needs review",
            ], widths, policy)
            row += 1
    _autosize(sheet, widths)

    # --- Blockers --------------------------------------------------------
    sheet = workbook.create_sheet("Blockers")
    widths = _header_row(sheet, ["#", "Outstanding"], policy)
    for index, blocker in enumerate(summary.blockers or ("None",), start=2):
        _fill_row(sheet, index, [index - 1 if summary.blockers else None, quoted(blocker)],
                  widths, policy)
    _autosize(sheet, widths)
    return workbook


def submission_to_bytes(
    submission: Submission,
    summary: SubmissionSummary,
    results: Mapping[str, Any],
    *,
    redact: bool = False,
) -> bytes:
    buffer = io.BytesIO()
    build_submission_workbook(submission, summary, results, redact=redact).save(buffer)
    return buffer.getvalue()


def submission_filename(submission: Submission) -> str:
    """A name carrying no email-derived text: the generated id only."""
    return f"losslift-{submission.submission_id}.xlsx"
