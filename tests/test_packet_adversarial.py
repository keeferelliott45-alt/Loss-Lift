"""Adversarial packets: the cases an independent review built to break the
run model, kept as regressions.

Self-contained (only tests.test_packet_claim_series helpers), so the same file
runs against any revision. Everything is synthetic (spec section 9).

Each case states what it guards: a claim silently lost across runs, page
numbering outside the page's own furniture splitting a report, an unnumbered
trailing report, a scanned packet, or a single report that must stay one run.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.extract_vision import VisionExtraction, parse_vision_response
from core.pipeline import run_pipeline
from core.schema import DocumentStatus
from tests.test_packet_claim_series import (
    COLUMNS,
    LARGE_CARRIER,
    LARGE_HEADERS,
    LEFT,
    LINE,
    SMALL_CARRIER,
    SMALL_HEADERS,
    SMALL_RUN,
    _large_run,
)

LETTER = ("LOSS RUN REPORT", "Valuation Date: 12/31/2022")
DAMAGED_CLAIM = ("71004418CL", "04/14/2022", "CLOSED", "1,000.00", "0.00", "1,000.00")
DATED_CODE = ("0HA-MATERIAL", "11/23/2022", "", "", "", "")


def _numbers(result):
    return [claim.claim_number for claim in result.document.claims]


def _runs(result):
    return [(run.pages, run.ambiguous) for run in getattr(result.document, "runs", [])]


def _rules(result):
    return sorted(set(result.reconciliation.rule_ids()))


def _total(rows):
    paid = sum(Decimal(row[3].replace(",", "")) for row in rows)
    reserve = sum(Decimal(row[4].replace(",", "")) for row in rows)
    incurred = sum(Decimal(row[5].replace(",", "")) for row in rows)
    return ("TOTAL", "", "", f"{paid:,.2f}", f"{reserve:,.2f}", f"{incurred:,.2f}")


def _sheet(document, *, top=(), headers=LARGE_HEADERS, rows=(), total=None,
           after=(), bottom=(), width=612, height=792):
    """One page: ``top`` lines from y=36, the table, ``after`` lines straight
    under the table, ``bottom`` lines ending 22pt above the page edge."""
    page = document.new_page(width=width, height=height)
    y = 36.0
    for line in top:
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, headers):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in [*rows, *([total] if total else [])]:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    y += LINE
    for line in after:
        page.insert_text((LEFT, y), line, fontsize=8)
        y += LINE
    for index, line in enumerate(bottom):
        page.insert_text((LEFT, height - 22 - LINE * (len(bottom) - 1 - index)), line,
                         fontsize=8)


def _read(tmp_path, sheets, name="packet.pdf"):
    document = pymupdf.open()
    for sheet in sheets:
        _sheet(document, **sheet)
    path = tmp_path / name
    document.save(path)
    document.close()
    return run_pipeline(path, use_vision=False, profiles_dir=tmp_path / "profiles")


def _rasterise(source_path, target_path, pages=None, dpi=60):
    source = pymupdf.open(source_path)
    out = pymupdf.open()
    for index, page in enumerate(source):
        if pages is None or index + 1 in pages:
            pixmap = page.get_pixmap(dpi=dpi)
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=pixmap)
        else:
            out.insert_pdf(source, from_page=index, to_page=index)
    out.save(target_path)
    out.close()
    source.close()
    return target_path


def _payload(headers, rows, label=None, position="footer", heading=None):
    page_label = None
    if label:
        number, count = label
        page_label = {"text": f"Page {number} of {count}", "number": number,
                      "of": count, "position": position}
    return {
        "headers": list(headers),
        "rows": [{"cells": list(row), "kind": "data"} for row in rows],
        "printed_claim_count": None,
        "valuation_date": "12/31/2022",
        "page_label": page_label,
        "report_heading": heading,
    }


def _read_scanned(tmp_path, sheets, payloads, rasterise=None):
    document = pymupdf.open()
    for sheet in sheets:
        _sheet(document, **sheet)
    digital = tmp_path / "digital.pdf"
    document.save(digital)
    document.close()
    scanned = _rasterise(digital, tmp_path / "scanned.pdf", rasterise)

    def extractor(path, pages, **kwargs):
        return VisionExtraction(
            tables=[parse_vision_response(payloads[page], page) for page in pages],
            failures={})

    return run_pipeline(scanned, use_vision=True, vision_extractor=extractor,
                        profiles_dir=tmp_path / "profiles")


def _claim_read_or_named(result, number):
    named = [f.rule_id for f in result.reconciliation.findings if number in f.message]
    return number in _numbers(result) or bool(named), named


# ==========================================================================
# (a) Claims silently lost across runs
# ==========================================================================


def test_a1_unnumbered_digital_packet_claim_without_amounts_is_not_silent(tmp_path):
    """Neither report numbers its pages, so the packet is one vote and the
    smaller report's claim is refused. It prints a date and a status but no
    figures, so R-23 has nothing to report; R-29 covers only vision rows; R-28
    needs an unsettled run. The refused row is recorded in
    refused_claim_rows, but no finding names it and the document is CLEAN
    (only R-18/R-19 warnings)."""
    large = _large_run(8)
    bare = (SMALL_RUN[0][0], SMALL_RUN[0][1], SMALL_RUN[0][2], "", "", "")
    result = _read(tmp_path, [
        {"top": (LARGE_CARRIER, *LETTER), "rows": large},
        {"top": (SMALL_CARRIER, *LETTER), "headers": SMALL_HEADERS, "rows": [bare]},
    ])
    ok, named = _claim_read_or_named(result, bare[0])
    assert ok, (
        f"CR-40117 neither read nor named. claims={_numbers(result)} "
        f"status={result.reconciliation.status.value} rules={_rules(result)} "
        f"refused={[r.identifier for r in getattr(result.document, 'refused_claim_rows', [])]} "
        f"last description={result.document.claims[-1].loss_description!r}"
    )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize("amounts", [True, False], ids=["amounts", "no-amounts"])
def test_a2_packet_stamp_carries_an_unnumbered_report_into_the_previous_run(
    tmp_path, amounts
):
    """A merge tool stamps "Page k of 3" in every footer of the packet; report
    A numbers itself in its header, report B does not. B's only label is the
    stamp, which continues A's stamp track, so B joins A's run and is
    outvoted -- P1-1 again. Without amounts nothing names the claim."""
    large = _large_run(16)
    small = [row if amounts else (*row[:3], "", "", "") for row in SMALL_RUN[:2]]
    result = _read(tmp_path, [
        {"top": ("Page 1 of 2", LARGE_CARRIER, *LETTER), "rows": large[:8],
         "bottom": ("Packet page 1 of 3",)},
        {"top": ("Page 2 of 2", LARGE_CARRIER, *LETTER), "rows": large[8:],
         "bottom": ("Packet page 2 of 3",)},
        {"top": (SMALL_CARRIER, *LETTER), "headers": SMALL_HEADERS, "rows": small,
         "bottom": ("Packet page 3 of 3",)},
    ])
    ok = all(_claim_read_or_named(result, row[0])[0] for row in small)
    assert ok and _numbers(result) == [*(r[0] for r in large), *(r[0] for r in small)], (
        f"runs={_runs(result)} claims={_numbers(result)} "
        f"status={result.reconciliation.status.value} rules={_rules(result)}"
    )


@pytest.mark.parametrize("amounts", [True, False], ids=["amounts", "no-amounts"])
def test_a3_same_carrier_other_policy_unnumbered_is_loud(tmp_path, amounts):
    """GUARD (passes): same top line, different policy and claim shape. By
    design this is unsettled; the claim is refused but R-28 names it."""
    large = _large_run(8)
    small = SMALL_RUN[0] if amounts else (*SMALL_RUN[0][:3], "", "", "")
    result = _read(tmp_path, [
        {"top": ("Page 1 of 1", LARGE_CARRIER, "Policy AU-1001", *LETTER), "rows": large},
        {"top": (LARGE_CARRIER, "Policy GL-2002", *LETTER), "headers": SMALL_HEADERS,
         "rows": [small]},
    ])
    read = small[0] in _numbers(result)
    ok, named = _claim_read_or_named(result, small[0])
    assert read or (ok and result.reconciliation.status is DocumentStatus.NEEDS_REVIEW), (
        _numbers(result), _rules(result))


# ==========================================================================
# (b) False page furniture still splitting
# ==========================================================================


def test_b1_back_reference_in_the_header_band_of_an_unlabelled_page(tmp_path):
    """Page 2 prints no number of its own, only "Continued from Page 1 of 2"
    under the letterhead (inside the top 15%). With one label on the page the
    one-number-per-count rule has nothing to compare, so the back-reference
    opens a settled run; page 2 votes alone and the dated cause code (and the
    damaged number) become claims."""
    large = _large_run(8)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 2", LARGE_CARRIER, *LETTER), "rows": large},
        {"top": (LARGE_CARRIER, "Continued from Page 1 of 2"),
         "rows": [DAMAGED_CLAIM, DATED_CODE]},
    ])
    assert DATED_CODE[0] not in _numbers(result), (
        f"runs={_runs(result)} claims={_numbers(result)}")


def test_b2_second_numbering_with_another_count_in_the_footer_band(tmp_path):
    """Page 2 of 2 of one report also prints, in its bottom band, a line from
    an attached form: "Certification of loss experience - Page 1 of 1". Counts
    differ, so both labels are kept and any index-1 label opens a run: the
    report splits, and the grand total on page 2 is checked against page 2's
    claims only."""
    large = _large_run(8)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 2", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": ("Page 2 of 2", LARGE_CARRIER, *LETTER), "rows": large[4:],
         "total": _total(large),
         "bottom": ("Certification of loss experience - Page 1 of 1",)},
    ])
    assert _runs(result) == [], f"runs={_runs(result)} rules={_rules(result)}"
    assert result.reconciliation.status is DocumentStatus.CLEAN, _rules(result)


def test_b3_back_reference_in_the_footer_band_of_an_unlabelled_page(tmp_path):
    """As b1, with the back-reference in the bottom band: "Claims shown
    include those listed on Page 1 of 2". (A line starting "Grand total ..."
    is taken for the table's total row and excluded, so it is worded as prose.)
    It is the page's only label, so it opens a settled run and page 2 votes
    alone."""
    large = _large_run(8)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 2", LARGE_CARRIER, *LETTER), "rows": large},
        {"top": (LARGE_CARRIER, *LETTER), "rows": [DAMAGED_CLAIM, DATED_CODE],
         "bottom": ("Claims shown include those listed on Page 1 of 2",)},
    ])
    assert DATED_CODE[0] not in _numbers(result), (
        f"runs={_runs(result)} claims={_numbers(result)}")


# ==========================================================================
# (c) Unnumbered trailing report variants
# ==========================================================================


def test_c1_addendum_without_a_letterhead_is_taken_for_another_report(tmp_path):
    """After "Page 2 of 2" an unnumbered page prints only the table. With no
    letterhead, the top line of its header band is the column-label row, so
    its identity differs from the carrier's and it is settled as a new report
    (INFERRED), voted on alone: the dated cause code becomes a claim. The same
    page under the carrier's letterhead is held unsettled (R-28) instead."""
    large = _large_run(8)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 2", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": ("Page 2 of 2", LARGE_CARRIER, *LETTER), "rows": large[4:]},
        {"top": (), "rows": [DAMAGED_CLAIM, DATED_CODE]},
    ])
    assert DATED_CODE[0] not in _numbers(result), (
        f"runs={_runs(result)} claims={_numbers(result)}")


