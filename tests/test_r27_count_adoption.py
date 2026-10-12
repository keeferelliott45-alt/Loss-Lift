"""R-27: the scanned path's claim count is adopted only on row evidence.

A clean scanned report used to read NEEDS_REVIEW no matter what, because the
vision model's claim count was never adopted (it is a bare integer with no
wording to scope it). Adoption is now safe exactly when the rows corroborate
the count: rows were parsed from a counted scanned page, the parsed count
equals the model's stated count, and the page accounting closes. Anything
else -- a disagreeing count, rows never read, accounting that does not close,
a text-layer count standing beside the model's -- stays NEEDS_REVIEW with
both values visible.

Adoption is recorded, never silent: an R-27 finding of severity INFO and
underwriting category, an observation a reviewer can dismiss and telemetry
can count, with provenance vision. Facts are untouched: printed_claim_count
stays None, statuses still come only from core.review, and no measured field
is renamed.

Everything here is synthetic and in-memory: no PDF, no corpus, no model call.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.reconcile import reconcile
from core.review import canonical_status
from core.schema import (
    Claim,
    DocumentStatus,
    FindingCategory,
    LogicalRun,
    LossRunDocument,
    Severity,
    SourceMethod,
    assign_row_ids,
)

A_DATE = date(2024, 3, 12)


def _claim(number, page=1, row=1, method=SourceMethod.VISION):
    return Claim(
        claim_number=number,
        source_page=page,
        source_row=row,
        source_method=method,
        date_of_loss=A_DATE,
        paid_total=Decimal("100.00"),
        reserve_total=Decimal("50.00"),
        incurred_total=Decimal("150.00"),
    )


def _document(**kw) -> LossRunDocument:
    kw.setdefault("source_filename", "synthetic.pdf")
    kw.setdefault("file_sha256", "synthetic")
    kw.setdefault("valuation_date", date(2024, 12, 31))
    assign_row_ids(kw.setdefault("claims", []))
    return LossRunDocument(**kw)


def _adopted(result):
    return [
        f for f in result.findings
        if f.rule_id == "R-27" and f.condition == "claim-count-adopted-vision"
    ]


def _r27(result):
    return [f for f in result.findings if f.rule_id == "R-27"]


# --------------------------------------------------------------------------
# Adoption: the rows corroborate the model's count
# --------------------------------------------------------------------------


def test_rows_corroborating_the_model_count_read_clean():
    """A clean single scanned report: rows parsed, counts equal, accounting
    closes. The run reads CLEAN and the adoption is recorded, not silent."""
    doc = _document(
        scanned_pages=[1],
        printed_count_evidence=[{"page": 1, "count": 3}],
        rows_seen_per_page={1: 3},
        claims=[_claim(f"SYN-{i}") for i in range(1, 4)],
    )
    result = reconcile(doc)
    assert result.status is DocumentStatus.CLEAN
    assert canonical_status(result) is DocumentStatus.CLEAN
    adopted = _adopted(result)
    assert len(adopted) == 1, result.findings
    finding = adopted[0]
    assert finding.severity is Severity.INFO
    assert finding.category is FindingCategory.UNDERWRITING
    assert finding.field == "claim_count"
    assert "adopted" in finding.message
    assert "vision" in finding.message
    assert "3" in finding.message
    assert finding.expected == 3 and finding.actual == 3
    assert not [f for f in result.findings if f.severity is Severity.ERROR]


def test_the_adopted_count_keeps_vision_provenance_and_facts():
    """Adoption changes no fact: the printed count stays unresolved on the
    document, the evidence keeps its pages, and the record names the model."""
    doc = _document(
        scanned_pages=[1],
        printed_count_evidence=[{"page": 1, "count": 2}],
        rows_seen_per_page={1: 2},
        claims=[_claim("SYN-1"), _claim("SYN-2")],
    )
    result = reconcile(doc)
    assert doc.printed_claim_count is None
    assert doc.printed_count_evidence == [{"page": 1, "count": 2}]
    adopted = _adopted(result)
    assert len(adopted) == 1
    assert "model" in adopted[0].message


def test_counts_across_several_scanned_pages_adopt_together():
    """A three-page scan whose every page prints the report's count: the rows
    parsed from the pages equal every stated count, so the check applies."""
    doc = _document(
        scanned_pages=[1, 2, 3],
        printed_count_evidence=[
            {"page": 1, "count": 6}, {"page": 2, "count": 6}, {"page": 3, "count": 6},
        ],
        rows_seen_per_page={1: 2, 2: 2, 3: 2},
        claims=[_claim(f"SYN-{i}", page=(i - 1) // 2 + 1, row=(i - 1) % 2 + 1)
                for i in range(1, 7)],
    )
    result = reconcile(doc)
    assert result.status is DocumentStatus.CLEAN
    assert len(_adopted(result)) == 1


# --------------------------------------------------------------------------
# Refusal: the count never stands alone
# --------------------------------------------------------------------------


def test_a_disagreeing_count_stays_needs_review_with_both_values():
    """The model says 5, the rows say 3: NEEDS_REVIEW, and the finding shows
    the model count and the parsed count, not one of them."""
    doc = _document(
        scanned_pages=[1],
        printed_count_evidence=[{"page": 1, "count": 5}],
        rows_seen_per_page={1: 3},
        claims=[_claim(f"SYN-{i}") for i in range(1, 4)],
    )
    result = reconcile(doc)
    assert result.status is DocumentStatus.NEEDS_REVIEW
    assert not _adopted(result)
    finding = _r27(result)[0]
    assert finding.severity is Severity.ERROR
    assert finding.category is FindingCategory.EXTRACTION
    assert "5 on page 1" in finding.message
    assert "3 claim(s) were parsed" in finding.message
    assert "Model count [5] vs parsed 3" in finding.message


def test_a_page_whose_rows_were_not_read_is_never_waved_through():
    """The vision reader answered with a count and no rows: nothing was read,
    so a count -- 3 or 0 alike -- verifies nothing."""
    for count in (3, 0):
        doc = _document(
            scanned_pages=[1],
            printed_count_evidence=[{"page": 1, "count": count}],
            rows_seen_per_page={1: 0},
            claims=[],
        )
        result = reconcile(doc)
        assert result.status is DocumentStatus.NEEDS_REVIEW, count
        assert not _adopted(result), count
        finding = _r27(result)[0]
        assert finding.severity is Severity.ERROR, count
        assert "no rows were read" in finding.message, finding.message


def test_page_accounting_that_does_not_close_blocks_adoption():
    """Four rows were seen, three recovered: a claim-like row went missing on
    the way to claims, and the count check must not bless that."""
    doc = _document(
        scanned_pages=[1],
        printed_count_evidence=[{"page": 1, "count": 3}],
        rows_seen_per_page={1: 4},
        claims=[_claim(f"SYN-{i}") for i in range(1, 4)],
    )
    result = reconcile(doc)
    assert result.status is DocumentStatus.NEEDS_REVIEW
    assert not _adopted(result)
    finding = _r27(result)[0]
    assert finding.severity is Severity.ERROR
    assert "do not match the claims recovered (3)" in finding.message


def test_vision_counts_that_disagree_with_each_other_adopt_nothing():
    """Two pages state 3 and 2: no single count is the report's, and parsed
    rows equal to either section would still adopt the wrong scope."""
    doc = _document(
        scanned_pages=[1, 2],
        printed_count_evidence=[{"page": 1, "count": 3}, {"page": 2, "count": 2}],
        rows_seen_per_page={1: 3, 2: 2},
        claims=[_claim(f"SYN-{i}", page=1) for i in range(1, 4)]
        + [_claim(f"SYN-{i}", page=2) for i in range(4, 6)],
    )
    result = reconcile(doc)
    assert result.status is DocumentStatus.NEEDS_REVIEW
    assert not _adopted(result)
    assert _r27(result)[0].severity is Severity.ERROR


def test_a_text_layer_count_beside_the_models_adopts_nothing():
    """A mixed document: the digital page's count has wording the vision count
    lacks, neither names the report's scope, and neither is verified."""
    doc = _document(
        scanned_pages=[2],
        printed_count_evidence=[{"page": 1, "count": 2}, {"page": 2, "count": 4}],
        rows_seen_per_page={1: 2, 2: 2},
        claims=[_claim(f"SYN-{i}", page=1, method=SourceMethod.DIGITAL)
                for i in range(1, 3)]
        + [_claim(f"SYN-{i}", page=2) for i in range(3, 5)],
    )
    result = reconcile(doc)
    assert result.status is DocumentStatus.NEEDS_REVIEW
    assert not _adopted(result)
    finding = _r27(result)[0]
    assert finding.severity is Severity.ERROR
    assert "text-layer" in finding.message


