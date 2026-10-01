"""Saved-email intake: every attachment accounted for, nothing unsafe read.

All messages and PDFs are synthetic (``tests/submission_fixtures.py``).
"""

from __future__ import annotations

import base64
import socket
import tempfile
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

from core import eml_intake
from core.eml_intake import (
    DEFAULT_LIMITS,
    EmlFatalError,
    parse_eml,
    process_attachments,
    read_submission,
    safe_display_name,
    stage_attachments,
)
from core.ingest import IngestedFile, discard
from core.pipeline import run_pipeline
from core.submission import IntakeOutcome, ProcessingState
from tests.submission_fixtures import (
    BODY_SECRET,
    SENDER,
    SUBJECT,
    claim_rows,
    eml,
    loss_run_pdf,
    packet_pdf,
    run,
)

PDF_A = loss_run_pdf()
PDF_B = loss_run_pdf(rows=claim_rows(3, start=81000000, big=40000))


def _outcomes(submission):
    return [(a.display_filename, a.outcome) for a in submission.attachments]


def _cleanup(done):
    for staged, _result in done.values():
        discard(staged)


@pytest.fixture
def profiles(tmp_path):
    return run(tmp_path / "profiles")


# --------------------------------------------------------------------------
# Discovery and outcomes
# --------------------------------------------------------------------------


def test_multiple_pdfs_in_one_email_are_each_read(profiles):
    submission, done = read_submission(
        eml([("a.pdf", "application/pdf", PDF_A), ("b.pdf", "application/pdf", PDF_B)]),
        profiles,
    )
    try:
        assert _outcomes(submission) == [("a.pdf", IntakeOutcome.ACCEPTED),
                                         ("b.pdf", IntakeOutcome.ACCEPTED)]
        assert all(a.processing is ProcessingState.PROCESSED for a in submission.attachments)
        assert len(done) == 2 and submission.intake_complete
        assert submission.document_ids == list(done)
    finally:
        _cleanup(done)


@pytest.mark.parametrize("declared", ["application/octet-stream", "image/png", "text/plain"])
def test_a_pdf_is_recognised_by_its_bytes_not_its_declared_type(declared, profiles):
    submission, done = read_submission(eml([("run.pdf", declared, PDF_A)]), profiles)
    try:
        attachment = submission.attachments[0]
        assert attachment.outcome is IntakeOutcome.ACCEPTED
        assert attachment.declared_mime == declared
        assert attachment.processing is ProcessingState.PROCESSED
    finally:
        _cleanup(done)


def test_non_pdf_bytes_named_pdf_are_rejected(profiles):
    submission, done = read_submission(
        eml([("loss run.pdf", "application/pdf", b"This is plain text, not a PDF.")]), profiles)
    attachment = submission.attachments[0]
    assert attachment.outcome is IntakeOutcome.REJECTED
    assert attachment.reason == eml_intake.REASON_NOT_PDF
    assert not done and not submission.intake_complete


def test_identical_pdfs_under_different_names_are_read_once(profiles):
    submission, done = read_submission(
        eml([("first.pdf", "application/pdf", PDF_A),
             ("again.pdf", "application/pdf", PDF_A)]), profiles)
    try:
        first, again = submission.attachments
        assert first.outcome is IntakeOutcome.ACCEPTED
        assert again.outcome is IntakeOutcome.DUPLICATE
        assert again.duplicate_of == first.attachment_id
        assert again.sha256 == first.sha256
        assert len(done) == 1
        # Resolved: a duplicate does not hold the submission open.
        assert submission.intake_complete
    finally:
        _cleanup(done)


def test_different_pdfs_sharing_one_name_are_both_kept(profiles):
    submission, done = read_submission(
        eml([("loss_run.pdf", "application/pdf", PDF_A),
             ("loss_run.pdf", "application/pdf", PDF_B)]), profiles)
    try:
        assert [a.outcome for a in submission.attachments] == [IntakeOutcome.ACCEPTED] * 2
        assert len(done) == 2
        staged_paths = {staged.path for staged, _ in done.values()}
        assert len(staged_paths) == 2  # distinct generated staging names
        assert all(p.name != "loss_run.pdf" for p in staged_paths)
    finally:
        _cleanup(done)


