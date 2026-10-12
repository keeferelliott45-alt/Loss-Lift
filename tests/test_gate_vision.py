"""The corpus gate measures the scanned path, and says when its evidence is missing.

A gate run with ``--vision-replay`` reads scanned pages from recorded answers.
The ``vision`` group records which pages were asked of the reader, which were
answered, which had no recording, and which had one that could not be used,
plus how the claims were read. A revision whose vision prompt or schema moved
finds none of the old recordings: that must surface as missing evidence and
fail the gate, never pass as "no change" and never pose as the engine reading
a scan differently.

Synthetic documents only; a stand-in model plays the live call.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

import core.extract_vision as vision
from core.extract_vision import recording_extractor, recording_key
from core.pipeline import run_pipeline
from tests.golden import fixtures as fx
from tests.test_extract_vision import StandInModel, transcription
from tools.corpus_gate import collect, compare as gate, seal
from tools.corpus_gate.manifest import Entry, Manifest

SALT = bytes(range(32))
DIGEST = collect.Digest(SALT)
SHA = "a" * 64
DOC = "doc-scan"


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def recorded(golden_dir, tmp_path):
    model = StandInModel(transcription(fx.SCANNED))
    path = golden_dir / "scanned.pdf"
    live = run_pipeline(path, use_vision=True,
                        vision_extractor=recording_extractor(tmp_path / "rec", client=model))
    assert model.calls == 1 and live.document.claims
    return path, tmp_path / "rec", live


def _collect(tmp_path, monkeypatch, path, replay=None):
    """Run the collector exactly as the gate does, salted, and return the metrics."""
    documents = tmp_path / "documents.json"
    documents.write_text(json.dumps([{"id": DOC, "path": str(path)}]))
    out = tmp_path / "out.jsonl"
    monkeypatch.setattr(sys, "stdin", io.StringIO(SALT.hex() + "\n"))
    root = Path(collect.__file__).resolve().parents[2]
    argv = ["--root", str(root), "--documents", str(documents), "--out", str(out)]
    if replay is not None:
        argv += ["--vision-replay", str(replay)]
    assert collect.main(argv) == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    document = next(record for record in records if record.get("kind") == "document")
    assert document["ok"] is True, document
    return document["metrics"]


def _outcome(before, after):
    manifest = Manifest(salt=SALT, entries=(Entry(DOC, "a.pdf", SHA, 10),), sha256="c" * 64)
    runs = []
    for label, metrics in (("baseline", before), ("candidate", after)):
        run = gate.RevisionRun(label, ("1" if label == "baseline" else "2") * 40)
        run.records[DOC] = {"kind": "document", "id": DOC, "ok": True, "metrics": metrics}
        run.complete = True
        runs.append(run)
    return gate.compare(manifest, *runs)


# --- what is measured ------------------------------------------------------


def test_a_digital_document_without_replay_measures_no_vision(golden_dir):
    result = run_pipeline(golden_dir / "us_basic.pdf", use_vision=False)
    measured = collect.measure(result, DIGEST)["vision"]
    assert measured == {
        "replay": False, "requested": [], "answered": [], "missing": [], "unusable": [],
        "claims": {"digital": len(result.document.claims)}, "over_cap": 0,
    }


def test_a_replayed_scan_is_measured_page_by_page(recorded, tmp_path, monkeypatch):
    path, directory, live = recorded
    measured = _collect(tmp_path, monkeypatch, path, directory)["vision"]
    assert measured["replay"] is True
    assert measured["requested"] == measured["answered"] == [1]
    assert measured["missing"] == measured["unusable"] == []
    assert measured["claims"] == {"vision": len(live.document.claims)}
    assert measured["over_cap"] == 0


def test_a_page_with_no_recording_is_measured_as_missing(golden_dir, tmp_path, monkeypatch):
    (tmp_path / "empty").mkdir()
    measured = _collect(tmp_path, monkeypatch, golden_dir / "scanned.pdf",
                        tmp_path / "empty")["vision"]
    assert measured["requested"] == measured["missing"] == [1]
    assert measured["answered"] == measured["unusable"] == []
    assert measured["claims"] == {}


def test_a_recording_that_cannot_be_used_is_not_called_missing(golden_dir, tmp_path, monkeypatch):
    path = golden_dir / "scanned.pdf"
    directory = tmp_path / "broken"
    directory.mkdir()
    document = vision._document_sha256(path)
    (directory / f"{recording_key(document, 1)}.json").write_text(
        json.dumps({"version": vision.RECORDING_VERSION, "page": 1, "model": "x", "text": "{not json"}))
    measured = _collect(tmp_path, monkeypatch, path, directory)["vision"]
    assert measured["requested"] == measured["unusable"] == [1]
    assert measured["answered"] == measured["missing"] == []


def test_a_changed_prompt_reads_every_recording_as_missing(recorded, tmp_path, monkeypatch):
    """The hazard this group exists for: a prompt change orphans the recordings."""
    path, directory, _live = recorded
    before = _collect(tmp_path, monkeypatch, path, directory)
    monkeypatch.setattr(vision, "load_prompt", lambda: "a different prompt")
    after = _collect(tmp_path, monkeypatch, path, directory)
    assert after["vision"]["missing"] == [1] and after["vision"]["answered"] == []
    outcome = _outcome(before, after)
    assert outcome.exit_code == gate.EXIT_CHANGED
    paths = {seal.schema_path(change.path) for change in outcome.changes}
    assert {"vision.missing", "vision.answered"} <= paths


def test_replaying_twice_measures_the_vision_group_identically(recorded, tmp_path, monkeypatch):
    path, directory, _live = recorded
    one = _collect(tmp_path, monkeypatch, path, directory)
    two = _collect(tmp_path, monkeypatch, path, directory)
    assert one["vision"] == two["vision"]
    assert _outcome(one, two).exit_code == gate.EXIT_PASS


def test_a_vision_claim_above_the_confidence_cap_is_counted(recorded):
    _path, _directory, live = recorded
    claim = live.document.claims[0]
    claim.field_confidence = {name: 0.99 for name in claim.field_confidence} or {"claim_number": 0.99}
    measured = collect.measure(live, DIGEST, vision=None)["vision"]
    assert claim.confidence > collect.VISION_CONFIDENCE_CAP
    assert measured["over_cap"] == 1


# --- what the seal accepts -------------------------------------------------


def _sealed(value):
    return seal.Sealer(SALT).vision(value)


GOOD = {"replay": True, "requested": [1, 2, 3], "answered": [1], "missing": [2],
        "unusable": [3], "claims": {"vision": 4}, "over_cap": 0}


def test_the_seal_accepts_a_consistent_vision_group():
    assert _sealed(dict(GOOD)) == GOOD


@pytest.mark.parametrize("change", [
    {"answered": [1, 4]},                       # answered but never requested
    {"missing": [9]},                           # missing but never requested
    {"unusable": [1]},                          # unusable and answered at once
    {"missing": [1, 2]},                        # missing and answered at once
    {"requested": [1, 1, 2, 3]},                # requested twice
    {"replay": False},                          # pages requested without a replay
    {"requested": [3, 2, 1]},                   # out of order
    {"over_cap": -1},
    {"claims": {"Not A Method!": 1}},           # a raw key that is not a field name
    {"extra": 1},
])
def test_the_seal_rejects_an_inconsistent_vision_group(change):
    value = {**GOOD, **change}
    with pytest.raises(seal.Rejected):
        _sealed(value)


def test_vision_paths_are_published_by_schema_name():
    for name in seal.VISION_KEYS:
        assert seal.schema_path(f"vision.{name}") == f"vision.{name}"
    assert seal.schema_path("vision.claims.vision") == "vision.claims.*"


def test_the_published_leak_bound_counts_every_known_schema_field():
    """docs/cloud-corpus-gate.md states how many schema fields a run can flag."""
    import re

    text = (Path(__file__).resolve().parents[1] / "docs" / "cloud-corpus-gate.md").read_text()
    stated = re.search(r"which of the (\d+) known schema fields\s+changed \((\d+) bits\)", text)
    assert stated and int(stated.group(1)) == int(stated.group(2)) == len(seal.KNOWN_PATHS)
    total = re.search(r"at most (\d+) bits", text)
    assert total and int(total.group(1)) == 2 + len(seal.KNOWN_PATHS) + 40 + 8 + 14
