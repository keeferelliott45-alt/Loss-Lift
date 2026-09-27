"""Regressions for the three P1 findings on 284fe8f, end to end.

These use only what every revision of the pipeline offers -- run_pipeline, the
vision response parser, the claims and unplaced rows it returns, and the
status -- so they can be run unchanged against the revision the findings were
made on, where each fails for the reason given, and against this one.

1. An unnumbered report bound after a numbered one joined the numbered one's
   run, so the larger claim series outvoted it and its claims were refused.
2. "Page 1 of N" anywhere in a page's text -- a paragraph, a table cell --
   opened a report, splitting one report in two and letting a continuation
   code vote itself into a claim.
3. Scanned pages carried no run evidence at all, so every scanned report in a
   packet shared one vote and the smaller one's claims were refused.

Everything is synthetic (spec section 9): invented carriers, numbers, amounts.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from core.extract_vision import VisionExtraction, parse_vision_response
from core.pipeline import run_pipeline
from core.schema import DocumentStatus
from tests.test_packet_claim_series import (
    CAUSE_CODES,
    COLUMNS,
    LARGE_CARRIER,
    LARGE_HEADERS,
    LEFT,
    LINE,
    SMALL_CARRIER,
    SMALL_HEADERS,
    SMALL_RUN,
    _assert_every_claim_read,
    _large_run,
    _read,
)

THIRD_CARRIER = "BLUE LEDGER CASUALTY COMPANY"

#: A claim whose number was printed run into the text beside it, and the dated
#: continuation line under it. Neither shape recurs, so a vote taken among
#: these two rows alone accepts both -- and the cause code becomes a claim.
DAMAGED_CLAIM = ("71004418CL", "04/14/2022", "CLOSED", "1,000.00", "0.00", "1,000.00")
DATED_CODE = ("0HA-MATERIAL", "11/23/2022", "", "", "", "")


def _numbers(result) -> list[str]:
    return [claim.claim_number for claim in result.document.claims]


# --------------------------------------------------------------------------
# A page builder that can print a page marker in the header or the footer,
# prose in the body, an extra description column, and a totals row.
# --------------------------------------------------------------------------

DESCRIBED_HEADERS = (*LARGE_HEADERS, "Description")
DESCRIBED_COLUMNS = (*COLUMNS, 510.0)


def _page_ex(document, carrier, headers, rows, *, marker=None, footer=None, notes=(),
             columns=COLUMNS, width=612, total=None, heading=True, body=()):
    page = document.new_page(width=width, height=792)
    y = 36.0
    if marker:
        page.insert_text((LEFT, y), marker, fontsize=8)
    y += LINE
    lines = (carrier, "LOSS RUN REPORT", "Valuation Date: 12/31/2022") if heading else ()
    for line in lines:
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y = max(y, 36.0 + 4 * LINE)
    for line in notes:
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(columns, headers):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in rows:
        for offset, cell in zip(columns, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    if total:
        for offset, cell in zip(columns, total):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    y += LINE
    for line in body:
        page.insert_text((LEFT, max(y, 400)), line, fontsize=9)
        y = max(y, 400) + LINE
    if footer:
        page.insert_text((LEFT, 770), footer, fontsize=8)


def _write_ex(path, pages):
    document = pymupdf.open()
    for carrier, headers, rows, options in pages:
        _page_ex(document, carrier, headers, rows, **options)
    document.save(path)
    document.close()
    return path


def _read_ex(tmp_path, pages, name="packet.pdf", **kwargs):
    return run_pipeline(
        _write_ex(tmp_path / name, pages),
        profiles_dir=tmp_path / "profiles",
        **{"use_vision": False, **kwargs},
    )


def _total(rows):
    """A totals row printed for these rows, as the carrier prints it."""
    paid = sum(Decimal(row[3].replace(",", "")) for row in rows)
    reserve = sum(Decimal(row[4].replace(",", "")) for row in rows)
    incurred = sum(Decimal(row[5].replace(",", "")) for row in rows)
    return ("TOTAL", "", "", f"{paid:,.2f}", f"{reserve:,.2f}", f"{incurred:,.2f}")


# ==========================================================================
# P1-1  An unnumbered report bound after a numbered one
# ==========================================================================


@pytest.mark.parametrize("small_count, large_count", [(1, 8), (2, 16), (3, 12)])
def test_p1_1_unnumbered_report_after_a_numbered_one_keeps_its_claims(
    tmp_path, small_count, large_count
):
    """A report that printed its last page ("2 of 2") has ended. The next page
    prints no number under another carrier's heading, so it is another report,
    voted on by its own claims -- not outvoted by the numbered report's."""
    small, large = SMALL_RUN[:small_count], _large_run(large_count)
    half = len(large) // 2
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large[:half], {"number": 1, "of": 2}),
        (LARGE_CARRIER, LARGE_HEADERS, large[half:], {"number": 2, "of": 2}),
        (SMALL_CARRIER, SMALL_HEADERS, small),          # no page numbers printed
    ])
    _assert_every_claim_read(result, [row[0] for row in (*large, *small)])