def test_c2_trailing_report_with_no_header_text_is_read_or_named(tmp_path):
    """No letterhead at all on the trailing page: an addendum to the finished
    report or another report, and nothing printed says which. The page is
    unsettled -- its claim is read, or named, and the document reviewed."""
    large = _large_run(8)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 2", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": ("Page 2 of 2", LARGE_CARRIER, *LETTER), "rows": large[4:]},
        {"top": (), "headers": SMALL_HEADERS, "rows": [SMALL_RUN[0]]},
    ])
    ok, _named = _claim_read_or_named(result, SMALL_RUN[0][0])
    assert ok and result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    assert [row[0] for row in large] == _numbers(result)[:len(large)]


def test_c3_generic_top_line_shared_by_two_carriers(tmp_path):
    """Both carriers' systems print "LOSS RUN REPORT" as the top line and the
    carrier below it. Identity is the top line only, so a second carrier's
    unnumbered report after "Page 1 of 1" is unsettled and its claim refused.
    Loud (R-28) -- recorded as a limitation; this asserts it is read."""
    large = _large_run(8)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 1", "LOSS RUN REPORT", LARGE_CARRIER, LETTER[1]), "rows": large},
        {"top": ("LOSS RUN REPORT", SMALL_CARRIER, LETTER[1]), "headers": SMALL_HEADERS,
         "rows": [SMALL_RUN[0]]},
    ])
    assert SMALL_RUN[0][0] in _numbers(result), (
        f"runs={_runs(result)} rules={_rules(result)}")


