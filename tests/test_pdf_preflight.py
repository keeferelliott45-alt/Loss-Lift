"""One PDF preflight on every intake path: direct bytes, a file path, an email.

Synthetic PDFs only (``tests/pdf_fixtures.py``). The behaviour tests use the
long-standing intake API -- ``ingest``, ``ingest_path``, ``IngestError`` and
email intake -- so they state what a caller sees, not how it is built.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import ingest as ingest_module
from core.eml_intake import read_submission
from core.ingest import IngestError, discard, ingest, ingest_path, stage_and_run
from core.submission import IntakeOutcome
from tests.pdf_fixtures import (
    MALFORMED_PDF,
    owner_only_pdf,
    password_pdf,
    synthetic_pdf,
    zero_page_pdf,
)
from tests.submission_fixtures import eml, loss_run_pdf, run

SECRET_NAME = "SECRET-CLIENT-NAME.pdf"


def _direct(data: bytes, tmp_path: Path, **limits):
    return ingest(data, SECRET_NAME, tmp_path / "direct", **limits)


def _path(data: bytes, tmp_path: Path, **limits):
    source = tmp_path / SECRET_NAME
    source.write_bytes(data)
    return ingest_path(source, tmp_path / "path", **limits)


def _staged_files(directory: Path) -> list[Path]:
    """Staged bytes left in a directory (the ownership marker is not one)."""
    if not directory.exists():
        return []
    return [p for p in directory.iterdir() if p.suffix == ".pdf"]


REFUSED = {
    "opening password": password_pdf,
    "damaged": lambda: MALFORMED_PDF,
    "no pages": zero_page_pdf,
}


# --------------------------------------------------------------------------
# What every path refuses, and what it accepts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(REFUSED))
@pytest.mark.parametrize("stage", [_direct, _path], ids=["direct", "path"])
def test_a_pdf_that_cannot_be_read_is_refused_and_nothing_is_left_staged(
        kind, stage, tmp_path):
    with pytest.raises(IngestError):
        stage(REFUSED[kind](), tmp_path)
    assert _staged_files(tmp_path / "direct") == []
    assert _staged_files(tmp_path / "path") == []


@pytest.mark.parametrize("stage", [_direct, _path], ids=["direct", "path"])
def test_owner_only_encryption_is_read_like_any_pdf(stage, tmp_path):
    staged = stage(owner_only_pdf(), tmp_path)
    try:
        assert staged.path.exists()
    finally:
        discard(staged)


@pytest.mark.parametrize("stage", [_direct, _path], ids=["direct", "path"])
def test_the_page_limit_applies_to_direct_and_path_intake(stage, tmp_path):
    with pytest.raises(IngestError, match="1000-page limit"):
        stage(synthetic_pdf("long", pages=1001), tmp_path)
    staged = stage(synthetic_pdf("exactly at the limit", pages=1000), tmp_path)
    discard(staged)


@pytest.mark.parametrize("stage", [_direct, _path], ids=["direct", "path"])
def test_custom_limits_are_exact_and_keyword_only(stage, tmp_path):
    three = synthetic_pdf("three", pages=3)
    staged = stage(three, tmp_path, max_pages=3, max_bytes=len(three))
    discard(staged)
    with pytest.raises(IngestError, match="3-page limit"):
        stage(synthetic_pdf("four", pages=4), tmp_path, max_pages=3)
    with pytest.raises(IngestError, match="limit"):
        stage(three, tmp_path, max_bytes=len(three) - 1)
    with pytest.raises(TypeError):
        ingest(three, "x.pdf", tmp_path, len(three))  # limits are keyword-only


def test_a_valid_pdf_records_its_page_count(tmp_path):
    for stage in (_direct, _path):
        staged = stage(synthetic_pdf("pages", pages=2), tmp_path)
        try:
            assert staged.page_count == 2
        finally:
            discard(staged)


# --------------------------------------------------------------------------
# Fixed reasons, identical on every path, carrying nothing from the file
# --------------------------------------------------------------------------


def _reasons():
    from core.ingest import PreflightReason

    return {
        "opening password": PreflightReason.PASSWORD,
        "damaged": PreflightReason.DAMAGED,
        "no pages": PreflightReason.NO_PAGES,
    }


@pytest.mark.parametrize("kind", sorted(REFUSED))
def test_direct_path_and_email_intake_refuse_for_the_same_reason(kind, tmp_path):
    from core.ingest import PdfPreflightError

    data = REFUSED[kind]()
    refusals = []
    for stage in (_direct, _path):
        with pytest.raises(PdfPreflightError) as caught:
            stage(data, tmp_path)
        refusals.append(caught.value)
    assert {error.reason for error in refusals} == {_reasons()[kind]}
    assert len({str(error) for error in refusals}) == 1

    submission, done = read_submission(
        eml([(SECRET_NAME, "application/pdf", data)]), run(tmp_path / "profiles"))
    (attachment,) = submission.attachments
    assert attachment.outcome is IntakeOutcome.REJECTED
    assert attachment.reason == str(refusals[0])
    assert not done


@pytest.mark.parametrize("kind", sorted(REFUSED) + ["too many pages", "too large",
                                                     "empty", "not a pdf"])
def test_a_refusal_names_no_file_path_or_parser_detail(kind, tmp_path):
    from core.ingest import PREFLIGHT_MESSAGES, PdfPreflightError

    others = {
        "too many pages": synthetic_pdf("long", pages=3),
        "too large": synthetic_pdf("big"),
        "empty": b"",
        "not a pdf": b"PK\x03\x04 not a pdf",
    }
    data = others[kind] if kind in others else REFUSED[kind]()
    limits = {"max_pages": 2, "max_bytes": 64 * 1024 * 1024}
    if kind == "too large":
        limits["max_bytes"] = 100
    for stage in (_direct, _path):
        with pytest.raises(PdfPreflightError) as caught:
            stage(data, tmp_path, **limits)
        message = str(caught.value)
        assert "SECRET" not in message and str(tmp_path) not in message
        templates = {m.split("{")[0] for m in PREFLIGHT_MESSAGES.values()}
        assert any(message.startswith(t) for t in templates), message


def test_a_refusal_is_still_an_ingest_error_for_existing_handlers():
    from core.ingest import PdfPreflightError

    assert issubclass(PdfPreflightError, IngestError)
    assert issubclass(PdfPreflightError, ValueError)


# --------------------------------------------------------------------------
# Email: siblings, duplicates and the same limits
# --------------------------------------------------------------------------


def test_valid_siblings_are_read_beside_refused_pdfs_and_duplicates(tmp_path):
    valid = loss_run_pdf()
    raw = eml([
        ("locked.pdf", "application/pdf", password_pdf()),
        ("claims.pdf", "application/pdf", valid),
        ("empty-tree.pdf", "application/pdf", zero_page_pdf()),
        ("copy.pdf", "application/pdf", valid),
        ("restricted.pdf", "application/pdf", owner_only_pdf()),
    ])
    submission, done = read_submission(raw, run(tmp_path / "profiles"))
    try:
        outcomes = [a.outcome for a in submission.attachments]
        assert outcomes == [IntakeOutcome.REJECTED, IntakeOutcome.ACCEPTED,
                            IntakeOutcome.REJECTED, IntakeOutcome.DUPLICATE,
                            IntakeOutcome.ACCEPTED]
        assert submission.attachments[3].duplicate_of == submission.attachments[1].attachment_id
        assert len(done) == 2
    finally:
        for staged, _result in done.values():
            discard(staged)


def test_the_email_page_limit_is_the_shared_default():
    from core.eml_intake import DEFAULT_LIMITS
    from core.ingest import MAX_PDF_PAGES, MAX_UPLOAD_BYTES

    assert MAX_PDF_PAGES == 1000
    assert DEFAULT_LIMITS.max_pdf_pages == MAX_PDF_PAGES
    assert DEFAULT_LIMITS.max_pdf_bytes == MAX_UPLOAD_BYTES


# --------------------------------------------------------------------------
# Cleanup: a refusal, a failure or an interruption leaves nothing behind
# --------------------------------------------------------------------------


def test_a_refused_upload_never_creates_a_staging_directory(tmp_path, monkeypatch):
    made: list[str] = []
    real_mkdtemp = ingest_module.tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        made.append("mkdtemp")
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    with pytest.raises(IngestError):
        ingest(password_pdf(), SECRET_NAME)
    assert made == []


def test_a_refused_snapshot_is_removed_with_its_private_directory(tmp_path, monkeypatch):
    stage = tmp_path / "losslift-refused-snapshot"

    def recording_mkdtemp(*, prefix: str) -> str:
        stage.mkdir()
        return str(stage)

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    source = tmp_path / "locked.pdf"
    source.write_bytes(password_pdf())
    with pytest.raises(IngestError):
        ingest_path(source, "")
    assert not stage.exists()
    assert source.exists(), "the caller's own file is never touched"


def test_an_interruption_during_the_snapshot_check_removes_the_snapshot(
        tmp_path, monkeypatch):
    workdir = tmp_path / "shared-stage"
    source = tmp_path / "claims.pdf"
    source.write_bytes(synthetic_pdf("interrupted"))
    earlier = ingest(synthetic_pdf("earlier upload"), "earlier.pdf", workdir)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(ingest_module, "preflight_pdf_path", interrupt)
    with pytest.raises(KeyboardInterrupt):
        ingest_path(source, workdir)
    assert _staged_files(workdir) == [earlier.path], "only the earlier upload remains"
    assert earlier.path.exists()
    discard(earlier)


def test_stage_and_run_never_runs_the_pipeline_on_a_refused_pdf(tmp_path):
    calls: list[str] = []
    with pytest.raises(IngestError):
        stage_and_run(zero_page_pdf(), "empty-tree.pdf", lambda staged: calls.append("run"),
                      tmp_path)
    assert calls == []
    assert _staged_files(tmp_path) == []


def test_path_intake_through_the_pipeline_refuses_an_opening_password(tmp_path):
    from core.pipeline import run_pipeline

    source = tmp_path / "locked.pdf"
    source.write_bytes(password_pdf())
    with pytest.raises(IngestError, match="password-protected"):
        run_pipeline(source, use_vision=False, use_llm=False)
