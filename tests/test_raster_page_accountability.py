"""A page whose picture went unread is not a page that was processed.

Classification asks one question of a page -- how many characters came off it
-- and a page carrying a rasterised loss run under a one-line heading answers
it with the heading. Seventy characters clears the scanned-page threshold, so
the page takes the digital path, the table inside the picture is never read,
and the page is recorded as processed because text was extracted from it.

The picture is not evidence of claims and not evidence of their absence. It
is unread source content, and the only honest status for it is unresolved.

What it must not do is flag every page carrying an image. Two kinds of page
look alike by area and are opposites in fact:

* a scan saved with an OCR text layer -- the whole page is one image, and
  every word extracted from it sits *on* that image, because the words are
  the transcription of it. That page has been read.
* a screenshot pasted under a heading -- the image covers most of the page
  and the words sit outside it, because they are a caption, not a reading.
  That page has not.

Measured across the documents available here, image area alone would have
called 67 of one 99-page document's pages unread; every one of them is a
searchable scan whose text layer extracts correctly. Overlap separates them
cleanly: those pages sit at 100% of words on the image, and the rasterised
loss run sits at 0%.

Every fixture is invented, and the pixels inside the images are deliberately
blank: nothing in the system reads them, which is the whole point.
"""

from __future__ import annotations

import pymupdf
import pytest

from core.pipeline import run_pipeline
from core.schema import DocumentStatus, RawRow, RawTable, Severity

