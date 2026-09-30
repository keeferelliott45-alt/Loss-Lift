"""A packet's loss runs as first-class runs.

A physical PDF can bind several insurance loss runs. Each is read, voted on and
reconciled as itself: its pages, the evidence that bounds them, its claims, its
printed totals and count, and its own status. The end-to-end regressions for
the three defects found at 284fe8f are in ``test_packet_p1_regressions``; this
file pins the run model those fixes rest on, per-run reconciliation, the
workbook and review screen, and the invariant that binding reports into a
packet loses none of their claims.

Everything is synthetic (spec section 9): invented carriers, numbers, amounts.
"""

from __future__ import annotations

import io
import random
from decimal import Decimal

import pymupdf
import pytest
from openpyxl import load_workbook

from core.export import build_workbook, resolve_columns, _DOCUMENT_COLUMNS
from core.pipeline import build_claims, build_mapping, rerun_reconciliation, run_pipeline
from core.review import canonical_status
from core.runs import (
    PageEvidence,
    identity_of,
    paginations_in,
    plan_runs,
    runs_overview,
    unsettled_runs,
)
from core.schema import DocumentStatus, RawRow, RawTable, RunConfidence, SourceMethod
from tests.test_packet_claim_series import (
    LARGE_CARRIER,
    LARGE_HEADERS,
    LEFT,
    SMALL_CARRIER,
    SMALL_HEADERS,
    SMALL_RUN,
    _large_run,
    _read,
)
from tests.test_packet_p1_regressions import (
    DAMAGED_CLAIM,
    DATED_CODE,
    THIRD_CARRIER,
    _numbers,
    _payload,
    _read_ex,
    _report_with_marker_on_page_two,
    _scanned,
    _total,
    _vision_reads,
    _write_ex,
)
from core.extract_vision import parse_vision_response


# ==========================================================================
# The run model behind each P1 fix
# ==========================================================================


def test_an_unnumbered_report_after_a_finished_one_is_its_own_run(tmp_path):
    small, large = SMALL_RUN[:2], _large_run(16)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large[:8], {"number": 1, "of": 2}),
        (LARGE_CARRIER, LARGE_HEADERS, large[8:], {"number": 2, "of": 2}),
        (SMALL_CARRIER, SMALL_HEADERS, small),
    ])
    runs = result.document.runs
    assert [run.pages for run in runs] == [[1, 2], [3]]
    assert runs[1].confidence is RunConfidence.INFERRED and not runs[1].ambiguous
    assert "printed its last page (2 of 2)" in runs[1].evidence[0].text
    assert "different heading" in runs[1].evidence[0].text
    assert [len(result.document.run_claims(run)) for run in runs] == [16, 2]


@pytest.mark.parametrize("variant", ["prose", "prose-near-top", "cell"])
def test_page_numbers_outside_the_furniture_split_nothing(tmp_path, variant):
    if variant == "prose":
        pages, _ = _report_with_marker_on_page_two(
            body=("Policy terms continued from Page 1 of 2",))
    elif variant == "prose-near-top":
        pages, _ = _report_with_marker_on_page_two(
            notes=("Policy terms continued from Page 1 of 2",))
    else:
        pages, _ = _report_with_marker_on_page_two(described=True, cell_note="SEE PAGE 1 OF 2")
    assert _read_ex(tmp_path, pages).document.runs == []


def test_scanned_reports_are_bounded_by_what_the_model_read(tmp_path):
    small, large = SMALL_RUN[:1], _large_run(8)
    scanned = _scanned(tmp_path, [
        (SMALL_CARRIER, SMALL_HEADERS, small, {"marker": "Page 1 of 1"}),
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 1"}),
    ])
    extractor = _vision_reads({
        1: _payload(SMALL_HEADERS, small, "Page 1 of 1", heading=SMALL_CARRIER),
        2: _payload(LARGE_HEADERS, large, "Page 1 of 1", heading=LARGE_CARRIER),
    })
    document = run_pipeline(scanned, use_vision=True, vision_extractor=extractor,
                            profiles_dir=tmp_path / "profiles").document
    assert [run.confidence for run in document.runs] == [RunConfidence.MODEL] * 2
    assert all(run.source_methods == [SourceMethod.VISION] for run in document.runs)


def test_unlabelled_scans_name_every_refused_claim(tmp_path):
    small, large = SMALL_RUN[:1], _large_run(8)
    scanned = _scanned(tmp_path, [
        (SMALL_CARRIER, SMALL_HEADERS, small, {}),
        (LARGE_CARRIER, LARGE_HEADERS, large, {}),
    ])
    extractor = _vision_reads({1: _payload(SMALL_HEADERS, small), 2: _payload(LARGE_HEADERS, large)})
    result = run_pipeline(scanned, use_vision=True, vision_extractor=extractor,
                          profiles_dir=tmp_path / "profiles")
    document = result.document
    assert document.runs == []
    assert [(row.identifier, row.bounded) for row in document.refused_claim_rows] == [
        (SMALL_RUN[0][0], False)]
    findings = [f for f in result.reconciliation.findings if f.rule_id == "R-29"]
    assert len(findings) == 1 and SMALL_RUN[0][0] in findings[0].message


def test_a_digital_and_a_scanned_report_are_two_runs(tmp_path):
    small, large = SMALL_RUN[:1], _large_run(8)
    mixed = _scanned(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 1"}),
        (SMALL_CARRIER, SMALL_HEADERS, small, {"marker": "Page 1 of 1"}),
    ], rasterise={2})
    extractor = _vision_reads({2: _payload(SMALL_HEADERS, small, "Page 1 of 1",
                                           heading=SMALL_CARRIER)})
    document = run_pipeline(mixed, use_vision=True, vision_extractor=extractor,
                            profiles_dir=tmp_path / "profiles").document
    assert [run.source_methods for run in document.runs] == [
        [SourceMethod.DIGITAL], [SourceMethod.VISION]]


def test_a_mixed_document_without_numbering_is_one_run(tmp_path):
    small, large = SMALL_RUN[:1], _large_run(8)
    mixed = _scanned(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {}),
        (SMALL_CARRIER, SMALL_HEADERS, small, {}),
    ], rasterise={2})
    extractor = _vision_reads({2: _payload(SMALL_HEADERS, small)})
    document = run_pipeline(mixed, use_vision=True, vision_extractor=extractor,
                            profiles_dir=tmp_path / "profiles").document
    assert document.runs == []