# ==========================================================================
# (d) Vision packets
# ==========================================================================


@pytest.mark.xfail(strict=True, reason=(
    "unchanged from main: where nothing is numbered each reader keeps its own "
    "claim-number vote, so a scanned page's dated cause code is voted on by "
    "that page's rows alone"))
def test_d1_scanned_continuation_of_an_unnumbered_digital_report(tmp_path):
    """No page anywhere is numbered, so each reader keeps its own vote: the
    scanned page 2 of a digital report votes alone and the dated cause code
    becomes a claim (the P1-3 sibling, surviving where nothing is numbered)."""
    large = _large_run(8)
    result = _read_scanned(
        tmp_path,
        [{"top": (LARGE_CARRIER, *LETTER), "rows": large},
         {"top": (LARGE_CARRIER, *LETTER), "rows": [DAMAGED_CLAIM, DATED_CODE]}],
        {2: _payload(LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], heading=LARGE_CARRIER)},
        rasterise={2},
    )
    assert result.document.scanned_pages == [2]
    assert DATED_CODE[0] not in _numbers(result), _numbers(result)


def test_d2_scanned_report_with_a_label_missing_on_its_first_page(tmp_path):
    """Scanned A (1 of 1), scanned B whose page 1 label the model missed and
    whose page 2 reads "2 of 2"; the model gives no heading on B's page 1."""
    large = _large_run(16)
    b1, b2 = [SMALL_RUN[0]], [SMALL_RUN[1]]
    result = _read_scanned(
        tmp_path,
        [{"top": ("Page 1 of 1", LARGE_CARRIER, *LETTER), "rows": large},
         {"top": ("Page 1 of 2", SMALL_CARRIER, *LETTER), "headers": SMALL_HEADERS, "rows": b1},
         {"top": ("Page 2 of 2", SMALL_CARRIER, *LETTER), "headers": SMALL_HEADERS, "rows": b2}],
        {1: _payload(LARGE_HEADERS, large, (1, 1), heading=LARGE_CARRIER),
         2: _payload(SMALL_HEADERS, b1),
         3: _payload(SMALL_HEADERS, b2, (2, 2), heading=SMALL_CARRIER)},
    )
    assert _numbers(result) == [*(r[0] for r in large), b1[0][0], b2[0][0]], (
        f"runs={_runs(result)} rules={_rules(result)}")


