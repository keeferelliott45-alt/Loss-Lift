"""Claim accounting, the JSON export, and corpus labels."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.accounting import claim_accounting
from core.export import JSON_SCHEMA, build_json, to_json_bytes
from core.review import canonical_run_status, canonical_status
from tests.test_packet_adversarial import LETTER, _read, _total
from tests.test_packet_claim_series import LARGE_CARRIER, SMALL_HEADERS, SMALL_RUN, _large_run
from tools.corpus_gate import labels as corpus_labels
from tools.corpus_gate.manifest import Entry, Manifest, SetupError

REPO = Path(__file__).resolve().parents[1]


def _single(tmp_path):
    large = _large_run(6)
    return _read(tmp_path, [
        {"top": (LARGE_CARRIER, "Named Insured: Jane Q. Sentinelle", *LETTER), "rows": large,
         "total": _total(large)},
    ], name="single.pdf")


def _packet(tmp_path):
    large = _large_run(12)
    return _read(tmp_path, [
        {"top": ("Page 1 of 3", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": ("HARBOR CREST SPECIALTY INSURANCE COMPANY", *LETTER),
         "headers": SMALL_HEADERS, "rows": list(SMALL_RUN[:1])},
        {"top": ("Page 2 of 3", LARGE_CARRIER, *LETTER), "rows": large[4:8]},
        {"top": ("Page 3 of 3", LARGE_CARRIER, *LETTER), "rows": large[8:],
         "total": _total(large)},
    ], name="packet.pdf")


# --- claim accounting --------------------------------------------------------

def test_a_single_report_is_accounted_for_as_one_run(tmp_path):
    result = _single(tmp_path)
    [account] = claim_accounting(result.document, result.reconciliation)
    assert account.run_id is None and account.boundary_settled
    assert account.claims_read == 6
    assert account.totals == "ties" and account.refused_rows == 0
    assert account.status is canonical_status(result.reconciliation)


def test_a_packet_accounts_for_every_run_and_every_refusal(tmp_path):
    result = _packet(tmp_path)
    accounts = claim_accounting(result.document, result.reconciliation)
    document = result.document
    assert [a.run_id for a in accounts] == [run.run_id for run in document.runs]
    assert sum(a.claims_read for a in accounts) == len(document.claims)
    assert sum(a.refused_rows for a in accounts) == len(document.refused_claim_rows) > 0
    unsettled = [a for a in accounts if not a.boundary_settled]
    assert unsettled and all(a.reasons for a in unsettled)
    for account in accounts:
        assert account.status is canonical_run_status(result.reconciliation, account.run_id)


# --- JSON export -------------------------------------------------------------

def test_the_json_export_carries_the_canonical_result(tmp_path):
    result = _packet(tmp_path)
    data = json.loads(to_json_bytes(result.document, result.reconciliation))
    assert data["schema"] == JSON_SCHEMA
    assert data["status"]["review_status"] == canonical_status(result.reconciliation).value
    assert set(data["status"]["runs"]) == {run.run_id for run in result.document.runs}
    assert len(data["runs"]) == len(result.document.runs)
    assert len(data["claims"]) == len(result.document.claims)
    assert all(claim["run_id"] for claim in data["claims"])
    assert all("source_page" in claim for claim in data["claims"])
    assert {f["rule_id"] for f in data["findings"]} >= {"R-28"}
    assert all(f["review"] == "open" for f in data["findings"])
    assert len(data["refused_claim_rows"]) == len(result.document.refused_claim_rows)


def test_the_json_export_redacts_claimant_data_everywhere(tmp_path):
    result = _single(tmp_path)
    for claim in result.document.claims:
        claim.claimant_name = "Jane Q. Sentinelle"
        claim.raw_cells["claimant_name"] = "Jane Q. Sentinelle"
    data = json.loads(to_json_bytes(result.document, result.reconciliation, redact=True))
    data["document"].pop("named_insured")      # the account's insured, not claimant data
    assert "Sentinelle" not in json.dumps(data)
    assert data["redacted"] is True
    assert all("claimant_name" not in claim for claim in data["claims"])
    assert all("claimant_name" not in claim["raw_cells"] for claim in data["claims"])


def test_the_json_export_without_a_reconciliation_is_not_trusted(tmp_path):
    result = _single(tmp_path)
    data = build_json(result.document, None)
    assert data["status"]["review_status"] == "NEEDS_REVIEW"
    assert data["status"]["trust"] == "unresolved"


# --- corpus labels -------------------------------------------------------------

def _manifest():
    return Manifest(salt=bytes(32), sha256="c" * 64,
                    entries=(Entry("doc-a", "a.pdf", "a" * 64, 10),
                             Entry("doc-b", "b.pdf", "b" * 64, 10)))


def test_a_labels_template_lists_every_document_with_nothing_known(tmp_path):
    path = tmp_path / "labels.json"
    corpus_labels.write_template(_manifest(), path)
    loaded = corpus_labels.load(path, _manifest())
    assert set(loaded.labels) == {"doc-a", "doc-b"}
    assert all(value is None for labels in loaded.labels.values() for value in labels.values())
    with pytest.raises(SetupError):
        corpus_labels.write_template(_manifest(), path)


@pytest.mark.parametrize("labels, words", [
    ({"doc-z": {"shape": "packet"}}, "does not list"),
    ({"doc-a": {"shape": "booklet"}}, "cannot take"),
    ({"doc-a": {"expected_runs": "2"}}, "cannot take"),
    ({"doc-a": {"final_status": "CLEAN"}}, "unknown label"),   # derived, never labelled
])
def test_labels_are_validated_against_the_manifest_and_vocabulary(labels, words):
    with pytest.raises(SetupError, match=words):
        corpus_labels.validate({"version": 1, "labels": labels}, _manifest())


def test_valid_labels_load():
    loaded = corpus_labels.validate({"version": 1, "labels": {"doc-a": {
        "carrier": "Some Carrier", "source": "scanned", "scan_quality": "poor", "shape": "packet",
        "expected_runs": 3, "printed_totals": True, "claim_count_band": "10-49"}}}, _manifest())
    assert loaded.labels["doc-a"]["expected_runs"] == 3


def test_labels_are_refused_inside_the_repository(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    completed = subprocess.run(
        [sys.executable, "-m", "tools.corpus_gate", "init-labels", "--manifest", str(manifest),
         "--labels", str(REPO / "labels.json"), "--repo", str(REPO)],
        cwd=REPO, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 3
    assert "must live outside the repository" in completed.stderr
    assert not (REPO / "labels.json").exists()