def test_p1_1_mechanism_a_page_after_n_of_n_is_not_in_that_run():
    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 2", "header")),
                        identity=identity_of("Northfield loss run")),
        2: PageEvidence(2, paginations=tuple(paginations_in("Page 2 of 2", "header")),
                        identity=identity_of("Northfield loss run")),
        3: PageEvidence(3, identity=identity_of("Harbor Crest loss run")),
    }
    assert [s.pages for s in plan_runs([1, 2, 3], evidence, {1, 2, 3})] == [[1, 2], [3]]


@pytest.mark.parametrize("where", ["prose", "cell"])
def test_p1_2_mechanism_body_pagination_is_seen_and_ignored(tmp_path, where):
    from core.extract_digital import extract_pdf

    if where == "prose":
        pages, _ = _report_with_marker_on_page_two(
            body=("Policy terms continued from Page 1 of 2",))
    else:
        pages, _ = _report_with_marker_on_page_two(described=True, cell_note="SEE PAGE 1 OF 2")
    extraction = extract_pdf(_write_ex(tmp_path / "one.pdf", pages))
    second = extraction.page_evidence[2]
    assert [(p.index, p.count, p.source) for p in second.paginations] == [(2, 2, "header")]
    assert [text.lower() for text in second.ignored] == ["page 1 of 2"]


def test_p1_2_a_footer_page_number_is_furniture(tmp_path):
    """Numbering printed at the foot of the page counts as much as at the top."""
    from core.extract_digital import extract_pdf

    large = _large_run(4)
    path = _write_ex(tmp_path / "footer.pdf", [
        (LARGE_CARRIER, LARGE_HEADERS, large[:2], {"footer": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, large[2:], {"footer": "Page 2 of 2"}),
    ])
    evidence = extract_pdf(path).page_evidence
    assert [(p.index, p.source) for p in evidence[1].paginations] == [(1, "footer")]
    assert [(p.index, p.source) for p in evidence[2].paginations] == [(2, "footer")]


def test_p1_3_numbering_the_model_places_in_the_body_is_not_kept():
    table = parse_vision_response(
        _payload(LARGE_HEADERS, _large_run(1), "Page 1 of 3", position="body"), 4)
    assert table.page_label is None and table.page_label_index is None
    malformed = parse_vision_response(
        {**_payload(LARGE_HEADERS, _large_run(1)), "page_label":
         {"text": "Page 4 of 3", "number": 4, "of": 3, "position": "footer"}}, 4)
    assert malformed.page_label_index is None
    boolean = parse_vision_response(
        {**_payload(LARGE_HEADERS, _large_run(1)), "page_label":
         {"text": "Page 1 of 1", "number": True, "of": 1, "position": "footer"}}, 4)
    assert boolean.page_label_index is None


# ==========================================================================
# Section restarts and unsettled boundaries
# ==========================================================================


def test_sections_restarting_their_numbering_under_one_heading_are_one_run(tmp_path):
    a, b = _large_run(8), _large_run(3, first=71004430)
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, a[:4], {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, a[4:], {"marker": "Page 2 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, b, {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], {"marker": "Page 2 of 2"}),
    ])
    assert result.document.runs == []
    assert _numbers(result) == [row[0] for row in (*a, *b)]
    assert [row.page for row in result.document.unplaced_rows] == [4]


def test_a_section_of_only_damaged_rows_is_not_voted_on_alone(tmp_path):
    a = _large_run(8)
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, a, {"marker": "Page 1 of 1"}),
        (LARGE_CARRIER, LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], {"marker": "Page 1 of 1"}),
    ])
    assert DATED_CODE[0] not in _numbers(result), _numbers(result)
    assert result.document.runs == []


def test_an_unnumbered_addendum_under_the_same_heading_is_unsettled(tmp_path):
    """The report printed "2 of 2"; the next page prints no number under the
    same heading. An addendum, or another report: nothing printed settles it.
    The page is kept apart, read under the report's vote, and reviewed."""
    large = _large_run(8)
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large[:4], {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, large[4:], {"marker": "Page 2 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], {"notes": ("ADDENDUM",)}),
    ])
    document = result.document
    assert DATED_CODE[0] not in _numbers(result), _numbers(result)
    assert _numbers(result) == [row[0] for row in large]
    assert [row.page for row in document.unplaced_rows] == [3]
    assert [(run.pages, run.ambiguous) for run in document.runs] == [([1, 2], False), ([3], True)]
    boundary = [f for f in result.reconciliation.findings if f.rule_id == "R-28"]
    assert len(boundary) == 1 and boundary[0].run_id == "run-2"
    assert "page(s) 3" in boundary[0].message and "same heading" in boundary[0].message
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    assert result.reconciliation.run_status["run-2"] is DocumentStatus.NEEDS_REVIEW


def test_a_report_that_stops_short_then_an_unnumbered_page_fails_closed(tmp_path):
    """"Page 1 of 3", then a page printing no number: the report's page 2 with
    its footer missing, or another carrier's report? Unsettled. Its claim is
    not voted on alone; the refused row is named in the boundary finding."""
    large = _large_run(8)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"number": 1, "of": 3}),
        (SMALL_CARRIER, SMALL_HEADERS, SMALL_RUN[:1]),
    ])
    document = result.document
    assert [(run.pages, run.ambiguous) for run in document.runs] == [([1], False), ([2], True)]
    assert "stopped at page 1 of 3" in document.runs[1].ambiguity
    boundary = [f for f in result.reconciliation.findings if f.rule_id == "R-28"]
    assert len(boundary) == 1
    assert SMALL_RUN[0][0] in boundary[0].message and "page(s) 2" in boundary[0].message
    assert [row.identifier for row in document.refused_claim_rows] == [SMALL_RUN[0][0]]
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_two_numbers_in_one_count_on_one_page_keep_the_continuing_one():
    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 2", "header"))),
        2: PageEvidence(2, paginations=tuple(
            paginations_in("Page 2 of 2", "header") + paginations_in("from Page 1 of 2", "header"))),
    }
    assert [(s.pages, s.ambiguous) for s in plan_runs([1, 2], evidence, {1, 2})] == [
        ([1, 2], False)]
    conflicting = {
        1: PageEvidence(1, paginations=tuple(
            paginations_in("Page 1 of 3", "header") + paginations_in("Page 3 of 3", "footer"))),
    }
    assert [s.ambiguous for s in plan_runs([1], conflicting, {1})] == [True]


def test_a_break_in_the_page_numbering_fails_closed():
    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 4", "footer"))),
        2: PageEvidence(2, paginations=tuple(paginations_in("Page 2 of 4", "footer"))),
        3: PageEvidence(3, paginations=tuple(paginations_in("Page 4 of 4", "footer"))),
    }
    segments = plan_runs([1, 2, 3], evidence, {1, 2, 3})
    assert [(s.pages, s.ambiguous) for s in segments] == [([1, 2], False), ([3], True)]
    assert "does not follow page 2 of 4" in segments[1].ambiguity