def test_d3_scanned_trailing_report_unlabelled_without_heading_is_loud(tmp_path):
    """GUARD: vision reads no label and no heading on the trailing report;
    it must be read or named."""
    large = _large_run(8)
    result = _read_scanned(
        tmp_path,
        [{"top": ("Page 1 of 1", LARGE_CARRIER, *LETTER), "rows": large},
         {"top": (SMALL_CARRIER, *LETTER), "headers": SMALL_HEADERS, "rows": [SMALL_RUN[0]]}],
        {1: _payload(LARGE_HEADERS, large, (1, 1), heading=LARGE_CARRIER),
         2: _payload(SMALL_HEADERS, [SMALL_RUN[0]])},
    )
    ok, _named = _claim_read_or_named(result, SMALL_RUN[0][0])
    assert ok and result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# ==========================================================================
# (e) Single-run documents whose behaviour changes vs 284fe8f
# ==========================================================================


def test_e1_report_numbering_only_its_first_page(tmp_path):
    """One report, "Page 1 of 3" on page 1 only, grand total on page 3. At
    284fe8f: one run, CLEAN. Pages 2-3 now form an unsettled run: R-28, and
    the grand total is checked against pages 2-3 only (R-04, R-05)."""
    large = _large_run(12)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 3", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": (LARGE_CARRIER, *LETTER), "rows": large[4:8]},
        {"top": (LARGE_CARRIER, *LETTER), "rows": large[8:], "total": _total(large),
         "after": ("Number of claims: 12",)},
    ])
    assert result.reconciliation.status is DocumentStatus.CLEAN, (
        f"runs={_runs(result)} rules={_rules(result)}")


