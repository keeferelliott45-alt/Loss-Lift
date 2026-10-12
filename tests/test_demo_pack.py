"""The demo pack behaves the way demo/README.md tells a prospect it will.

A live demo that contradicts its own runbook is worse than no demo, so each
document's status, run count and talking-point findings are pinned here, and
the PDFs must rebuild byte for byte. Synthetic documents only.
"""

from __future__ import annotations

from demo.build import DEMO, EMAIL_FILENAME, RECORDINGS, build, check, read


def test_every_demo_document_behaves_as_the_runbook_says(tmp_path):
    build(tmp_path)
    assert check(tmp_path) == []


def test_the_demo_pdfs_and_recordings_rebuild_byte_for_byte(tmp_path):
    first = build(tmp_path / "a")
    second = build(tmp_path / "b")
    # The saved email's MIME boundary varies run to run; everything else is
    # the same bytes, the recorded vision answer included.
    first.pop(EMAIL_FILENAME), second.pop(EMAIL_FILENAME)
    assert first == second
    assert {item.filename for item in DEMO} < set(first)
    assert any(name.startswith(f"{RECORDINGS}/") for name in first)


def test_the_scanned_replay_never_reaches_a_model(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    build(tmp_path)
    item = next(entry for entry in DEMO if entry.replay)
    result = read(tmp_path / item.filename, replay=True)
    assert result.document.claims
    assert {claim.source_method.value for claim in result.document.claims} == {"vision"}
    assert not result.document.failed_pages