def test_a_missing_footer_inside_the_count_is_bridged():
    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 3", "footer"))),
        2: PageEvidence(2),
        3: PageEvidence(3, paginations=tuple(paginations_in("Page 3 of 3", "footer"))),
    }
    assert [(s.pages, s.ambiguous) for s in plan_runs([1, 2, 3], evidence, {1, 2, 3})] == [
        ([1, 2, 3], False)]


def test_unsettled_pages_without_a_claims_table_settle_nothing():
    """A cover letter or a policy page between reports carries no claim. Its
    numbering breaking says nothing about where a loss run ends."""
    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 3", "footer")),
                        identity="a"),
        2: PageEvidence(2, identity="policy form"),
        3: PageEvidence(3, paginations=tuple(paginations_in("Page 1 of 1", "footer")),
                        identity="b"),
    }
    segments = plan_runs([1, 2, 3], evidence, {1, 3})
    assert [(s.pages, s.ambiguous) for s in segments] == [([1, 2], False), ([3], False)]


# ==========================================================================
# Each run reconciled on its own
# ==========================================================================


def _two_carrier_packet(tmp_path, *, small_total=None, large_total=None, name="packet.pdf"):
    large, small = _large_run(8), list(SMALL_RUN)
    pages = [
        (LARGE_CARRIER, LARGE_HEADERS, large,
         {"marker": "Page 1 of 1", "total": large_total or _total(large)}),
        (SMALL_CARRIER, SMALL_HEADERS, small,
         {"marker": "Page 1 of 1", "total": small_total or _total(small)}),
    ]
    return _read_ex(tmp_path, pages, name=name), large, small


def test_every_claim_and_total_belongs_to_exactly_one_run(tmp_path):
    result, large, small = _two_carrier_packet(tmp_path)
    document = result.document
    assert [run.run_id for run in document.runs] == ["run-1", "run-2"]
    owners = [[run.run_id for run in document.runs if run.holds(c.source_page)]
              for c in document.claims]
    assert all(len(owner) == 1 for owner in owners)
    assert [len(document.run_claims(run)) for run in document.runs] == [8, 3]
    assert document.runs[0].printed_totals["incurred_total"] == Decimal("8000.00")
    assert document.runs[1].printed_totals["incurred_total"] == Decimal("7800.00")
    assert document.runs[0].carrier == LARGE_CARRIER
    assert document.runs[1].carrier == SMALL_CARRIER
    # The packet prints no total of its own; each run's is its own.
    assert document.printed_totals == {}
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.CLEAN}
    assert result.reconciliation.status is DocumentStatus.CLEAN
    assert "R-04" not in result.reconciliation.rule_ids()


def test_one_run_off_its_total_makes_the_packet_need_review(tmp_path):
    result, _large, small = _two_carrier_packet(
        tmp_path, small_total=("TOTAL", "", "", "2,050.00", "5,750.00", "7,900.00"))
    findings = [f for f in result.reconciliation.findings if f.rule_id == "R-04"]
    assert findings and all(f.run_id == "run-2" for f in findings)
    assert result.reconciliation.run_status == {
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.NEEDS_REVIEW}
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_signed_totals_are_read_per_run(tmp_path):
    """The signed-currency reading carries into each run's own total."""
    headers = (*LARGE_HEADERS[:3], "Paid Total", "Recovery", "Incurred Total")
    rows = [
        ("71004410", "01/10/2022", "CLOSED", "$5,000.00", "-$1,200.00", "$3,800.00"),
        ("71004411", "01/11/2022", "CLOSED", "$4,000.00", "($3,615.00)", "$385.00"),
    ]
    total = ("TOTAL", "", "", "$9,000.00", "-$4,815.00", "$4,185.00")
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, headers, rows, {"marker": "Page 1 of 1", "total": total}),
        (SMALL_CARRIER, SMALL_HEADERS, list(SMALL_RUN),
         {"marker": "Page 1 of 1", "total": _total(SMALL_RUN)}),
    ])
    first = result.document.runs[0]
    # Recoveries printed as credits are normalised with the claims' own, so
    # the printed figure and the claims it totals stay comparable.
    recovered = sum(claim.recovery_total for claim in result.document.run_claims(first))
    assert first.printed_totals.get("recovery_total") == recovered == Decimal("4815.00")
    assert "R-26" not in result.reconciliation.rule_ids()
    assert not [f for f in result.reconciliation.findings
                if f.rule_id == "R-04" and f.run_id == "run-1"]


def test_the_same_claim_number_in_two_runs_is_not_a_duplicate(tmp_path):
    """Two carriers can issue the same claim number: different claims."""
    one = ("40117", "02/14/2022", "OPEN", "250.00", "750.00", "1,000.00")
    other = ("40117", "07/03/2022", "OPEN", "250.00", "750.00", "1,000.00")
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, [one, *_large_run(3, first=40200)],
         {"marker": "Page 1 of 1"}),
        (SMALL_CARRIER, SMALL_HEADERS, [other, *_large_run(2, first=40300)],
         {"marker": "Page 1 of 1"}),
    ])
    assert "R-11" not in result.reconciliation.rule_ids()
    assert "R-12" not in result.reconciliation.rule_ids()


def test_the_same_claim_read_in_two_runs_is_counted_once_or_reviewed(tmp_path):
    """Number and loss date both repeat: the packet counts one claim twice."""
    shared = ("40117", "02/14/2022", "OPEN", "250.00", "750.00", "1,000.00")
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, [shared, *_large_run(3, first=40200)],
         {"marker": "Page 1 of 1"}),
        (SMALL_CARRIER, SMALL_HEADERS, [shared, *_large_run(2, first=40300)],
         {"marker": "Page 1 of 1"}),
    ])
    across = [f for f in result.reconciliation.findings
              if f.rule_id == "R-11" and f.condition.startswith("across-runs:40117:")]
    assert len(across) == 1 and "run-1, run-2" in across[0].message
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_run_whose_claims_carry_no_amounts_keeps_them(tmp_path):
    """A report listing claims with dates and statuses but no figures."""
    bare = [(row[0], row[1], row[2], "", "", "") for row in SMALL_RUN]
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, _large_run(12), {"number": 1, "of": 1}),
        (SMALL_CARRIER, SMALL_HEADERS, bare, {"number": 1, "of": 1}),
    ])
    assert _numbers(result)[-3:] == [row[0] for row in SMALL_RUN]
    assert result.document.refused_claim_rows == []
    assert result.reconciliation.run_status["run-2"] is DocumentStatus.NEEDS_REVIEW  # R-07


