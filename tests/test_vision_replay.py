"""Vision answers recorded once and replayed deterministically.

The scanned path must be protected by the same regression checks as the
digital one without the gate or the suite ever calling a live model. A stand-in
model plays the live call here; everything after it -- the parser, the run
planner, reconciliation, the gate's measurement -- is the real code, fed the
recorded answer.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

import core.extract_vision as vision
from core.extract_vision import recording_extractor, recording_key, replay_extractor
from core.pipeline import run_pipeline
from core.review import canonical_status
from core.schema import DocumentStatus
from tests.golden import fixtures as fx
from tests.test_extract_vision import StandInModel, transcription
from tools.corpus_gate import collect

DIGEST = collect.Digest(bytes(range(32)))


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    """No key: any attempt to reach the real model fails the test."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def recorded(golden_dir, tmp_path):
    model = StandInModel(transcription(fx.SCANNED))
    path = golden_dir / "scanned.pdf"
    live = run_pipeline(path, use_vision=True,
                        vision_extractor=recording_extractor(tmp_path / "rec", client=model))
    assert model.calls == 1
    return path, tmp_path / "rec", live


def _view(result):
    return (
        [claim.model_dump(mode="json", exclude={"row_id"}) for claim in result.document.claims],
        result.reconciliation.status,
        sorted(finding.rule_id for finding in result.reconciliation.findings),
        result.document.failed_pages,
    )


def test_a_replay_reproduces_the_recorded_run_without_a_model(recorded):
    path, directory, live = recorded
    replayed = run_pipeline(path, use_vision=True, vision_extractor=replay_extractor(directory))
    assert live.document.claims, "the recording must have read claims"
    assert _view(replayed) == _view(live)


def test_replaying_twice_measures_identically(recorded):
    path, directory, _live = recorded
    first = run_pipeline(path, use_vision=True, vision_extractor=replay_extractor(directory))
    second = run_pipeline(path, use_vision=True, vision_extractor=replay_extractor(directory))
    one, two = collect.measure(first, DIGEST), collect.measure(second, DIGEST)
    one.pop("warnings"), two.pop("warnings")
    assert one == two


def test_a_page_with_no_recording_is_unread_not_empty(golden_dir, tmp_path):
    result = run_pipeline(golden_dir / "scanned.pdf", use_vision=True,
                          vision_extractor=replay_extractor(tmp_path / "empty"))
    assert result.document.claims == []
    assert 1 in result.document.failed_pages or 1 in result.document.unresolved_pages
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW
    assert any("no recorded vision answer" in warning for warning in result.warnings)


def test_a_changed_prompt_does_not_reuse_an_old_answer(recorded, monkeypatch):
    path, directory, _live = recorded
    document = vision._document_sha256(path)
    before = recording_key(document, 1)
    monkeypatch.setattr(vision, "load_prompt", lambda: "a different prompt")
    assert recording_key(document, 1) != before
    replayed = run_pipeline(path, use_vision=True, vision_extractor=replay_extractor(directory))
    assert replayed.document.claims == []


def test_a_recording_holds_the_raw_answer_under_its_key(recorded):
    path, directory, _live = recorded
    key = recording_key(vision._document_sha256(path), 1)
    record = json.loads((directory / f"{key}.json").read_text())
    assert record["page"] == 1 and record["version"] == vision.RECORDING_VERSION
    assert json.loads(record["text"])["headers"] == transcription(fx.SCANNED)["headers"]


def test_the_collector_replays_recorded_answers(recorded, tmp_path, monkeypatch):
    from pathlib import Path

    path, directory, live = recorded
    documents = tmp_path / "documents.json"
    documents.write_text(json.dumps([{"id": "doc-scan", "path": str(path)}]))
    out = tmp_path / "out.jsonl"
    monkeypatch.setattr(sys, "stdin", io.StringIO(bytes(range(32)).hex() + "\n"))
    root = Path(collect.__file__).resolve().parents[2]
    assert collect.main(["--root", str(root), "--documents", str(documents), "--out", str(out),
                         "--vision-replay", str(directory)]) == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    document = next(record for record in records if record.get("kind") == "document")
    assert document["ok"] is True
    assert document["metrics"]["claim_count"] == len(live.document.claims) > 0
