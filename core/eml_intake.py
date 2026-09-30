"""Read a saved email (``.eml``) into a submission, safely and completely.

Three steps, each separately callable and testable:

1. :func:`parse_eml` reads the message with the standard library's ``email``
   package and lists every attachment with its intake outcome. It decides from
   the bytes alone; nothing is written to disk and no attachment is opened.
2. :func:`stage_attachments` validates each accepted PDF through the same path
   a direct upload takes (``core.ingest.ingest``) plus a page check, and stages
   it under a generated name. A failure part-way through discards everything
   this call staged.
3. :func:`process_attachments` runs each staged PDF through the pipeline with
   ``core.ingest.run_or_discard``, so a failed read never leaves a file behind.

What this never does: render HTML, fetch anything, follow a link, execute or
unpack an attachment, keep the email body or the raw message, or put a header,
a filename or parser output into an error message it did not write itself.

It does not sanitise PDFs, and cannot promise to find every kind of active
content a PDF may carry. It only refuses what it can recognise.

Limits are applied before the work they bound: the raw size before parsing,
the part count and depth while walking, an attachment's encoded size before it
is decoded, and the running total before its bytes are kept.
"""

from __future__ import annotations

import email
import email.policy
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage, Message
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator

from core.ingest import MAX_UPLOAD_BYTES, PDF_MAGIC, IngestError, IngestedFile, discard, ingest
from core.ingest import run_or_discard, sha256_bytes
from core.submission import (
    Attachment,
    IntakeOutcome,
    ProcessingState,
    Submission,
    new_id,
)


@dataclass(frozen=True)
class EmlLimits:
    """Bounds on one saved email. Every one is checked before the work it limits."""

    max_email_bytes: int = 150 * 1024 * 1024
    max_parts: int = 200
    max_depth: int = 8
    max_attachments: int = 25
    max_total_attachment_bytes: int = 200 * 1024 * 1024
    max_pdf_bytes: int = MAX_UPLOAD_BYTES
    max_pdf_pages: int = 1000


DEFAULT_LIMITS = EmlLimits()


class EmlFatalError(ValueError):
    """The email as a whole cannot be read. The message is privacy-safe."""


# Fixed, privacy-safe reasons. Nothing from the email is ever interpolated
# into these except counts and limits LossLift itself set.
REASON_NOT_PDF = "Its contents are not a PDF, whatever its name or type says."
REASON_NESTED_EMAIL = "It is an attached email. Save its attachments and upload them directly."
REASON_ARCHIVE = "It is an archive (ZIP or similar). Extract the loss runs and upload them directly."
REASON_EXECUTABLE = "It is a program or script, which LossLift never opens."
REASON_UNSUPPORTED = "LossLift reads loss runs in PDF only; this file type is not supported."
REASON_UNSAFE_NAME = ("Its file name contains a path or control characters, which LossLift "
                      "refuses rather than guess at.")
REASON_UNDECODABLE = "Its contents could not be decoded from the email."
REASON_EMPTY = "It is empty."
REASON_DUPLICATE = "The same file as attachment {position}; it is read once."
REASON_DUPLICATE_OF_REFUSED = ("The same file as attachment {position}, which was not read: "
                               "{reason}")
REASON_ENCRYPTED = "It is password-protected. Upload an unprotected copy."
REASON_DAMAGED = "It could not be opened as a PDF; the file may be damaged."
REASON_TOO_LARGE = "It is larger than the {limit} MB limit for one PDF."
REASON_TOO_MANY_PAGES = "It has more than the {limit}-page limit for one PDF."
REASON_OVER_TOTAL = ("The email's attachments exceed the {limit} MB limit in total; this and "
                     "any later attachments were not read.")
REASON_OVER_COUNT = ("The email has more than {limit} attachments; this and any later "
                     "attachments were not read.")
REASON_STAGING = "It could not be staged for reading."
REASON_PROCESSING = "It could not be read as a loss run."

