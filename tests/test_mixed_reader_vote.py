"""One claim-number vote per established report, whichever reader read each page.

A report read partly off a text layer and partly off a scan kept one vote per
reader. The scanned page's rows then voted on themselves alone, and a dated
cause code printed under a claim -- a shape no other row shares -- was
accepted as a claim. Where the planner has established that the pages are one
report (a settled run, or a single report whose every page prints its own
numbering, none of them blind), the two readers' identifiers now form one
vote. Claim assembly, read methods, raw cells, warnings, refused rows and the
vision confidence cap stay per reader. Nothing is pooled across reports, or
where the boundary is unsure: the unnumbered case remains a known gap
(``test_packet_adversarial.test_d1``, strict xfail).

Synthetic PDFs and an offline vision stand-in only.
"""

from __future__ import annotations

from core.extract_vision import VisionExtraction
from core.pipeline import run_pipeline
from core.schema import DocumentStatus, SourceMethod
from tests.test_packet_claim_series import (
    LARGE_CARRIER,
    LARGE_HEADERS,
    SMALL_CARRIER,
    SMALL_HEADERS,
    SMALL_RUN,
    _large_run,
)
from tests.test_packet_p1_regressions import (
    DAMAGED_CLAIM,
    DATED_CODE,
    _numbers,
    _payload,
    _scanned,
    _vision_reads,
)


def _read(tmp_path, pages, payloads, rasterise):
    pdf = _scanned(tmp_path, pages, rasterise=rasterise)
    return run_pipeline(pdf, use_vision=True, vision_extractor=_vision_reads(payloads),
                        profiles_dir=tmp_path / "profiles")


def _named(result, identifier):
    refused = any(row.identifier == identifier for row in result.document.refused_claim_rows)
    unplaced = any(identifier in str(row) for row in result.document.unplaced_rows)
    found = any(identifier in f.message for f in result.reconciliation.findings)
    return refused or unplaced or found


def test_a_scanned_last_page_of_a_numbered_report_votes_with_its_first(tmp_path):
    large = _large_run(8)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], {"marker": "Page 2 of 2"}),
    ], {2: _payload(LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], "Page 2 of 2",
                    position="header", heading=LARGE_CARRIER)}, rasterise={2})
    assert result.document.scanned_pages == [2]
    assert DATED_CODE[0] not in _numbers(result)
    assert _numbers(result) == [row[0] for row in large]
    # The damaged claim is refused by the shared vote, never lost.
    assert _named(result, DAMAGED_CLAIM[0])
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_digital_last_page_of_a_scanned_report_votes_with_its_first(tmp_path):
    large = _large_run(8)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], {"marker": "Page 2 of 2"}),
    ], {1: _payload(LARGE_HEADERS, large, "Page 1 of 2", position="header",
                    heading=LARGE_CARRIER)}, rasterise={1})
    assert result.document.scanned_pages == [1]
    assert DATED_CODE[0] not in _numbers(result)
    assert _numbers(result) == [row[0] for row in large]


def test_vision_claims_keep_their_method_and_confidence_cap(tmp_path):
    large = _large_run(8)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large[:5], {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, large[5:], {"marker": "Page 2 of 2"}),
    ], {2: _payload(LARGE_HEADERS, large[5:], "Page 2 of 2", position="header",
                    heading=LARGE_CARRIER)}, rasterise={2})
    assert _numbers(result) == [row[0] for row in large]
    scanned = [c for c in result.document.claims if c.source_page == 2]
    assert scanned and all(c.source_method is SourceMethod.VISION for c in scanned)
    assert all(c.confidence <= 0.85 for c in scanned)
    assert all(c.raw_cells.get("claim_number") for c in scanned)


def test_a_small_scanned_report_beside_a_larger_digital_one_keeps_its_claims(tmp_path):
    small, large = SMALL_RUN[:2], _large_run(8)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 1"}),
        (SMALL_CARRIER, SMALL_HEADERS, small, {"marker": "Page 1 of 1"}),
    ], {2: _payload(SMALL_HEADERS, small, "Page 1 of 1", heading=SMALL_CARRIER)},
        rasterise={2})
    assert _numbers(result) == [row[0] for row in (*large, *small)]
    assert result.document.unplaced_rows == []


def test_distinct_carrier_series_are_never_pooled(tmp_path):
    small, large = SMALL_RUN[:1], _large_run(8)
    result = _read(tmp_path, [
        (SMALL_CARRIER, SMALL_HEADERS, small, {"marker": "Page 1 of 1"}),
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 1"}),
    ], {1: _payload(SMALL_HEADERS, small, "Page 1 of 1", heading=SMALL_CARRIER)},
        rasterise={1})
    assert _numbers(result) == [row[0] for row in (*small, *large)]


def test_an_unnumbered_scanned_page_is_not_pooled_and_stays_under_review(tmp_path):
    """The scanned page prints no numbering: nothing establishes it belongs to
    the report, so no shared vote is taken, and the result needs review."""
    large = _large_run(8)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], {}),
    ], {2: _payload(LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], heading=LARGE_CARRIER)},
        rasterise={2})
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_failed_scanned_page_changes_nothing_else(tmp_path):
    large = _large_run(8)
    pdf = _scanned(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], {"marker": "Page 2 of 2"}),
    ], rasterise={2})

    def failing(path, pages, **_kwargs):
        return VisionExtraction(tables=[], failures={p: "replay missing" for p in pages})

    result = run_pipeline(pdf, use_vision=True, vision_extractor=failing,
                          profiles_dir=tmp_path / "profiles")
    assert _numbers(result) == [row[0] for row in large]
    assert 2 in result.document.failed_pages or 2 in result.document.unresolved_pages
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