def test_edits_reconcile_per_run_again(tmp_path):
    result, _large, _small = _two_carrier_packet(tmp_path)
    document = result.document
    document.claims[-1].incurred_total = Decimal("9999.00")
    again = rerun_reconciliation(document)
    assert again.run_status["run-2"] is DocumentStatus.NEEDS_REVIEW
    assert again.run_status["run-1"] is DocumentStatus.CLEAN


# ==========================================================================
# Single-run documents are unchanged
# ==========================================================================


def test_a_single_report_has_no_runs_and_no_run_columns(tmp_path):
    large = _large_run(6)
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large[:3], {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, large[3:], {"marker": "Page 2 of 2", "total": _total(large)}),
    ])
    document = result.document
    assert document.runs == [] and not document.is_packet
    assert result.reconciliation.run_status == {}
    assert all(f.run_id is None for f in result.reconciliation.findings)
    workbook = build_workbook(document, result.reconciliation)
    assert "Runs" not in workbook.sheetnames
    headers = [cell.value for cell in workbook["Claim Detail"][1]]
    assert "Run ID" not in headers
    assert len(headers) == len(resolve_columns("Underwriting standard")) + len(_DOCUMENT_COLUMNS)
    assert runs_overview(document, result.reconciliation) == []


def test_a_lone_report_that_stops_before_its_last_page_is_not_clean(tmp_path):
    """A single report whose own numbering declares pages the PDF lacks.

    One report is not a packet and carries no run, so before this nothing held
    the fact that its numbering stopped short: the document read CLEAN with
    part of its table absent. The pages it says it has and the PDF does not are
    a claim-accountability hole, named by R-28.
    """
    large = _large_run(6)
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 2"}),
    ])
    document = result.document
    assert not document.is_packet
    assert document.runs == []
    assert document.incomplete_report
    assert "page 1 of 2" in document.incomplete_report
    assert result.reconciliation.run_status == {}
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    r28 = [f for f in result.reconciliation.findings if f.rule_id == "R-28"]
    assert len(r28) == 1 and r28[0].run_id is None
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def test_a_cover_page_before_a_report_does_not_make_a_packet(tmp_path):
    large = _large_run(4)
    document = pymupdf.open()
    cover = document.new_page(width=612, height=792)
    cover.insert_text((LEFT, 100), "Renewal submission for the account", fontsize=12)
    document.save(tmp_path / "cover.pdf")
    document.close()
    body = _write_ex(tmp_path / "body.pdf", [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 1"})])
    merged = pymupdf.open(tmp_path / "cover.pdf")
    merged.insert_pdf(pymupdf.open(body))
    merged.save(tmp_path / "with_cover.pdf")
    result = run_pipeline(tmp_path / "with_cover.pdf", use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    assert result.document.runs == []
    assert _numbers(result) == [row[0] for row in large]


# ==========================================================================
# Export and review screen
# ==========================================================================


def test_the_workbook_names_each_claims_run_and_lists_the_runs(tmp_path):
    result, large, small = _two_carrier_packet(tmp_path)
    document = result.document
    workbook = load_workbook(io.BytesIO(_workbook_bytes(document, result.reconciliation)))
    sheet = workbook["Claim Detail"]
    headers = [cell.value for cell in sheet[1]]
    run_column = headers.index("Run ID")
    assert headers[run_column + 1] == "Carrier"
    runs = [row[run_column].value for row in sheet.iter_rows(min_row=2)]
    assert runs == ["run-1"] * len(large) + ["run-2"] * len(small)
    carriers = [row[run_column + 1].value for row in sheet.iter_rows(min_row=2)]
    assert carriers == [LARGE_CARRIER] * len(large) + [SMALL_CARRIER] * len(small)

    assert workbook.sheetnames.index("Runs") == workbook.sheetnames.index("Loss Summary") + 1
    runs_sheet = workbook["Runs"]
    header = [cell.value for cell in runs_sheet[1]]
    rows = [[cell.value for cell in row] for row in runs_sheet.iter_rows(min_row=2)]
    assert [row[0] for row in rows] == ["run-1", "run-2"]
    assert [row[header.index("Pages")] for row in rows] == ["1", "2"]
    assert [row[header.index("Claims read")] for row in rows] == [8, 3]
    assert [row[header.index("Status")] for row in rows] == ["CLEAN", "CLEAN"]


def test_the_workbook_marks_an_unsettled_run(tmp_path):
    large = _large_run(8)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"number": 1, "of": 3}),
        (SMALL_CARRIER, SMALL_HEADERS, SMALL_RUN[:1]),
    ])
    workbook = load_workbook(io.BytesIO(_workbook_bytes(result.document, result.reconciliation)))
    sheet = workbook["Runs"]
    header = [cell.value for cell in sheet[1]]
    second = [cell.value for cell in sheet[3]]
    assert second[header.index("Settled")] == "no"
    assert "stopped at page 1 of 3" in second[header.index("Why not settled")]
    exceptions = workbook["Exceptions"]
    columns = [cell.value for cell in exceptions[1]]
    assert columns[-1] == "Run ID"
    r28 = [row for row in exceptions.iter_rows(min_row=2, values_only=True) if row[0] == "R-28"]
    assert r28 and r28[0][-1] == "run-2"
    assert unsettled_runs(result.document) == [
        ("run-2", "2", result.document.runs[1].ambiguity)]


def _workbook_bytes(document, reconciliation):
    buffer = io.BytesIO()
    build_workbook(document, reconciliation).save(buffer)
    return buffer.getvalue()


# ==========================================================================
# Invariants: no claim is lost to another run
# ==========================================================================

_SHAPES = (
    lambda n: f"{71004410 + n}",
    lambda n: f"CR-{40100 + n}",
    lambda n: f"WC{2022000 + n}-A",
    lambda n: f"{n + 1:03d}-LX-{7000 + n}",
)
_CARRIERS = (LARGE_CARRIER, SMALL_CARRIER, THIRD_CARRIER, "GRANITE POINT MUTUAL")


def _report(shape, count, first):
    return [
        (shape(first + n), f"{1 + n % 9:02d}/{10 + n % 10}/2022",
         ("OPEN", "CLOSED")[n % 2], "100.00", "0.00", "100.00")
        for n in range(count)
    ]