#: MIME defects that mean the structure itself is broken, so some parts may be
#: missing from the walk. Encoding defects on one part are not in this list:
#: they make that attachment fail, not the whole inventory doubtful.
_STRUCTURAL_DEFECTS = (
    "NoBoundaryInMultipartDefect",
    "StartBoundaryNotFoundDefect",
    "CloseBoundaryNotFoundDefect",
    "MultipartInvariantViolationDefect",
    "MissingHeaderBodySeparatorDefect",
    "FirstHeaderLineIsContinuationDefect",
    "MisplacedEnvelopeHeaderDefect",
)
_DECODING_DEFECTS = (
    "InvalidBase64PaddingDefect",
    "InvalidBase64CharactersDefect",
    "InvalidBase64LengthDefect",
)

_ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"Rar!", b"7z\xbc\xaf\x27\x1c",
                  b"\x1f\x8b", b"BZh", b"\xfd7zXZ")
_EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"#!")
_EXECUTABLE_SUFFIXES = (".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".js", ".vbs",
                        ".ps1", ".sh", ".jar", ".msi", ".lnk", ".hta", ".wsf")
_ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z", ".gz", ".tgz", ".tar", ".bz2", ".xz")
#: Office files are ZIP containers too; they are said to be unsupported, not
#: called archives, so the reason matches what the sender attached.
_OFFICE_SUFFIXES = (".xlsx", ".xlsm", ".xls", ".docx", ".doc", ".pptx", ".csv")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085  ‪-‮⁦-⁩]")
_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass
class PendingAttachment:
    """An attachment between parsing and staging. Holds its bytes in memory."""

    attachment: Attachment
    data: bytes | None = None
    staged: IngestedFile | None = None


@dataclass
class ParsedEmail:
    """The submission, and the bytes of the PDFs it accepted, in memory."""

    submission: Submission
    pending: list[PendingAttachment] = field(default_factory=list)

    def accepted(self) -> list[PendingAttachment]:
        return [p for p in self.pending if p.attachment.outcome is IntakeOutcome.ACCEPTED]


# --------------------------------------------------------------------------
# Filenames
# --------------------------------------------------------------------------


def safe_display_name(raw: str | None, position: int) -> tuple[str, bool]:
    """A decoded name fit to display, and whether the supplied name was unsafe.

    A name carrying a path, a drive letter or a control character is refused
    rather than repaired: repairing it would present a name the sender never
    gave. The display name is never used on disk.
    """
    fallback = f"attachment-{position}"
    if raw is None or not raw.strip():
        return fallback, False
    name = unicodedata.normalize("NFC", raw.strip())
    if (_CONTROL.search(name) or "/" in name or "\\" in name or _DRIVE.match(name)
            or name in (".", "..") or name.startswith("..")):
        return fallback, True
    return name[:200], False


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _defect_names(part: Message) -> set[str]:
    return {type(defect).__name__ for defect in getattr(part, "defects", [])}


def _walk(message: Message, limits: EmlLimits) -> Iterator[tuple[Message, int]]:
    """Every part, depth first, with its depth. Raises on the part/depth limits."""
    count = 0
    stack: list[tuple[Message, int]] = [(message, 0)]
    while stack:
        part, depth = stack.pop()
        count += 1
        if count > limits.max_parts:
            raise EmlFatalError(
                f"The email has more than {limits.max_parts} parts, which is over the limit."
            )
        if depth > limits.max_depth:
            raise EmlFatalError(
                f"The email nests parts more than {limits.max_depth} levels deep, which is "
                f"over the limit."
            )
        yield part, depth
        # An attached message of any kind is a leaf here: it is rejected, never
        # descended into (message/external-body would point at remote content).
        if part.is_multipart() and part.get_content_maintype() != "message":
            children = part.get_payload()
            if isinstance(children, list):
                stack.extend((child, depth + 1) for child in reversed(children))


def _is_attachment(part: Message) -> bool:
    """A part the sender attached, as opposed to the message body.

    Text bodies (plain or HTML) with no filename and no attachment disposition
    are the message itself and are skipped unread. Everything else that is not
    a container counts, so an attachment cannot hide by leaving out a name.
    """
    if part.get_content_maintype() == "message":
        return True
    if part.is_multipart():
        return False
    disposition = (part.get_content_disposition() or "").lower()
    if disposition == "attachment" or part.get_filename():
        return True
    return part.get_content_maintype() not in ("text", "multipart")