def test_p1_1_unnumbered_report_between_numbered_ones_keeps_its_claims(tmp_path):
    large = _large_run(8)
    tail = _large_run(4, first=71009900)
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"number": 1, "of": 1}),
        (SMALL_CARRIER, SMALL_HEADERS, SMALL_RUN[:1]),
        (THIRD_CARRIER, LARGE_HEADERS, tail, {"number": 1, "of": 1}),
    ])
    _assert_every_claim_read(
        result, [*(r[0] for r in large), SMALL_RUN[0][0], *(r[0] for r in tail)]
    )


def test_p1_1_detail_block_after_n_of_n_reads_the_claim_and_not_its_codes(tmp_path):
    """The trailing report is a detail block: the claim line, then undated
    cause codes. Its own vote is taken over rows that read as claims, so the
    claim is read and the codes stay part of its description."""
    large = _large_run(16)
    block = [
        SMALL_RUN[0],
        *((code, "", "STRUCK BY", "FALLING", "STOCK IN", "AISLE") for code in CAUSE_CODES),
    ]
    result = _read(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large[:4], {"number": 1, "of": 2}),
        (LARGE_CARRIER, LARGE_HEADERS, large[4:], {"number": 2, "of": 2}),
        (SMALL_CARRIER, SMALL_HEADERS, block),
    ])
    numbers = _numbers(result)
    assert not set(CAUSE_CODES) & set(numbers), numbers
    assert numbers == [*(row[0] for row in large), SMALL_RUN[0][0]], numbers


# ==========================================================================
# P1-2  "Page 1 of N" printed in body prose or a table cell
# ==========================================================================


def _report_with_marker_on_page_two(notes=(), described=False, cell_note=None, body=()):
    large = _large_run(8)
    damaged, code = DAMAGED_CLAIM, DATED_CODE
    options = {}
    headers = LARGE_HEADERS
    if described:
        headers = DESCRIBED_HEADERS
        options = {"columns": DESCRIBED_COLUMNS, "width": 792}
        large = [(*row, "REAR ENDED") for row in large]
        damaged = (*damaged, cell_note or "BACKED INTO POLE")
        code = (*code, "")
    return [
        (LARGE_CARRIER, headers, large, {"marker": "Page 1 of 2", **options}),
        (LARGE_CARRIER, headers, [damaged, code],
         {"marker": "Page 2 of 2", "notes": notes, "body": body, **options}),
    ], large


@pytest.mark.parametrize("variant", ["control", "prose", "prose-near-top", "cell"])
def test_p1_2_page_one_of_n_in_body_text_does_not_split_the_report(tmp_path, variant):
    """Only numbering in the page's furniture counts. A sentence ("continued
    from Page 1 of 2") or a cell ("SEE PAGE 1 OF 2") is the page talking about
    a page: it is seen, recorded as ignored, and splits nothing. A sentence
    set just under the letterhead sits in the header band, and is refused
    there by the page's own number: one page cannot be both 1 and 2 of 2."""
    if variant == "control":
        pages, large = _report_with_marker_on_page_two()
    elif variant == "prose":
        pages, large = _report_with_marker_on_page_two(
            body=("Policy terms continued from Page 1 of 2",)
        )
    elif variant == "prose-near-top":
        pages, large = _report_with_marker_on_page_two(
            notes=("Policy terms continued from Page 1 of 2",)
        )
    else:
        pages, large = _report_with_marker_on_page_two(
            described=True, cell_note="SEE PAGE 1 OF 2"
        )
    result = _read_ex(tmp_path, pages)
    document = result.document

    assert DATED_CODE[0] not in _numbers(result), _numbers(result)
    assert _numbers(result) == [row[0] for row in large]
    unplaced = [row for row in document.unplaced_rows if row.page == 2]
    assert len(unplaced) == 1, "the damaged claim's figures must be reported"
    assert unplaced[0].parsed_amounts.get("incurred_total") == Decimal("1000.00")
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# ==========================================================================
# P1-3  Scanned and mixed packets
# ==========================================================================


def _rasterise(source_path, target_path, *, pages=None, dpi=60):
    """Replace pages with pictures of themselves: no text layer, so vision."""
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


def _payload(headers, rows, marker=None, *, position="footer", heading=None):
    label = None
    if marker:
        _page, number, _of, count = marker.split()
        label = {"text": marker, "number": int(number), "of": int(count), "position": position}
    return {
        "headers": list(headers),
        "rows": [{"cells": list(row), "kind": "data"} for row in rows],
        "printed_claim_count": None,
        "valuation_date": "12/31/2022",
        "page_label": label,
        "report_heading": heading,
    }


def _vision_reads(payloads):
    def extractor(path, pages, **kwargs):
        return VisionExtraction(
            tables=[parse_vision_response(payloads[page], page) for page in pages],
            failures={},
        )
    return extractor


def _scanned(tmp_path, pages, *, rasterise=None):
    digital = _write_ex(tmp_path / "digital.pdf", pages)
    return _rasterise(digital, tmp_path / "scanned.pdf", pages=rasterise)


