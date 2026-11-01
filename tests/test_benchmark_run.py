"""The benchmark reports the canonical status, names what blocks it, and never
reports an unmeasured accuracy as perfect or writes a finding's text."""
from __future__ import annotations

import csv

from benchmark import run as bench
from core.schema import (
    DocumentStatus,
    Finding,
    FindingCategory,
    FindingScope,
    ReconciliationResult,
    Severity,
)

SECRET = "Claimant JANE EXAMPLE slipped on the loading dock"


def _finding(rule: str, severity: Severity, category: FindingCategory) -> Finding:
    return Finding(rule_id=rule, severity=severity, message=SECRET,
                   scope=FindingScope.DOCUMENT, category=category, subject="document")


def _row() -> dict[str, object]:
    return {name: "" for name in bench.RESULT_FIELDS}


def test_an_extraction_warning_reads_needs_review_though_the_engine_says_clean():
    reconciliation = ReconciliationResult(
        status=DocumentStatus.CLEAN,
        findings=[
            _finding("R-15", Severity.WARN, FindingCategory.EXTRACTION),
            _finding("R-15", Severity.WARN, FindingCategory.EXTRACTION),
            _finding("R-18", Severity.WARN, FindingCategory.UNDERWRITING),
        ],
    )
    row = _row()
    bench.describe_status(row, reconciliation, needs_mapping=False)
    assert row["engine_status"] == "CLEAN"
    assert row["reconciliation_status"] == "NEEDS_REVIEW"
    # R-18 is an underwriting observation and blocks nothing.
    assert row["blocking_rules"] == "R-15x2"


def test_an_unclean_run_is_named_among_the_blockers():
    reconciliation = ReconciliationResult(
        status=DocumentStatus.CLEAN,
        run_status={"run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.NEEDS_REVIEW},
    )
    row = _row()
    bench.describe_status(row, reconciliation, needs_mapping=False)
    assert row["reconciliation_status"] == "NEEDS_REVIEW"
    assert row["blocking_rules"] == "run:run-2x1"


def test_a_clean_document_has_no_blockers_and_mapping_still_wins():
    reconciliation = ReconciliationResult(
        status=DocumentStatus.CLEAN,
        findings=[_finding("R-18", Severity.WARN, FindingCategory.UNDERWRITING)],
    )
    row = _row()
    bench.describe_status(row, reconciliation, needs_mapping=False)
    assert (row["reconciliation_status"], row["blocking_rules"]) == ("CLEAN", "")
    bench.describe_status(row, reconciliation, needs_mapping=True)
    assert row["reconciliation_status"] == "NEEDS_MAPPING"


def test_no_finding_text_reaches_the_row():
    reconciliation = ReconciliationResult(
        status=DocumentStatus.NEEDS_REVIEW,
        findings=[_finding("R-01", Severity.ERROR, FindingCategory.FINANCIAL)],
    )
    row = _row()
    bench.describe_status(row, reconciliation, needs_mapping=False)
    assert all(SECRET not in str(value) for value in row.values())


def _scored(**money) -> dict:
    return {"document": "doc:x", "category": "scored", "qualified_truth": True,
            "metrics": {"overall": {"money": money},
                        "status": {"false_clean_documents": 0}}}


def test_truth_fills_money_accuracy_and_false_clean():
    row = _row()
    bench.apply_truth(row, _scored(correct=99, incorrect=1, missing=0,
                                   precision=0.99, recall=0.99))
    assert row["money_fields_exact"] == 99
    assert row["money_fields_expected"] == 100
    assert (row["money_precision"], row["money_recall"]) == (0.99, 0.99)
    assert row["false_clean"] == "no"
    assert row["truth_source"] == "qualification"


def test_an_empty_rate_is_blank_never_perfect():
    row = _row()
    bench.apply_truth(row, _scored(correct=0, precision=None, recall=None))
    assert (row["money_precision"], row["money_recall"]) == ("", "")


def test_unqualified_or_unscored_records_are_ignored():
    for record in ({"document": "doc:x", "category": "missing_file", "qualified_truth": True},
                   {**_scored(correct=5, precision=1.0, recall=1.0), "qualified_truth": False}):
        row = _row()
        bench.apply_truth(row, record)
        assert row["money_recall"] == "" and row["truth_source"] == ""


def test_an_older_results_file_is_widened_without_losing_rows(tmp_path):
    path = tmp_path / "results.csv"
    old_fields = bench.RESULT_FIELDS[:bench.RESULT_FIELDS.index("engine_status")]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({**{f: "" for f in old_fields}, "doc_id": "old", "label": "x"})
    new = {**_row(), "doc_id": "new", "blocking_rules": "R-21x1"}
    bench.append_rows(path, [new])
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == bench.RESULT_FIELDS
        rows = list(reader)
    assert [r["doc_id"] for r in rows] == ["old", "new"]
    assert rows[0]["blocking_rules"] == "" and rows[1]["blocking_rules"] == "R-21x1"


def test_qualification_records_are_found_by_file_bytes_not_name(tmp_path, monkeypatch):
    import json

    from tools.corpus_gate import manifest as manifests
    from tools.qualification import public

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "carrier_a.pdf").write_bytes(b"%PDF-1.4 synthetic a")
    (docs / "carrier_b.pdf").write_bytes(b"%PDF-1.4 synthetic b")
    gate = manifests.create(docs, tmp_path / "manifest.json")
    entry_a = next(e for e in gate.entries if e.path == "carrier_a.pdf")
    record = _scored(correct=10, precision=1.0, recall=1.0)
    record["document"] = public.seal(entry_a.id, gate.salt)
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps({"records": [record]}))
    monkeypatch.setattr(bench, "load_manifest",
                        lambda: [{"doc_id": "carrier_a"}, {"doc_id": "carrier_b"},
                                 {"doc_id": "not_here"}])

    found = bench.load_qualification(report, tmp_path / "manifest.json", docs)
    assert set(found) == {"carrier_a"}
    assert found["carrier_a"]["metrics"]["overall"]["money"]["correct"] == 10