LINE = 14.0
LEFT = 40.0
COLUMNS = (0.0, 95.0, 175.0, 245.0, 330.0, 415.0)
HEADERS = ("Claim No", "Date of Loss", "Status", "Paid Total", "Reserve Total", "Total Incurred")
ROWS = (
    ("CN-1001", "03/12/2024", "OPEN", "1,200.00", "3,800.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "2,450.00", "0.00", "2,450.00"),
    ("CN-1003", "09/21/2024", "OPEN", "775.50", "1,224.50", "2,000.00"),
)
MASTHEAD = (
    "MERIDIAN MUTUAL ASSURANCE",
    "LOSS RUN REPORT",
    "Named Insured: Northwind Fabrication Ltd",
    "Policy Number: GL-4417-2024",
    "Policy Period: 01/01/2024 to 12/31/2024",
    "Valuation Date: 12/31/2024",
)
#: Wordy and full of figures, so that neither text volume nor numeric density
#: can be what separates a read page from an unread one.
NUMERIC_PROSE = (
    "SECTION 4 - LIMITS AND DEDUCTIBLES",
    "The limit of liability shall be 1,000,000 per occurrence and 2,000,000",
    "in the aggregate, subject to a deductible of 25,000 each and every loss.",
    "Coverage 12 applies only where item 7 of the schedule has been completed.",
    "Interest at 4.5 percent per annum applies to late payment under clause 9.",
    "The figure 45292 in appendix 3 is a reference number and not an amount.",
)


def _blank_png(width=40, height=20):
    """An image with nothing in it. Nothing reads the pixels; only geometry."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pixmap.set_rect(pixmap.irect, (216, 216, 216))
    return pixmap.tobytes("png")


def _image(page, rect):
    page.insert_image(rect, stream=_blank_png(), keep_proportion=False)


def _text(page, lines, y=50.0, x=LEFT, size=9):
    for line in lines:
        page.insert_text((x, y), line, fontsize=size)
        y += LINE
    return y


def _table(page, y, rows=ROWS):
    for offset, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in rows:
        for offset, cell in zip(COLUMNS, row):
            page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    return y


def _save(document, path):
    document.save(path)
    document.close()
    return path


def _digital_table_page(document):
    page = document.new_page(width=612, height=792)
    y = _text(page, MASTHEAD)
    _table(page, y + LINE)
    return page


def _r22(result):
    return [f for f in result.reconciliation.findings if f.rule_id == "R-22"]


@pytest.fixture()
def raster_loss_run(tmp_path):
    """Page 1 a sound table; page 2 a heading over a rasterised loss run.

    Page 2 is the defect: 70-odd characters of heading, and a picture over
    most of the page that no stage reads. The heading clears the scanned-page
    threshold, so the page never goes to vision.
    """
    document = pymupdf.open()
    _digital_table_page(document)
    page = document.new_page(width=612, height=792)
    # Just over the scanned-page threshold, as the real page was: a heading
    # long enough to keep the page on the digital path and say nothing.
    _text(page, (
        "Appendix 2 - Loss Runs",
        "Since Policy Year 2018",
        "(Provided by the carrier)",
    ))
    # Words sit above the image, not on it: a caption, not a transcription.
    _image(page, pymupdf.Rect(40, 150, 572, 740))
    return _save(document, tmp_path / "raster.pdf")


# --------------------------------------------------------------------------
# 1, 2, 10. The defect
# --------------------------------------------------------------------------


def test_the_fixture_reproduces_the_shape(raster_loss_run):
    """Precondition: the raster page is digital, has text, and is image-heavy."""
    from core.classify import classify_pdf

    classification = classify_pdf(raster_loss_run)
    page = next(p for p in classification.pages if p.page == 2)
    assert not page.is_scanned, "page 2 must take the digital path"
    assert page.char_count >= 20, "page 2 must yield text"
    document = pymupdf.open(raster_loss_run)
    rects = [
        rect
        for image in document[1].get_images(full=True)
        for rect in document[1].get_image_rects(image[0])
    ]
    covered = sum(rect.width * rect.height for rect in rects)
    assert covered / (612 * 792) > 0.5, "the picture must dominate the page"


def test_a_raster_dominant_page_is_not_processed(raster_loss_run):
    result = run_pipeline(raster_loss_run, use_vision=False)
    assert 2 not in result.document.processed_pages
    assert 2 in result.document.unresolved_pages


def test_a_raster_dominant_page_cannot_read_clean(raster_loss_run):
    result = run_pipeline(raster_loss_run, use_vision=False)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_r22_names_the_raster_page_with_its_provenance(raster_loss_run):
    result = run_pipeline(raster_loss_run, use_vision=False)
    raised = _r22(result)
    assert [f.page for f in raised] == [2]
    finding = raised[0]
    assert finding.rule_id == "R-22"
    assert finding.severity is Severity.ERROR
    assert finding.condition == "page-2"
    assert "2" in finding.message
    assert any("2" in warning for warning in result.warnings)


def test_a_read_table_does_not_account_for_a_picture_beside_it(tmp_path):
    """One page, a table that was read and a picture that was not.

    Subtracting every page a table came off treated the table as answering
    for the whole page. A carrier that prints a short summary table above a
    pasted appendix puts both on one sheet: the summary reads, the appendix
    is never looked at, and the page was called processed on the strength of
    the half that worked. The claim it yields makes it worse, not better --
    the document has rows, so nothing else looks twice at it.
    """
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    _text(page, ("MERIDIAN MUTUAL ASSURANCE", "Valuation Date: 12/31/2024"))
    _table(page, 95.0, rows=ROWS[:1])
    # Words all sit above y=150; the picture starts below them.
    _image(page, pymupdf.Rect(40, 250, 572, 780))
    path = _save(document, tmp_path / "mixed.pdf")

    result = run_pipeline(path, use_vision=False)
    assert len(result.document.claims) == 1, "the readable table must still read"
    assert 1 not in result.document.processed_pages
    assert 1 in result.document.unresolved_pages
    assert [f.page for f in _r22(result)] == [1]
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_r22_does_not_blame_a_vision_reader_that_never_ran(raster_loss_run):
    """The picture was never submitted to vision, so vision cannot have failed.

    ``unresolved_pages`` meant one thing when R-22 was written -- a vision
    reader answered and returned nothing -- so the rule described every page
    in it that way. These pages are excluded from ``scanned_pages`` and are
    never shown to any reader, including when vision is off entirely. The
    page was right and the reason was false, which points a reviewer at a
    switch that would not have helped.
    """
    result = run_pipeline(raster_loss_run, use_vision=False)
    finding = _r22(result)[0]
    said = f"{finding.message} {finding.actual}".lower()
    assert "vision" not in said, said
    assert "picture" in said, said


def test_the_claims_on_the_readable_page_are_untouched(raster_loss_run):
    """Stated for the record: this unit reads nothing out of the picture."""
    result = run_pipeline(raster_loss_run, use_vision=False)
    assert {c.claim_number for c in result.document.claims} == {
        "CN-1001", "CN-1002", "CN-1003"
    }


# --------------------------------------------------------------------------
# 3, 4, 5, 9. Images that must not manufacture a finding
# --------------------------------------------------------------------------


def test_a_small_logo_on_a_table_page_stays_processed(tmp_path):
    document = pymupdf.open()
    page = _digital_table_page(document)
    _image(page, pymupdf.Rect(480, 30, 570, 70))
    path = _save(document, tmp_path / "logo.pdf")
    result = run_pipeline(path, use_vision=False)
    assert 1 in result.document.processed_pages
    assert result.document.unresolved_pages == []
    assert _r22(result) == []


def test_a_harmless_image_on_a_cover_page_raises_nothing(tmp_path):
    document = pymupdf.open()
    cover = document.new_page(width=612, height=792)
    _text(cover, (
        "REQUEST FOR PROPOSAL",
        "Property and Casualty Insurance Programme",
        "This document is issued for the purpose of soliciting proposals.",
        "Proposers should read every section before responding to this request.",
    ))
    _image(cover, pymupdf.Rect(150, 300, 460, 500))
    _digital_table_page(document)
    path = _save(document, tmp_path / "cover.pdf")
    result = run_pipeline(path, use_vision=False)
    assert 1 in result.document.processed_pages
    assert _r22(result) == []


def test_a_table_read_over_a_background_image_stays_processed(tmp_path):
    """The picture covers the page, and the table on top of it was read."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(0, 0, 612, 792))
    y = _text(page, MASTHEAD)
    _table(page, y + LINE)
    path = _save(document, tmp_path / "watermark.pdf")
    result = run_pipeline(path, use_vision=False)
    assert 1 in result.document.processed_pages
    assert result.document.unresolved_pages == []
    assert _r22(result) == []


def test_a_searchable_scan_page_is_not_called_unread(tmp_path):
    """Requirement stated directly: image area cannot decide this.

    A full-page image whose words sit on it is a scan saved with a text
    layer. It covers more of the page than the rasterised loss run does and
    carries far more text and more figures, and it has been read.
    """
    document = pymupdf.open()
    _digital_table_page(document)
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(0, 0, 612, 792))
    _text(page, NUMERIC_PROSE, y=120)
    path = _save(document, tmp_path / "searchable.pdf")
    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.processed_pages
    assert 2 not in result.document.unresolved_pages
    assert _r22(result) == []


