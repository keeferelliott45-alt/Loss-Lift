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

from core.classify import SCANNED_CHAR_THRESHOLD
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


# --------------------------------------------------------------------------
# A PDF written by hand, because no library API produces an inline image.
#
# A picture can be drawn straight into the content stream between BI and EI
# instead of being stored as an XObject and referenced. Nothing about the
# page looks different; the picture is simply not in the page's resources,
# so asking the resources what pictures a page carries answers "none".
# --------------------------------------------------------------------------


def _pdf(pages: list[bytes]) -> bytes:
    """Assemble content streams into a PDF with a correct xref table."""
    objects: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids["
        + b" ".join(b"%d 0 R" % (3 + index) for index in range(len(pages)))
        + b"]/Count %d>>" % len(pages),
    ]
    font_number = 3 + 2 * len(pages)
    for index, _ in enumerate(pages):
        objects.append(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources"
            b"<</Font<</F1 %d 0 R>>>>/Contents %d 0 R>>"
            % (font_number, 3 + len(pages) + index)
        )
    for content in pages:
        objects.append(
            b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream"
        )
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = bytearray(b"%PDF-1.7\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % number + body + b"endobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start,
    )
    return bytes(out)


def _lines(cells) -> bytes:
    """Content-stream text. ``cells`` is (x, top-origin y, text)."""
    return b"".join(
        b"BT /F1 8.5 Tf %.1f %.1f Td (%s) Tj ET\n"
        % (x, 792 - y, text.replace("(", r"\(").replace(")", r"\)").encode())
        for x, y, text in cells
    )


#: A four-pixel grey square, hex-encoded so the stream stays printable. The
#: pixels are irrelevant: nothing reads them, which is the whole point.
_INLINE_IMAGE = (
    b"q\n%.1f 0 0 %.1f %.1f %.1f cm\n"
    b"BI /W 4 /H 4 /CS /G /BPC 8 /F /AHx ID\n" + b"80" * 16 + b">\nEI\nQ\n"
)


def _inline_raster(rect, cells) -> bytes:
    left, top, right, bottom = rect
    return _INLINE_IMAGE % (
        right - left, bottom - top, left, 792 - bottom
    ) + _lines(cells)


def _append_inline_raster(document, rect, cells):
    """Append a page whose picture is drawn into the content stream.

    Built by hand and then read back in, so the rest of the document can be
    made the ordinary way: only the picture needs the hand-written stream.
    """
    with pymupdf.open(stream=_pdf([_inline_raster(rect, cells)]), filetype="pdf") as source:
        document.insert_pdf(source)


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


def test_visible_prose_over_a_picture_is_not_a_scan(tmp_path):
    """This page used to pass for a searchable scan, and it is not one.

    It was written here as one: a full-page picture with words standing on
    it, more of them and more numerate than the rasterised loss run carries,
    asserted to be read. Every word of it is drawn to be seen, so it is prose
    printed over a picture -- and whatever the picture holds, none of this
    says. Reading it as a scan is the false negative Codex reported: enough
    words inside the image to clear any overlap threshold, and no reading of
    the image among them.

    The genuine article is two tests below, and it differs in the only way
    that matters: its text is written invisibly, because the picture already
    shows it.
    """
    document = pymupdf.open()
    _digital_table_page(document)
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(0, 0, 612, 792))
    _text(page, NUMERIC_PROSE, y=120)
    path = _save(document, tmp_path / "visible_prose.pdf")
    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.unresolved_pages
    assert 2 not in result.document.processed_pages
    assert [f.page for f in _r22(result)] == [2]


def test_an_image_free_document_is_unchanged(tmp_path):
    document = pymupdf.open()
    _digital_table_page(document)
    path = _save(document, tmp_path / "plain.pdf")
    result = run_pipeline(path, use_vision=False)
    assert result.document.unresolved_pages == []
    assert _r22(result) == []


# --------------------------------------------------------------------------
# Pictures the resources do not mention, and pictures mentioned twice
# --------------------------------------------------------------------------


@pytest.fixture()
def inline_raster(tmp_path):
    """Page 1 a sound table; page 2 a caption over an inline-image loss run."""
    document = pymupdf.open()
    _digital_table_page(document)
    _append_inline_raster(
        document,
        (40, 150, 572, 740),
        [
            (LEFT, 50, "Appendix 2 - Loss Runs"),
            (LEFT, 64, "Since Policy Year 2018"),
            (LEFT, 78, "(Provided by the carrier)"),
        ],
    )
    return _save(document, tmp_path / "inline.pdf")