def test_e2_last_page_number_printed_straight_under_a_short_table(tmp_path):
    """A generator that flows its footer: on the short last page "Page 2 of 2"
    sits right under the totals, mid-page, outside both bands, so it is
    ignored. Page 2 is then unnumbered after "1 of 2": unsettled."""
    large = _large_run(8)
    result = _read(tmp_path, [
        {"top": (LARGE_CARRIER, *LETTER), "rows": large[:6],
         "bottom": ("Page 1 of 2",)},
        {"top": (LARGE_CARRIER, *LETTER), "rows": large[6:], "total": _total(large),
         "after": ("Page 2 of 2",)},
    ])
    assert result.reconciliation.status is DocumentStatus.CLEAN, (
        f"runs={_runs(result)} rules={_rules(result)}")


def test_e3_sections_under_changing_top_lines_with_a_grand_total(tmp_path):
    """One carrier's account loss run, one section per policy, each numbered
    from 1 under a top line naming the policy; the grand total closes the last
    section. Different top lines make separate runs, and the grand total is
    checked against the last section's claims alone."""
    a, b = _large_run(6), _large_run(4, first=71005500)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 1", "Loss Run - Policy AU-1001", LARGE_CARRIER, *LETTER),
         "rows": a},
        {"top": ("Page 1 of 1", "Loss Run - Policy AU-1002", LARGE_CARRIER, *LETTER),
         "rows": b, "total": _total([*a, *b])},
    ])
    assert "R-04" not in _rules(result), f"runs={_runs(result)} rules={_rules(result)}"


def test_e4_cover_page_with_a_large_loss_table(tmp_path):
    """An unnumbered large-loss summary listing two of the report's claims,
    ahead of the report's "Page 1 of 2". At 284fe8f the repeats are one
    run's duplicates (R-11). Now the cover is its own run: the duplicates
    are in two runs, R-11 is silent, and the packet can read CLEAN with
    those claims counted twice."""
    large = _large_run(8)
    repeated = large[:2]
    result = _read(tmp_path, [
        {"top": ("LARGE LOSS SUMMARY", LARGE_CARRIER), "rows": repeated},
        {"top": ("Page 1 of 2", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": ("Page 2 of 2", LARGE_CARRIER, *LETTER), "rows": large[4:],
         "total": _total(large)},
    ])
    numbers = _numbers(result)
    duplicated = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not duplicated or result.reconciliation.status is DocumentStatus.NEEDS_REVIEW, (
        f"duplicated={duplicated} runs={_runs(result)} "
        f"status={result.reconciliation.status.value} rules={_rules(result)}")


def test_e5_one_claim_on_the_last_page_hides_its_own_footer(tmp_path):
    """One report; its last page holds a single claim and no totals row, and
    prints "Page 2 of 2" at the foot of the sheet. The word-position table
    takes that footer line for a data row (claim-number cell "Page 2 of 2"),
    and page_evidence excludes words inside table rows, so the page's own
    number is "ignored": page 2 is unnumbered after "1 of 2", unsettled."""
    large = _large_run(9)
    result = _read(tmp_path, [
        {"top": (LARGE_CARRIER, *LETTER), "rows": large[:8], "bottom": ("Page 1 of 2",)},
        {"top": (LARGE_CARRIER, *LETTER), "rows": large[8:], "bottom": ("Page 2 of 2",)},
    ])
    assert result.reconciliation.status is DocumentStatus.CLEAN, (
        f"runs={_runs(result)} rules={_rules(result)} why="
        f"{[run.ambiguity for run in getattr(result.document, 'runs', []) if run.ambiguous]}")


def test_e6_every_page_prints_page_1_of_1_letterhead_on_page_one_only(tmp_path):
    """Pages exported one at a time and combined: each prints "Page 1 of 1";
    only page 1 carries the letterhead, so page 2's identity is its
    column-label row. A restart under a different top line is a new report:
    the grand total on page 2 is checked against page 2's claims alone."""
    large = _large_run(8)
    result = _read(tmp_path, [
        {"top": ("Page 1 of 1", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": ("Page 1 of 1",), "rows": large[4:], "total": _total(large)},
    ])
    assert result.reconciliation.status is DocumentStatus.CLEAN, (
        f"runs={_runs(result)} rules={_rules(result)}")
