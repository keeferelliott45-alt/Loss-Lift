"""The mixed/accounting pack: well-formed cases whose truth and replay match the page.

These tests check the pack, not LossLift. How LossLift scores on it is
measured with ``tools.qualification.cases.evaluate_case`` and reported;
a disagreement never changes the truth here.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from tests.qualification.packs.mixed_accounting import build_cases
from tests.qualification.packs.mixed_accounting.pages import Page, Row, transcription
from tools.qualification.cases import Expectation, sha256_file
from tools.qualification.truth import CRITICAL_FIELDS, LabelState

EXPECTED_IDS = {
    "bounded-digital-then-scan", "bounded-scan-then-digital", "two-series-digital-packet",
    "mixed-series-packet", "policy-change-boundary", "cover-page-then-report",
    "foreign-page-bridged", "missing-replay", "same-number-two-carriers",
    "run-counts-packet", "section-counts-only", "printed-count-mismatch",
}


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    return build_cases(tmp_path_factory.mktemp("mixed-accounting"))


def _pages_text(case) -> list[str]:
    with pymupdf.open(case.pdf) as document:
        return [page.get_text() for page in document]


def test_the_pack_has_its_twelve_cases(cases):
    assert {case.case_id for case in cases} == EXPECTED_IDS


def test_each_truth_is_complete_and_bound_to_its_file(cases):
    for case in cases:
        truth = case.truth
        assert truth.sha256 == sha256_file(case.pdf), case.case_id
        assert truth.page_count == len(_pages_text(case))
        for claim in truth.claims:
            assert set(CRITICAL_FIELDS) <= set(claim.fields)
            assert truth.pages[claim.anchor.page - 1].role == "claims"


def test_digital_pages_print_every_claim_the_truth_anchors_there(cases):
    for case in cases:
        texts = _pages_text(case)
        for claim in case.truth.claims:
            text = texts[claim.anchor.page - 1]
            if text.strip():  # a scanned page has no text layer to check against
                assert claim.claim_number in text, (case.case_id, claim.position)


def test_scanned_pages_have_no_text_layer_and_digital_pages_do(cases):
    families = {case.case_id: case.truth.format_family for case in cases}
    for case in cases:
        texts = _pages_text(case)
        if families[case.case_id] == "digital":
            assert all(text.strip() for text in texts), case.case_id
        else:
            assert any(not text.strip() for text in texts), case.case_id


def test_runs_cover_their_pages_and_printed_counts_are_placed_as_printed(cases):
    by_id = {case.case_id: case.truth for case in cases}
    packet = by_id["run-counts-packet"]
    assert [run.printed.claim_count.value for run in packet.runs] == [Decimal(4), Decimal(2)]
    assert packet.printed.claim_count.state is LabelState.ABSENT
    sections = by_id["section-counts-only"]
    assert [s.printed.claim_count.value for s in sections.sections] == [Decimal(3), Decimal(2)]
    assert sections.printed.claim_count.state is LabelState.ABSENT
    mismatch = by_id["printed-count-mismatch"]
    assert mismatch.printed.claim_count.value == Decimal(6) and len(mismatch.claims) == 5
    cover = by_id["cover-page-then-report"]
    assert cover.pages[0].role == "not_loss_run"


def test_one_claim_number_printed_by_two_carriers_is_two_occurrences(cases):
    truth = next(c.truth for c in cases if c.case_id == "same-number-two-carriers")
    repeated = [c for c in truth.claims if c.claim_number == "5500103"]
    assert [c.anchor.page for c in repeated] == [1, 2]


def test_review_cases_are_declared_consistently(cases):
    review = {c.case_id for c in cases if c.expectation is Expectation.REVIEW}
    assert review == {"missing-replay", "section-counts-only", "printed-count-mismatch"}
    for case in cases:
        assert (case.truth.status == "NEEDS_REVIEW") == (case.case_id in review)


def test_scanned_cases_read_offline_or_from_an_empty_replay(cases):
    for case in cases:
        if case.truth.format_family == "digital":
            assert case.extractor is None and case.replay is None
    missing = next(c for c in cases if c.case_id == "missing-replay")
    assert missing.extractor is None and missing.replay is not None
    assert not missing.replay.exists() or not any(missing.replay.iterdir())


def test_a_transcription_says_only_what_the_page_prints():
    rows = (Row(("AG-1", "01/13/2024", "OPEN", "10.00", "0.00", "10.00"), "AG-1"),)
    bare = transcription(Page(("LOSS RUN REPORT",), rows))
    assert bare["printed_claim_count"] is None
    assert bare["page_label"] is None and bare["valuation_date"] is None
    assert bare["report_heading"] is None
    full = transcription(Page(("ASHGROVE MUTUAL INSURANCE COMPANY", "Valuation Date: 12/31/2024"),
                              rows, ("TOTAL", "", "", "10.00", "0.00", "10.00"),
                              after=("Total Claims: 1",), footer=("Page 1 of 1",)))
    assert full["printed_claim_count"] == 1
    assert full["page_label"]["number"] == 1 and full["page_label"]["of"] == 1
    assert full["valuation_date"] == "12/31/2024"
    assert full["rows"][-1]["kind"] == "total"
    section = transcription(Page(("X",), rows, after=("Claim Count = 1",)))
    assert section["printed_claim_count"] is None  # a section count is not the report's


def test_the_offline_reader_transcribes_each_scanned_page_as_built(cases):
    case = next(c for c in cases if c.case_id == "bounded-digital-then-scan")
    extraction = case.extractor(case.pdf, [2])
    (table,) = extraction.tables
    printed = [c.claim_number for c in case.truth.claims if c.anchor.page == 2]
    assert [row.cells[0] for row in table.rows] == printed
    assert not extraction.failures


def test_a_rebuild_prints_the_same_content_and_truth(cases, tmp_path):
    again = {case.case_id: case for case in build_cases(tmp_path)}
    for case in cases:
        other = again[case.case_id]
        assert _pages_text(case) == _pages_text(other), case.case_id
        assert case.truth.claims == other.truth.claims
        assert case.truth.runs == other.truth.runs
