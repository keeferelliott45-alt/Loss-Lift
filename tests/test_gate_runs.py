"""The corpus gate measures the run architecture, and only real changes in it.

A packet's logical runs, their statuses, the claims each holds, refused and
unplaced rows and the canonical trust status are all measured, so a change to
any of them fails the gate. A revision that predates logical runs, one that
records ``runs=[]``, and a single report all measure alike, so the change of
representation alone is never a behavioural change. Synthetic documents only.
"""

from __future__ import annotations

import itertools
import sys
from types import SimpleNamespace

import pytest

from core.review import canonical_run_status, canonical_status
from core.schema import DocumentStatus, Finding, FindingScope, ReconciliationResult, Severity
from tools.corpus_gate import collect, compare as gate, seal
from tools.corpus_gate.manifest import Entry, Manifest
from tests.test_packet_adversarial import LETTER, _read, _total
from tests.test_packet_claim_series import (
    LARGE_CARRIER,
    SMALL_HEADERS,
    SMALL_RUN,
    _large_run,
)

SALT = bytes(range(32))
DIGEST = collect.Digest(SALT)
SHA = "a" * 64


def _single(tmp_path):
    large = _large_run(6)
    return _read(tmp_path, [
        {"top": (LARGE_CARRIER, *LETTER), "rows": large, "total": _total(large)},
    ], name="single.pdf")