def test_p1_3_two_scanned_reports_keep_their_own_claim_series(tmp_path):
    """The vision model reports each scanned page's numbering. Each report
    opens at its own page 1 and is voted on alone."""
    small, large = SMALL_RUN[:1], _large_run(8)
    scanned = _scanned(tmp_path, [
        (SMALL_CARRIER, SMALL_HEADERS, small, {"marker": "Page 1 of 1"}),
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 1"}),
    ])
    extractor = _vision_reads({
        1: _payload(SMALL_HEADERS, small, "Page 1 of 1", heading=SMALL_CARRIER),
        2: _payload(LARGE_HEADERS, large, "Page 1 of 1", heading=LARGE_CARRIER),
    })
    result = run_pipeline(scanned, use_vision=True, vision_extractor=extractor,
                          profiles_dir=tmp_path / "profiles")
    document = result.document
    assert document.scanned_pages == [1, 2]
    assert _numbers(result) == [row[0] for row in (*small, *large)]
    assert document.unplaced_rows == []
    assert "R-23" not in result.reconciliation.rule_ids()


@pytest.mark.parametrize("amounts", [True, False], ids=["with-amounts", "no-amounts"])
def test_p1_3_unlabelled_scans_fail_closed(tmp_path, amounts):
    """Nothing on the scanned pages bounds the reports, so they are one vote.
    The smaller report's claim may be refused by it -- but never quietly: the
    document needs review and a finding names the claim. Without amounts
    the refused row carried no money for R-23 to report, and nothing did."""
    small = [row if amounts else (*row[:3], "", "", "") for row in SMALL_RUN[:1]]
    large = _large_run(8)
    scanned = _scanned(tmp_path, [
        (SMALL_CARRIER, SMALL_HEADERS, small, {}),
        (LARGE_CARRIER, LARGE_HEADERS, large, {}),
    ])
    extractor = _vision_reads({
        1: _payload(SMALL_HEADERS, small),
        2: _payload(LARGE_HEADERS, large),
    })
    result = run_pipeline(scanned, use_vision=True, vision_extractor=extractor,
                          profiles_dir=tmp_path / "profiles")
    read = SMALL_RUN[0][0] in _numbers(result)
    named = any(SMALL_RUN[0][0] in f.message for f in result.reconciliation.findings)
    assert read or named, "the smaller report's claim vanished without a finding"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.xfail(strict=True, reason=(
    "not one of the three P1s: a single report read partly off a text layer "
    "and partly off a scan keeps main's vote per reader, so the scanned page's "
    "dated cause code is still voted on by that page's rows"))
def test_p1_3_a_scanned_continuation_of_a_digital_report_shares_its_vote(tmp_path):
    """A report read partly off a text layer and partly off a scan."""
    large = _large_run(8)
    pages = [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 2"}),
        (LARGE_CARRIER, LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], {"marker": "Page 2 of 2"}),
    ]
    mixed = _scanned(tmp_path, pages, rasterise={2})
    extractor = _vision_reads({
        2: _payload(LARGE_HEADERS, [DAMAGED_CLAIM, DATED_CODE], "Page 2 of 2",
                    position="header", heading=LARGE_CARRIER),
    })
    result = run_pipeline(mixed, use_vision=True, vision_extractor=extractor,
                          profiles_dir=tmp_path / "profiles")
    assert result.document.scanned_pages == [2]
    assert DATED_CODE[0] not in _numbers(result), _numbers(result)
    assert _numbers(result) == [row[0] for row in large]


def test_p1_3_a_digital_report_beside_a_scanned_report_keeps_both(tmp_path):
    small, large = SMALL_RUN[:1], _large_run(8)
    mixed = _scanned(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {"marker": "Page 1 of 1"}),
        (SMALL_CARRIER, SMALL_HEADERS, small, {"marker": "Page 1 of 1"}),
    ], rasterise={2})
    extractor = _vision_reads({2: _payload(SMALL_HEADERS, small, "Page 1 of 1",
                                           heading=SMALL_CARRIER)})
    result = run_pipeline(mixed, use_vision=True, vision_extractor=extractor,
                          profiles_dir=tmp_path / "profiles")
    assert _numbers(result) == [row[0] for row in (*large, *small)]
    assert result.document.unplaced_rows == []


def test_a_mixed_document_without_numbering_votes_per_reader_as_before(tmp_path):
    """No page bounds anything: each reader keeps its own vote, as on main."""
    small, large = SMALL_RUN[:1], _large_run(8)
    mixed = _scanned(tmp_path, [
        (LARGE_CARRIER, LARGE_HEADERS, large, {}),
        (SMALL_CARRIER, SMALL_HEADERS, small, {}),
    ], rasterise={2})
    extractor = _vision_reads({2: _payload(SMALL_HEADERS, small)})
    result = run_pipeline(mixed, use_vision=True, vision_extractor=extractor,
                          profiles_dir=tmp_path / "profiles")
    assert _numbers(result) == [row[0] for row in (*large, *small)]
