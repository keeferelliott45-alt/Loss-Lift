"""A digital page that yielded no table must not be called processed.

``processed_pages`` was seeded from ``extraction.page_texts`` -- the set of
pages raw text came off. Text coming off a page is not the same fact as the
page having been read: a continuation sheet printed without its own header
yields words, no header line, and so no table at all. The page then reported
as successfully processed while every row on it disappeared, and R-22 -- the
rule written for exactly this, which keys on failed, skipped, unresolved and
unaccounted pages -- could not see it because the page sat in ``processed``.

The vision path already draws this distinction: a reader that answers with no
rows marks the page *unresolved*, and R-22's own docstring calls that "the
quiet one". The digital path had the same hole with no such record.

The correction is not to call every page without a table unread. A cover, an
instruction sheet, a separator and a covering letter are pages the extractor
looked at and correctly found no table on; they are accounted for. What must
stay unresolved is the page that presents as columnar data -- the shape the
extractor itself reads tables from -- and still produced none.

Every fixture here is invented: made-up identifiers, no names, no addresses,
no descriptions. They reproduce the structure of the failure, not its content.
"""

from __future__ import annotations

import pdfplumber
import pymupdf
import pytest

from core.extract_digital import extract_page_table, extract_pdf
from core.pipeline import run_pipeline
from core.schema import DocumentStatus, RawRow, RawTable, Severity

LINE = 14.0
LEFT = 40.0
COLUMNS = (0.0, 95.0, 175.0, 245.0, 330.0, 415.0)
HEADERS = ("Claim No", "Date of Loss", "Status", "Paid Total", "Reserve Total", "Total Incurred")

MASTHEAD = (
    "MERIDIAN MUTUAL ASSURANCE",
    "LOSS RUN REPORT",
    "Named Insured: Northwind Fabrication Ltd",
    "Policy Number: GL-4417-2024",
    "Policy Period: 01/01/2024 to 12/31/2024",
    "Valuation Date: 12/31/2024",
)