def _packet_with_a_refused_row(tmp_path):
    """Report A "Page 1 of 3", another carrier's unnumbered page, A resumes:
    the interleaved page's claim is refused and named (adversarial r3-4)."""
    large = _large_run(12)
    return _read(tmp_path, [
        {"top": ("Page 1 of 3", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": ("HARBOR CREST SPECIALTY INSURANCE COMPANY", *LETTER),
         "headers": SMALL_HEADERS, "rows": list(SMALL_RUN[:1])},
        {"top": ("Page 2 of 3", LARGE_CARRIER, *LETTER), "rows": large[4:8]},
        {"top": ("Page 3 of 3", LARGE_CARRIER, *LETTER), "rows": large[8:],
         "total": _total(large)},
    ], name="packet.pdf")


def _as_older_revision(result):
    """The same result as a revision without logical runs, refused rows or
    ``core.review.canonical_status`` would hand the collector."""
    fields = {name: getattr(result.document, name) for name in type(result.document).model_fields
              if name not in ("runs", "refused_claim_rows")}
    document = SimpleNamespace(**fields)
    reconciliation = SimpleNamespace(status=result.reconciliation.status,
                                     findings=result.reconciliation.findings)
    return SimpleNamespace(document=document, reconciliation=reconciliation,
                           warnings=result.warnings, needs_mapping=result.needs_mapping)


@pytest.fixture
def without_policy_function(monkeypatch):
    """Measure as a revision that predates ``core.review``'s policy functions."""
    monkeypatch.setitem(sys.modules, "core.review", None)


def test_a_single_report_measures_as_one_run_whatever_the_representation(
        tmp_path, without_policy_function):
    result = _single(tmp_path)
    old = collect.measure(_as_older_revision(result), DIGEST)
    sys.modules.pop("core.review")
    new = collect.measure(result, DIGEST)
    for group in ("runs", "refused", "review_status", "findings"):
        assert old[group] == new[group], group
    assert new["runs"] == {"count": 1, "packet": False, "unsettled": 0, "incomplete": 0,
                           "items": {}}


def test_a_packet_is_measured_run_by_run(tmp_path):
    result = _packet_with_a_refused_row(tmp_path)
    runs = collect.measure(result, DIGEST)["runs"]
    assert runs["packet"] is True and runs["count"] == len(result.document.runs) >= 2
    assert runs["unsettled"] == sum(run.ambiguous for run in result.document.runs) >= 1
    for index, run in enumerate(result.document.runs, start=1):
        item = runs["items"][str(index)]
        assert item["run_id"] == run.run_id
        assert item["pages"] == run.pages
        assert item["claims"] == len(result.document.run_claims(run))
        assert item["status"] == canonical_run_status(result.reconciliation, run.run_id).value
        assert item["ambiguous"] is run.ambiguous


def test_a_refused_claim_candidate_is_measured(tmp_path):
    result = _packet_with_a_refused_row(tmp_path)
    refused = result.document.refused_claim_rows
    assert refused, "the fixture must refuse a claim-like row"
    metrics = collect.measure(result, DIGEST)
    assert metrics["refused"]["count"] == len(refused)
    assert metrics["refused"]["by_page"] == {str(refused[0].page): len(refused)}
    assert sum(item["refused"] for item in metrics["runs"]["items"].values()) == len(refused)


def test_unplaced_rows_are_measured_per_run(tmp_path):
    result = _packet_with_a_refused_row(tmp_path)
    from core.schema import UnplacedRow

    document = result.document
    extra = document.unplaced_rows[:]
    document.unplaced_rows = extra + [UnplacedRow(page=document.runs[-1].pages[0], row=3,
                                                  amounts={"incurred_total": "12.00"})]
    items = collect.measure(result, DIGEST)["runs"]["items"]
    assert sum(item["unplaced"] for item in items.values()) == len(extra) + 1


def test_the_same_finding_in_two_runs_has_two_identities():
    def finding(run_id):
        return Finding(rule_id="R-04", scope=FindingScope.DOCUMENT, subject="document",
                       condition="incurred_total", category="financial", field="incurred_total",
                       severity=Severity.ERROR, message="total", run_id=run_id)

    one = collect._findings(SimpleNamespace(findings=[finding("run-1")]), DIGEST)
    two = collect._findings(SimpleNamespace(findings=[finding("run-2")]), DIGEST)
    assert one["identity"] != two["identity"]


def _flag(rule, category, severity, run_id=None, n=0):
    return Finding(rule_id=rule, scope=FindingScope.DOCUMENT, subject="document",
                   condition=f"c{n}", category=category, severity=severity,
                   message="m", run_id=run_id)


KINDS = [("R-15", "extraction", Severity.WARN), ("R-04", "financial", Severity.ERROR),
         ("R-13", "underwriting", Severity.WARN), ("R-14", "underwriting", Severity.INFO),
         ("R-19", "underwriting", Severity.WARN)]


@pytest.mark.parametrize("combo, run_status, needs_mapping", [
    (combo, run_status, needs_mapping)
    for size in range(0, 3) for combo in itertools.combinations(KINDS, size)
    for run_status in ({}, {"run-1": DocumentStatus.CLEAN},
                       {"run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.NEEDS_REVIEW})
    for needs_mapping in (False, True)
])
def test_the_collectors_fallback_policy_is_the_canonical_one(
    monkeypatch, combo, run_status, needs_mapping
):
    """For a revision without ``core.review``'s functions the collector applies
    the same policy itself; the two must never drift apart."""
    findings = [_flag(*kind, run_id="run-1" if n % 2 else None, n=n) for n, kind in enumerate(combo)]
    engine = (DocumentStatus.NEEDS_REVIEW if any(f.severity is Severity.ERROR for f in findings)
              else DocumentStatus.CLEAN)
    result = ReconciliationResult(status=engine, findings=findings, run_status=run_status)
    expected_document = canonical_status(result, needs_mapping=needs_mapping)
    expected_runs = {run: canonical_run_status(result, run, needs_mapping=needs_mapping)
                     for run in ("run-1", "run-2")}
    assert {run: collect._canonical_run_status(result, run, needs_mapping=needs_mapping)
            for run in expected_runs} == expected_runs
    monkeypatch.setitem(sys.modules, "core.review", None)
    holder = SimpleNamespace(needs_mapping=needs_mapping)
    assert collect._canonical_status(holder, result) == expected_document.value
    for run, expected in expected_runs.items():
        assert collect._canonical_run_status(
            result, run, needs_mapping=needs_mapping) == expected.value


def _unmapped(result):
    """The same result as a revision whose column mapping is still to confirm."""
    return SimpleNamespace(document=result.document, reconciliation=result.reconciliation,
                           warnings=result.warnings, needs_mapping=True)


def test_no_run_of_a_packet_awaiting_its_mapping_is_measured_clean(tmp_path):
    """Codex P2 on 95e445c: run statuses take the document's mapping state."""
    result = _packet_with_a_refused_row(tmp_path)
    mapped = collect.measure(result, DIGEST)
    assert "CLEAN" in {item["status"] for item in mapped["runs"]["items"].values()}
    unmapped = collect.measure(_unmapped(result), DIGEST)
    assert unmapped["review_status"] == "NEEDS_REVIEW"
    assert {item["status"] for item in unmapped["runs"]["items"].values()} == {"NEEDS_REVIEW"}


def test_a_revision_whose_run_policy_predates_the_mapping_input(tmp_path, monkeypatch):
    """A revision with ``canonical_run_status(reconciliation, run_id)`` only is
    measured under the same rule: an unmapped document has no clean run."""
    import core.review as review

    result = _packet_with_a_refused_row(tmp_path)
    original = review.canonical_run_status
    monkeypatch.setattr(review, "canonical_run_status",
                        lambda reconciliation, run_id: original(reconciliation, run_id))
    unmapped = collect.measure(_unmapped(result), DIGEST)
    assert {item["status"] for item in unmapped["runs"]["items"].values()} == {"NEEDS_REVIEW"}
    mapped = collect.measure(result, DIGEST)
    assert "CLEAN" in {item["status"] for item in mapped["runs"]["items"].values()}


def _manifest():
    return Manifest(salt=SALT, entries=(Entry("doc-a", "a.pdf", SHA, 10),), sha256="c" * 64)


def _outcome(before, after):
    runs = []
    for label, metrics in (("baseline", before), ("candidate", after)):
        run = gate.RevisionRun(label, ("1" if label == "baseline" else "2") * 40)
        run.records["doc-a"] = {"kind": "document", "id": "doc-a", "ok": True, "metrics": metrics}
        run.complete = True
        runs.append(run)
    return gate.compare(_manifest(), *runs)


def test_a_claim_moving_between_runs_fails_the_gate_and_says_where(tmp_path):
    before = collect.measure(_packet_with_a_refused_row(tmp_path), DIGEST)
    after = collect.measure(_packet_with_a_refused_row(tmp_path), DIGEST)
    assert _outcome(before, after).exit_code == gate.EXIT_PASS
    after["runs"]["items"]["1"]["claims"] -= 1
    after["runs"]["items"]["2"]["claims"] += 1
    outcome = _outcome(before, after)
    assert outcome.exit_code == gate.EXIT_CHANGED
    assert {seal.schema_path(change.path) for change in outcome.changes} \
        == {"runs.items.*.claims"}


def test_a_trust_status_change_is_critical_and_published():
    base = {"review_status": "CLEAN"}
    outcome = _outcome(base, {"review_status": "NEEDS_REVIEW"})
    assert [change.critical for change in outcome.changes] == [True]
    published = seal.public_result(outcome, _manifest(), 1, 0)
    seal.validate_public(published)
    assert published["documents"]["doc-a"]["review_status"] \
        == {"baseline": "CLEAN", "candidate": "NEEDS_REVIEW"}
    assert "review_status" in seal.render_public(published)


def test_the_collectors_unaccounted_rules_are_the_canonical_ones():
    from core.review import UNACCOUNTED_RULES
    assert collect._UNACCOUNTED_RULES == UNACCOUNTED_RULES
