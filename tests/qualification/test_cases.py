"""The frozen case interface: build_cases(root) -> list[QualificationCase]."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest

from core.extract_vision import VisionExtraction, parse_vision_response
from tests.qualification.synthetic import document_spec, loss_run_pdf, rows
from tests.test_packet_adversarial import LETTER, _rasterise, _sheet
from tests.test_packet_claim_series import LARGE_CARRIER, LARGE_HEADERS
from tools.qualification.cases import (
    Expectation,
    QualificationCase,
    build_all,
    check_expectation,
    document_truth,
    evaluate_case,
    offline,
    run_case,
)
from tools.qualification.score import QualificationMetrics
from tools.qualification.truth import TruthError

PRINTED = rows(4, start=66601000, big=52000)


def _spec(printed=PRINTED, status="CLEAN"):
    spec = document_spec(printed, status=status)
    del spec["id"], spec["sha256"]
    return spec


def _supported(root: Path, case_id="exact-loss-run") -> QualificationCase:
    pdf = root / f"{case_id}.pdf"
    pdf.write_bytes(loss_run_pdf(rows=PRINTED))
    return QualificationCase(case_id, pdf, document_truth(case_id, pdf, _spec()),
                             Expectation.SUPPORTED)


def _untied_total(root: Path) -> QualificationCase:
    """The carrier's printed total does not add up: a correct reading needs review."""
    document = pymupdf.open()
    wrong = ("TOTAL", "", "", "99,000.00", "0.00", "99,000.00")
    _sheet(document, top=(LARGE_CARRIER, "Named Insured: Untied Test Co", *LETTER),
           rows=PRINTED, total=wrong, bottom=("Page 1 of 1",))
    pdf = root / "untied.pdf"
    document.save(pdf)
    document.close()
    spec = _spec(status="NEEDS_REVIEW")
    spec["printed"]["totals"] = {"paid_total": "99000.00", "reserve_total": "0.00",
                                 "incurred_total": "99000.00"}
    return QualificationCase("untied-total", pdf, document_truth("untied-total", pdf, spec),
                             Expectation.REVIEW)


def test_a_case_truth_is_bound_to_the_bytes_it_was_built_with(tmp_path):
    case = _supported(tmp_path)
    assert case.truth.document_id == case.case_id and len(case.truth.sha256) == 64
    case.pdf.write_bytes(case.pdf.read_bytes() + b"\n% changed\n")
    with pytest.raises(TruthError, match="not the file its truth describes"):
        run_case(case, tmp_path / "work")


def test_a_supported_case_read_exactly_meets_its_expectation(tmp_path):
    outcome = evaluate_case(_supported(tmp_path), tmp_path / "work")
    assert outcome.met, outcome.shortfalls
    assert outcome.metrics.overall.claims.matched == len(PRINTED)
    assert outcome.metrics.status.documents_auto_accepted == 1


def test_a_review_case_is_met_only_when_it_is_not_accepted(tmp_path):
    outcome = evaluate_case(_untied_total(tmp_path), tmp_path / "work")
    assert outcome.met, outcome.shortfalls
    assert outcome.metrics.status.documents_auto_accepted == 0
    # Review is not perfection: the claims are still scored.
    assert outcome.metrics.overall.claims.matched == len(PRINTED)


def test_shortfalls_are_named_by_fixed_categories(tmp_path):
    case = _supported(tmp_path)
    metrics = QualificationMetrics(documents=1)
    metrics.status.documents = 1
    metrics.overall.claims.missing = 1
    metrics.overall.critical.null_as_zero = 1
    metrics.status.false_clean_documents = 1
    metrics.accounting.truth_runs = 1
    assert set(check_expectation(case, metrics)) == {
        "false_clean", "claims_missing", "critical_null_as_zero", "runs_differ",
        "status_differs"}
    review = _untied_total(tmp_path)
    metrics.status.documents_auto_accepted = 1
    assert set(check_expectation(review, metrics)) == {"false_clean",
                                                       "auto_accepted_review_case"}


def test_a_case_declares_itself_consistently(tmp_path):
    case = _supported(tmp_path)
    with pytest.raises(TruthError, match="case id"):
        QualificationCase("Bad Id", case.pdf, case.truth, Expectation.SUPPORTED)
    with pytest.raises(TruthError, match="names another document"):
        QualificationCase("other-case", case.pdf, case.truth, Expectation.SUPPORTED)
    with pytest.raises(TruthError, match="must be NEEDS_REVIEW"):
        QualificationCase(case.case_id, case.pdf, case.truth, Expectation.REVIEW)
    with pytest.raises(TruthError, match="not both"):
        QualificationCase(case.case_id, case.pdf, case.truth, Expectation.SUPPORTED,
                          replay=tmp_path, extractor=lambda path, pages: None)


def test_build_all_gives_each_pack_its_own_directory_and_unique_ids(tmp_path):
    seen: list[Path] = []

    def pack(root: Path) -> list[QualificationCase]:
        seen.append(root)
        return [_supported(root)]

    cases = build_all([pack], tmp_path / "packs")
    assert len(cases) == 1 and seen[0].parent == tmp_path / "packs"
    with pytest.raises(TruthError, match="share an id"):
        build_all([pack, pack], tmp_path / "again")


def _scanned(root: Path) -> Path:
    digital = root / "digital-source.pdf"
    digital.write_bytes(loss_run_pdf(rows=PRINTED))
    return _rasterise(digital, root / "scanned.pdf", dpi=60)


def _payload():
    total = ("TOTAL", "", "", "55,000.00", "0.00", "55,000.00")
    return {
        "headers": list(LARGE_HEADERS),
        "rows": [{"cells": list(r), "kind": "data"} for r in PRINTED]
        + [{"cells": list(total), "kind": "total"}],
        "printed_claim_count": None, "valuation_date": "12/31/2022",
        "page_label": {"text": "Page 1 of 1", "number": 1, "of": 1, "position": "footer"},
        "report_heading": None,
    }


def test_an_offline_extractor_reads_a_scanned_case(tmp_path):
    pdf = _scanned(tmp_path)
    calls: list[list[int]] = []

    def extractor(path, pages, **_kwargs):
        calls.append(list(pages))
        return VisionExtraction(tables=[parse_vision_response(_payload(), p) for p in pages],
                                failures={})

    spec = _spec(status="NEEDS_REVIEW")
    spec["format_family"] = "scanned"
    case = QualificationCase("scanned-offline", pdf, document_truth("scanned-offline", pdf, spec),
                             Expectation.REVIEW, extractor=extractor)
    result = run_case(case, tmp_path / "work")
    assert calls == [[1]]
    assert [c.claim_number for c in result.document.claims] == [r[0] for r in PRINTED]


def test_a_scanned_case_with_no_replay_is_read_as_unread(tmp_path):
    pdf = _scanned(tmp_path)
    spec = _spec(status="NEEDS_REVIEW")
    spec["format_family"] = "scanned"
    case = QualificationCase("scanned-missing", pdf,
                             document_truth("scanned-missing", pdf, spec), Expectation.REVIEW,
                             replay=tmp_path / "no-recordings")
    outcome = evaluate_case(case, tmp_path / "work")
    assert outcome.metrics.accounting.claim_pages_unread == 1
    assert outcome.metrics.overall.claims.missing == len(PRINTED)
    assert outcome.met  # not accepted: its errors are measured, not hidden


def test_offline_hides_live_credentials_and_restores_them(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    with offline():
        assert "GEMINI_API_KEY" not in os.environ
    assert os.environ["GEMINI_API_KEY"] == "not-a-real-key"