def test_an_image_free_document_is_unchanged(tmp_path):
    document = pymupdf.open()
    _digital_table_page(document)
    path = _save(document, tmp_path / "plain.pdf")
    result = run_pipeline(path, use_vision=False)
    assert result.document.unresolved_pages == []
    assert _r22(result) == []


# --------------------------------------------------------------------------
# 6, 7, 8. Vision accounting must not move
# --------------------------------------------------------------------------


def _scan_pages(path, count):
    document = pymupdf.open()
    for _ in range(count):
        page = document.new_page(width=612, height=792)
        _image(page, pymupdf.Rect(0, 0, 612, 792))
    return _save(document, path)


def test_vision_success_accounts_for_an_image_page(tmp_path):
    path = _scan_pages(tmp_path / "scan_ok.pdf", 1)
    from core.extract_vision import VisionExtraction

    def extractor(_path, pages):
        return VisionExtraction(
            tables=[
                RawTable(
                    page=page,
                    headers=list(HEADERS),
                    rows=[RawRow(page=page, line_index=1, cells=list(ROWS[0]))],
                )
                for page in pages
            ],
            failures={},
        )

    result = run_pipeline(path, use_vision=True, vision_extractor=extractor)
    assert 1 in result.document.processed_pages
    assert result.document.unresolved_pages == []
    assert _r22(result) == []


def test_vision_failure_is_still_preserved(tmp_path):
    path = _scan_pages(tmp_path / "scan_fail.pdf", 1)
    from core.extract_vision import VisionExtraction

    def extractor(_path, pages):
        return VisionExtraction(tables=[], failures={p: "reader failed" for p in pages})

    result = run_pipeline(path, use_vision=True, vision_extractor=extractor)
    assert 1 in result.document.failed_pages
    assert 1 not in result.document.processed_pages
    assert [f.page for f in _r22(result)] == [1]


def test_partial_vision_success_does_not_absorb_the_failed_page(tmp_path):
    path = _scan_pages(tmp_path / "scan_partial.pdf", 2)
    from core.extract_vision import VisionExtraction

    def extractor(_path, pages):
        return VisionExtraction(
            tables=[
                RawTable(
                    page=1,
                    headers=list(HEADERS),
                    rows=[RawRow(page=1, line_index=1, cells=list(ROWS[0]))],
                ),
                RawTable(page=2, headers=list(HEADERS), rows=[]),
            ],
            failures={},
        )

    result = run_pipeline(path, use_vision=True, vision_extractor=extractor)
    assert 1 in result.document.processed_pages
    assert 2 in result.document.unresolved_pages
    assert [f.page for f in _r22(result)] == [2]


def test_a_scanned_page_is_still_skipped_when_vision_is_off(tmp_path):
    path = _scan_pages(tmp_path / "scan_off.pdf", 1)
    result = run_pipeline(path, use_vision=False)
    assert 1 in result.document.skipped_pages
    assert 1 not in result.document.unresolved_pages
    assert [f.page for f in _r22(result)] == [1]