# --------------------------------------------------------------------------
# The digital path is unmoved
# --------------------------------------------------------------------------


def test_pure_digital_unresolved_counts_keep_the_old_finding():
    """No scanned pages: the multi-count document reports exactly as before,
    with no vision sentence and no adoption."""
    doc = _document(
        scanned_pages=[],
        printed_count_evidence=[{"page": 1, "count": 3}, {"page": 2, "count": 2}],
        rows_seen_per_page={1: 3, 2: 3},
        claims=[_claim(f"SYN-{i}", method=SourceMethod.DIGITAL) for i in range(1, 7)],
    )
    result = reconcile(doc)
    assert result.status is DocumentStatus.NEEDS_REVIEW
    assert not _adopted(result)
    finding = _r27(result)[0]
    assert finding.severity is Severity.ERROR
    assert "3 on page 1" in finding.message and "2 on page 2" in finding.message
    assert "vision" not in finding.message


# --------------------------------------------------------------------------
# Packets: per-run semantics
# --------------------------------------------------------------------------


def _packet() -> LossRunDocument:
    """Run 1 digital with its own printed count; run 2 scanned, corroborated."""
    runs = [
        LogicalRun(
            run_id="run-1", pages=[1], confidence="printed",
            valuation_date=date(2024, 12, 31),
            printed_claim_count=2,
            printed_count_evidence=[{"page": 1, "count": 2}],
        ),
        LogicalRun(
            run_id="run-2", pages=[2], confidence="printed",
            valuation_date=date(2024, 12, 31),
            printed_count_evidence=[{"page": 2, "count": 3}],
        ),
    ]
    return _document(
        page_count=2,
        processed_pages=[1, 2],
        scanned_pages=[2],
        printed_count_evidence=[{"page": 1, "count": 2}, {"page": 2, "count": 3}],
        rows_seen_per_page={1: 2, 2: 3},
        claims=[_claim(f"D-{i}", page=1, method=SourceMethod.DIGITAL)
                for i in range(1, 3)]
        + [_claim(f"V-{i}", page=2) for i in range(1, 4)],
        runs=runs,
    )