def _classify_bytes(data: bytes, name: str) -> str | None:
    """None for a PDF by its bytes; otherwise the fixed rejection reason."""
    head = data[:16]
    if head.startswith(PDF_MAGIC):
        return None
    if any(head.startswith(magic) for magic in _EXECUTABLE_MAGIC) or name.lower().endswith(
            _EXECUTABLE_SUFFIXES):
        return REASON_EXECUTABLE
    if name.lower().endswith(_OFFICE_SUFFIXES):
        return REASON_UNSUPPORTED
    if any(head.startswith(magic) for magic in _ARCHIVE_MAGIC) or name.lower().endswith(
            _ARCHIVE_SUFFIXES):
        return REASON_ARCHIVE
    if name.lower().endswith(".pdf"):
        return REASON_NOT_PDF
    return REASON_UNSUPPORTED


def _encoded_size(part: Message) -> int:
    payload = part.get_payload()
    if isinstance(payload, str):
        return len(payload)
    if isinstance(payload, bytes):
        return len(payload)
    return 0


def _estimated_decoded(part: Message) -> int:
    """An upper bound on the decoded size, known before decoding."""
    size = _encoded_size(part)
    encoding = str(part.get("Content-Transfer-Encoding", "")).strip().lower()
    return size * 3 // 4 + 4 if encoding == "base64" else size


def _metadata(message: Message, submission: Submission) -> None:
    """Sender, subject and date, if they parse. Session memory only."""
    try:
        subject = message.get("Subject")
        submission.subject = str(subject).strip()[:300] if subject else None
    except Exception:  # noqa: BLE001 - a malformed header is simply absent
        submission.subject = None
    try:
        sender = message.get("From")
        submission.sender = str(sender).strip()[:300] if sender else None
    except Exception:  # noqa: BLE001
        submission.sender = None
    try:
        raw_date = message.get("Date")
        submission.email_date = parsedate_to_datetime(str(raw_date)) if raw_date else None
    except Exception:  # noqa: BLE001
        submission.email_date = None
    for name in ("subject", "sender"):
        value = getattr(submission, name)
        if value is not None:
            setattr(submission, name, _CONTROL.sub(" ", value).strip() or None)