def test_the_inline_fixture_is_invisible_to_the_resources(inline_raster):
    """Precondition: the picture is real, dominant, and not in get_images()."""
    document = pymupdf.open(inline_raster)
    page = document[1]
    assert page.get_images(full=True) == [], (
        "the fixture no longer reproduces an inline image"
    )
    blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 1]
    assert len(blocks) == 1
    left, top, right, bottom = blocks[0]["bbox"]
    assert (right - left) * (bottom - top) / (612 * 792) > 0.5
    assert len(page.get_text().strip()) >= SCANNED_CHAR_THRESHOLD, (
        "the page must stay on the digital path"
    )


def test_an_inline_raster_page_is_not_processed(inline_raster):
    result = run_pipeline(inline_raster, use_vision=False)
    assert 2 not in result.document.processed_pages
    assert 2 in result.document.unresolved_pages
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_an_inline_raster_page_keeps_its_identity_and_reason(inline_raster):
    result = run_pipeline(inline_raster, use_vision=False)
    raised = _r22(result)
    assert [f.page for f in raised] == [2]
    finding = raised[0]
    assert finding.condition == "page-2"
    assert finding.severity is Severity.ERROR
    said = f"{finding.message} {finding.actual}".lower()
    assert "vision" not in said, said
    assert "picture" in said, said
    assert {c.claim_number for c in result.document.claims} == {
        "CN-1001", "CN-1002", "CN-1003"
    }


def test_caption_text_does_not_buy_an_inline_raster_page_off(tmp_path):
    """More words beside the picture is not more of the picture read.

    The caption here is longer than the table on page 1 and carries more
    figures than the claims do. None of it is inside the picture, and none of
    it says what the picture holds.
    """
    document = pymupdf.open()
    _digital_table_page(document)
    _append_inline_raster(
        document,
        (40, 170, 572, 750),
        [
            (LEFT, 50, "Appendix 2 - Loss Runs, policy years 2018 through 2023"),
            (LEFT, 64, "Provided by the carrier on 11 February 2024 under cover"),
            (LEFT, 78, "of its letter of 9 February 2024, reference 4417-2024-08."),
            (LEFT, 92, "Totals shown are gross of the 25,000 deductible and are"),
            (LEFT, 106, "stated in USD. 12 claims are listed across 3 policy years."),
        ],
    )
    path = _save(document, tmp_path / "wordy.pdf")
    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.unresolved_pages
    assert [f.page for f in _r22(result)] == [2]


def test_one_image_under_two_resource_names_is_measured_once(tmp_path):
    """Two names for one picture are one picture, asked about once.

    ``get_image_rects`` answers for an xref, not for the name it was reached
    by, so every name repeats every placement. Two placements written by two
    names produced four rectangles; two hundred would produce forty thousand,
    and both geometry passes walk that list for every word on the page.
    """
    from core.classify import _image_rects

    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(40, 40, 200, 120))
    _image(page, pymupdf.Rect(40, 400, 200, 480))
    path = _save(document, tmp_path / "twice.pdf")

    reopened = pymupdf.open(path)
    placed = reopened[0]
    names = placed.get_images(full=True)
    assert len(names) == 2 and len({image[0] for image in names}) == 1, (
        "the fixture no longer reproduces one xref under two names"
    )
    assert len(_image_rects(placed)) == 2, (
        "each placement must be counted once, and both must survive"
    )


def test_both_placements_of_one_image_are_kept(tmp_path):
    """Dedup must not collapse a picture printed twice into one.

    Two halves of one page, each covering a third of it, are together more of
    the page than either is alone -- and if only one survived, a page that is
    mostly picture would measure as a page that is not.
    """
    from core.classify import classify_pdf

    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(0, 0, 612, 300))
    _image(page, pymupdf.Rect(0, 320, 612, 620))
    _text(page, ("Appendix 2 - Loss Runs", "Since Policy Year 2018"), y=700)
    path = _save(document, tmp_path / "halves.pdf")

    classified = next(
        p for p in classify_pdf(path).pages if p.page == 1
    )
    assert classified.image_fraction > 0.5, classified.image_fraction
    assert classified.carries_unread_image


# --------------------------------------------------------------------------
# Which text counts as having read a picture
#
# Geometry cannot answer this. A scan saved with an OCR layer and a raster
# appendix under a stamped label both put words inside the picture, and the
# sparsest genuine scan in the corpus available here carries less text over
# its image than a page of overlaid labels does. Measured on real documents,
# every candidate geometric threshold -- share of words, share of the
# picture's area covered by text, share of its height carrying any -- puts
# genuine scans on both sides of every line that separates the two.
#
# The PDF says which it is. An OCR layer is written in render mode 3, drawn
# invisibly because the picture already shows it; a label a human is meant to
# read is drawn visibly. That is the producer's own statement about what the
# text is for, not an inference from where it sits.
# --------------------------------------------------------------------------


def _ocr_layer(page, lines, rect, size=9):
    """Invisible text, as a scanner writes when it saves a searchable page."""
    y = rect.y0 + 20
    for line in lines:
        page.insert_text((rect.x0 + 20, y), line, fontsize=size, render_mode=3)
        y += LINE