def test_a_mixed_packet_judges_each_run_by_its_own_evidence():
    """The digital run's printed count is checked by R-05 as always; the
    scanned run's count is adopted on its own rows; neither borrows."""
    result = reconcile(_packet())
    adopted = _adopted(result)
    assert len(adopted) == 1
    assert adopted[0].run_id == "run-2"
    assert adopted[0].message.count("3") >= 1
    assert not [f for f in result.findings if f.rule_id == "R-27"
                and f.run_id == "run-1"], result.findings
    assert not [f for f in result.findings if f.rule_id == "R-05"], result.findings


def test_a_mixed_packets_run_statuses_follow_the_same_policy():
    """The digital run and the adopted scanned run both read clean under the
    one status policy; nothing reclassified anything by hand."""
    from core.review import canonical_run_status

    result = reconcile(_packet())
    assert canonical_run_status(result, "run-1") is DocumentStatus.CLEAN
    assert canonical_run_status(result, "run-2") is DocumentStatus.CLEAN


# --------------------------------------------------------------------------
# Through the pipeline, with a stand-in model (replay path, no key, no network)
# --------------------------------------------------------------------------


from core.extract_vision import extract_scanned_pages
from core.pipeline import run_pipeline
from core.telemetry import document_facts
from tests.golden import fixtures as fx
from tests.test_extract_vision import StandInModel, transcription


def _vision(model):
    def extract(path, pages):
        return extract_scanned_pages(path, pages, client=model)

    return extract


def test_a_clean_scanned_report_reads_clean_through_the_pipeline(golden_dir):
    """The fixture the fact base describes: one scanned page, four claims, the
    count the page prints equal to the rows read. The run reads CLEAN, the
    adoption is a recorded finding, provenance stays vision, and no measured
    fact moved."""
    model = StandInModel(transcription(fx.SCANNED))
    result = run_pipeline(
        golden_dir / "scanned.pdf", use_vision=True, vision_extractor=_vision(model)
    )
    document = result.document
    errors = [f for f in result.reconciliation.findings if f.severity is Severity.ERROR]
    assert not errors, errors
    adopted = [
        f for f in result.reconciliation.findings
        if f.rule_id == "R-27" and f.condition == "claim-count-adopted-vision"
    ]
    assert len(adopted) == 1
    assert adopted[0].severity is Severity.INFO
    assert adopted[0].expected == len(fx.SCANNED.claims)
    assert "vision" in adopted[0].message
    assert document.printed_claim_count is None
    assert document.printed_count_evidence == [
        {"page": 1, "count": len(fx.SCANNED.claims)}
    ]
    assert all(claim.source_method is SourceMethod.VISION for claim in document.claims)


def test_telemetry_records_the_adoption_as_vision_sourced(golden_dir):
    """The adoption reaches telemetry through the finding it records: an
    R-27/INFO observation, and no measured field changed name or meaning."""
    model = StandInModel(transcription(fx.SCANNED))
    result = run_pipeline(
        golden_dir / "scanned.pdf", use_vision=True, vision_extractor=_vision(model)
    )
    facts = document_facts(result)
    assert facts["findings"]["by_rule_severity"]["R-27/INFO"] == 1
    assert facts["findings"]["by_rule_category"]["R-27/underwriting"] == 1
    assert facts["claims"]["printed_claim_count"] is None
    assert facts["claims"]["r05"] == "not_printed"