@pytest.mark.parametrize("seed", range(8))
def test_binding_reports_into_a_packet_loses_no_claim(tmp_path, seed):
    """Read alone, each report yields its claims. Bound together -- in any
    order, any sizes, numbered or with a trailing unnumbered report -- the
    packet yields exactly the same claims, each in exactly one run."""
    rng = random.Random(seed)
    count = rng.randint(2, 4)
    kinds = rng.sample(range(len(_SHAPES)), count)
    reports = []
    for position, kind in enumerate(kinds):
        size = rng.choice([1, 2, 3, 9, 14])
        rows = _report(_SHAPES[kind], size, first=rng.randint(0, 50))
        per_page = rng.choice([6, 20])
        chunks = [rows[i:i + per_page] for i in range(0, len(rows), per_page)]
        numbered = position < count - 1 or rng.random() < 0.5
        headers = rng.choice([LARGE_HEADERS, SMALL_HEADERS])
        pages = [
            (_CARRIERS[kind], headers, chunk,
             {"marker": f"Page {i + 1} of {len(chunks)}"} if numbered else {})
            for i, chunk in enumerate(chunks)
        ]
        reports.append((pages, [row[0] for row in rows]))

    alone = []
    for index, (pages, expected) in enumerate(reports):
        result = _read_ex(tmp_path, pages, name=f"alone-{index}.pdf")
        assert _numbers(result) == expected
        alone.append(expected)

    packet = _read_ex(tmp_path, [page for pages, _ in reports for page in pages],
                      name="packet.pdf")
    assert _numbers(packet) == [number for numbers in alone for number in numbers]
    document = packet.document
    assert [len(document.run_claims(run)) for run in document.runs] == [len(n) for n in alone]
    assert not any(run.ambiguous for run in document.runs)


def _rows(page, numbers, *, dated=True):
    return [
        RawRow(cells=[number, "03/14/2022" if dated else "", "OPEN" if dated else "",
                      "100.00", "0.00", "100.00"], page=page, line_index=10 + index)
        for index, number in enumerate(numbers)
    ]


@pytest.mark.parametrize("seed", range(25))
def test_every_claim_like_row_becomes_a_claim_or_is_recorded(seed):
    """Whatever the grouping, a row with a well-formed identifier and a date or
    status of its own is either a claim or on the refused list -- never only
    folded into another claim or reduced to a warning."""
    from core.pipeline import identifier_shapes_by_run

    rng = random.Random(seed)
    tables, runs = [], {}
    for page in range(1, rng.randint(2, 5) + 1):
        shape = rng.choice(_SHAPES)
        numbers = [shape(rng.randint(0, 999)) for _ in range(rng.randint(1, 10))]
        tables.append(RawTable(page=page, headers=list(LARGE_HEADERS),
                               rows=_rows(page, list(dict.fromkeys(numbers)))))
        runs[page] = rng.randint(0, 2)
    mapping = build_mapping(list(LARGE_HEADERS))
    grouping = rng.choice([None, runs])
    refused = []
    claims, _warnings, _unplaced = build_claims(
        tables, mapping, "us", "mdy", runs=grouping, refused=refused)
    read = {(c.source_page, c.source_row) for c in claims}
    recorded = {(r.page, r.row) for r in refused}
    for table in tables:
        for row in table.rows:
            key = (row.page, row.line_index)
            assert (key in read) != (key in recorded), key


def test_a_report_that_does_not_number_its_first_page_is_one_run():
    """"Page 2 of 3" after one unnumbered page says that page was its page 1."""
    evidence = {
        1: PageEvidence(1, identity="acme"),
        2: PageEvidence(2, paginations=tuple(paginations_in("Page 2 of 3", "footer")),
                        identity="acme"),
        3: PageEvidence(3, paginations=tuple(paginations_in("Page 3 of 3", "footer")),
                        identity="acme"),
    }
    segments = plan_runs([1, 2, 3], evidence, {1, 2, 3})
    assert [(s.pages, s.ambiguous) for s in segments] == [([1, 2, 3], False)]
    assert "began 1 page(s) earlier, on page 1" in segments[0].evidence[0].text


def test_numbering_that_cannot_account_for_the_pages_before_it_is_unsettled():
    evidence = {
        1: PageEvidence(1, identity="acme"),
        2: PageEvidence(2, paginations=tuple(paginations_in("Page 4 of 5", "footer"))),
    }
    assert [(s.pages, s.ambiguous) for s in plan_runs([1, 2], evidence, {1, 2})] == [
        ([1], False), ([2], True)]