@pytest.mark.parametrize("name", [
    "../../etc/passwd.pdf", "/tmp/evil.pdf", "C:\\Windows\\evil.pdf", "C:evil.pdf",
    "sub/dir.pdf", "bad\x00name.pdf", "bad\nname.pdf", "..", "rtl\u202eFDP.exe",
])
def test_an_unsafe_filename_is_refused(name):
    display, unsafe = safe_display_name(name, 3)
    assert unsafe and display == "attachment-3"


def test_an_attachment_with_an_unsafe_name_is_rejected_not_renamed():
    submission = parse_eml(eml([("../../x.pdf", "application/pdf", PDF_A)])).submission
    attachment = submission.attachments[0]
    assert attachment.outcome is IntakeOutcome.REJECTED
    assert attachment.reason == eml_intake.REASON_UNSAFE_NAME
    assert attachment.display_filename == "attachment-1"


def test_an_internationalised_filename_is_decoded(profiles):
    name = "Schadenübersicht_東京_2024.pdf"
    submission, done = read_submission(eml([(name, "application/pdf", PDF_A)]), profiles)
    try:
        assert submission.attachments[0].display_filename == name
        assert submission.attachments[0].outcome is IntakeOutcome.ACCEPTED
    finally:
        _cleanup(done)


def test_an_attachment_without_a_name_is_still_listed():
    raw = eml([(None, "application/pdf", PDF_A)])
    submission = parse_eml(raw).submission
    assert submission.attachments[0].display_filename == "attachment-1"
    assert submission.attachments[0].outcome is IntakeOutcome.ACCEPTED


# --------------------------------------------------------------------------
# Unsupported attachments
# --------------------------------------------------------------------------


def test_a_nested_email_is_rejected_and_never_opened():
    inner = eml([("hidden.pdf", "application/pdf", PDF_B)], subject="inner")
    parsed = parse_eml(eml([("fwd.eml", "message/rfc822", inner),
                            ("a.pdf", "application/pdf", PDF_A)]))
    outcomes = _outcomes(parsed.submission)
    assert outcomes == [("fwd.eml", IntakeOutcome.REJECTED), ("a.pdf", IntakeOutcome.ACCEPTED)]
    assert parsed.submission.attachments[0].reason == eml_intake.REASON_NESTED_EMAIL
    # The PDF inside the forwarded email is not discovered as an attachment.
    assert len(parsed.submission.attachments) == 2


@pytest.mark.parametrize("name, mime, data, reason", [
    ("runs.zip", "application/zip", b"PK\x03\x04" + b"\0" * 40, eml_intake.REASON_ARCHIVE),
    ("runs.7z", "application/octet-stream", b"7z\xbc\xaf\x27\x1c" + b"\0" * 10,
     eml_intake.REASON_ARCHIVE),
    ("setup.exe", "application/octet-stream", b"MZ\x90\x00" + b"\0" * 40,
     eml_intake.REASON_EXECUTABLE),
    ("run.pdf", "application/pdf", b"MZ\x90\x00" + b"\0" * 40, eml_intake.REASON_EXECUTABLE),
    ("macro.js", "text/javascript", b"alert(1)", eml_intake.REASON_EXECUTABLE),
    ("losses.xlsx", "application/vnd.ms-excel", b"PK\x03\x04" + b"\0" * 40,
     eml_intake.REASON_UNSUPPORTED),
    ("scan.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"\0" * 20, eml_intake.REASON_UNSUPPORTED),
])
def test_unsupported_attachments_are_rejected_with_a_plain_reason(name, mime, data, reason):
    attachment = parse_eml(eml([(name, mime, data)])).submission.attachments[0]
    assert attachment.outcome is IntakeOutcome.REJECTED
    assert attachment.reason == reason


def test_a_valid_pdf_beside_an_invalid_attachment_is_still_read(profiles):
    submission, done = read_submission(
        eml([("bad.pdf", "application/pdf", b"not a pdf"),
             ("good.pdf", "application/pdf", PDF_A)]), profiles)
    try:
        bad, good = submission.attachments
        assert bad.outcome is IntakeOutcome.REJECTED
        assert good.processing is ProcessingState.PROCESSED
        assert len(done) == 1
        assert not submission.intake_complete  # the rejection stays outstanding
    finally:
        _cleanup(done)


def test_an_encrypted_pdf_is_rejected_at_staging(tmp_path, profiles):
    document = pymupdf.open(stream=PDF_A, filetype="pdf")
    locked = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                              owner_pw="owner", user_pw="user")
    submission, done = read_submission(eml([("locked.pdf", "application/pdf", locked)]),
                                       profiles)
    assert submission.attachments[0].outcome is IntakeOutcome.REJECTED
    assert submission.attachments[0].reason == eml_intake.REASON_ENCRYPTED
    assert not done