def parse_eml(raw: bytes, limits: EmlLimits = DEFAULT_LIMITS) -> ParsedEmail:
    """Every attachment in a saved email, each with an intake outcome.

    Raises :class:`EmlFatalError` when the email as a whole cannot be read or a
    global limit is breached before any attachment could be trusted -- in
    which case nothing has been kept. A structural defect found while reading
    marks the inventory incomplete instead, so the attachments that were
    found stay visible but the submission is never called complete.
    """
    if not raw:
        raise EmlFatalError("The email file is empty.")
    if len(raw) > limits.max_email_bytes:
        raise EmlFatalError(
            f"The email is larger than the {limits.max_email_bytes // (1024 * 1024)} MB limit."
        )
    try:
        message = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:  # noqa: BLE001 - parser detail is never shown
        raise EmlFatalError("The file could not be read as an email.") from None
    if not isinstance(message, (EmailMessage, Message)) or not message.keys():
        raise EmlFatalError("The file does not look like a saved email (no headers).")

    submission = Submission.new()
    _metadata(message, submission)
    parsed = ParsedEmail(submission=submission)
    by_hash: dict[str, Attachment] = {}
    total = 0
    position = 0
    stopped = False

    for part, _depth in _walk(message, limits):
        defects = _defect_names(part)
        if defects & set(_STRUCTURAL_DEFECTS) and submission.inventory_complete:
            submission.inventory_complete = False
            submission.intake_problems.append(
                "Part of the email's structure is damaged, so attachments may be missing "
                "from this list."
            )
        if not _is_attachment(part):
            continue
        position += 1
        raw_name = None
        try:
            raw_name = part.get_filename()
        except Exception:  # noqa: BLE001 - an unreadable name is no name
            raw_name = None
        name, unsafe = safe_display_name(raw_name, position)
        attachment = Attachment(
            attachment_id=new_id("att"),
            position=position,
            display_filename=name,
            declared_mime=part.get_content_type(),
            size_bytes=0,
            sha256=None,
            outcome=IntakeOutcome.REJECTED,
        )
        pending = PendingAttachment(attachment=attachment)
        parsed.pending.append(pending)
        submission.attachments.append(attachment)

        if stopped:
            attachment.reason = submission.intake_problems[-1]
            continue
        if position > limits.max_attachments:
            stopped = True
            attachment.reason = REASON_OVER_COUNT.format(limit=limits.max_attachments)
            submission.intake_problems.append(attachment.reason)
            submission.inventory_complete = False
            continue
        if part.get_content_maintype() == "message":
            attachment.reason = (REASON_NESTED_EMAIL if part.get_content_type() in (
                "message/rfc822", "message/global") else REASON_UNSUPPORTED)
            continue
        if unsafe:
            attachment.reason = REASON_UNSAFE_NAME
            continue
        estimate = _estimated_decoded(part)
        if estimate > limits.max_pdf_bytes + 4:
            attachment.reason = REASON_TOO_LARGE.format(limit=limits.max_pdf_bytes // (1024 * 1024))
            continue
        if total + estimate > limits.max_total_attachment_bytes + 4:
            stopped = True
            attachment.reason = REASON_OVER_TOTAL.format(
                limit=limits.max_total_attachment_bytes // (1024 * 1024))
            submission.intake_problems.append(attachment.reason)
            submission.inventory_complete = False
            continue
        try:
            data = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            data = None
        # Decoding records its own defects on the part, so look again now.
        if data is None or _defect_names(part) & set(_DECODING_DEFECTS):
            attachment.outcome = IntakeOutcome.FAILED
            attachment.reason = REASON_UNDECODABLE
            continue
        attachment.size_bytes = len(data)
        total += len(data)
        if not data:
            attachment.reason = REASON_EMPTY
            continue
        if len(data) > limits.max_pdf_bytes:
            attachment.reason = REASON_TOO_LARGE.format(limit=limits.max_pdf_bytes // (1024 * 1024))
            continue
        attachment.sha256 = sha256_bytes(data)
        rejection = _classify_bytes(data, name)
        if rejection is not None:
            attachment.reason = rejection
            continue
        first = by_hash.get(attachment.sha256)
        if first is not None:
            attachment.outcome = IntakeOutcome.DUPLICATE
            attachment.duplicate_of = first.attachment_id
            attachment.reason = REASON_DUPLICATE.format(position=first.position)
            continue
        by_hash[attachment.sha256] = attachment
        attachment.outcome = IntakeOutcome.ACCEPTED
        attachment.processing = ProcessingState.PENDING
        pending.data = data
    return parsed


# --------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------


def _explain_copies(submission: Submission, original: Attachment, reason: str) -> None:
    """A copy of a PDF that was not read must not say it "is read once".

    The copy stays a duplicate: it is resolved exactly when its original is,
    so one file is never two blockers.
    """
    for other in submission.attachments:
        if other.duplicate_of == original.attachment_id:
            other.reason = REASON_DUPLICATE_OF_REFUSED.format(
                position=original.position, reason=reason)


def _page_problem(data: bytes, limits: EmlLimits) -> str | None:
    """Open the PDF in memory: refuse it if protected, damaged or too long."""
    import pymupdf

    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            # Only a password that is needed to open the file refuses it. An
            # owner password (print or copy restrictions) is common on carrier
            # PDFs and does not stop reading -- a direct upload reads them too.
            if document.needs_pass:
                return REASON_ENCRYPTED
            if document.page_count > limits.max_pdf_pages:
                return REASON_TOO_MANY_PAGES.format(limit=limits.max_pdf_pages)
            if document.page_count == 0:
                return REASON_DAMAGED
    except Exception:  # noqa: BLE001 - never surface parser detail
        return REASON_DAMAGED
    return None


def stage_attachments(
    parsed: ParsedEmail,
    limits: EmlLimits = DEFAULT_LIMITS,
    workdir: Any = None,
) -> list[PendingAttachment]:
    """Validate and stage every accepted PDF; return the staged ones.

    Uses the same ``ingest`` as a direct upload, under a generated staging
    name. A problem with one attachment rejects that attachment and lets its
    siblings continue. Anything else -- an interruption included -- discards
    every file this call staged before it propagates, and the bytes held in
    memory are released either way.
    """
    staged: list[PendingAttachment] = []

    def refuse(attachment: Attachment, outcome: IntakeOutcome, reason: str) -> None:
        attachment.outcome = outcome
        attachment.processing = ProcessingState.NOT_APPLICABLE
        attachment.reason = reason
        _explain_copies(parsed.submission, attachment, reason)

    try:
        for pending in parsed.accepted():
            attachment = pending.attachment
            problem = _page_problem(pending.data or b"", limits)
            if problem is not None:
                refuse(attachment, IntakeOutcome.REJECTED, problem)
                pending.data = None
                continue
            try:
                pending.staged = ingest(pending.data or b"", attachment.display_filename, workdir)
            except IngestError:
                refuse(attachment, IntakeOutcome.REJECTED, REASON_NOT_PDF)
                pending.data = None
                continue
            except OSError:
                refuse(attachment, IntakeOutcome.FAILED, REASON_STAGING)
                pending.data = None
                continue
            pending.data = None
            staged.append(pending)
    except BaseException:
        for pending in staged:
            if pending.staged is not None:
                discard(pending.staged)
                pending.staged = None
        for pending in parsed.pending:
            pending.data = None
        raise
    return staged


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------


def process_attachments(
    staged: list[PendingAttachment],
    run: Callable[[IngestedFile], Any],
    submission: Submission | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, tuple[IngestedFile, Any]]:
    """Read each staged PDF; map document id to (staged file, result).

    ``run`` is the pipeline call a direct upload makes. A PDF the pipeline
    cannot read is marked failed and its staged copy discarded
    (``run_or_discard``); its siblings continue. An interruption discards the
    staged copies not yet handed back, then propagates.
    """
    done: dict[str, tuple[IngestedFile, Any]] = {}
    remaining = list(staged)
    total = len(staged)
    try:
        while remaining:
            if progress is not None:
                progress(total - len(remaining), total)
            pending = remaining.pop(0)
            attachment = pending.attachment
            assert pending.staged is not None
            try:
                result = run_or_discard(pending.staged, run)
            except Exception:  # noqa: BLE001 - one bad PDF never stops its siblings
                attachment.processing = ProcessingState.FAILED
                attachment.processing_reason = REASON_PROCESSING
                pending.staged = None
                if submission is not None:
                    _explain_copies(submission, attachment, REASON_PROCESSING)
                continue
            document_id = result.document.document_id
            attachment.processing = ProcessingState.PROCESSED
            attachment.document_id = document_id
            done[document_id] = (pending.staged, result)
    except BaseException:
        for pending in remaining:
            if pending.staged is not None:
                discard(pending.staged)
                pending.staged = None
                pending.attachment.processing = ProcessingState.FAILED
                pending.attachment.processing_reason = REASON_PROCESSING
        raise
    return done


def read_submission(
    raw: bytes,
    run: Callable[[IngestedFile], Any],
    limits: EmlLimits = DEFAULT_LIMITS,
    workdir: Any = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Submission, dict[str, tuple[IngestedFile, Any]]]:
    """Parse, stage and process one saved email. Raises only on a fatal error.

    ``progress(read, total)`` is called before each PDF is read, so a caller
    can show that a large email is moving.
    """
    parsed = parse_eml(raw, limits)
    staged = stage_attachments(parsed, limits, workdir)
    done = process_attachments(staged, run, parsed.submission, progress)
    return parsed.submission, done