def test_numbering_restarts_are_not_run_boundaries(tmp_path):
    """A packet shaped like a board submission: a cover, policy forms and
    schedules each numbering their own pages, one carrier's report restarting
    its numbering by section, and a second carrier's one-claim report. Seven
    page-1s, two loss runs -- and every claim read."""
    from tests.test_packet_p1_regressions import _page_ex

    document = pymupdf.open()

    def forms(title, count):
        for index in range(count):
            page = document.new_page(width=612, height=792)
            page.insert_text((40, 36), f"{title}   Page {index + 1} of {count}", fontsize=8)
            page.insert_text((40, 120), "This form describes coverage. See Page 1 of 3 "
                             "of the declarations.", fontsize=9)

    forms("RFP COVER", 1)
    forms("POLICY DECLARATIONS", 3)
    forms("ENDORSEMENTS", 2)
    large = _large_run(24)
    _page_ex(document, LARGE_CARRIER, LARGE_HEADERS, large[:8], marker="Page 1 of 2")
    _page_ex(document, LARGE_CARRIER, LARGE_HEADERS, large[8:16], marker="Page 2 of 2")
    _page_ex(document, LARGE_CARRIER, LARGE_HEADERS, large[16:], marker="Page 1 of 1")
    forms("SCHEDULE OF LOCATIONS", 2)
    _page_ex(document, SMALL_CARRIER, SMALL_HEADERS, list(SMALL_RUN[:1]), marker="Page 1 of 1")
    forms("TERMS AND CONDITIONS", 4)
    document.save(tmp_path / "board.pdf")
    document.close()

    result = run_pipeline(tmp_path / "board.pdf", use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    runs = result.document.runs
    assert [(run.page_range, run.ambiguous) for run in runs] == [("1-11", False), ("12-16", False)]
    assert [len(result.document.run_claims(run)) for run in runs] == [24, 1]
    assert _numbers(result) == [row[0] for row in large] + [SMALL_RUN[0][0]]
    assert result.reconciliation.status is DocumentStatus.CLEAN


# ==========================================================================
# Review findings on the first implementation, kept as regressions
# ==========================================================================


def _table(page, numbers, *, headers=LARGE_HEADERS, dated=True, extra=()):
    rows = [RawRow(cells=[number, "03/14/2022" if dated else "", "OPEN" if dated else "",
                          "100.00", "0.00", "100.00"], page=page, line_index=10 + i)
            for i, number in enumerate(numbers)]
    rows += [RawRow(cells=list(cells), page=page, line_index=40 + i)
             for i, cells in enumerate(extra)]
    return RawTable(page=page, headers=list(headers), rows=rows)


def test_a_continuation_line_never_joins_another_runs_claim():
    """The first line of run 2 is prose; it is not folded into run 1's last claim."""
    mapping = build_mapping(list(LARGE_HEADERS))
    first = _table(1, [f"{71004410 + n}" for n in range(4)])
    second = _table(2, [], extra=[("ACME HOLDINGS INC - LOCATION 4", "", "", "", "", "")])
    second.rows = second.rows + _table(2, ["CR-40117"]).rows
    claims, _w, _u = build_claims([first, second], mapping, "us", "mdy", runs={1: 0, 2: 1})
    assert "ACME" not in (claims[3].loss_description or "")


def test_a_run_keeps_its_own_series_beside_a_shape_it_shares_with_another():
    """Run 2 prints its own numbering and one number shaped like run 1's. Its
    own series is its vote; the shared shape does not take it away."""
    mapping = build_mapping(list(LARGE_HEADERS))
    first = _table(1, [f"WC{1000000 + n}" for n in range(20)])
    second = _table(2, ["123-456-789", "123-456-790", "123-456-791", "WC7654321"])
    claims, _w, _u = build_claims([first, second], mapping, "us", "mdy", runs={1: 0, 2: 1})
    numbers = [claim.claim_number for claim in claims]
    assert {"123-456-789", "123-456-790", "123-456-791", "WC7654321"} <= set(numbers)


def test_an_unsettled_run_does_not_outvote_the_settled_run_before_it(tmp_path):
    """A settled three-claim report, then twenty unnumbered claims under the
    same heading: the unsettled run borrows the settled run's vote, it does not
    pool into it -- the settled run keeps its claims."""
    small = [(f"123-456-{780 + n}", "03/14/2022", "OPEN", "100.00", "0.00", "100.00")
             for n in range(3)]
    many = [(f"WC{1000000 + n}", "03/14/2022", "OPEN", "100.00", "0.00", "100.00")
            for n in range(20)]
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, small, {"marker": "Page 1 of 1"}),
        (LARGE_CARRIER, LARGE_HEADERS, many[:10], {}),
        (LARGE_CARRIER, LARGE_HEADERS, many[10:], {}),
    ])
    assert _numbers(result)[:3] == [row[0] for row in small]
    runs = result.document.runs
    assert [(run.pages, run.ambiguous) for run in runs] == [([1], False), ([2, 3], True)]
    boundary = [f for f in result.reconciliation.findings if f.rule_id == "R-28"]
    assert boundary and "20 row(s) there read as claims" in boundary[0].message
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_report_cut_short_by_the_next_one_is_reported():
    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 3", "footer")),
                        identity="northfield"),
        2: PageEvidence(2, paginations=tuple(paginations_in("Page 2 of 3", "footer")),
                        identity="northfield"),
        3: PageEvidence(3, paginations=tuple(paginations_in("Page 1 of 1", "footer")),
                        identity="harbor crest"),
    }
    from core.runs import logical_runs

    runs = logical_runs([1, 2, 3], evidence, {1, 2, 3})
    assert [(run.pages, run.ambiguous) for run in runs] == [([1, 2], False), ([3], False)]
    assert "stops at page 2 of 3" in runs[0].incomplete


def test_the_truncated_run_makes_the_packet_need_review(tmp_path):
    large = _large_run(8)
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large[:4], {"marker": "Page 1 of 3"}),
        (LARGE_CARRIER, LARGE_HEADERS, large[4:], {"marker": "Page 2 of 3"}),
        (SMALL_CARRIER, SMALL_HEADERS, list(SMALL_RUN), {"marker": "Page 1 of 1"}),
    ])
    incomplete = [f for f in result.reconciliation.findings
                  if f.rule_id == "R-28" and f.condition == "run-1:incomplete"]
    assert len(incomplete) == 1 and "stops at page 2 of 3" in incomplete[0].message
    assert result.reconciliation.run_status["run-1"] is DocumentStatus.NEEDS_REVIEW


def test_a_form_bound_inside_a_report_does_not_end_it():
    """An ACORD form numbered "1 of 1" between the report's pages 2 and 3."""
    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 3", "footer"))),
        2: PageEvidence(2, paginations=tuple(paginations_in("Page 2 of 3", "footer"))),
        3: PageEvidence(3, paginations=tuple(paginations_in("Page 1 of 1", "footer")),
                        identity="acord certificate"),
        4: PageEvidence(4, paginations=tuple(paginations_in("Page 3 of 3", "footer"))),
    }
    segments = plan_runs([1, 2, 3, 4], evidence, {1, 2, 4})
    assert [(s.pages, s.ambiguous) for s in segments] == [([1, 2, 3, 4], False)]


def test_a_packet_stamp_does_not_carry_a_finished_report_on():
    evidence = {
        1: PageEvidence(1, paginations=tuple(
            paginations_in("Page 1 of 2", "header") + paginations_in("Page 1 of 4", "footer")),
            identity="northfield"),
        2: PageEvidence(2, paginations=tuple(
            paginations_in("Page 2 of 2", "header") + paginations_in("Page 2 of 4", "footer")),
            identity="northfield"),
        3: PageEvidence(3, paginations=tuple(paginations_in("Page 3 of 4", "footer")),
                        identity="harbor crest"),
        4: PageEvidence(4, paginations=tuple(paginations_in("Page 4 of 4", "footer")),
                        identity="harbor crest"),
    }
    segments = plan_runs([1, 2, 3, 4], evidence, {1, 2, 3, 4})
    assert [(s.pages, s.ambiguous) for s in segments] == [([1, 2], False), ([3, 4], False)]


def test_two_runs_failing_alike_are_two_findings(tmp_path):
    """Each run prints a total its claims do not reach, by the same amount. Two
    findings, one per run -- not one finding for "the whole packet"."""
    large, other = _large_run(8), _large_run(8, first=40200)
    wrong = ("TOTAL", "", "", "8,000.00", "0.00", "9,000.00")
    result = _read_ex(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 1", "total": wrong}),
        (SMALL_CARRIER, SMALL_HEADERS, other, {"marker": "Page 1 of 1", "total": wrong}),
    ])
    r04 = [f for f in result.reconciliation.findings if f.rule_id == "R-04"]
    assert sorted(f.run_id for f in r04) == ["run-1", "run-2"]