def test_a_pdf_header_on_a_broken_file_is_rejected_at_staging(profiles):
    submission, done = read_submission(
        eml([("broken.pdf", "application/pdf", b"%PDF-1.7\n garbage garbage")]), profiles)
    assert submission.attachments[0].outcome is IntakeOutcome.REJECTED
    assert submission.attachments[0].reason == eml_intake.REASON_DAMAGED
    assert not done


# --------------------------------------------------------------------------
# Malformed MIME
# --------------------------------------------------------------------------


def _raw_multipart(parts: str, *, close: bool = True) -> bytes:
    body = (
        "From: sender@example.test\r\nSubject: synthetic\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="BOUND"\r\n\r\n' + parts
        + ("--BOUND--\r\n" if close else "")
    )
    return body.encode("ascii")


def _pdf_part(name: str, data: bytes, *, broken_base64: bool = False) -> str:
    encoded = base64.b64encode(data).decode()
    if broken_base64:
        encoded = encoded[:-7] + "!!*"  # characters outside the alphabet, bad padding
    return (
        "--BOUND\r\nContent-Type: application/pdf\r\nContent-Transfer-Encoding: base64\r\n"
        f'Content-Disposition: attachment; filename="{name}"\r\n\r\n{encoded}\r\n'
    )


def test_a_badly_encoded_attachment_fails_and_its_sibling_continues(profiles):
    raw = _raw_multipart(_pdf_part("bad.pdf", PDF_B, broken_base64=True)
                         + _pdf_part("good.pdf", PDF_A))
    submission, done = read_submission(raw, profiles)
    try:
        bad, good = submission.attachments
        assert bad.outcome is IntakeOutcome.FAILED
        assert bad.reason == eml_intake.REASON_UNDECODABLE
        assert good.processing is ProcessingState.PROCESSED
    finally:
        _cleanup(done)


def test_a_missing_close_boundary_marks_the_inventory_incomplete(profiles):
    raw = _raw_multipart(_pdf_part("good.pdf", PDF_A), close=False)
    submission, done = read_submission(raw, profiles)
    try:
        assert submission.attachments[0].processing is ProcessingState.PROCESSED
        assert submission.inventory_complete is False
        assert not submission.intake_complete
    finally:
        _cleanup(done)


def test_a_missing_boundary_parameter_is_never_reported_as_complete():
    raw = (b"From: a@example.test\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed\r\n\r\n"
           b"--X\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.4 ...\r\n--X--\r\n")
    submission = parse_eml(raw).submission
    assert submission.inventory_complete is False
    assert not submission.intake_complete


@pytest.mark.parametrize("raw", [b"", b"\x00\x01\x02 not an email at all" * 3,
                                 b"just some words with no header separator"])
def test_a_file_that_is_not_an_email_is_a_fatal_error(raw):
    with pytest.raises(EmlFatalError):
        parse_eml(raw)


# --------------------------------------------------------------------------
# Limits (each applied before the work it bounds)
# --------------------------------------------------------------------------


