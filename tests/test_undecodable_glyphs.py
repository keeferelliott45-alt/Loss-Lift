"""Text printed in a font whose characters cannot be decoded is never read.

A font embedded without a map from its glyphs to characters extracts as
``(cid:N)`` placeholders: the page shows something, and what it shows is
unknown. A carrier's title and a print-date line set in such a font were read
as the carrier ``(cid`` and as a claim whose number was a string of glyph
codes -- an invented claim on a report that printed "NO LOSSES".

Undecodable glyphs are withheld from every reading of the page -- no cell,
claim, carrier or total is made of them -- and the page is reported
unresolved (R-22), because text nobody could read may be anything.

Synthetic PDFs only.
"""

from __future__ import annotations

import pymupdf

from core.pipeline import run_pipeline
from core.review import canonical_status
from core.schema import DocumentStatus

LEFT, LINE = 36.0, 14.0
COLUMNS = (0, 95, 170, 240, 325, 410)
HEADERS = ("Claim Number", "Loss Date", "Status", "Paid Total", "Reserve Total",
           "Incurred Total")
ROWS = (
    ("AG-24101", "01/14/2024", "CLOSED", "1,000.00", "0.00", "1,000.00"),
    ("AG-24102", "02/15/2024", "CLOSED", "1,250.00", "0.00", "1,250.00"),
    ("AG-24103", "03/16/2024", "CLOSED", "1,500.00", "0.00", "1,500.00"),
)
TOTAL = ("TOTAL", "", "", "3,750.00", "0.00", "3,750.00")


def _pdf(tmp_path, rows, total, *, undecodable_title, undecodable_footer):
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_font(fontname="U0", fontbuffer=pymupdf.Font("cjk").buffer)
    y = 40.0
    for line, garbled in (("ASHGROVE MUTUAL INSURANCE COMPANY", undecodable_title),
                          ("LOSS RUN REPORT", False),
                          ("Named Insured: Ridgeway Test Freight LLC", False),
                          ("Valuation Date: 12/31/2024", False)):
        page.insert_text((LEFT, y), line, fontsize=9, fontname="U0" if garbled else "helv")
        y += LINE
    y += LINE
    for x, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + x, y), label, fontsize=8)
    y += LINE
    for row in [*rows, *([total] if total else [])]:
        for x, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + x, y), cell, fontsize=8)
        y += LINE
    if undecodable_footer:
        page.insert_text((LEFT, y + LINE), "Mar 7, 2019 9:16:54 AM", fontsize=8, fontname="U0")
    # Strip the glyph-to-character map and the font program it could be
    # recovered from: what remains extracts as (cid:N) placeholders.
    for xref in range(1, document.xref_length()):
        for key in ("ToUnicode", "FontFile2"):
            if document.xref_get_key(xref, key)[0] != "null":
                document.xref_set_key(xref, key, "null")
    path = tmp_path / "undecodable.pdf"
    document.save(path)
    document.close()
    return path


def _read(tmp_path, rows=ROWS, total=TOTAL, *, title=True, footer=True):
    path = _pdf(tmp_path, rows, total, undecodable_title=title, undecodable_footer=footer)
    return run_pipeline(path, use_vision=False, profiles_dir=tmp_path / "profiles")


def test_the_fixture_prints_undecodable_glyphs(tmp_path):
    import pdfplumber

    path = _pdf(tmp_path, ROWS, TOTAL, undecodable_title=True, undecodable_footer=True)
    with pdfplumber.open(path) as pdf:
        assert "(cid:" in pdf.pages[0].extract_text()


def test_no_claim_is_made_of_undecodable_glyphs(tmp_path):
    result = _read(tmp_path)
    assert [c.claim_number for c in result.document.claims] == [r[0] for r in ROWS]


def test_undecodable_glyphs_are_never_the_carrier(tmp_path):
    result = _read(tmp_path)
    assert "cid" not in (result.document.carrier or "").lower()


def test_a_page_with_undecodable_text_is_reviewed_not_trusted(tmp_path):
    result = _read(tmp_path)
    assert result.document.unresolved_pages == [1]
    assert "decoded" in result.document.unresolved_reasons[1]
    assert "R-22" in result.reconciliation.rule_ids()
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def _no_losses_pdf(tmp_path):
    """A report printing no claims: zero totals, "Total Claims: 0", and a
    print-date line in the undecodable font straight beneath them."""
    document = pymupdf.open()
    page = document.new_page(width=792, height=612)
    page.insert_font(fontname="U0", fontbuffer=pymupdf.Font("cjk").buffer)
    y = 40.0
    for line in ("Ashgrove Claims", "Loss Run Report"):
        page.insert_text((LEFT, y), line, fontsize=12, fontname="U0")
        y += LINE
    for line in ("Insured Name: RIDGEWAY TEST FREIGHT LLC", "Policy Number: ESP730024901",
                 "Policy Period: 07/01/2014 - 07/01/2015"):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for x, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + x, y), label, fontsize=8)
    y += LINE
    for lead in ("", "Total Claims: 0"):
        if lead:
            page.insert_text((LEFT, y), lead, fontsize=8)
        for x in COLUMNS[3:]:
            page.insert_text((LEFT + x, y), "$0.00", fontsize=8)
        y += LINE
    page.insert_text((LEFT, y), "Mar 7, 2019", fontsize=7, fontname="U0")
    page.insert_text((LEFT + COLUMNS[4], y), "12:09 AM", fontsize=7, fontname="U0")
    page.insert_text((LEFT, y + LINE), "NO LOSSES", fontsize=9)
    for xref in range(1, document.xref_length()):
        for key in ("ToUnicode", "FontFile2"):
            if document.xref_get_key(xref, key)[0] != "null":
                document.xref_set_key(xref, key, "null")
    path = tmp_path / "no-losses.pdf"
    document.save(path)
    document.close()
    return path


def test_a_no_losses_report_invents_no_claim(tmp_path):
    result = run_pipeline(_no_losses_pdf(tmp_path), use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    assert result.document.claims == []
    assert "cid" not in (result.document.carrier or "").lower()
    assert result.document.unresolved_pages == [1]
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def test_a_page_without_undecodable_text_is_unaffected(tmp_path):
    result = _read(tmp_path, title=False, footer=False)
    assert result.document.unresolved_pages == []
    assert [c.claim_number for c in result.document.claims] == [r[0] for r in ROWS]
    assert canonical_status(result.reconciliation) is DocumentStatus.CLEAN