def test_a_claim_added_by_hand_joins_the_run_of_its_page(tmp_path):
    from core.pipeline import apply_edits, to_records

    result, _large, _small = _two_carrier_packet(tmp_path)
    document = result.document
    columns = ["claim_number", "date_of_loss", "claim_status", "paid_total",
               "reserve_total", "incurred_total"]
    records = to_records(document, columns)
    records.append({"claim_number": "CR-40999", "date_of_loss": "2022-05-05",
                    "claim_status": "OPEN", "paid_total": "0", "reserve_total": "10",
                    "incurred_total": "10", "_page": 2})
    edited = apply_edits(document, records)
    added = next(claim for claim in edited.claims if claim.claim_number == "CR-40999")
    assert edited.run_of(added.source_page).run_id == "run-2"
    # Provenance of a read row is not editable through the page cell.
    moved = to_records(document, columns)
    moved[0]["_page"] = 2
    assert apply_edits(document, moved).claims[0].source_page == 1


def test_a_claim_added_with_no_page_belongs_to_no_run_and_is_named(tmp_path):
    """A row a reviewer adds to a packet without saying which run it belongs to
    must not default into run-1. It belongs to no run, R-11 names it, and the
    packet needs review even though every run's own arithmetic still ties."""
    from core.pipeline import edit_claims, to_records

    result, _large, _small = _two_carrier_packet(tmp_path)
    document = result.document
    columns = ["claim_number", "date_of_loss", "claim_status", "paid_total",
               "reserve_total", "incurred_total"]
    records = to_records(document, columns)
    records.append({"claim_number": "CR-40999", "date_of_loss": "2022-05-05",
                    "claim_status": "OPEN", "paid_total": "", "reserve_total": "",
                    "incurred_total": ""})
    edited = edit_claims(result, records)
    added = next(c for c in edited.document.claims if c.claim_number == "CR-40999")
    assert added.source_page is None
    for run in edited.document.runs:
        assert added not in edited.document.run_claims(run)
    assert edited.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    assert any(f.rule_id == "R-11" and "CR-40999" in f.message
               for f in edited.reconciliation.findings)


def test_each_row_carries_its_runs_valuation_date(tmp_path):
    large, small = _large_run(4), list(SMALL_RUN)
    document = pymupdf.open()
    from tests.test_packet_p1_regressions import _page_ex

    _page_ex(document, LARGE_CARRIER, LARGE_HEADERS, large, marker="Page 1 of 1")
    page = document.new_page(width=612, height=792)
    page.insert_text((LEFT, 36), "Page 1 of 1", fontsize=8)
    for y, line in ((50, SMALL_CARRIER), (64, "LOSS RUN REPORT"), (78, "Valuation Date: 06/30/2023")):
        page.insert_text((LEFT, y), line, fontsize=9)
    from tests.test_packet_claim_series import COLUMNS
    for offset, label in zip(COLUMNS, SMALL_HEADERS):
        page.insert_text((LEFT + offset, 120), label, fontsize=8.5)
    for index, row in enumerate(small):
        for offset, cell in zip(COLUMNS, row):
            page.insert_text((LEFT + offset, 134 + 14 * index), cell, fontsize=8.5)
    document.save(tmp_path / "dates.pdf")
    document.close()
    result = run_pipeline(tmp_path / "dates.pdf", use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    runs = result.document.runs
    assert [str(run.valuation_date) for run in runs] == ["2022-12-31", "2023-06-30"]
    workbook = load_workbook(io.BytesIO(_workbook_bytes(result.document, result.reconciliation)))
    sheet = workbook["Claim Detail"]
    headers = [cell.value for cell in sheet[1]]
    column = headers.index("Valuation date")
    values = [str(row[column].value)[:10] for row in sheet.iter_rows(min_row=2)]
    assert values == ["2022-12-31"] * len(large) + ["2023-06-30"] * len(small)


# --------------------------------------------------------------------------
# Page evidence: where it sits, and what it is read from
# --------------------------------------------------------------------------


def test_text_outside_the_crop_box_is_not_furniture(tmp_path):
    from core.extract_digital import extract_pdf

    document = pymupdf.open()
    page = document.new_page(width=612, height=1008)
    page.insert_text((40, 10), "Page 9 of 9", fontsize=8)
    page.insert_text((40, 236), "Page 1 of 2", fontsize=8)
    page.insert_text((40, 252), LARGE_CARRIER, fontsize=9)
    page.set_cropbox(pymupdf.Rect(0, 216, 612, 1008))
    document.save(tmp_path / "crop.pdf")
    document.close()
    evidence = extract_pdf(tmp_path / "crop.pdf").page_evidence[1]
    assert [(p.index, p.count) for p in evidence.paginations] == [(1, 2)]


def test_the_claims_tables_own_labels_name_no_report():
    from core.runs import identity_of

    assert identity_of("Claim Number Loss Date Status", exclude=["Claim Number", "Loss Date",
                                                                 "Status"]) is None
    assert identity_of("NORTHFIELD AUTO Policy AU-1001 Printed March 31, 2023 10:15 AM") == \
        identity_of("NORTHFIELD AUTO Policy AU-1002 Printed April 2, 2023 9:01 AM")


def test_headings_are_compared_by_what_each_names():
    from core.runs import same_heading

    report = identity_of("NORTHWIND MUTUAL LOSS RUN REPORT Valuation Date")
    # A section line the report adds is still that report: it names the same carrier.
    assert same_heading(report, identity_of("NORTHWIND MUTUAL LOSS RUN REPORT ADDENDUM")) is True
    assert same_heading(report, identity_of("NORTHWIND MUTUAL")) is True
    # Generic words alone name nothing, so they neither join nor separate.
    assert same_heading(report, identity_of("LOSS RUN REPORT CONTINUED")) is None
    assert same_heading(identity_of("LOSS RUN REPORT NORTHWIND MUTUAL"),
                        identity_of("LOSS RUN REPORT HARBOR CREST")) is False
    assert same_heading(report, None) is None


def test_numbering_on_a_recognised_scan_is_a_reading(tmp_path):
    """A searchable scan's page numbers come from OCR: counted, as a reading."""
    from core.runs import OCR, Pagination, PageEvidence as Evidence, plan_packet

    evidence = {
        1: Evidence(1, paginations=(Pagination(1, 1, OCR, "Page 1 of 1"),), identity="alpha harbor"),
        2: Evidence(2, paginations=(Pagination(1, 1, OCR, "Page 1 of 1"),), identity="beta crest"),
    }
    plan = plan_packet([1, 2], evidence, {1, 2})
    assert [run.confidence for run in plan.runs] == [RunConfidence.MODEL] * 2


def test_the_model_must_state_the_numbers_it_reports():
    def label(text, number, of):
        return parse_vision_response(
            {**_payload(LARGE_HEADERS, _large_run(1)), "page_label":
             {"text": text, "number": number, "of": of, "position": "footer"}}, 3)

    assert label("Page 7 of 8", 1, 8).page_label_index is None
    assert label("1 of 3", 1, 3).page_label_index is None     # not page numbering to the text layer either
    assert label("Pg. 2 of 3", 2, 3).page_label_index == 2
    assert label("Sheet 1 of 4", 1, 4).page_label_count == 4


def test_a_claim_repeated_under_two_loss_dates_across_runs_is_two_findings():
    """One report bound twice, a claim number repeating with two loss dates:
    two findings with their own identities -- never a crash."""
    from core.reconcile import reconcile
    from core.schema import Claim, LogicalRun, LossRunDocument
    from datetime import date

    def claim(page, row, loss):
        return Claim(claim_number="X1", date_of_loss=loss, source_page=page, source_row=row,
                     incurred_total=Decimal("1"), paid_total=Decimal("1"), reserve_total=Decimal("0"))

    runs = [LogicalRun(run_id=f"run-{n}", pages=[n], confidence=RunConfidence.PRINTED)
            for n in (1, 2, 3)]
    document = LossRunDocument(
        source_filename="x.pdf", file_sha256="0" * 64, page_count=3, runs=runs,
        claims=[claim(1, 1, date(2022, 1, 5)), claim(1, 2, date(2022, 2, 5)),
                claim(2, 1, date(2022, 1, 5)), claim(3, 1, date(2022, 2, 5))],
    )
    across = [f for f in reconcile(document).findings if f.condition.startswith("across-runs:")]
    assert len(across) == 2


def test_a_generic_heading_does_not_confirm_a_page_belongs_to_the_report():
    """A report prints "ACME Insurance Loss Run Report"; unnumbered pages after
    its "2 of 5" print only "Loss Run Report" -- another report whose name is
    in a logo, perhaps. They are kept with it, but marked unconfirmed, so any
    claim its vote refuses there is reported."""
    from core.runs import plan_packet

    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 5", "footer")),
                        identity=identity_of("ACME Insurance Loss Run Report")),
        2: PageEvidence(2, paginations=tuple(paginations_in("Page 2 of 5", "footer")),
                        identity=identity_of("ACME Insurance Loss Run Report")),
        3: PageEvidence(3, identity=identity_of("Loss Run Report")),
        4: PageEvidence(4, identity=identity_of("Loss Run Report")),
    }
    plan = plan_packet([1, 2, 3, 4], evidence, {1, 2, 3, 4})
    assert plan.runs == [] and plan.blind == {3, 4}