def test_limit_raw_email_size_is_checked_before_parsing(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("parsed despite the size limit")

    monkeypatch.setattr(eml_intake.email, "message_from_bytes", refuse)
    with pytest.raises(EmlFatalError, match="larger than"):
        parse_eml(b"x" * 2048, replace(DEFAULT_LIMITS, max_email_bytes=1024))


def test_limit_part_count():
    raw = eml([(f"{n}.pdf", "application/pdf", PDF_A) for n in range(6)])
    with pytest.raises(EmlFatalError, match="parts"):
        parse_eml(raw, replace(DEFAULT_LIMITS, max_parts=4))


def test_limit_nesting_depth():
    leaf = "--B3\r\nContent-Type: application/pdf\r\n\r\n%PDF-\r\n--B3--\r\n"
    raw = ("From: a@example.test\r\nMIME-Version: 1.0\r\n"
           'Content-Type: multipart/mixed; boundary="B1"\r\n\r\n'
           '--B1\r\nContent-Type: multipart/mixed; boundary="B2"\r\n\r\n'
           '--B2\r\nContent-Type: multipart/mixed; boundary="B3"\r\n\r\n' + leaf
           + "--B2--\r\n--B1--\r\n").encode()
    with pytest.raises(EmlFatalError, match="levels deep"):
        parse_eml(raw, replace(DEFAULT_LIMITS, max_depth=2))


def test_limit_attachment_count_stops_and_says_so():
    raw = eml([(f"{n}.txt", "application/octet-stream", b"x" * (n + 1)) for n in range(5)])
    submission = parse_eml(raw, replace(DEFAULT_LIMITS, max_attachments=3)).submission
    assert [a.position for a in submission.attachments] == [1, 2, 3, 4, 5]
    assert all(a.outcome is IntakeOutcome.REJECTED for a in submission.attachments[3:])
    assert "more than 3 attachments" in submission.attachments[3].reason
    assert submission.inventory_complete is False


def test_limit_total_attachment_bytes_is_checked_before_decoding(monkeypatch):
    decoded: list[str] = []
    original = eml_intake.Message.get_payload

    def spy(self, *args, **kwargs):
        if kwargs.get("decode"):
            decoded.append(self.get_filename())
        return original(self, *args, **kwargs)

    raw = eml([("a.pdf", "application/pdf", PDF_A), ("b.pdf", "application/pdf", PDF_B)])
    limit = len(PDF_A) + len(PDF_B) // 2
    monkeypatch.setattr(eml_intake.Message, "get_payload", spy)
    submission = parse_eml(raw, replace(DEFAULT_LIMITS, max_total_attachment_bytes=limit)).submission
    assert submission.attachments[0].outcome is IntakeOutcome.ACCEPTED
    assert submission.attachments[1].outcome is IntakeOutcome.REJECTED
    assert "in total" in submission.attachments[1].reason
    assert "b.pdf" not in decoded  # refused on its encoded size, never decoded
    assert submission.inventory_complete is False


def test_limit_one_pdf_size():
    submission = parse_eml(eml([("a.pdf", "application/pdf", PDF_A)]),
                           replace(DEFAULT_LIMITS, max_pdf_bytes=len(PDF_A) // 2)).submission
    assert submission.attachments[0].outcome is IntakeOutcome.REJECTED
    assert "larger than" in submission.attachments[0].reason


def test_limit_page_count(profiles):
    three_pages = loss_run_pdf(rows=claim_rows(9), pages=3)
    submission, done = read_submission(
        eml([("long.pdf", "application/pdf", three_pages)]), profiles,
        replace(DEFAULT_LIMITS, max_pdf_pages=2))
    assert submission.attachments[0].outcome is IntakeOutcome.REJECTED
    assert "2-page limit" in submission.attachments[0].reason
    assert not done


# --------------------------------------------------------------------------
# Nothing rendered, fetched or kept
# --------------------------------------------------------------------------


def test_no_network_no_html_rendering_and_no_body_kept(monkeypatch, profiles):
    def no_network(*_a, **_k):
        raise AssertionError("intake attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    raw = eml([("a.pdf", "application/pdf", PDF_A),
               ("page.html", "text/html", f"<img src='https://x.test/{BODY_SECRET}'>".encode())])
    submission, done = read_submission(raw, profiles)
    try:
        # The HTML attachment is listed and rejected, never rendered.
        assert submission.attachments[1].outcome is IntakeOutcome.REJECTED
        text = repr(submission.__dict__) + repr([a.__dict__ for a in submission.attachments])
        assert BODY_SECRET not in text
        for _staged, result in done.values():
            assert BODY_SECRET not in repr(result.warnings)
    finally:
        _cleanup(done)


def test_email_metadata_is_kept_in_memory_but_never_as_an_identifier():
    submission = parse_eml(eml([("a.pdf", "application/pdf", PDF_A)])).submission
    assert submission.sender == SENDER and submission.subject == SUBJECT
    assert submission.email_date is not None
    ids = [submission.submission_id] + [a.attachment_id for a in submission.attachments]
    for identifier in ids:
        assert "example" not in identifier and "Harbor" not in identifier


def test_reasons_never_carry_header_or_parser_text():
    raw = _raw_multipart(_pdf_part("SECRET-NAME.pdf", PDF_B, broken_base64=True))
    submission = parse_eml(raw).submission
    for attachment in submission.attachments:
        assert "SECRET" not in attachment.reason
    assert all("SECRET" not in problem for problem in submission.intake_problems)


# --------------------------------------------------------------------------
# Staged-file lifecycle
# --------------------------------------------------------------------------


def test_a_pdf_the_pipeline_cannot_read_is_discarded(tmp_path):
    parsed = parse_eml(eml([("a.pdf", "application/pdf", PDF_A),
                            ("b.pdf", "application/pdf", PDF_B)]))
    staged = stage_attachments(parsed)
    paths = [p.staged.path for p in staged]
    calls = []

    def flaky(source: IngestedFile):
        calls.append(source)
        if len(calls) == 1:
            raise RuntimeError("synthetic pipeline failure")
        return run_pipeline(source, use_vision=False, profiles_dir=tmp_path / "p")

    done = process_attachments(staged, flaky)
    try:
        first, second = parsed.submission.attachments
        assert first.processing is ProcessingState.FAILED
        assert first.processing_reason == eml_intake.REASON_PROCESSING
        assert not paths[0].exists()          # the failed one is gone
        assert paths[1].exists()              # its sibling is kept for the user
        assert second.processing is ProcessingState.PROCESSED
    finally:
        _cleanup(done)
    assert not paths[1].exists()


def test_an_interruption_while_staging_discards_everything_staged(monkeypatch):
    parsed = parse_eml(eml([("a.pdf", "application/pdf", PDF_A),
                            ("b.pdf", "application/pdf", PDF_B)]))
    real_ingest = eml_intake.ingest
    made: list[Path] = []

    def ingest_then_interrupt(data, name, workdir=None, **limits):
        if made:
            raise KeyboardInterrupt
        staged = real_ingest(data, name, workdir, **limits)
        made.append(staged.path)
        return staged

    monkeypatch.setattr(eml_intake, "ingest", ingest_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        stage_attachments(parsed)
    assert made and not made[0].exists()
    assert all(p.data is None for p in parsed.pending)


def test_an_interruption_while_processing_discards_what_was_not_handed_back(tmp_path):
    parsed = parse_eml(eml([("a.pdf", "application/pdf", PDF_A),
                            ("b.pdf", "application/pdf", PDF_B)]))
    staged = stage_attachments(parsed)
    paths = [p.staged.path for p in staged]

    def interrupt(_source):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        process_attachments(staged, interrupt)
    assert not any(path.exists() for path in paths)


def test_a_rerun_after_some_pdfs_were_read_discards_those_too(profiles):
    """Streamlit raises a rerun from the progress callback: nothing is handed back."""
    parsed = parse_eml(eml([("a.pdf", "application/pdf", PDF_A),
                            ("b.pdf", "application/pdf", PDF_B)]))
    staged = stage_attachments(parsed)
    paths = [p.staged.path for p in staged]

    class Rerun(BaseException):
        pass

    def progress(read, _total):
        if read == 1:
            raise Rerun

    with pytest.raises(Rerun):
        process_attachments(staged, profiles, parsed.submission, progress)
    assert not any(path.exists() for path in paths)
    assert all(a.processing is ProcessingState.FAILED for a in parsed.submission.attachments)


def test_cleanup_never_touches_unrelated_files(tmp_path, profiles):
    bystander = Path(tempfile.gettempdir()) / f"not-losslift-{tmp_path.name}.txt"
    bystander.write_text("keep me")
    try:
        submission, done = read_submission(eml([("a.pdf", "application/pdf", PDF_A)]), profiles)
        _cleanup(done)
        assert bystander.read_text() == "keep me"
    finally:
        bystander.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Same results as a direct upload
# --------------------------------------------------------------------------


def _signature(result):
    document = result.document
    claims = [c.model_dump(exclude={"row_id"}) for c in document.claims]
    findings = sorted(
        (f.rule_id, f.severity.value, f.claim_number or "", f.field or "", f.message)
        for f in result.reconciliation.findings
    )
    return {
        "claims": claims,
        "findings": findings,
        "status": result.reconciliation.status,
        "runs": [(r.run_id, r.pages) for r in document.runs],
        "facts": (document.carrier, document.named_insured, document.policy_number,
                  document.valuation_date, document.printed_totals,
                  document.printed_claim_count, document.file_sha256),
    }


def test_the_same_pdf_reads_identically_direct_and_through_an_email(tmp_path):
    from core.ingest import stage_and_run

    runner = run(tmp_path / "profiles")
    direct_staged, direct = stage_and_run(PDF_B, "b.pdf", runner)
    submission, done = read_submission(eml([("b.pdf", "application/pdf", PDF_B)]), runner)
    try:
        (_staged, via_email), = done.values()
        assert _signature(via_email) == _signature(direct)
        # Only submission metadata differs: a new document id per upload.
        assert via_email.document.document_id != direct.document.document_id
        assert via_email.document.source_filename == direct.document.source_filename
    finally:
        discard(direct_staged)
        _cleanup(done)


def test_a_packet_pdf_is_one_attachment_with_its_runs_intact(tmp_path):
    runner = run(tmp_path / "profiles")
    data = packet_pdf()
    submission, done = read_submission(eml([("packet.pdf", "application/pdf", data)]), runner)
    try:
        assert len(submission.attachments) == 1 and len(done) == 1
        (_staged, via_email), = done.values()
        from core.ingest import stage_and_run
        direct_staged, direct = stage_and_run(data, "packet.pdf", runner)
        try:
            assert len(via_email.document.runs) == 2
            assert _signature(via_email)["runs"] == _signature(direct)["runs"]
        finally:
            discard(direct_staged)
    finally:
        _cleanup(done)


# --------------------------------------------------------------------------
# Review fixes
# --------------------------------------------------------------------------


def test_an_owner_password_pdf_reads_exactly_as_a_direct_upload(tmp_path):
    """Print/copy restrictions do not stop reading; only a needed password does."""
    from core.ingest import stage_and_run

    document = pymupdf.open(stream=PDF_B, filetype="pdf")
    restricted = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                                  owner_pw="owner-only", user_pw="",
                                  permissions=pymupdf.PDF_PERM_ACCESSIBILITY)
    runner = run(tmp_path / "profiles")
    submission, done = read_submission(
        eml([("restricted.pdf", "application/pdf", restricted)]), runner)
    direct_staged, direct = stage_and_run(restricted, "restricted.pdf", runner)
    try:
        assert submission.attachments[0].outcome is IntakeOutcome.ACCEPTED
        (_s, via_email), = done.values()
        assert _signature(via_email) == _signature(direct)
    finally:
        discard(direct_staged)
        _cleanup(done)


def test_every_attached_message_type_is_refused_and_never_walked():
    raw = (
        "From: a@example.test\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        '--B\r\nContent-Type: message/external-body; access-type=URL; '
        'URL="https://remote.example.test/losses.pdf"\r\n\r\n'
        "Content-Type: application/pdf\r\n\r\n\r\n"
        "--B\r\nContent-Type: message/partial; id=\"x\"; number=1\r\n\r\n"
        "Content-Type: application/pdf\r\n\r\n%PDF-1.4 fragment\r\n"
        "--B--\r\n"
    ).encode()
    submission = parse_eml(raw).submission
    assert [a.declared_mime for a in submission.attachments] == [
        "message/external-body", "message/partial"]
    assert all(a.outcome is IntakeOutcome.REJECTED for a in submission.attachments)
    assert all(a.reason == eml_intake.REASON_UNSUPPORTED for a in submission.attachments)


def test_a_copy_of_a_pdf_that_was_not_read_says_so_and_is_one_blocker(profiles):
    from core.submission import summarise_submission

    broken = b"%PDF-1.7\n not really a pdf"
    submission, done = read_submission(
        eml([("one.pdf", "application/pdf", broken),
             ("two.pdf", "application/pdf", broken)]), profiles)
    first, copy = submission.attachments
    assert first.outcome is IntakeOutcome.REJECTED
    assert copy.outcome is IntakeOutcome.DUPLICATE
    assert "read once" not in copy.reason and "not read" in copy.reason
    blockers = summarise_submission(submission, {}).blockers
    assert sum("two.pdf" in b for b in blockers) == 0
    assert sum("one.pdf" in b for b in blockers) == 1
    assert submission.set_attachment_aside(first.attachment_id)
    assert submission.intake_complete


def test_a_copy_of_a_pdf_the_pipeline_failed_on_says_so(tmp_path):
    def fail(_source):
        raise RuntimeError("synthetic")

    submission, done = read_submission(
        eml([("one.pdf", "application/pdf", PDF_A), ("two.pdf", "application/pdf", PDF_A)]),
        fail)
    first, copy = submission.attachments
    assert first.processing is ProcessingState.FAILED
    assert "could not be read as a loss run" in copy.reason


def test_progress_is_reported_before_each_pdf_is_read(profiles):
    calls = []
    submission, done = read_submission(
        eml([("a.pdf", "application/pdf", PDF_A), ("b.pdf", "application/pdf", PDF_B)]),
        profiles, progress=lambda read, total: calls.append((read, total)))
    try:
        assert calls == [(0, 2), (1, 2)]
    finally:
        _cleanup(done)
