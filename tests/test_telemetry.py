"""Local telemetry: counts and tokens, never anything the document said."""

from __future__ import annotations

import json

import pytest

from core import telemetry
from core.pipeline import edit_claims, run_pipeline, to_records
from core.review import AUTO_SAFE, NEEDS_REVIEW, UNRESOLVED, trust_class
from core.schema import DocumentStatus, Finding, FindingScope, ReconciliationResult, Severity
from tests.test_packet_adversarial import LETTER, _read, _total
from tests.test_packet_claim_series import LARGE_CARRIER, SMALL_HEADERS, SMALL_RUN, _large_run

SENTINELS = ("SENTINELLE", "Sentinelle", "SNTL", "Jane", "Northfield", "NORTHFIELD", "71004410",
             "1,000.00", "packet.pdf")


@pytest.fixture(autouse=True)
def isolated_telemetry(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("LOSSLIFT_TELEMETRY_PATH", str(path))
    monkeypatch.delenv("LOSSLIFT_TELEMETRY", raising=False)
    return path


def _clean_single(tmp_path):
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


def _no_document_content(text: str) -> None:
    for sentinel in SENTINELS:
        assert sentinel not in text, sentinel


def test_a_processed_document_is_described_by_counts_and_tokens(tmp_path, isolated_telemetry):
    result = _clean_single(tmp_path)
    event = telemetry.processed_event(result, session=telemetry.new_session())
    assert event["event"] == "document_processed"
    assert event["document_class"] == "digital:single"
    assert event["review_status"] == "CLEAN" and event["trust"] == AUTO_SAFE
    assert event["claims"]["claim_count"] == 6 and event["claims"]["r04"] == "tie"
    assert set(event["timings"]) >= {"classify", "digital", "reconcile", "total"}
    assert telemetry.emit(event)
    _no_document_content(isolated_telemetry.read_text())


def test_a_packet_is_measured_run_by_run_without_its_names(tmp_path, isolated_telemetry):
    result = _packet(tmp_path)
    event = telemetry.processed_event(result)
    runs = event["runs"]
    assert runs["packet"] is True and runs["count"] == len(result.document.runs)
    assert runs["unsettled"] >= 1 and len(runs["items"]) == runs["count"]
    assert event["trust"] == UNRESOLVED          # a run boundary is unsettled (R-28)
    assert event["claims"]["refused"] == len(result.document.refused_claim_rows)
    telemetry.emit(event)
    _no_document_content(isolated_telemetry.read_text())


def test_an_event_carrying_document_text_is_refused_not_written(isolated_telemetry):
    event = {"schema": 1, "event": "document_processed", "carrier": "acme"}
    with pytest.raises(telemetry.UnsafeEvent):
        telemetry._check(event)
    assert telemetry.emit(event) is False
    assert not isolated_telemetry.exists()
    assert telemetry.emit({"schema": 1, "event": "exported", "note": "Jane Q. Sentinelle"}) is False


def test_edits_are_recorded_by_field_and_emptiness_only(tmp_path, isolated_telemetry):
    result = _clean_single(tmp_path)
    logged = len(result.document.review_log.entries)
    records = to_records(result.document)
    records[0]["incurred_total"] = "999999.99"
    records[1]["claimant_name"] = "Jane Q. Sentinelle"
    edit_claims(result, records)
    events = telemetry.review_events(result, result.document.review_log.entries[logged:])
    assert {(e["event"], e["field"]) for e in events} >= {
        ("claim_edited", "incurred_total"), ("claim_edited", "claimant_name")}
    telemetry.emit(events)
    text = isolated_telemetry.read_text()
    _no_document_content(text)
    assert "999999" not in text


def test_an_export_is_recorded_with_the_status_it_left_with(tmp_path):
    result = _packet(tmp_path)
    event = telemetry.export_event(result, fmt="xlsx", redacted=True, seconds_since_processed=4.5)
    assert (event["review_status"], event["trust"], event["format"]) \
        == ("NEEDS_REVIEW", UNRESOLVED, "xlsx")


def _finding(rule, category, severity):
    return Finding(rule_id=rule, scope=FindingScope.DOCUMENT, subject="document", condition=rule,
                   category=category, severity=severity, message="m")


@pytest.mark.parametrize("findings, expected", [
    ([], AUTO_SAFE),
    ([_finding("R-13", "underwriting", Severity.WARN)], AUTO_SAFE),
    ([_finding("R-15", "extraction", Severity.WARN)], NEEDS_REVIEW),
    ([_finding("R-04", "financial", Severity.ERROR)], NEEDS_REVIEW),
    ([_finding("R-28", "extraction", Severity.ERROR)], UNRESOLVED),
    ([_finding("R-23", "extraction", Severity.WARN)], UNRESOLVED),
])
def test_three_outcomes(findings, expected):
    status = (DocumentStatus.NEEDS_REVIEW if any(f.severity is Severity.ERROR for f in findings)
              else DocumentStatus.CLEAN)
    assert trust_class(ReconciliationResult(status=status, findings=findings)) == expected
    assert trust_class(ReconciliationResult(status=DocumentStatus.CLEAN),
                       needs_mapping=True) == UNRESOLVED


def test_rates_by_document_class(tmp_path, isolated_telemetry):
    single = _clean_single(tmp_path)
    packet = _packet(tmp_path)
    for result in (single, packet, single):          # the latest event per document counts
        telemetry.emit(telemetry.processed_event(result))
    summary = telemetry.summarize(isolated_telemetry)
    assert summary["overall"] == {"documents": 2, "auto_safe": 0.5, "needs_review": 0.0,
                                  "unresolved": 0.5}
    assert summary["by_class"]["digital:packet"]["unresolved"] == 1.0
    assert summary["by_class"]["digital:single"]["auto_safe"] == 1.0


def test_telemetry_can_be_turned_off(tmp_path, isolated_telemetry, monkeypatch):
    monkeypatch.setenv("LOSSLIFT_TELEMETRY", "off")
    assert telemetry.emit(telemetry.processed_event(_clean_single(tmp_path))) is False
    assert not isolated_telemetry.exists()


def test_an_unwritable_file_never_stops_processing(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("LOSSLIFT_TELEMETRY_PATH", str(blocker / "events.jsonl"))
    assert telemetry.emit(telemetry.processed_event(_clean_single(tmp_path))) is False


def test_every_event_line_is_json(tmp_path, isolated_telemetry):
    telemetry.emit(telemetry.processed_event(_clean_single(tmp_path)))
    for line in isolated_telemetry.read_text().splitlines():
        assert json.loads(line)["schema"] == telemetry.SCHEMA
