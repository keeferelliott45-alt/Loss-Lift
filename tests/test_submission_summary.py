"""What a submission lets an underwriter use, and what it keeps open.

Synthetic emails and PDFs only (``tests/submission_fixtures.py``).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.eml_intake import read_submission
from core.evidence import claim_evidence
from core.ingest import discard
from core.submission import (
    IntakeOutcome,
    ProcessingState,
    Submission,
    status_label,
    summarise_submission,
)
from tests.submission_fixtures import (
    INSURED,
    OTHER_INSURED,
    claim_rows,
    eml,
    loss_run_pdf,
    run,
)


@pytest.fixture
def read(tmp_path):
    held = []

    def _read(attachments):
        submission, done = read_submission(eml(attachments), run(tmp_path / "profiles"))
        held.append(done)
        return submission, {doc_id: result for doc_id, (_s, result) in done.items()}

    yield _read
    for done in held:
        for staged, _result in done.values():
            discard(staged)


PDF_2022 = loss_run_pdf(rows=claim_rows(4, big=30000), valuation="12/31/2022")
PDF_2023 = loss_run_pdf(rows=claim_rows(3, start=91000000), policy="GL-200",
                        valuation="12/31/2023")


def test_a_clean_single_insured_submission_is_complete_and_totalled(read):
    submission, results = read([("2022.pdf", "application/pdf", PDF_2022),
                                ("2023.pdf", "application/pdf", PDF_2023)])
    summary = summarise_submission(submission, results)
    assert summary.status == "ready" and summary.intake_complete
    assert summary.named_insured == INSURED
    (account,) = summary.accounts
    assert account.claims == 7
    assert account.open_claims == 1
    assert account.incurred_total == Decimal("30000") + Decimal("1000") * 3 + Decimal("3000")
    assert account.incurred_unavailable is None
    assert status_label(summary) == "Complete and reconciled"


def test_a_rejected_sibling_keeps_the_submission_incomplete(read):
    submission, results = read([("run.pdf", "application/pdf", PDF_2022),
                                ("notes.docx", "application/octet-stream", b"PK\x03\x04xx")])
    summary = summarise_submission(submission, results)
    assert summary.status == "incomplete"
    assert summary.accounts[0].claims == 4  # the readable document is still usable
    assert any("notes.docx" in b and "rejected" in b for b in summary.blockers)


def test_setting_a_rejection_aside_clears_it_with_a_qualified_status(read):
    submission, results = read([("run.pdf", "application/pdf", PDF_2022),
                                ("notes.docx", "application/octet-stream", b"PK\x03\x04xx")])
    rejected = submission.attachments[1]
    assert submission.set_attachment_aside(rejected.attachment_id)
    summary = summarise_submission(submission, results)
    assert summary.status == "ready"
    assert rejected.outcome is IntakeOutcome.REJECTED  # recorded beside, not instead
    assert status_label(summary).endswith("(1 attachment(s) set aside by a reviewer)")


def test_an_accepted_pdf_or_a_damaged_email_cannot_be_set_aside(read):
    submission, results = read([("run.pdf", "application/pdf", PDF_2022)])
    assert not submission.set_attachment_aside(submission.attachments[0].attachment_id)
    submission.inventory_complete = False
    assert summarise_submission(submission, results).status == "incomplete"


def test_a_failed_read_holds_the_submission_open():
    submission = Submission.new()
    from core.submission import Attachment
    submission.attachments.append(Attachment(
        attachment_id="att-x", position=1, display_filename="run.pdf",
        declared_mime="application/pdf", size_bytes=10, sha256="0" * 64,
        outcome=IntakeOutcome.ACCEPTED, processing=ProcessingState.FAILED,
        processing_reason="It could not be read as a loss run."))
    summary = summarise_submission(submission, {})
    assert summary.status == "incomplete"
    assert any("was not read" in b for b in summary.blockers)
    assert summary.named_insured is None


def test_a_duplicate_attachment_never_inflates_the_totals(read):
    once, results_once = read([("run.pdf", "application/pdf", PDF_2022)])
    twice, results_twice = read([("run.pdf", "application/pdf", PDF_2022),
                                 ("copy.pdf", "application/pdf", PDF_2022)])
    one = summarise_submission(once, results_once).accounts[0]
    two = summarise_submission(twice, results_twice).accounts[0]
    assert (two.claims, two.incurred_total) == (one.claims, one.incurred_total)
    assert summarise_submission(twice, results_twice).status == "ready"


def test_a_claim_repeated_across_valuations_is_counted_once_at_its_latest_value(read):
    earlier = loss_run_pdf(rows=claim_rows(2, big=30000), valuation="06/30/2022")
    later = loss_run_pdf(rows=claim_rows(2, big=45000), valuation="12/31/2022")
    submission, results = read([("june.pdf", "application/pdf", earlier),
                                ("dec.pdf", "application/pdf", later)])
    (account,) = summarise_submission(submission, results).accounts
    assert account.claims == 2
    assert account.incurred_total == Decimal("45000") + Decimal("1000")
    (large,) = account.large_claims
    assert large.incurred_total == Decimal("45000")
    later_document = next(r.document for r in results.values()
                          if str(r.document.valuation_date) == "2022-12-31")
    assert large.provenance.document_id == later_document.document_id


def test_separate_insureds_are_never_combined(read):
    other = loss_run_pdf(insured=OTHER_INSURED, rows=claim_rows(2, start=55000000))
    submission, results = read([("a.pdf", "application/pdf", PDF_2022),
                                ("b.pdf", "application/pdf", other)])
    summary = summarise_submission(submission, results)
    assert summary.named_insured is None
    assert "2 different insureds" in summary.named_insured_note
    assert len(summary.accounts) == 2
    assert all(a.incurred_total is None and "not combined" in a.incurred_unavailable
               for a in summary.accounts)
    assert summary.status == "needs_review"


def test_mixed_currencies_are_not_totalled(read):
    submission, results = read([("a.pdf", "application/pdf", PDF_2022),
                                ("b.pdf", "application/pdf", PDF_2023)])
    second = results[submission.attachments[1].document_id].document
    second.currency = "CAD"
    (account,) = summarise_submission(submission, results).accounts
    assert account.incurred_total is None
    assert "currency" in account.incurred_unavailable


def test_every_large_claim_keeps_provenance_reachable_as_evidence(read):
    submission, results = read([("a.pdf", "application/pdf", PDF_2022)])
    (account,) = summarise_submission(submission, results).accounts
    (large,) = account.large_claims
    attachment = submission.attachment(large.provenance.attachment_id)
    assert attachment.document_id == large.provenance.document_id
    document = results[large.provenance.document_id].document
    claim = next(c for c in document.claims if c.claim_number == large.claim_number)
    assert (claim.source_page, claim.source_row) == (large.provenance.page, large.provenance.row)
    evidence = claim_evidence(claim, "incurred_total")
    assert evidence.page == large.provenance.page


def test_a_document_awaiting_mapping_is_a_blocker_and_not_summed(read):
    submission, results = read([("a.pdf", "application/pdf", PDF_2022)])
    (result,) = results.values()
    result.mapping.fields = {}  # nothing mapped: the document waits on its columns
    assert result.needs_mapping
    summary = summarise_submission(submission, results)
    assert summary.status == "needs_review"
    assert summary.accounts == ()
    assert any("columns mapped" in b for b in summary.blockers)


def test_intake_completeness_is_kept_apart_from_trust(read):
    submission, results = read([("a.pdf", "application/pdf", PDF_2022)])
    (result,) = results.values()
    result.document.claims[0].incurred_total = Decimal("1")  # breaks R-01/R-04
    from core.reconcile import reconcile
    result.reconciliation = reconcile(result.document)
    summary = summarise_submission(submission, results)
    assert summary.intake_complete is True
    assert summary.status == "needs_review"


def test_an_email_with_no_loss_run_says_so(read):
    submission, results = read([("notes.txt", "text/plain", b"hello")])
    summary = summarise_submission(submission, results)
    assert summary.status == "incomplete"
    assert summary.named_insured is None