def test_a_restart_under_a_generic_heading_is_an_ambiguous_boundary():
    """A restart whose heading names nothing is a section or another report:
    kept apart and reported, never merged unseen."""
    from core.runs import plan_packet

    evidence = {
        1: PageEvidence(1, paginations=tuple(paginations_in("Page 1 of 1", "footer")),
                        identity=identity_of("ACME Insurance Loss Run Report")),
        2: PageEvidence(2, paginations=tuple(paginations_in("Page 1 of 1", "footer")),
                        identity=identity_of("Loss Run Report")),
    }
    plan = plan_packet([1, 2], evidence, {1, 2})
    assert [(run.pages, run.ambiguous) for run in plan.runs] == [([1], False), ([2], True)]


def test_a_restart_under_the_same_naming_words_is_a_section():
    from core.runs import plan_packet

    evidence = {
        n: PageEvidence(n, paginations=tuple(paginations_in("Page 1 of 1", "footer")),
                        identity=identity_of(f"ACME Insurance Loss Run Report {line}"))
        for n, line in ((1, "General Liability"), (2, "Commercial Auto"))
    }
    assert plan_packet([1, 2], evidence, {1, 2}).runs == []


def test_headings_sharing_only_part_of_a_name_settle_nothing():
    from core.runs import confirms, same_heading

    alpha = identity_of("ALPHA MUTUAL INSURANCE")
    harbor = identity_of("ALPHA HARBOR INSURANCE")
    assert same_heading(alpha, harbor) is None
    assert not confirms(harbor, alpha) and not confirms(alpha, harbor)
    assert same_heading(identity_of("NATIONAL INDEMNITY COMPANY"),
                        identity_of("GENERAL CASUALTY COMPANY")) is None


def test_a_heading_change_under_continuing_numbering_is_unconfirmed():
    from core.runs import plan_packet

    evidence = {
        n: PageEvidence(n, paginations=tuple(paginations_in(f"Page {n} of 4", "footer")),
                        identity=identity_of("Carrier Alpha Loss Run" if n < 3
                                             else "Carrier Beta Claims Listing"))
        for n in (1, 2, 3, 4)
    }
    plan = plan_packet([1, 2, 3, 4], evidence, {1, 2, 3, 4})
    assert plan.runs == [] and plan.blind == {3, 4}


def test_refusals_on_unconfirmed_pages_are_reported(tmp_path):
    """End to end: a numbered report, then an unnumbered report whose heading
    is only a generic line the first one also prints. One vote, as nothing
    splits them -- and the refused claim is named, not dropped."""
    from tests.test_packet_p1_regressions import _page_ex

    large = _large_run(12)
    document = pymupdf.open()
    _page_ex(document, "ACME INSURANCE", LARGE_HEADERS, large[:6], marker="Page 1 of 5")
    _page_ex(document, "ACME INSURANCE", LARGE_HEADERS, large[6:], marker="Page 2 of 5")
    page = document.new_page(width=612, height=792)
    page.insert_text((LEFT, 64), "LOSS RUN REPORT", fontsize=9)
    from tests.test_packet_claim_series import COLUMNS
    for offset, label in zip(COLUMNS, SMALL_HEADERS):
        page.insert_text((LEFT + offset, 120), label, fontsize=8.5)
    for offset, cell in zip(COLUMNS, (SMALL_RUN[0][0], SMALL_RUN[0][1], SMALL_RUN[0][2], "", "", "")):
        if cell:
            page.insert_text((LEFT + offset, 134), cell, fontsize=8.5)
    document.save(tmp_path / "generic.pdf")
    document.close()
    result = run_pipeline(tmp_path / "generic.pdf", use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    named = any(SMALL_RUN[0][0] in f.message for f in result.reconciliation.findings)
    assert SMALL_RUN[0][0] in _numbers(result) or named
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