FIRST_ROWS = (
    ("CN-1001", "03/12/2024", "OPEN", "1,200.00", "3,800.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "2,450.00", "0.00", "2,450.00"),
)
#: The rows that vanish: same columns, same shapes, printed without a header.
CONTINUED_ROWS = (
    ("CN-1003", "09/21/2024", "OPEN", "775.50", "1,224.50", "2,000.00"),
    ("CN-1004", "10/02/2024", "CLOSED", "310.00", "0.00", "310.00"),
)

#: Prose pages. Deliberately wordy, and one of them deliberately full of
#: numbers, so that neither text volume nor the presence of figures can be
#: what decides a page is unread.
COVER_PROSE = (
    "REQUEST FOR PROPOSAL",
    "Property and Casualty Insurance Programme",
    "This document is issued for the purpose of soliciting proposals.",
    "Proposers should read every section before responding to this request.",
    "Questions must be submitted in writing before the stated closing date.",
    "No proposal will be accepted after the closing date stated herein.",
)
NUMERIC_PROSE = (
    "SECTION 4 - LIMITS AND DEDUCTIBLES",
    "The limit of liability shall be 1,000,000 per occurrence and 2,000,000",
    "in the aggregate, subject to a deductible of 25,000 each and every loss.",
    "Coverage 12 applies only where item 7 of the schedule has been completed.",
    "Interest at 4.5 percent per annum applies to late payment under clause 9.",
    "The figure 45292 in appendix 3 is a reference number and not an amount.",
)
#: Long enough to stay above the scanned-page character threshold: a divider
#: that fell under it would be classified a scan and skipped, which is a
#: different outcome under a different rule and not what these tests measure.
SEPARATOR_PROSE = (
    "EXHIBIT B",
    "LOSS INFORMATION",
    "The loss runs for each policy period follow this divider sheet.",
)


def _masthead(page, y):
    for line in MASTHEAD:
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    return y + LINE


def _header_row(page, y):
    for offset, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    return y + LINE


def _data_rows(page, y, rows):
    for row in rows:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    return y


def _prose(page, y, lines):
    for line in lines:
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    return y


def _build(path, pages):
    """Write a PDF from a list of page specs.

    Each spec is ``("table", rows)``, ``("headerless", rows)`` or
    ``("prose", lines)``.
    """
    document = pymupdf.open()
    for kind, payload in pages:
        page = document.new_page(width=612, height=792)
        y = 50.0
        if kind == "table":
            y = _masthead(page, y)
            y = _header_row(page, y)
            _data_rows(page, y, payload)
        elif kind == "headerless":
            _data_rows(page, y, payload)
        elif kind == "prose":
            _prose(page, y, payload)
        else:  # pragma: no cover - guards a typo in a test spec
            raise AssertionError(f"unknown page kind {kind!r}")
    document.save(path)
    document.close()
    return path


@pytest.fixture()
def continuation(tmp_path):
    """Page 1 a headed table, page 2 the same table continued without one."""
    return _build(
        tmp_path / "continuation.pdf",
        [("table", FIRST_ROWS), ("headerless", CONTINUED_ROWS)],
    )


@pytest.fixture()
def fully_read(tmp_path):
    """Both pages carry their own header, so both are genuinely read."""
    return _build(
        tmp_path / "fully_read.pdf",
        [("table", FIRST_ROWS), ("table", CONTINUED_ROWS)],
    )


@pytest.fixture()
def with_cover(tmp_path):
    """A prose cover ahead of a table that reads completely."""
    return _build(
        tmp_path / "with_cover.pdf",
        [("prose", COVER_PROSE), ("table", FIRST_ROWS + CONTINUED_ROWS)],
    )


def _r22(result):
    return [f for f in result.reconciliation.findings if f.rule_id == "R-22"]


# --------------------------------------------------------------------------
# 1. The reproduction itself
# --------------------------------------------------------------------------


def test_the_continuation_page_yields_text_but_no_table(continuation):
    """The precondition: text extraction succeeds, table extraction does not."""
    extraction = extract_pdf(continuation)
    assert extraction.page_texts[2].strip(), "page 2 produced no text at all"
    assert [table.page for table in extraction.tables] == [1], (
        "page 2 was expected to yield no table; the fixture no longer "
        "reproduces the failure"
    )
    with pdfplumber.open(continuation) as pdf:
        assert extract_page_table(pdf.pages[1], 2) is None


def test_the_continuation_rows_do_not_reach_the_claims(continuation):
    """Stated for the record: this unit does not recover the lost rows."""
    result = run_pipeline(continuation, use_vision=False)
    numbers = {claim.claim_number for claim in result.document.claims}
    assert numbers == {"CN-1001", "CN-1002"}


# --------------------------------------------------------------------------
# 2-4. What the page's status must say
# --------------------------------------------------------------------------


def test_a_page_that_produced_no_table_is_not_processed(continuation):
    result = run_pipeline(continuation, use_vision=False)
    assert 2 not in result.document.processed_pages, (
        "page 2 yielded no table and must not be reported as processed"
    )


def test_that_page_is_recorded_unresolved(continuation):
    result = run_pipeline(continuation, use_vision=False)
    assert 2 in result.document.unresolved_pages
    assert 2 not in result.document.failed_pages
    assert 2 not in result.document.skipped_pages


def test_the_document_cannot_read_clean(continuation):
    result = run_pipeline(continuation, use_vision=False)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_r22_names_the_page_with_its_own_provenance(continuation):
    result = run_pipeline(continuation, use_vision=False)
    raised = _r22(result)
    assert [f.page for f in raised] == [2]
    finding = raised[0]
    assert finding.severity is Severity.ERROR
    assert finding.condition == "page-2"
    assert "2" in finding.message


def test_r22_does_not_blame_a_vision_reader_that_never_ran(continuation):
    """The page failed on the digital side, and the finding has to say so.

    ``unresolved_pages`` used to mean one thing -- a vision reader answered
    and returned nothing -- so R-22 described every page in it that way.
    Putting a digital page in the same set made the finding state that a
    model had declined to read a page no model was ever shown, with vision
    switched off entirely. The page was right and the reason was false, which
    sends a reviewer to turn on a reader that would not have helped.
    """
    result = run_pipeline(continuation, use_vision=False)
    finding = _r22(result)[0]
    said = f"{finding.message} {finding.actual}".lower()
    assert "vision" not in said, said
    assert "table" in said, said


# --------------------------------------------------------------------------
# 5-6, 9-10. Pages the extractor correctly found no table on
# --------------------------------------------------------------------------


def test_a_prose_cover_page_stays_accounted_for(with_cover):
    result = run_pipeline(with_cover, use_vision=False)
    assert 1 in result.document.processed_pages
    assert 1 not in result.document.unresolved_pages
    assert _r22(result) == []


def test_a_packet_page_between_two_loss_sections_is_exempt(tmp_path):
    path = _build(
        tmp_path / "interleaved.pdf",
        [
            ("table", FIRST_ROWS),
            ("prose", SEPARATOR_PROSE),
            ("table", CONTINUED_ROWS),
        ],
    )
    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.processed_pages
    assert _r22(result) == []


def test_many_non_table_pages_do_not_become_a_wall_of_findings(tmp_path):
    path = _build(
        tmp_path / "packet.pdf",
        [
            ("prose", COVER_PROSE),
            ("prose", NUMERIC_PROSE),
            ("prose", SEPARATOR_PROSE),
            ("prose", COVER_PROSE),
            ("table", FIRST_ROWS + CONTINUED_ROWS),
        ],
    )
    result = run_pipeline(path, use_vision=False)
    assert _r22(result) == []
    assert set(result.document.unresolved_pages) == set()


def test_text_volume_and_printed_numbers_do_not_make_a_page_unread(tmp_path):
    """Requirement stated directly: neither words nor figures decide this.

    The prose page here is longer than the continuation page and carries more
    printed numbers than it does. If either were the signal, this page would
    be flagged and the real one would not.
    """
    path = _build(
        tmp_path / "numeric_prose.pdf",
        [("table", FIRST_ROWS + CONTINUED_ROWS), ("prose", NUMERIC_PROSE)],
    )
    result = run_pipeline(path, use_vision=False)
    assert 2 in result.document.processed_pages
    assert _r22(result) == []


# --------------------------------------------------------------------------
# 7-8. What must not move
# --------------------------------------------------------------------------


def test_a_fully_extracted_multi_page_table_is_unchanged(fully_read):
    result = run_pipeline(fully_read, use_vision=False)
    assert set(result.document.processed_pages) == {1, 2}
    assert result.document.unresolved_pages == []
    assert _r22(result) == []
    numbers = {claim.claim_number for claim in result.document.claims}
    assert numbers == {"CN-1001", "CN-1002", "CN-1003", "CN-1004"}


def test_vision_empty_result_behaviour_is_unchanged(tmp_path, monkeypatch):
    """A vision reader answering with no rows still marks the page unresolved.

    Built as a one-page scan so the digital path contributes nothing: the only
    page outcome under test is the one the vision reader decides.
    """
    document = pymupdf.open()
    document.new_page(width=612, height=792)  # no text: a scan
    path = tmp_path / "scan.pdf"
    document.save(path)
    document.close()

    from core.extract_vision import VisionExtraction

    def extractor(_path, pages):
        return VisionExtraction(
            tables=[RawTable(page=page, headers=list(HEADERS), rows=[]) for page in pages],
            failures={},
        )

    result = run_pipeline(path, use_vision=True, vision_extractor=extractor)
    assert 1 in result.document.unresolved_pages
    assert 1 not in result.document.processed_pages
    assert [f.page for f in _r22(result)] == [1]
    # The reader that gave up here really was the vision one, and the finding
    # still says so: carrying a reason must not cost the original case its own.
    assert "vision" in _r22(result)[0].message.lower()


def test_a_vision_page_that_returned_rows_stays_processed(tmp_path):
    document = pymupdf.open()
    document.new_page(width=612, height=792)
    path = tmp_path / "scan_rows.pdf"
    document.save(path)
    document.close()

    from core.extract_vision import VisionExtraction

    def extractor(_path, pages):
        return VisionExtraction(
            tables=[
                RawTable(
                    page=page,
                    headers=list(HEADERS),
                    rows=[
                        RawRow(page=page, line_index=1, cells=list(FIRST_ROWS[0])),
                    ],
                )
                for page in pages
            ],
            failures={},
        )

    result = run_pipeline(path, use_vision=True, vision_extractor=extractor)
    assert 1 in result.document.processed_pages
    assert result.document.unresolved_pages == []
