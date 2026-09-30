"""The runner and command line, end to end over a synthetic private corpus.

The corpus, manifest, truth and output all live in a temporary directory
outside the repository, as real ones must. The checkout's cleanliness is
supplied, since a test runs inside a working tree being edited.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pymupdf
import pytest

from core.extract_vision import recording_extractor
from tests.qualification.synthetic import document_spec, loss_run_pdf, rows, truth_file
from tests.test_extract_vision import StandInModel
from tests.test_packet_adversarial import _rasterise
from tests.test_packet_claim_series import LARGE_HEADERS
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import SetupError
from tools.qualification import __main__ as cli
from tools.qualification import public, runner
from tools.qualification.runner import Checkout

CLEAN_CHECKOUT = Checkout("c" * 40, True)
INSURED = "Harbor Test Fabrication LLC"
FIRST = rows(4, start=55501000, big=41250)
SECOND = rows(3, start=55502000, big=None)


@pytest.fixture
def private(tmp_path):
    """A corpus of two synthetic loss runs, its manifest and complete truth."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "Harbor client losses.pdf").write_bytes(loss_run_pdf(rows=FIRST))
    (corpus / "second report.pdf").write_bytes(loss_run_pdf(rows=SECOND, policy="GL-200"))
    manifest = manifests.create(corpus, tmp_path / "manifest.json")
    by_name = {Path(e.path).name: e for e in manifest.entries}
    specs = [
        document_spec(FIRST, doc_id=by_name["Harbor client losses.pdf"].id,
                      sha256=by_name["Harbor client losses.pdf"].sha256),
        document_spec(SECOND, doc_id=by_name["second report.pdf"].id,
                      sha256=by_name["second report.pdf"].sha256),
    ]
    truth = truth_file(specs, tmp_path / "truth.json")
    return corpus, tmp_path / "manifest.json", truth, tmp_path / "out", manifest


def _run(private, **kwargs):
    corpus, manifest, truth, out, _m = private
    return runner.run(corpus=corpus, manifest_path=manifest, truth_path=truth, out=out,
                      checkout=kwargs.pop("checkout", CLEAN_CHECKOUT), **kwargs)


def _private_strings(private):
    _corpus, _manifest, _truth, _out, manifest = private
    strings = [INSURED, "Harbor", "second report", str(private[0]),
               *[e.id for e in manifest.entries]]
    strings += [row[0] for row in FIRST + SECOND]
    strings += ["41,250", "41250", "2022-01-10"]
    return strings


def test_an_exact_corpus_scores_perfectly_and_says_nothing_private(private):
    report = _run(private)
    totals = report["totals"]
    claims = totals["overall"]["claims"]
    assert report["categories"]["scored"] == 2
    assert (claims["matched"], claims["missing"], claims["invented"]) == (7, 0, 0)
    assert claims["precision"] == claims["recall"] == 1.0
    assert totals["overall"]["money"]["precision"] == 1.0
    assert report["qualification"] == "measured"
    assert report["commit"] == CLEAN_CHECKOUT.commit
    assert all(r["document"].startswith("doc:") for r in report["per_document"])
    out = private[3]
    written = (out / "qualification.json").read_text() + (out / "qualification.txt").read_text()
    for value in _private_strings(private):
        assert value.casefold() not in written.casefold(), value


def test_uncommitted_code_is_never_scored(private):
    with pytest.raises(SetupError, match="uncommitted"):
        _run(private, checkout=Checkout("d" * 40, False))


def test_private_inputs_and_output_must_live_outside_the_repository(private):
    corpus, manifest, truth, _out, _m = private
    with pytest.raises(SetupError, match="outside the repository"):
        runner.run(corpus=corpus, manifest_path=manifest, truth_path=truth,
                   out=runner.REPO_ROOT / "qualification-out", checkout=CLEAN_CHECKOUT)


def test_a_document_whose_bytes_changed_is_not_scored(private):
    corpus = private[0]
    target = corpus / "second report.pdf"
    target.write_bytes(target.read_bytes() + b"\n% appended\n")
    report = _run(private)
    assert report["categories"] == {"scored": 1, "missing_file": 0, "bytes_changed": 1,
                                    "pipeline_failed": 0}
    assert report["qualification"] == "insufficient"
    assert "documents_not_scored" in report["insufficient_because"]


def test_no_live_model_credentials_are_visible_while_reading(private, monkeypatch):
    import core.pipeline as pipeline

    seen: list[str | None] = []
    real = pipeline.run_pipeline

    def spy(*args, **kwargs):
        seen.append(os.environ.get("GEMINI_API_KEY"))
        assert kwargs.get("use_llm") is False and kwargs.get("use_vision") is False
        return real(*args, **kwargs)

    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    _run(private)
    assert seen == [None, None]
    assert os.environ["GEMINI_API_KEY"] == "not-a-real-key"


def test_a_pipeline_failure_is_a_category_without_its_message(private, monkeypatch):
    import core.pipeline as pipeline

    def explode(*_args, **_kwargs):
        raise RuntimeError("SECRET-PARSER-DETAIL 55501000")

    monkeypatch.setattr(pipeline, "run_pipeline", explode)
    report = _run(private)
    assert report["categories"]["pipeline_failed"] == 2
    text = json.dumps(report)
    assert "SECRET-PARSER-DETAIL" not in text and "RuntimeError" in text


def test_a_leak_stops_the_report_from_being_written(private, monkeypatch):
    monkeypatch.setattr(public, "seal", lambda document_id, salt: document_id)
    with pytest.raises(public.PrivateDataInReport):
        _run(private)
    assert not (private[3] / "qualification.json").exists()


