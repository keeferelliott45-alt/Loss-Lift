"""The submission workspace in the Streamlit app, driven headlessly.

Session state is filled exactly as ``_extract_email`` leaves it, from a
synthetic saved email read through the real intake path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.eml_intake import read_submission
from core.ingest import discard, stage_and_run
from tests.submission_fixtures import claim_rows, eml, loss_run_pdf, run

APP = str(Path(__file__).resolve().parent.parent / "app.py")
HOSTILE_NAME = "<img src=x onerror=alert(1)>[click](javascript:x).pdf"


def _app(done, submission=None, direct=None):
    at = AppTest.from_file(APP, default_timeout=120)
    documents = {doc_id: result for doc_id, (_s, result) in done.items()}
    staged = {doc_id: staged for doc_id, (staged, _r) in done.items()}
    if direct is not None:
        staged_file, result = direct
        documents[result.document.document_id] = result
        staged[result.document.document_id] = staged_file
    at.session_state["documents"] = documents
    at.session_state["order"] = list(documents)
    at.session_state["staged"] = staged
    if submission is not None:
        at.session_state["submissions"] = {submission.submission_id: submission}
        at.session_state["submission_order"] = [submission.submission_id]
        at.session_state["submission_of"] = {d: submission.submission_id for d in done}
    return at.run()


@pytest.fixture
def emailed(tmp_path):
    raw = eml([
        (HOSTILE_NAME, "application/pdf", loss_run_pdf(rows=claim_rows(3, big=50000))),
        ("readme.docx", "application/octet-stream", b"PK\x03\x04" + b"\0" * 20),
    ])
    submission, done = read_submission(raw, run(tmp_path / "profiles"))
    yield submission, done
    for staged, _r in done.values():
        discard(staged)


def _texts(at):
    return [t.value for t in at.text] + [m.value for m in at.markdown] + [
        c.value for c in at.caption]


def _open_submission(at, submission):
    return at.button(key=f"open-sub-{submission.submission_id}").click().run()


def test_the_queue_lists_the_submission_and_opens_it(emailed):
    submission, done = emailed
    at = _app(done, submission)
    assert not at.exception
    at = _open_submission(at, submission)
    assert not at.exception
    texts = _texts(at)
    assert any("## 📨 Submission" in t for t in texts)
    assert any("Incomplete" in t for t in texts)
    assert len(at.dataframe) == 1  # the attachment inventory
    inventory = at.dataframe[0].value
    assert list(inventory["Outcome"]) == ["Accepted", "Rejected"]


def test_grouping_and_status_survive_reruns(emailed):
    submission, done = emailed
    at = _open_submission(_app(done, submission), submission)
    for _ in range(3):
        at = at.run()
        assert not at.exception
        assert at.session_state["open_submission"] == submission.submission_id
        assert set(at.session_state["submission_of"]) == set(done)
        assert any("Incomplete" in t for t in _texts(at))


def test_setting_the_rejection_aside_gives_a_qualified_status_that_persists(emailed):
    submission, done = emailed
    at = _open_submission(_app(done, submission), submission)
    rejected = submission.attachments[1]
    at = at.checkbox(key=f"aside-{rejected.attachment_id}").check().run()
    assert not at.exception
    at = at.run()
    texts = _texts(at)
    assert any("Complete and reconciled (1 attachment(s) set aside by a reviewer)" in t
               for t in texts)


def test_a_document_and_its_submission_link_both_ways(emailed):
    submission, done = emailed
    at = _open_submission(_app(done, submission), submission)
    (document_id,) = list(done)
    at = at.button(key=f"sub-open-{document_id}").click().run()
    assert not at.exception
    assert at.session_state["open_document"] == document_id
    labels = [b.label for b in at.button]
    assert "← Back to submission" in labels
    back = next(b for b in at.button if b.label == "← Back to submission")
    at = back.click().run()
    assert at.session_state["open_document"] is None
    assert at.session_state["open_submission"] == submission.submission_id
    assert any("## 📨 Submission" in t for t in _texts(at))


def test_a_large_claim_opens_its_evidence(emailed):
    submission, done = emailed
    at = _open_submission(_app(done, submission), submission)
    evidence = [b for b in at.button if b.label == "Evidence"]
    assert evidence, "no large claim offered its evidence"
    at = evidence[0].click().run()
    assert at.session_state["open_document"] == next(iter(done))


def test_a_hostile_file_name_is_never_rendered_as_html_or_markdown(emailed):
    submission, done = emailed
    at = _app(done, submission)
    for page in (at, _open_submission(at, submission)):
        for value in [m.value for m in page.markdown]:
            assert "<img" not in value
            assert "](javascript" not in value
    # Opened as a document, the header shows the name escaped.
    at = at.button(key=f"sub-open-{next(iter(done))}").click().run()
    header = next(m.value for m in at.markdown if m.value.startswith("## 📄"))
    assert "<img" not in header and "&lt;img" in header


def test_the_export_is_offered_redacted_by_default(emailed):
    submission, done = emailed
    at = _open_submission(_app(done, submission), submission)
    toggle = at.toggle(key=f"sub-redact-{submission.submission_id}")
    assert toggle.value is True


def test_a_direct_pdf_upload_behaves_as_before(tmp_path):
    direct = stage_and_run(loss_run_pdf(), "direct.pdf", run(tmp_path / "profiles"))
    try:
        at = _app({}, direct=direct)
        assert not at.exception
        texts = _texts(at)
        assert not any("Submissions from saved emails" in t for t in texts)
        document_id = direct[1].document.document_id
        at = at.button(key=f"open-{document_id}").click().run()
        labels = [b.label for b in at.button]
        assert "← Back to queue" in labels and "← Back to submission" not in labels
    finally:
        discard(direct[0])


def test_the_uploader_accepts_pdf_and_saved_email():
    source = Path(APP).read_text(encoding="utf-8")
    assert 'type=["pdf", "eml"]' in source