def test_visible_labels_over_a_picture_do_not_read_it(tmp_path):
    """Codex's counterexample: enough words on the image, none of them it.

    Every word here stands inside the picture, so overlap alone calls the
    page read. They are a stamped label, drawn to be seen, and they say
    nothing about the claims table underneath.
    """
    document = pymupdf.open()
    _digital_table_page(document)
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(0, 0, 612, 792))
    _text(
        page,
        (
            "CONFIDENTIAL - FOR UNDERWRITING USE ONLY",
            "Issued 11 February 2024 under reference 4417-2024-08",
            "This copy supersedes the copy issued 9 February 2024",
            "Page 2 of 2. Figures stated in USD, gross of deductible.",
            "Distribution limited to the named recipient. 25,000 limit.",
            "Retain for 7 years in accordance with clause 12 of the RFP.",
        ),
        y=200,
    )
    path = _save(document, tmp_path / "labelled.pdf")

    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.unresolved_pages
    assert 2 not in result.document.processed_pages
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    assert [f.page for f in _r22(result)] == [2]


def test_a_searchable_scan_with_an_ocr_layer_stays_processed(tmp_path):
    """The control the rule above must not break.

    Same geometry as the page before it -- one picture over the whole sheet,
    words standing on it -- and the opposite fact, because the words are the
    picture's own transcription.
    """
    document = pymupdf.open()
    _digital_table_page(document)
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(0, 0, 612, 792))
    _ocr_layer(
        page,
        ("MERIDIAN MUTUAL ASSURANCE", "LOSS RUN REPORT", "CN-1004 10/02/2024 CLOSED"),
        pymupdf.Rect(0, 0, 612, 792),
    )
    path = _save(document, tmp_path / "searchable.pdf")

    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.processed_pages
    assert 2 not in result.document.unresolved_pages
    assert _r22(result) == []


def test_a_sparse_ocr_layer_still_reads_its_picture(tmp_path):
    """A nearly blank scanned sheet is still a scanned sheet.

    The sparsest genuine scan in the corpus available here carries two spans
    over a full-page image. Any rule keyed on how *much* text lies over a
    picture calls that page unread; the layer being a transcription is what
    makes it read, not how much of it there is.
    """
    document = pymupdf.open()
    _digital_table_page(document)
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(0, 0, 612, 792))
    # Two short lines: enough to stay on the digital path, far less than a
    # page of transcription, and still the picture's own reading.
    _ocr_layer(
        page,
        ("Continued overleaf. See attached schedule.", "Page 2 of 2"),
        pymupdf.Rect(0, 0, 612, 792),
    )
    path = _save(document, tmp_path / "sparse_scan.pdf")

    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.processed_pages
    assert _r22(result) == []


def test_a_picture_with_nothing_over_it_stays_unresolved(tmp_path):
    """Nothing claims to have read it, so nothing did."""
    document = pymupdf.open()
    _digital_table_page(document)
    page = document.new_page(width=612, height=792)
    _image(page, pymupdf.Rect(40, 150, 572, 740))
    _text(
        page,
        (
            "Appendix 2 - Loss Runs",
            "Since Policy Year 2018",
            "(Provided by the carrier)",
        ),
    )
    path = _save(document, tmp_path / "bare.pdf")

    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.unresolved_pages
    assert [f.page for f in _r22(result)] == [2]


def test_the_union_is_right_and_does_not_go_cubic(tmp_path):
    """Hundreds of overlapping placements, measured correctly and quickly.

    Compressing the coordinates into a grid and asking every cell which
    rectangles cover it is cubic in the number of rectangles: two hundred
    placements give four hundred columns and four hundred rows, and each of
    those hundred and sixty thousand cells is then asked about all two
    hundred. A sweep down the columns merges each one's spans instead.
    """
    import time

    from core.classify import _covered_fraction

    document = pymupdf.open()
    page = document.new_page(width=612, height=792)

    # Tiled with overlap along one band: the union is exactly the span they
    # cover, so the answer is known without measuring it.
    band = [pymupdf.Rect(i * 2, 100, i * 2 + 4, 300) for i in range(300)]
    expected = (598 + 4) * 200 / (612 * 792)
    assert _covered_fraction(band, page) == pytest.approx(expected, rel=1e-9)

    # Distinct in both directions, which is what makes the grid explode.
    staircase = [pymupdf.Rect(i, i, i + 150, i + 150) for i in range(300)]
    started = time.perf_counter()
    covered = _covered_fraction(staircase, page)
    elapsed = time.perf_counter() - started
    assert 0.0 < covered < 1.0
    assert elapsed < 5.0, f"union took {elapsed:.1f}s; the grid path is back"


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