# --------------------------------------------------------------------------
# Scanned pages: replay or unread, never a live call
# --------------------------------------------------------------------------


def _scanned_corpus(tmp_path):
    corpus = tmp_path / "scanned-corpus"
    corpus.mkdir()
    digital = tmp_path / "digital.pdf"
    digital.write_bytes(loss_run_pdf(rows=FIRST))
    _rasterise(digital, corpus / "scan.pdf", dpi=60)
    manifest = manifests.create(corpus, tmp_path / "scanned-manifest.json")
    (entry,) = manifest.entries
    spec = document_spec(FIRST, doc_id=entry.id, sha256=entry.sha256)
    spec["format_family"] = "scanned"
    spec["status"] = "NEEDS_REVIEW"  # not asserted: scanned policy is not under test
    truth = truth_file([spec], tmp_path / "scanned-truth.json")
    return corpus, tmp_path / "scanned-manifest.json", truth


def _transcription():
    """Exactly what the scanned page prints, as the vision prompt asks for it."""
    total = ("TOTAL", "", "", "44,250.00", "0.00", "44,250.00")
    return {
        "headers": list(LARGE_HEADERS),
        "rows": [{"cells": list(r), "kind": "data"} for r in FIRST]
        + [{"cells": list(total), "kind": "total"}],
        "printed_claim_count": None,
        "valuation_date": "12/31/2022",
        "page_label": {"text": "Page 1 of 1", "number": 1, "of": 1, "position": "footer"},
        "report_heading": None,
    }


def test_scanned_pages_without_recordings_are_unread_and_insufficient(tmp_path):
    corpus, manifest, truth = _scanned_corpus(tmp_path)
    report = runner.run(corpus=corpus, manifest_path=manifest, truth_path=truth,
                        out=tmp_path / "out-none", checkout=CLEAN_CHECKOUT,
                        vision_replay=tmp_path / "no-recordings")
    assert report["totals"]["accounting"]["claim_pages_unread"] == 1
    assert report["totals"]["overall"]["claims"]["missing"] == len(FIRST)
    assert report["qualification"] == "insufficient"
    assert "claim_pages_unread" in report["insufficient_because"]


def test_scanned_pages_are_scored_from_synthetic_recordings(tmp_path):
    corpus, manifest, truth = _scanned_corpus(tmp_path)
    recordings = tmp_path / "recordings"
    model = StandInModel(_transcription())
    recording_extractor(recordings, client=model)(corpus / "scan.pdf", [1])
    assert model.calls == 1
    report = runner.run(corpus=corpus, manifest_path=manifest, truth_path=truth,
                        out=tmp_path / "out-replay", checkout=CLEAN_CHECKOUT,
                        vision_replay=recordings)
    claims = report["totals"]["overall"]["claims"]
    assert report["vision"] == "replay"
    assert report["totals"]["accounting"]["claim_pages_unread"] == 0
    assert (claims["matched"], claims["missing"], claims["invented"]) == (len(FIRST), 0, 0)
    assert report["totals"]["overall"]["money"]["incorrect"] == 0


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def test_validate_reports_counts_and_changed_bytes(private, capsys):
    corpus, manifest, truth, _out, _m = private
    assert cli.main(["validate", "--manifest", str(manifest), "--truth", str(truth),
                     "--corpus", str(corpus)]) == 0
    printed = capsys.readouterr().out
    assert "documents=2" in printed and "claim_occurrences=7" in printed
    (corpus / "second report.pdf").write_bytes(b"%PDF-1.4 replaced")
    assert cli.main(["validate", "--manifest", str(manifest), "--truth", str(truth),
                     "--corpus", str(corpus)]) == 1


def test_an_unusable_truth_file_exits_2_without_its_values(private, capsys, tmp_path):
    _corpus, manifest, truth, _out, _m = private
    data = json.loads(truth.read_text())
    data["documents"][0]["claims"][0]["fields"]["paid_total"] = 41250.5
    bad = tmp_path / "bad-truth.json"
    bad.write_text(json.dumps(data))
    assert cli.main(["validate", "--manifest", str(manifest), "--truth", str(bad)]) == 2
    error = capsys.readouterr().err
    assert "41250" not in error and FIRST[0][0] not in error and "Harbor" not in error


def test_run_from_the_command_line_needs_a_clean_checkout(private, monkeypatch, capsys):
    corpus, manifest, truth, out, _m = private
    monkeypatch.setattr(runner, "checkout_state", lambda: Checkout("e" * 40, False))
    code = cli.main(["run", "--corpus", str(corpus), "--manifest", str(manifest),
                     "--truth", str(truth), "--out", str(out)])
    assert code == 2 and "uncommitted" in capsys.readouterr().err
    monkeypatch.setattr(runner, "checkout_state", lambda: CLEAN_CHECKOUT)
    assert cli.main(["run", "--corpus", str(corpus), "--manifest", str(manifest),
                     "--truth", str(truth), "--out", str(out)]) == 0
    assert "precision 100.00%" in capsys.readouterr().out


def test_the_real_checkout_state_names_this_commit():
    state = runner.checkout_state()
    assert len(state.commit) == 40
    assert isinstance(state.clean, bool)


def test_a_scan_helper_really_is_image_only(tmp_path):
    corpus, _manifest, _truth = _scanned_corpus(tmp_path)
    with pymupdf.open(corpus / "scan.pdf") as document:
        assert document[0].get_text().strip() == ""
